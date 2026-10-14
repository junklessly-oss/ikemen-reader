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
