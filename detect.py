"""
detect.py - find where a game keeps its saves by watching what it changed during its first session.

No "before" snapshot is needed: after the game closes we look for files created or modified since
it started (Windows records both times), and for registry keys under HKCU\\Software whose
"last written" time is after the start. Noise from other programs (browsers, sync apps, Windows)
is filtered out, and the remaining places are scored by how much they look like this game's saves.
The owner always confirms the result; nothing is set silently.
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from titles import clean_title

# folders (by name, anywhere in the path) that never hold a VN's saves but change constantly
_NOISE_DIRS = {
    "microsoft", "windows", "packages", "temp", "tmp", "cache", "caches", "code cache", "gpucache",
    "shadercache", "dxcache", "glcache", "d3dscache", "crashdumps", "crashpad", "logs", "log",
    "google", "chrome", "mozilla", "firefox", "edge", "brave", "opera software", "discord",
    "onedrive", "dropbox", "google drive", "googledrive", "steam", "steamapps", "nvidia",
    "nvidia corporation", "amd", "intel", "spotify", "slack", "zoom", "teams", "code", "vscode",
    ".vscode", "pip", "npm", "node_modules", "__pycache__", "python", "jetbrains", "adobe",
    "comms", "connecteddevicesplatform", "elevateddiagnostics", "history", "inetcache",
    "ikemenreader", "vnlauncher", "lunatranslator", "lunahook", "locale.emulator", "claude",
    "squirreltemp", "programs", "fontcache", "iconcache", "webcache", "notifications",
    "explorer", "recent", "d3dscache", "unity", "unitycrashhandler", "mediafoundation",
}
_NOISE_FILES = re.compile(r"\.(log|tmp|lock|etl|dmp|pf|db-wal|db-shm|ldb|journal|lnk|crdownload|partial)$|^~\$",
                          re.IGNORECASE)
_SAVE_WORDS = ("save", "セーブ", "userdata", "savedata", "sav")      # strong hint
_WEAK_WORDS = ("データ", "data", "user", "system")                     # weak hint

_REG_NOISE = {"microsoft", "classes", "policies", "wow6432node", "google", "mozilla", "nvidia corporation",
              "intel", "amd", "valve", "discord", "spotify", "registeredapplications", "clients",
              "appdatalow", "oem", "python", "7-zip", "winrar", "jetbrains", "adobe", "zoom"}


@dataclass
class Candidate:
    path: str                 # folder path, or registry key (HKEY_CURRENT_USER\...)
    kind: str                 # "folder" / "registry"
    score: float
    files: int = 0            # changed files (folder) / changed keys (registry)
    reasons: list[str] = field(default_factory=list)


def default_roots(game_dir: str) -> list[Path]:
    env = os.environ
    home = Path.home()
    roots = [Path(game_dir)]
    for v in ("APPDATA", "LOCALAPPDATA", "PUBLIC", "ProgramData"):
        if env.get(v):
            roots.append(Path(env[v]) / ("Documents" if v == "PUBLIC" else ""))
    if env.get("LOCALAPPDATA"):
        roots.append(Path(env["LOCALAPPDATA"]).parent / "LocalLow")
    roots += [home / "Documents", home / "Saved Games", home / "OneDrive" / "Documents"]
    seen, out = set(), []
    for r in roots:
        k = os.path.normcase(str(r))
        if r.is_dir() and k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _tokens(*names: str) -> set[str]:
    """Meaningful name fragments to recognise this game's folders by (title, exe name, maker...)."""
    out = set()
    for n in names:
        if not n:
            continue
        n = unicodedata.normalize("NFKC", n).casefold()
        out.add(re.sub(r"[\s\-_・:：!！?？'\"()（）\[\]【】]", "", n))
        for part in re.split(r"[\s\-_・:：!！?？'\"()（）\[\]【】―－~～/]+", n):
            if len(part) >= 3 or (len(part) >= 2 and not part.isascii()):
                out.add(part)
    return {t for t in out if t and t not in {"the", "game", "exe", "ver", "edition"}}


def _matches(name: str, tokens: set[str]) -> bool:
    n = re.sub(r"[\s\-_・]", "", unicodedata.normalize("NFKC", name).casefold())
    return any(t in n or (len(n) >= 3 and n in t) for t in tokens)


def _walk_changed(root: Path, since: float, skip: set[str], limit: int = 400_000) -> Iterable[Path]:
    """Files under root created/modified at or after `since`, skipping noisy folders."""
    stack, seen = [root], 0
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    seen += 1
                    if seen > limit:
                        return
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if e.name.casefold() not in _NOISE_DIRS and os.path.normcase(e.path) not in skip:
                                stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False) and not _NOISE_FILES.search(e.name):
                            st = e.stat(follow_symlinks=False)
                            if max(st.st_mtime, st.st_ctime) >= since:
                                yield Path(e.path)
                    except OSError:
                        continue
        except OSError:
            continue


def find_folders(game_dir: str, since: float, names: list[str], skip: Iterable[str] = (),
                 roots: Optional[list[Path]] = None) -> list[Candidate]:
    tokens = _tokens(*names)
    skip_n = {os.path.normcase(os.path.abspath(s)) for s in skip if s}
    game_dir_n = os.path.normcase(os.path.abspath(game_dir))
    roots = roots if roots is not None else default_roots(game_dir)
    root_set = {os.path.normcase(str(r)) for r in roots}
    by_dir: dict[str, list[Path]] = defaultdict(list)
    for root in roots:
        # the game folder is a root itself; don't walk it again as part of a bigger root
        for f in _walk_changed(root, since, skip_n | ({game_dir_n} if os.path.normcase(str(root)) != game_dir_n else set())):
            by_dir[os.path.normcase(str(f.parent))].append(f)

    cands: dict[str, Candidate] = {}
    for d, files in by_dir.items():
        if d in root_set and d != game_dir_n:
            continue                     # loose files directly in %APPDATA% etc. aren't a game's save folder
        folder = files[0].parent
        reasons, score = [], 0.0
        parts = folder.parts
        inside_game = d.startswith(game_dir_n + os.sep) or d == game_dir_n
        if inside_game:
            score += 3
            reasons.append("in the game folder")
            if d == game_dir_n:
                score -= 2.5             # the game folder itself mostly gets config/log files
        rel_parts = list(parts[-4:])
        if any(_matches(p, tokens) for p in rel_parts):
            score += 4
            reasons.append("named after the game")
        named = any(_matches(p, tokens) for p in rel_parts)
        save_like = any(w in folder.name.casefold() for w in _SAVE_WORDS)
        if save_like:
            score += 2
            reasons.append("looks like a save folder")
        elif any(w in folder.name.casefold() for w in _WEAK_WORDS):
            score += 0.5
        if not inside_game and not named and not save_like:
            continue                     # outside the game folder, something must tie it to this game
        exts = {f.suffix.lower() for f in files}
        if exts & {".sav", ".dat", ".save", ".bin", ".ksd", ".kdt", ".sys", ".rsd", ".json"}:
            score += 1
        if exts <= {".ini", ".cfg", ".txt", ".xml"}:
            score -= 1                   # only settings/text changed
        score += min(len(files), 20) * 0.05
        cands[d] = Candidate(str(folder), "folder", score, len(files), reasons)

    # merge sub-folders into a parent that is itself a candidate (e.g. savedata\ and savedata\thumb\);
    # the parent keeps the best score of the group (no bonus per merged folder)
    for k in sorted(cands, key=len, reverse=True):
        parent = next((q for q in sorted(cands, key=len) if q != k and q != game_dir_n
                       and k.startswith(q + os.sep)), None)
        if parent is not None and k in cands:
            cands[parent].files += cands[k].files
            cands[parent].score = max(cands[parent].score, cands[k].score)
            for r in cands[k].reasons:
                if r not in cands[parent].reasons:
                    cands[parent].reasons.append(r)
            del cands[k]
    out = [c for c in cands.values() if c.score > 0.5]
    return sorted(out, key=lambda c: c.score, reverse=True)[:5]


# --------------------------------------------------------------------------- before the first session
# When a game is added, its save folder can often be found straight away - no session needed: the other
# device already knows it (same place, other user name / game folder), or the game was played here before
# and its save folder exists. Only a suggestion: the owner sees it in the game window and saves it.

# .exe / folder names too generic to recognise a game's folders by ("Start.exe" must not match "Start Menu")
_GENERIC = {"start", "launcher", "launch", "game", "setup", "config", "play", "main", "bgi", "system", "data",
            "update", "patch", "program", "app", "siglusengine", "nscripter", "kirikiri", "krkr", "tyrano",
            "renpy", "player", "visual", "novel", "novels", "users", "documents", "games", "install", "runtime"}
_PROFILE = re.compile(r"^[a-z]:\\users\\[^\\]+\\(.+)$", re.IGNORECASE)


@dataclass
class Suggestion:
    path: str
    reason: str               # "other_device" / "existing"


def _under(path: str, folder: str) -> Optional[str]:
    """path relative to folder, or None if it isn't inside it."""
    p, f = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(folder)).rstrip("\\/")
    if p.startswith(f + os.sep):
        return os.path.normpath(path)[len(f) + 1:]
    return None


def from_other_device(game_dir: str, other_exe: str, other_save: str, home: Optional[Path] = None) -> list[Path]:
    """Where the other device's save folder would be on this device, most likely first: the same place
    inside this device's game folder; the same place in this user's profile (C:\\Users\\<other name>\\...
    -> this user); the very same path (e.g. C:\\ProgramData\\..., D:\\Saves\\...)."""
    if not other_save.strip():
        return []
    home = home or Path.home()
    out: list[Path] = []
    if other_exe.strip():
        rel = _under(other_save, os.path.dirname(os.path.normpath(other_exe)))
        if rel is not None:
            out.append(Path(game_dir) / rel)
    m = _PROFILE.match(os.path.normpath(other_save))
    if m:
        out.append(home / m.group(1))
    elif not out:
        out.append(Path(os.path.normpath(other_save)))
    return out


def _name_tokens(names: Iterable[str]) -> set[str]:
    return {t for t in _tokens(*names) if t not in _GENERIC and (len(t) >= 4 or not t.isascii())}


# words a save folder's name may add to the game's name ("SaDisticBlooD_savefiles", "Game Saves", "Game2 data")
_SAVE_AFFIX = re.compile(r"(?:savedata|savefiles?|savegames?|saves|save|userdata|data|"
                         r"セーブデータ|セーブ|データ|\d+|ver|v)*")


def _flat_name(name: str) -> str:
    """A name reduced to its letters for comparing: edition / version tags gone ("slowdamage 普及版" ->
    "slowdamage"), no spaces or punctuation, one case."""
    t = unicodedata.normalize("NFKC", clean_title(name or "")).casefold()
    return re.sub(r"[\W_]+", "", t)


def _whole_names(names: Iterable[str]) -> set[str]:
    """The game's names as wholes (title, .exe name, game folder name), minus ones that say nothing."""
    out = set()
    for n in names:
        f = _flat_name(n)
        if f and f not in _GENERIC and (len(f) >= 3 or not f.isascii()):
            out.add(f)
    return out


def _same_game(folder_name: str, names: set[str]) -> bool:
    """Is this folder named after the game as a WHOLE? Its name must be one of the game's names, at most with
    save words, numbers or edition tags around it. One shared word is not enough: "BLACKcyc" is not
    "Black Sheep Town"."""
    f = _flat_name(folder_name)
    if not f:
        return False
    for n in names:
        if f == n:
            return True
        i = f.find(n)
        if i >= 0 and _SAVE_AFFIX.fullmatch(f[:i]) and _SAVE_AFFIX.fullmatch(f[i + len(n):]):
            return True
    return False


_NOT_SAVES = {".log", ".txt", ".ini", ".cfg", ".xml", ".lnk", ".url", ".exe", ".dll", ".tmp", ".md", ".html",
              ".htm", ".pdf", ".ico", ".png", ".jpg", ".jpeg", ".bmp"}


def _looks_like_saves(folder: Path, limit: int = 2000) -> bool:
    """Something in it besides logs, settings, notes, pictures and programs."""
    stack, seen = [folder], 0
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    seen += 1
                    if seen > limit:
                        return False
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False) and Path(e.name).suffix.lower() not in _NOT_SAVES:
                        return True
        except OSError:
            continue
    return False


def _has_files(folder: Path, limit: int = 2000) -> bool:
    stack, seen = [folder], 0
    while stack:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    seen += 1
                    if seen > limit:
                        return False
                    if e.is_file(follow_symlinks=False):
                        return True
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
        except OSError:
            continue
    return False


def _settle(folder: Path) -> Path:
    """Go down through folders that hold nothing but one sub-folder (Maker\\Game\\1.10 -> 1.10)."""
    for _ in range(4):
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return folder
        if len(entries) == 1 and entries[0].is_dir(follow_symlinks=False):
            folder = Path(entries[0].path)
        else:
            return folder
    return folder


def _newest(folder: Path, limit: int = 2000) -> float:
    newest, stack, seen = 0.0, [folder], 0
    while stack and seen < limit:
        try:
            with os.scandir(stack.pop()) as it:
                for e in it:
                    seen += 1
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        else:
                            newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return newest


def _save_subfolder(folder: Path, depth: int = 3) -> Optional[Path]:
    """Where in a folder named after the game its saves are. The folder itself, unless it also holds clutter
    (logs, engine folders - Unity's Bst/Player.log, Bst/Unity, Bst/Utage/Savebst) and no save files of its
    own: then the one save-named folder below it, or None when there isn't exactly one (not sure)."""
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return None
    loose_saves = any(e.is_file(follow_symlinks=False) and Path(e.name).suffix.lower() not in _NOT_SAVES for e in entries)
    clutter = any((e.is_file(follow_symlinks=False) and Path(e.name).suffix.lower() == ".log") or
                  (e.is_dir(follow_symlinks=False) and not e.name.isdigit()
                   and not any(w in e.name.casefold() for w in _SAVE_WORDS)) for e in entries)
    if loose_saves or not clutter:
        return folder
    found: list[Path] = []
    level = [folder]
    for _ in range(depth):
        nxt = []
        for d in level:
            try:
                subs = [Path(e.path) for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
            except OSError:
                continue
            for sub in subs:
                (found if any(w in sub.name.casefold() for w in _SAVE_WORDS) else nxt).append(sub)
        level = nxt
    return found[0] if len(found) == 1 else None


def existing_save_folders(game_dir: str, names: Iterable[str], skip: Iterable[str] = (),
                          roots: Optional[list[Path]] = None) -> list[Path]:
    """The save folder that already exists, when there's exactly one clear candidate: a save-named folder
    inside the game folder, or else a folder named after the whole game in the usual save places (AppData,
    Documents, Saved Games...) holding save files. Two or more candidates = not sure = nothing."""
    whole = _whole_names(names)
    skip_n = {os.path.normcase(os.path.abspath(s)) for s in skip if s}
    game_dir_n = os.path.normcase(os.path.abspath(game_dir))
    roots = roots if roots is not None else default_roots(game_dir)
    inside, named = [], []
    for root in roots:
        is_game = os.path.normcase(os.path.abspath(root)) == game_dir_n
        level = [Path(root)]
        for depth in range(2 if is_game else 3):
            nxt = []
            for d in level:
                try:
                    entries = [e for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
                except OSError:
                    continue
                for e in entries:
                    n = os.path.normcase(e.path)
                    if e.name.casefold() in _NOISE_DIRS or n in skip_n or (not is_game and n == game_dir_n):
                        continue
                    if is_game:
                        if any(w in e.name.casefold() for w in _SAVE_WORDS) and _looks_like_saves(Path(e.path)):
                            inside.append(Path(e.path))
                            continue
                    else:
                        if whole and _same_game(e.name, whole) and _looks_like_saves(Path(e.path)):
                            found = _save_subfolder(_settle(Path(e.path)))
                            if found is not None:
                                named.append(found)
                            else:
                                named.append(None)             # (named after the game, but which part?)
                            continue
                    nxt.append(Path(e.path))
            level = nxt
    def distinct(paths: list[Path]) -> list[Path]:
        seen, out = set(), []
        for p in paths:
            k = os.path.normcase(str(p))
            if k not in seen:
                seen.add(k)
                out.append(p)
        return out
    if any(p is None for p in named):
        return [] if not inside else (inside if len(distinct(inside)) == 1 else [])
    inside, named = distinct(inside), distinct(named)
    if inside:
        return inside if len(inside) == 1 else []     # e.g. "save" and "savedata" both there: not sure
    return named if len(named) == 1 else []           # two editions, two makers' folders...: not sure


def suggest_save_folder(game_dir: str, names: Iterable[str], other: Optional[tuple[str, str]] = None,
                        usable=lambda p: True, skip: Iterable[str] = (), home: Optional[Path] = None,
                        roots: Optional[list[Path]] = None) -> Optional[Suggestion]:
    """The save folder to suggest for a game on this device, or None. other = (exe, save folder) on the
    other device. usable(path) -> False for folders that may not be synced (see save_folder_problem).
    The other device's location wins (both devices must sync the same folder level); a translated path
    that exists here comes first, but one that doesn't exist yet is still right for a game never run here
    (the first sync creates it with the other device's saves)."""
    if other:
        places = [p for p in from_other_device(game_dir, other[0], other[1], home) if usable(p)]
        for p in places:
            if p.is_dir():
                return Suggestion(str(p), "other_device")
        if places:
            return Suggestion(str(places[0]), "other_device")
    for p in existing_save_folders(game_dir, names, skip, roots):
        if usable(p):
            return Suggestion(str(p), "existing")
    return None


def find_registry(since: float, names: list[str]) -> list[Candidate]:
    """HKCU\\Software\\<Maker>\\<Game> keys written since `since` that are named after the game."""
    if sys.platform != "win32":
        return []
    import winreg

    tokens = _tokens(*names)
    # FILETIME (100ns since 1601) for `since`
    since_ft = int((since + 11644473600) * 10_000_000)
    found: list[Candidate] = []
    try:
        sw = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software")
    except OSError:
        return []
    with sw:
        i = 0
        while True:
            try:
                maker = winreg.EnumKey(sw, i)
            except OSError:
                break
            i += 1
            if maker.casefold() in _REG_NOISE:
                continue
            try:
                with winreg.OpenKey(sw, maker) as mk:
                    j = 0
                    while True:
                        try:
                            game = winreg.EnumKey(mk, j)
                        except OSError:
                            break
                        j += 1
                        try:
                            with winreg.OpenKey(mk, game) as gk:
                                written = winreg.QueryInfoKey(gk)[2]
                        except OSError:
                            continue
                        if written >= since_ft and (_matches(game, tokens) or _matches(maker, tokens)):
                            found.append(Candidate(f"HKEY_CURRENT_USER\\Software\\{maker}\\{game}",
                                                   "registry", 3 + (1 if _matches(game, tokens) else 0), 1,
                                                   ["named after the game"]))
            except OSError:
                continue
    return sorted(found, key=lambda c: c.score, reverse=True)[:3]
