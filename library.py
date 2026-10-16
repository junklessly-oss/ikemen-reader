"""
library.py - what Ikemen Reader remembers about your games.

Two kinds of data:

  * LOCAL settings (this device only): which device slot this is (Device 1 / Device 2), the synced
    cloud folder, Locale Emulator / Luna locations, language and display options.
    Stored in %APPDATA%\\IkemenReader\\settings.json.
  * SHARED game info (lives in the cloud folder, so both devices see it): one small JSON file
    per game under  <cloud>/games/, cover images under  <cloud>/artwork/, and <cloud>/devices.json
    (which Windows computer last used each device slot).
    Each game stores exe path and save folder PER DEVICE, and playtime PER DEVICE (so the two
    devices never overwrite each other's numbers); the UI only ever shows the total.

Device slots: internally "PC" = Device 1 and "Laptop" = Device 2 (the names the first version
used, so existing data needs no conversion). The UI never shows these names; it says
"This Device" / "Other Device".
"""
from __future__ import annotations

import functools
import json
import re
import unicodedata
import os
import shutil
import socket
import time
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

from sync_engine import safe_game_id

APP_DIR_NAME = "IkemenReader"
OLD_APP_DIR_NAME = "VNLauncher"

SLOTS = ("PC", "Laptop")                 # Device 1, Device 2
SLOT_NUMBER = {"PC": 1, "Laptop": 2}
STATUSES = ("", "reading", "finished", "dropped", "plan")   # also the order shown in every menu
SORTS = ("recent", "hours", "title", "custom")       # custom = the owner's own order (dragged into place)
FOLDER_PLACES = ("mixed", "first", "last")           # where folders go among the games on the main page


def other_slot(slot: str) -> str:
    return SLOTS[1] if slot == SLOTS[0] else SLOTS[0]


def this_computer() -> str:
    return os.environ.get("COMPUTERNAME") or socket.gethostname()


def default_app_dir() -> Path:
    base = Path(os.environ.get("APPDATA") or str(Path.home() / ".config"))
    new, old = base / APP_DIR_NAME, base / OLD_APP_DIR_NAME
    if not new.exists() and old.is_dir():
        # first run after the rename: carry settings, sync records and backups over (the old folder
        # is left in place as a safety copy)
        try:
            shutil.copytree(old, new)
        except OSError:
            pass
    return new


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file and swap it in (never half-written). If a sync app is holding the file for a
    moment, try again for a few seconds before giving up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".vnsync.tmp")
    for attempt in range(8):
        try:
            tmp.write_text(text, "utf-8")
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 7:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            time.sleep(0.4)


def _known(cls, d: dict) -> dict:
    """Only the keys the dataclass knows, each coerced to the right type (a value of the wrong type,
    e.g. from a damaged or hand-edited file, falls back to the default), so odd files still load."""
    if not isinstance(d, dict):
        return {}
    out = {}
    for f in fields(cls):
        if f.name not in d:
            continue
        v, default = d[f.name], f.default
        if default is MISSING or isinstance(default, (dict, list)) or f.default_factory is not MISSING:
            out[f.name] = v
        elif isinstance(default, bool):
            out[f.name] = v if isinstance(v, bool) else default
        elif isinstance(default, (int, float)):
            try:
                out[f.name] = type(default)(v) if v is not None and not isinstance(v, bool) else default
            except (TypeError, ValueError):
                out[f.name] = default
        elif isinstance(default, str):
            out[f.name] = v if isinstance(v, str) else default
        else:
            out[f.name] = v
    return out


# --------------------------------------------------------------------------- local settings
@dataclass
class LocalSettings:
    machine_name: str                 # device slot: "PC" (Device 1) or "Laptop" (Device 2)
    cloud_root: str
    le_path: str = ""                 # LEProc.exe on this device
    luna_path: str = ""               # LunaTranslator.exe or a LunaHook program on this device
    language: str = "en"              # "en" / "ja"
    pulse: bool = True                # breathing sync lights
    vndb: bool = True                 # automatically fetch covers/titles from VNDB
    vndb_safe_only: bool = False      # only offer covers VNDB rates as safe
    sort: str = "recent"              # one of SORTS
    status_filter: str = "all"        # "all" or one of STATUSES[1:]
    theme: str = "dark"               # "dark" / "light" / "custom"
    custom_colors: dict = field(default_factory=dict)   # the custom theme: colour role -> "#RRGGBB"
    bg_image: str = ""                # library background picture (a copy kept in the app folder)
    bg_opacity: float = 0.35          # 0..1
    bg_zoom: float = 1.0              # background crop: 1 = fill the library area, up to 4x
    bg_x: float = 0.5                 # centre of the visible part, 0..1 of the picture
    bg_y: float = 0.5
    # (new settings go at the end: some code fills the earlier ones by position)
    font_en: str = "Hiragino Maru Gothic ProN"   # font family for the English interface
    font_ja: str = "Hiragino Maru Gothic ProN"   # font family for the Japanese interface
    show_stats: bool = True           # playtime / last played on the covers and the bottom bar
    show_bookmarks: bool = True       # reading-status bookmarks on the covers
    auto_update: bool = False         # install new versions by itself (off: asks when one is available)
    folder_place: str = "mixed"       # where folders go in the automatic sorts: one of FOLDER_PLACES
    last_sort: str = "hours"          # the automatic sort used last (My Arrangement's folder options use it)
    anki_path: str = ""               # anki.exe on this device (opened from the easter egg)


def load_local_settings(app_dir: Optional[Path] = None) -> Optional[LocalSettings]:
    f = (app_dir or default_app_dir()) / "settings.json"
    try:
        data = json.loads(f.read_text("utf-8"))
        if not isinstance(data.get("cloud_root"), str) or not data.get("cloud_root"):
            return None
        if "theme" not in data and data.get("light_mode") is True:   # settings from before themes
            data["theme"] = "light"
        s = LocalSettings(**_known(LocalSettings, data))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if s.machine_name not in SLOTS:
        s.machine_name = SLOTS[0]
    if s.language not in ("en", "ja"):
        s.language = "en"
    if s.folder_place not in FOLDER_PLACES:
        s.folder_place = "mixed"
    if s.last_sort not in ("recent", "hours", "title"):
        s.last_sort = "hours"
    if s.sort not in SORTS:
        s.sort = "recent"
    if s.status_filter != "all" and s.status_filter not in STATUSES[1:]:
        s.status_filter = "all"
    if s.theme not in ("dark", "light", "custom"):
        s.theme = "dark"
    import math
    fin = lambda v, d: v if math.isfinite(v) else d          # (a hand-edited "NaN" or "Infinity")
    s.bg_opacity = min(1.0, max(0.0, fin(s.bg_opacity, 0.35)))
    s.bg_zoom = min(4.0, max(1.0, fin(s.bg_zoom, 1.0)))
    s.bg_x, s.bg_y = min(1.0, max(0.0, fin(s.bg_x, 0.5))), min(1.0, max(0.0, fin(s.bg_y, 0.5)))
    import re as _re
    s.custom_colors = {k: v for k, v in (s.custom_colors if isinstance(s.custom_colors, dict) else {}).items()
                       if isinstance(k, str) and isinstance(v, str) and _re.fullmatch(r"#[0-9A-Fa-f]{6}", v)}
    return s


def save_local_settings(settings: LocalSettings, app_dir: Optional[Path] = None) -> None:
    _atomic_write((app_dir or default_app_dir()) / "settings.json", json.dumps(asdict(settings), indent=2))


# --------------------------------------------------------------------------- shared game data
@dataclass
class MachinePaths:
    exe: str = ""        # the game's .exe on that device
    save_dir: str = ""   # the game's save folder on that device


@dataclass
class Game:
    game_id: str
    title: str
    artwork: str = ""                                   # file name inside <cloud>/artwork
    paths: dict[str, MachinePaths] = field(default_factory=dict)
    playtime: dict[str, float] = field(default_factory=dict)  # seconds (to the millisecond), per device
    last_played: float = 0.0                            # unix time, shared
    status: str = ""                                    # reading status, one of STATUSES
    use_le: bool = False                                # "Launch With Locale Emulator"
    use_luna: bool = False                              # "Launch With Luna"
    title_locked: bool = False                          # the owner typed this title; never auto-change
    auto_title: str = ""                                # last cleaned window title (kept even when locked)
    manual_title: str = ""                              # the owner's own title, kept while Automatic is on
    registry_key: str = ""                              # HKCU key holding saves, if the game uses one
    artwork_from: str = ""                              # "user" / "vndb" / "removed" (owner removed it:
                                                        # never fetch one automatically again) / ""
    vndb_id: str = ""
    crop_zoom: float = 1.0                              # cover crop: 1.0 = fill the frame, up to 4x
    crop_x: float = 0.5                                 # centre of the visible part, 0..1 of the image
    crop_y: float = 0.5
    folder: str = ""                                    # id of the folder it's in ("" = main grid)
    names: list[str] = field(default_factory=list)      # other names from VNDB (English title, aliases...): search
    names_for: str = ""                                 # the vndb_id those names belong to

    @property
    def total_playtime(self) -> float:
        return sum(self.playtime.values())

    def paths_for(self, machine: str) -> MachinePaths:
        return self.paths.get(machine) or MachinePaths()

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @staticmethod
    def from_json(text: str) -> "Game":
        d = json.loads(text)
        if not isinstance(d, dict) or not isinstance(d.get("game_id"), str) or not d["game_id"] \
                or not isinstance(d.get("title"), str):
            raise ValueError("not a game file")
        rest = _known(Game, d)
        paths = d.get("paths") if isinstance(d.get("paths"), dict) else {}
        rest["paths"] = {m: MachinePaths(**_known(MachinePaths, p)) for m, p in paths.items() if isinstance(p, dict)}
        playtime = d.get("playtime") if isinstance(d.get("playtime"), dict) else {}
        rest["playtime"] = {}
        for m, s in playtime.items():
            try:
                v = float(s)
                if v == v and v != float("inf"):              # (not NaN / infinity)
                    rest["playtime"][m] = max(0.0, round(v, 3))
            except (TypeError, ValueError):
                pass
        names = rest.get("names")
        rest["names"] = [n for n in names if isinstance(n, str) and n][:40] if isinstance(names, list) else []
        if rest.get("status") not in STATUSES:
            rest["status"] = ""
        art = rest.get("artwork", "")
        if art and (Path(art).name != art or art in (".", "..")):
            rest["artwork"] = ""         # only a plain file name inside <cloud>/artwork is ever used
        lp = rest.get("last_played", 0.0)
        if not (0.0 <= lp < 1e11):       # (also catches NaN / infinity)
            rest["last_played"] = 0.0
        if not (1.0 <= rest.get("crop_zoom", 1.0) <= 4.0):
            rest["crop_zoom"] = 1.0
        for k in ("crop_x", "crop_y"):
            if not (0.0 <= rest.get(k, 0.5) <= 1.0):
                rest[k] = 0.5
        return Game(**rest)


def format_playtime(seconds: int) -> str:
    if 0 < seconds < 60:
        return f"{int(seconds)}s"
    h, rem = divmod(int(seconds), 3600)
    if h == 0:
        return f"{rem // 60}m"
    return f"{h}h {rem // 60:02d}m"


class Library:
    def __init__(self, cloud_root: str | Path):
        self.root = Path(cloud_root)
        self.games_dir = self.root / "games"
        self.art_dir = self.root / "artwork"
        self.devices_file = self.root / "devices.json"
        self.folders_file = self.root / "folders.json"
        self.order_file = self.root / "order.json"          # the library's own order (Custom)

    def _file(self, game_id: str) -> Path:
        return self.games_dir / f"{safe_game_id(game_id)}.json"

    def get(self, game_id: str) -> Optional[Game]:
        try:
            return Game.from_json(self._file(game_id).read_text("utf-8"))
        except Exception:  # noqa: BLE001 - missing, half-synced or damaged: treat as absent
            return None

    def list_games(self) -> list[Game]:
        games = []
        seen = set()
        for f in sorted(self.games_dir.glob("*.json")):
            try:
                g = Game.from_json(f.read_text("utf-8"))
            except Exception:  # noqa: BLE001 - unreadable, half-synced or damaged: skip just this one
                continue
            if g.game_id in seen:
                continue           # e.g. a sync app's duplicate copy of the same game file
            seen.add(g.game_id)
            games.append(g)
        return sorted(games, key=lambda g: g.title.lower())

    def find(self, title_or_id: str) -> Optional[Game]:
        key = title_or_id.strip().lower()
        for g in self.list_games():
            if key in (g.title.lower(), g.game_id.lower()):
                return g
        return None

    def find_by_exe(self, exe: str) -> Optional[Game]:
        """The game that already uses this .exe on any device (to avoid adding it twice)."""
        want = os.path.normcase(os.path.abspath(exe))
        for g in self.list_games():
            for p in g.paths.values():
                if p.exe and os.path.normcase(os.path.abspath(p.exe)) == want:
                    return g
        return None

    def save(self, game: Game) -> None:
        _atomic_write(self._file(game.game_id), game.to_json())

    def _check_title(self, title: str, except_id: str = "") -> str:
        title = title.strip()
        if not title:
            raise ValueError("error.title_empty")
        for g in self.list_games():
            if g.game_id != except_id and g.title.lower() == title.lower():
                raise ValueError("error.title_taken")
        return title

    def _free_id(self, title: str) -> str:
        # leave room for " (2)" etc. within the 80-character limit: otherwise the limit would cut the
        # number off again and this would look at the same taken name forever
        base = safe_game_id(safe_game_id(title)[:72])
        gid, n = base, 1
        # only the game file counts: a removed-then-re-added game reconnects to its saves in the cloud
        while self._file(gid).exists() or safe_game_id(gid) != gid:
            n += 1
            gid = f"{base} ({n})"
            if n > 100000:
                raise ValueError("error.title_taken")
        return gid

    def add_game(self, title: str, machine: str, exe: str = "", save_dir: str = "",
                 artwork_src: Optional[str] = None, **extra) -> Game:
        title = self._check_title(title)
        game = Game(game_id=self._free_id(title), title=title,
                    paths={machine: MachinePaths(exe, save_dir)}, **extra)
        self.save(game)
        if artwork_src:
            self.set_artwork(game.game_id, artwork_src, source=extra.get("artwork_from") or "user",
                             reset_crop=False)
        return self.get(game.game_id)

    def set_paths(self, game_id: str, machine: str, exe: Optional[str] = None,
                  save_dir: Optional[str] = None) -> Game:
        game = self.get(game_id)
        if game is None:
            raise KeyError(game_id)
        p = game.paths_for(machine)
        if exe is not None:
            p.exe = exe
        if save_dir is not None:
            p.save_dir = save_dir
        game.paths[machine] = p
        self.save(game)
        return game

    def update(self, game_id: str, **changes) -> Game:
        """Re-read the game, apply simple field changes, save. Used for checkboxes, status, etc."""
        game = self.get(game_id)
        if game is None:
            raise KeyError(game_id)
        if "title" in changes:
            changes["title"] = self._check_title(changes["title"], except_id=game_id)
        for k, v in changes.items():
            if not hasattr(game, k) or k in ("game_id", "paths", "playtime"):
                raise AttributeError(k)
            setattr(game, k, v)
        self.save(game)
        return game

    def edit(self, game_id: str, title: str, paths: dict[str, MachinePaths],
             artwork_src: Optional[str] = None, artwork_from: str = "user", **changes) -> Game:
        """Apply the game settings dialog. The game id (which names the cloud save folder) never
        changes, so renaming is safe. Re-reads first so playtime added meanwhile isn't lost."""
        if self.get(game_id) is None:
            raise KeyError(game_id)
        title = self._check_title(title, except_id=game_id)
        if artwork_src:
            self.set_artwork(game_id, artwork_src, source=artwork_from)
        game = self.get(game_id)
        game.title = title
        for machine, p in paths.items():
            game.paths[machine] = MachinePaths(p.exe.strip(), p.save_dir.strip())
        for k, v in changes.items():
            if not hasattr(game, k) or k in ("game_id", "paths", "playtime"):
                raise AttributeError(k)
            setattr(game, k, v)
        self.save(game)
        return game

    def remove(self, game_id: str) -> None:
        """Forget a game. Its saves (local and in the cloud) are left alone."""
        game = self.get(game_id)
        self._file(game_id).unlink(missing_ok=True)
        if game and game.artwork:
            (self.art_dir / game.artwork).unlink(missing_ok=True)
        if game and game.folder:
            self._drop_folder_if_empty(game.folder)

    # -- folders (like app folders on a phone: a game is in at most one; empty folders disappear)
    def _folders_raw(self) -> dict[str, dict]:
        """folder id -> {"name", "sort" ("" = same as the library), "order" (game ids, for Custom)}."""
        try:
            data = json.loads(self.folders_file.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        out = {}
        for fid, v in data.items():
            if not isinstance(v, dict):
                continue
            order = v.get("order")
            out[fid] = {"name": v.get("name") if isinstance(v.get("name"), str) else "",
                        "sort": v.get("sort") if v.get("sort") in SORTS else "",
                        "order": [x for x in order if isinstance(x, str)] if isinstance(order, list) else []}
        return out

    def list_folders(self) -> dict[str, str]:
        """folder id -> name."""
        return {fid: v["name"] for fid, v in self._folders_raw().items()}

    def _write_folders(self, folders: dict[str, str]) -> None:
        raw = self._folders_raw()
        data = {}
        for fid, name in folders.items():
            extra = raw.get(fid, {})
            entry = {"name": name}
            if extra.get("sort"):
                entry["sort"] = extra["sort"]
            if extra.get("order"):
                entry["order"] = extra["order"]
            data[fid] = entry
        _atomic_write(self.folders_file, json.dumps(data, indent=2, ensure_ascii=False))

    def _write_folder_field(self, fid: str, key: str, value) -> None:
        raw = self._folders_raw()
        if fid not in raw:
            raise KeyError(fid)
        raw[fid][key] = value
        data = {f: {k: v for k, v in e.items() if k == "name" or v} for f, e in raw.items()}
        _atomic_write(self.folders_file, json.dumps(data, indent=2, ensure_ascii=False))

    def folder_sort(self, fid: str) -> str:
        """The folder's own sort, or "" when it follows the library's."""
        return self._folders_raw().get(fid, {}).get("sort", "")

    def set_folder_sort(self, fid: str, sort: str) -> None:
        self._write_folder_field(fid, "sort", sort if sort in SORTS else "")

    def folder_order(self, fid: str) -> list[str]:
        return list(self._folders_raw().get(fid, {}).get("order", []))

    def set_folder_order(self, fid: str, order: list[str]) -> None:
        self._write_folder_field(fid, "order", [x for x in order if isinstance(x, str)])

    # -- the library's own order (sort: Custom): game ids and "folder:<id>" for folders
    def library_order(self) -> list[str]:
        try:
            data = json.loads(self.order_file.read_text("utf-8"))
        except (OSError, ValueError):
            return []
        order = data.get("library") if isinstance(data, dict) else None
        return [x for x in order if isinstance(x, str)] if isinstance(order, list) else []

    def set_library_order(self, order: list[str]) -> None:
        _atomic_write(self.order_file, json.dumps({"library": [x for x in order if isinstance(x, str)]},
                                                  indent=1, ensure_ascii=False))

    def create_folder(self, name: str) -> str:
        import uuid
        folders = self.list_folders()
        fid = uuid.uuid4().hex[:10]
        folders[fid] = name.strip()
        self._write_folders(folders)
        return fid

    def rename_folder(self, fid: str, name: str) -> None:
        folders = self.list_folders()
        folders[fid] = name.strip()
        self._write_folders(folders)

    def delete_folder(self, fid: str) -> None:
        """Remove the folder; its games go back to the main grid (nothing is deleted)."""
        for g in self.list_games():
            if g.folder == fid:
                self.update(g.game_id, folder="")
        folders = self.list_folders()
        if folders.pop(fid, None) is not None:
            self._write_folders(folders)

    def set_folder(self, game_id: str, fid: str) -> Game:
        """Put a game in a folder ("" = take it out). A folder left empty is removed."""
        current = self.get(game_id)
        if current is None:
            raise KeyError(game_id)
        old = current.folder
        game = self.update(game_id, folder=fid)
        if old and old != fid:
            self._drop_folder_if_empty(old)
            if old in self._folders_raw() and game_id in self.folder_order(old):
                self.set_folder_order(old, [x for x in self.folder_order(old) if x != game_id])
        if fid and game_id in self.library_order():
            self.set_library_order([x for x in self.library_order() if x != game_id])
        return game

    def _drop_folder_if_empty(self, fid: str) -> None:
        # only called right after this device took a game out, so it can't race a folder the other
        # device is still filling
        if not any(g.folder == fid for g in self.list_games()):
            folders = self.list_folders()
            if folders.pop(fid, None) is not None:
                self._write_folders(folders)

    def set_artwork(self, game_id: str, src: str, source: str = "user", reset_crop: bool = True) -> Game:
        game = self.get(game_id)
        if game is None:
            raise KeyError(game_id)
        src_path = Path(src)
        self.art_dir.mkdir(parents=True, exist_ok=True)
        dest_name = f"{safe_game_id(game_id)}{src_path.suffix.lower() or '.jpg'}"
        if game.artwork and game.artwork != dest_name:
            (self.art_dir / game.artwork).unlink(missing_ok=True)
        if src_path.resolve() != (self.art_dir / dest_name).resolve():
            shutil.copy2(src_path, self.art_dir / dest_name)
        game.artwork = dest_name
        game.artwork_from = source
        if reset_crop:
            game.crop_zoom, game.crop_x, game.crop_y = 1.0, 0.5, 0.5
        self.save(game)
        return game

    def clear_artwork(self, game_id: str) -> Game:
        """Remove the cover. Remembered as "removed" so VNDB won't add one back automatically."""
        game = self.get(game_id)
        if game is None:
            raise KeyError(game_id)
        if game.artwork:
            (self.art_dir / game.artwork).unlink(missing_ok=True)
        game.artwork = ""
        game.artwork_from = "removed"
        game.crop_zoom, game.crop_x, game.crop_y = 1.0, 0.5, 0.5
        self.save(game)
        return game

    def artwork_path(self, game: Game) -> Optional[Path]:
        p = self.art_dir / game.artwork if game.artwork else None
        return p if p and p.is_file() else None

    def record_session(self, game_id: str, machine: str, seconds: float,
                       when: Optional[float] = None) -> Game:
        """Add playtime and set "last played". Re-reads first so an edit made meanwhile isn't lost."""
        game = self.get(game_id)
        if game is None:
            raise KeyError(game_id)
        game.playtime[machine] = round(game.playtime.get(machine, 0) + max(0.0, float(seconds)), 3)
        game.last_played = when if when is not None else time.time()
        self.save(game)
        return game

    def add_playtime(self, game_id: str, machine: str, seconds: float) -> Game:
        return self.record_session(game_id, machine, seconds)

    def change_signature(self) -> tuple:
        """A cheap fingerprint of everything shared in the cloud folder that the window shows: the game files,
        folders, covers and saves (names, sizes and modified times only, nothing is read). If it differs from
        last time, something changed - e.g. the other device added a game or uploaded newer saves."""
        def listing(folder: Path, deep: bool = False) -> tuple:
            out = []
            try:
                for root, dirs, files in (os.walk(folder) if deep else [(str(folder), [], os.listdir(folder))]):
                    for name in files:
                        if name.endswith(".vnsync.tmp") or name.endswith(".tmp"):
                            continue
                        try:
                            st = os.stat(os.path.join(root, name))
                        except OSError:
                            continue
                        out.append((os.path.relpath(os.path.join(root, name), folder), st.st_size, st.st_mtime_ns))
            except OSError:
                return ()
            return tuple(sorted(out))
        def stamp(f: Path):
            try:
                st = f.stat()
                return (st.st_size, st.st_mtime_ns)
            except OSError:
                return None
        return (listing(self.games_dir), listing(self.art_dir), listing(self.root / "saves", deep=True),
                stamp(self.folders_file), stamp(self.order_file))

    # -- device slots (safeguard against both computers picking the same number)
    def _devices(self) -> dict:
        try:
            d = json.loads(self.devices_file.read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(d, dict):
            return {}
        return {k: v for k, v in d.items() if isinstance(v, dict) and isinstance(v.get("computer"), str)}

    def slot_owner(self, slot: str) -> Optional[str]:
        """The Windows computer name that last used this device slot, if any."""
        return (self._devices().get(slot) or {}).get("computer") or None

    def claim_slot(self, slot: str, computer: Optional[str] = None) -> None:
        d = self._devices()
        d[slot] = {"computer": computer or this_computer(), "seen": time.time()}
        _atomic_write(self.devices_file, json.dumps(d, indent=2, ensure_ascii=False))

    def known_computers(self) -> set[str]:
        return {v.get("computer") for v in self._devices().values() if v.get("computer")}


def sort_games(games: list[Game], how: str) -> list[Game]:
    if how == "hours":
        return sorted(games, key=lambda g: (-g.total_playtime, title_sort_key(g.title)))
    if how == "title":
        return sorted(games, key=lambda g: title_sort_key(g.title))
    return sorted(games, key=lambda g: (-g.last_played, title_sort_key(g.title)))


_PUNCT = re.compile(r"[\s\-‐―–—~・:!?\"「」『』【】()\[\].,、。☆★♪†×/&+]")


def _fold(text: str) -> str:
    """Width, case and katakana/hiragana don't matter; apostrophes vanish (Bunny's = bunnys)."""
    s = unicodedata.normalize("NFKC", text).casefold().replace("'", "").replace("’", "")
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in s)


def _is_kanji(c: str) -> bool:
    o = ord(c)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF or c == "々"


def _is_kana(c: str) -> bool:
    return 0x3040 <= ord(c) <= 0x30FF


@functools.lru_cache(maxsize=16384)
def search_key(text: str) -> str:
    """Folded, without spaces or punctuation ("サクラノ詩 -櫻の森の上を舞う-" -> "さくらの詩櫻の森の上を舞う")."""
    return _PUNCT.sub("", _fold(text))


@functools.lru_cache(maxsize=16384)
def _words(text: str) -> tuple[str, ...]:
    """The Latin / number words of a name, in order ("Sakura no Toki" -> sakura, no, toki;
    "11月のアルカディア" -> 11). Japanese characters only separate them."""
    return tuple(re.findall(r"[0-9a-zÀ-ɏ]+", _fold(text)))      # (Latin letters incl. accented)


def matches_search(game: "Game", query: str) -> bool:
    """Does the search box's text find this game (its title, or any name VNDB knows it by)? Typing has to
    follow a name, not just contain the letters:
    - with kanji: the name contains it, anywhere ("刻" finds サクラノ刻);
    - kana: the name starts with it ("まま" finds ままごと and ママトモ);
    - Latin letters / numbers: the start of a word ("ind" finds Indigo, "toki" and "sakura no" find
      Sakura no Toki, "sakuranotoki" too); a single letter finds only words that start with it."""
    key = search_key(query)
    if not key:
        return True
    names = (game.title, *game.names)
    if any(_is_kanji(c) for c in key):
        return any(key in search_key(n) for n in names)
    if any(_is_kana(c) for c in key):
        return any(search_key(n).startswith(key) for n in names)
    qw = _words(query)
    if not qw:
        return False
    for n in names:
        words = _words(n)
        for i in range(len(words)):
            if len(qw) == 1:
                if "".join(words[i:]).startswith(qw[0]):
                    return True
            elif words[i:i + len(qw) - 1] == qw[:-1] and i + len(qw) - 1 < len(words)                     and words[i + len(qw) - 1].startswith(qw[-1]):
                return True
    return False


@functools.lru_cache(maxsize=16384)       # (asked for every game on every keystroke in the search box)
def title_sort_key(title: str) -> tuple:
    """Japanese-aware ordering: Latin titles first (A-Z), then kana in あいうえお order (katakana
    sorted together with hiragana), then kanji. Not a full reading-based sort (kanji readings
    would need a dictionary), but it keeps kana titles in gojūon order."""
    out = []
    for ch in title.casefold():
        o = ord(ch)
        if 0x30A1 <= o <= 0x30F6:            # katakana -> hiragana
            ch = chr(o - 0x60)
            o -= 0x60
        if 0x3041 <= o <= 0x3096:
            group = 1
        elif 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF:
            group = 2
        else:
            group = 0
        out.append((group, ch))
    return tuple(out)
