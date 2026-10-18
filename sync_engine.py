"""
sync_engine.py - keeps a game's saves in step with a copy in a cloud-synced folder.

How it decides what to do
-------------------------
For every game we remember (locally, per machine) the "fingerprint" of the saves the
last time this machine synced. Comparing three fingerprints tells us who changed:

    local   = fingerprint of the saves on this machine right now
    cloud   = fingerprint of the copy in the synced folder right now
    base    = fingerprint recorded after this machine's last successful sync

    local == cloud                -> nothing to do
    cloud empty                   -> push local up
    local empty                   -> pull cloud down
    base == local (cloud changed) -> pull
    base == cloud (local changed) -> push
    anything else                 -> CONFLICT (both changed, or never synced before)

Whatever is about to be overwritten is backed up first (locally, not in the cloud), and those
backups can be restored later.

"The saves" are the files in the game's save folder, plus - for games that keep saves in the
Windows registry - an export of one registry key, carried as a virtual file named
__registry__.json that travels to the cloud next to the save files.

On the cloud side only, duplicate files made by the sync apps themselves (Dropbox "conflicted
copy", OneDrive "name-COMPUTER.ext", Google Drive "name (1).ext") are ignored.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Optional

import regsync

IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
TMP_SUFFIX = ".vnsync.tmp"
REG_FILE = "__registry__.json"
KEEP_BACKUPS = 10


class Action(str, Enum):
    NOTHING = "nothing"
    PULLED = "pulled"      # cloud -> this machine
    PUSHED = "pushed"      # this machine -> cloud
    CONFLICT = "conflict"  # both sides changed; user must choose


@dataclass
class SyncResult:
    action: Action
    message: str = ""      # a key for i18n.tr()


@dataclass
class ConflictInfo:
    game_id: str
    local_mtime: Optional[float]
    cloud_mtime: Optional[float]


@dataclass
class Backup:
    name: str              # folder name, used to restore it
    when: datetime
    reason: str            # "local-before-pull", "cloud-before-push", "local-before-restore", ...
    path: Path


# Called with ConflictInfo; return "local", "cloud", or None (leave it alone for now).
ConflictResolver = Callable[[ConflictInfo], Optional[str]]


# --------------------------------------------------------------------------- cloud-app duplicates
_DROPBOX = re.compile(r"\((?:[^()]*conflicted copy[^()]*|[^()]*競合コピー[^()]*)\)", re.IGNORECASE)
_NUMBERED = re.compile(r"^(?P<stem>.+?) \((?P<n>\d{1,3})\)$")


def is_cloud_duplicate(rel: str, siblings: set[str], computers: Iterable[str]) -> bool:
    """True if rel (posix path inside a cloud save folder) looks like a copy made by the sync app.
    siblings: every rel path in that folder; computers: known Windows computer names."""
    name = rel.rsplit("/", 1)[-1]
    parent = rel[: -len(name)]
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    ext = f".{ext}" if dot else ""
    if _DROPBOX.search(name):
        return True
    for c in computers:                                  # OneDrive: "saveg-DESKTOP-ABC.dat"
        suffix = f"-{c}".lower()
        if c and stem.lower().endswith(suffix) and len(stem) > len(suffix):
            original = parent + stem[: -len(suffix)] + ext
            if original in siblings:
                return True
    m = _NUMBERED.match(stem)                            # Google Drive: "saveg (1).dat"
    if m and (parent + m.group("stem") + ext) in siblings:
        return True
    return False


# --------------------------------------------------------------------------- helpers
def _files(folder: Path, computers: Optional[Iterable[str]] = None) -> dict[str, Path]:
    """Map of relative posix path -> file, ignoring junk files and our temp files.
    With computers given (cloud side), the sync apps' duplicate copies are ignored too."""
    found: dict[str, Path] = {}
    if not folder.is_dir():
        return found
    for p in folder.rglob("*"):
        if p.is_file() and p.name.lower() not in IGNORED_NAMES and not p.name.endswith(TMP_SUFFIX):
            found[p.relative_to(folder).as_posix()] = p
    if computers is not None:
        names = set(found)
        computers = list(computers)
        found = {r: p for r, p in found.items() if not is_cloud_duplicate(r, names, computers)}
    return found


_HASHES: dict[tuple, str] = {}          # (path, size, modified time) -> content hash


def _file_hash(path: Path) -> str:
    """Content hash of one file. Remembered by path, size and modified time (nanoseconds), so the
    status lights don't re-read every save file on every check."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    known = _HASHES.get(key)
    if known is not None:
        return known
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if len(_HASHES) > 20000:                # a long session: start afresh rather than grow forever
        _HASHES.clear()
    _HASHES[key] = h.hexdigest()
    return _HASHES[key]


def _fingerprint_map(files: dict[str, Path]) -> Optional[str]:
    if not files:
        return None
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(rel.encode("utf-8") + b"\0")
        h.update(_file_hash(files[rel]).encode() + b"\0")
    return h.hexdigest()


def fingerprint(folder: Path) -> Optional[str]:
    """Content hash of a save folder. None if the folder is missing or has no files."""
    return _fingerprint_map(_files(folder))


def _newest(files: dict[str, Path]) -> Optional[float]:
    return max((p.stat().st_mtime for p in files.values()), default=None)


def _writable(p: Path) -> None:
    """Clear a read-only flag so the file can be replaced or removed."""
    try:
        if p.exists() and not os.access(p, os.W_OK):
            os.chmod(p, 0o666)
    except OSError:
        pass


def _retry(fn, attempts: int = 6, wait: float = 0.5):
    """Some games keep a save file open for a moment after closing: retry briefly before giving up."""
    for i in range(attempts):
        try:
            return fn()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(wait)


def _copy_atomic(src: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + TMP_SUFFIX)

    def do():
        shutil.copy2(src, tmp)
        _writable(tmp)                 # a read-only source would make the copy read-only too
        _writable(target)
        os.replace(tmp, target)        # atomic: sync clients never see a half-written save
    try:
        _retry(do)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _mirror_map(src: dict[str, Path], dst: Path, dst_files: dict[str, Path]) -> None:
    """Make dst's files (as listed in dst_files) identical to src. Never deletes dst itself, and
    never touches files that dst_files leaves out (e.g. a sync app's duplicates)."""
    dst.mkdir(parents=True, exist_ok=True)
    for rel, p in src.items():
        _copy_atomic(p, dst / rel)
    emptied = set()
    keep = {r.casefold() for r in src}  # Windows names ignore case: "Save.dat" IS "save.dat"
    for rel, p in dst_files.items():
        if rel.casefold() not in keep:
            _writable(p)
            _retry(lambda p=p: p.unlink(missing_ok=True))
            emptied.add(p.parent)
    # tidy up only folders that our deletions left empty (a game's own empty folders stay)
    root = _norm(dst)
    for d in sorted(emptied, key=lambda x: len(x.parts), reverse=True):
        while _norm(d) != root and _norm(d).startswith(root + os.sep):
            try:
                if any(d.iterdir()):
                    break
                d.rmdir()
            except OSError:
                break
            d = d.parent


class UnsafeSaveFolder(ValueError):
    """The save folder would be dangerous to sync: reason is an i18n key."""

    def __init__(self, reason: str, path: str = ""):
        super().__init__(reason)
        self.reason, self.path = reason, path


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p))).rstrip("\\/")


def _broad_folders() -> set[str]:
    """Folders far too big to be one game's save folder (syncing makes the folder match the other
    device's copy exactly, so everything else in it would be removed)."""
    home = Path.home()
    env = os.environ
    cands = [home, home / "Documents", home / "Desktop", home / "Downloads", home / "Pictures", home / "Music",
             home / "Videos", home / "Saved Games", home / "OneDrive", home / "AppData", home / "AppData" / "LocalLow"]
    for v in ("APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "SYSTEMROOT",
              "PUBLIC", "USERPROFILE", "OneDrive", "WINDIR"):
        if env.get(v):
            cands.append(Path(env[v]))
            cands.append(Path(env[v]) / "Documents")
    return {_norm(c) for c in cands}


def save_folder_problem(local_dir, cloud_root=None) -> Optional[str]:
    """None if the folder is fine to sync, otherwise an i18n key explaining why not."""
    if local_dir is None or not str(local_dir).strip() or str(local_dir).strip() in (".", "./", ".\\"):
        return "error.save_empty"
    p = Path(str(local_dir).strip())
    if not p.is_absolute():
        return "error.save_relative"
    n = _norm(p)
    if p.exists() and not p.is_dir():
        return "error.save_file"
    if p.anchor and n == _norm(p.anchor):
        return "error.save_broad"               # a whole drive, e.g. D:\
    if n in _broad_folders():
        return "error.save_broad"
    if cloud_root:
        c = _norm(cloud_root)
        if n == c or n.startswith(c + os.sep) or c.startswith(n + os.sep):
            return "error.save_cloud"           # would copy the cloud folder into itself
    return None


def _check_local_dir(local_dir, cloud_root=None) -> Path:
    """Refuse folders that would be dangerous to sync (blank, a whole drive, Documents, the cloud
    folder itself, ...)."""
    problem = save_folder_problem(local_dir, cloud_root)
    if problem:
        raise UnsafeSaveFolder(problem, str(local_dir))
    return Path(str(local_dir).strip())


_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}


def safe_game_id(name: str) -> str:
    """A file/folder-safe id from a title: no characters Windows forbids, not a reserved device name
    (CON, NUL, COM1...), not empty, and short enough for Windows paths."""
    gid = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    gid = gid[:80].rstrip(" .")
    if not gid:
        gid = "game"
    if gid.split(".")[0].lower() in _RESERVED:
        gid = "_" + gid
    return gid


def _backup_order(name: str) -> tuple:
    """Oldest-to-newest order of backup folders: by time, then by the -2, -3 ... -10 added when several
    are made within one second (plain alphabetical order would put "-10" before "-2")."""
    m = re.match(r"^(\d{8}-\d{6})-(.+?)(?:-(\d+))?$", name)
    if not m:
        return ("", 0, name)
    return (m.group(1), int(m.group(3) or 1), name)


# --------------------------------------------------------------------------- engine
_LOCKS: dict[tuple, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class SyncEngine:
    def __init__(self, cloud_root: str | Path, state_dir: str | Path,
                 computers: Callable[[], Iterable[str]] = lambda: ()):
        """
        cloud_root: the folder inside OneDrive/Drive/Dropbox, e.g. C:/Users/You/OneDrive/VNSaves
        state_dir:  a LOCAL (not synced) folder for this machine's bookkeeping and backups
        computers:  returns the Windows computer names known to use this cloud folder (used to
                    recognise OneDrive's "name-COMPUTER.ext" duplicates)
        """
        self.cloud_root = Path(cloud_root)
        self.state_dir = Path(state_dir)
        self._computers = computers

    def _lock(self, game_id: str) -> threading.RLock:
        """One sync at a time per game: the startup upload, Play's sync and a restore of the same game
        must never copy the same files at the same moment. (Different games don't wait for each other.)
        Shared by every engine in the program: a new one is made whenever the settings change."""
        key = (_norm(self.cloud_root), safe_game_id(game_id).casefold())
        with _LOCKS_GUARD:
            return _LOCKS.setdefault(key, threading.RLock())

    # -- paths
    def cloud_dir(self, game_id: str) -> Path:
        return self.cloud_root / "saves" / safe_game_id(game_id)

    def _state_file(self, game_id: str) -> Path:
        return self.state_dir / "state" / f"{safe_game_id(game_id)}.json"

    def _backup_root(self, game_id: str) -> Path:
        return self.state_dir / "backups" / safe_game_id(game_id)

    def _reg_stage(self, game_id: str) -> Path:
        return self.state_dir / "registry" / safe_game_id(game_id) / REG_FILE

    # -- the two sides
    def _cloud_files(self, game_id: str) -> dict[str, Path]:
        """The cloud copy, minus duplicates made by the sync apps. A file this device synced itself
        is never taken for a duplicate (a game may really have both "save.dat" and "save (1).dat")."""
        folder = self.cloud_dir(game_id)
        everything = _files(folder)
        kept = _files(folder, computers=list(self._computers()))
        ours = set(self._load_state(game_id).get("files") or [])
        return {r: p for r, p in everything.items() if r in kept or r in ours}

    def _local_files(self, game_id: str, local_dir: Path, registry_key: str = "") -> dict[str, Path]:
        files = _files(local_dir)
        files.pop(REG_FILE, None)       # a real file with this name can't be told apart; skip it
        if registry_key:
            data = regsync.export_key(registry_key)
            if data is not None:
                stage = self._reg_stage(game_id)
                stage.parent.mkdir(parents=True, exist_ok=True)
                text = regsync.dumps(data)
                if not stage.is_file() or stage.read_text("utf-8") != text:
                    stage.write_text(text, "utf-8")   # only rewrite when it changed
                files[REG_FILE] = stage
        return files

    def _write_local(self, src: dict[str, Path], local_dir: Path, registry_key: str) -> None:
        """Put src (cloud files or a backup) onto this machine: files into the save folder,
        the registry part (if any) back into the registry."""
        reg = None
        if registry_key and REG_FILE in src:     # read and check the registry part BEFORE changing anything
            reg = regsync.loads(src[REG_FILE].read_text("utf-8"))
            if not regsync._valid(reg):
                raise ValueError("damaged registry export")
        plain = {r: p for r, p in src.items() if r != REG_FILE}
        current = _files(local_dir)
        current.pop(REG_FILE, None)
        _mirror_map(plain, local_dir, current)
        if reg is not None:
            regsync.import_key(registry_key, reg)

    # -- bookkeeping
    def _load_state(self, game_id: str) -> dict:
        """This device's record of the last sync. A missing or damaged record reads as "never synced"
        (which only ever leads to asking, never to overwriting)."""
        try:
            data = json.loads(self._state_file(game_id).read_text("utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        if not isinstance(data.get("base"), (str, type(None))):
            data["base"] = None
        if not isinstance(data.get("files"), list) or not all(isinstance(x, str) for x in data["files"]):
            data["files"] = []
        return data

    def _load_base(self, game_id: str) -> Optional[str]:
        return self._load_state(game_id).get("base")

    def _save_base(self, game_id: str, fp: Optional[str], files: Iterable[str] = ()) -> None:
        f = self._state_file(game_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + TMP_SUFFIX)
        tmp.write_text(json.dumps({"base": fp, "files": sorted(files)}), "utf-8")
        os.replace(tmp, f)                      # atomic: a crash never leaves a half-written record

    def _backup(self, game_id: str, files: dict[str, Path], label: str, protect: str = "") -> None:
        """Copy files into a new backup; only the newest KEEP_BACKUPS stay (never `protect`: the backup
        being restored right now)."""
        if not files:
            return
        root = self._backup_root(game_id)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        # several backups within one second are numbered in the order they're made, whatever their kind
        n = 1 + (sum(1 for d in root.iterdir() if d.name.startswith(stamp + "-")) if root.is_dir() else 0)
        dest = root / (f"{stamp}-{label}" if n == 1 else f"{stamp}-{label}-{n}")
        while dest.exists():
            n += 1
            dest = root / f"{stamp}-{label}-{n}"
        _mirror_map(files, dest, {})
        kept = sorted((p for p in root.iterdir() if p.is_dir() and p.name != protect), key=lambda p: _backup_order(p.name))
        for old in kept[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)

    # -- decision logic (pure, easy to test)
    @staticmethod
    def decide(local_fp: Optional[str], cloud_fp: Optional[str], base: Optional[str]) -> Action:
        if local_fp == cloud_fp:
            return Action.NOTHING
        if cloud_fp is None:
            return Action.PUSHED
        if local_fp is None:
            return Action.PULLED
        if base is not None:
            if base == local_fp:
                return Action.PULLED
            if base == cloud_fp:
                return Action.PUSHED
        return Action.CONFLICT

    # -- actions
    def _pull(self, game_id: str, local_dir: Path, registry_key: str,
              local: dict[str, Path]) -> SyncResult:
        self._backup(game_id, local, "local-before-pull")
        self._write_local(self._cloud_files(game_id), local_dir, registry_key)
        now = self._local_files(game_id, local_dir, registry_key)
        self._save_base(game_id, _fingerprint_map(now), now)
        return SyncResult(Action.PULLED, "sync.pulled")

    def _push(self, game_id: str, local_dir: Path, registry_key: str,
              local: dict[str, Path], protect: str = "") -> SyncResult:
        cloud = self._cloud_files(game_id)
        self._backup(game_id, cloud, "cloud-before-push", protect)
        _mirror_map(local, self.cloud_dir(game_id), cloud)
        self._save_base(game_id, _fingerprint_map(local), local)
        return SyncResult(Action.PUSHED, "sync.pushed")

    def _sync(self, game_id: str, local_dir: str | Path, registry_key: str,
              on_conflict: Optional[ConflictResolver], allow_pull: bool) -> SyncResult:
        local_dir = _check_local_dir(local_dir, self.cloud_root)
        local = self._local_files(game_id, local_dir, registry_key)
        cloud = self._cloud_files(game_id)
        local_fp, cloud_fp = _fingerprint_map(local), _fingerprint_map(cloud)
        action = self.decide(local_fp, cloud_fp, self._load_base(game_id))
        if action is Action.NOTHING:
            if local_fp is not None:
                self._save_base(game_id, local_fp, local)
            return SyncResult(Action.NOTHING, "sync.up_to_date")
        if action is Action.PUSHED:
            return self._push(game_id, local_dir, registry_key, local)
        if action is Action.PULLED:
            if not allow_pull:   # after playing: local didn't change, so there's nothing to upload
                return SyncResult(Action.NOTHING, "sync.nothing_to_upload")
            return self._pull(game_id, local_dir, registry_key, local)
        choice = on_conflict(ConflictInfo(game_id, _newest(local), _newest(cloud))) if on_conflict else None
        if choice == "local":
            return self._push(game_id, local_dir, registry_key, local)
        if choice == "cloud":
            return self._pull(game_id, local_dir, registry_key, local)
        return SyncResult(Action.CONFLICT, "sync.conflict_unresolved")

    # -- public API used by the launcher / UI
    def has_synced(self, game_id: str) -> bool:
        """Has this device synced this game before? (No: its saves and the cloud's can't have "both changed" -
        a mismatch on the first sync means this device already had files of its own.)"""
        return self._load_base(game_id) is not None

    def status(self, game_id: str, local_dir: str | Path, registry_key: str = "") -> Action:
        """What before_launch WOULD do right now. Changes nothing (used for the UI's status lights)."""
        return self.decide(_fingerprint_map(self._local_files(game_id, _check_local_dir(local_dir, self.cloud_root), registry_key)),
                           _fingerprint_map(self._cloud_files(game_id)), self._load_base(game_id))

    def before_launch(self, game_id: str, local_dir: str | Path,
                      on_conflict: Optional[ConflictResolver] = None, registry_key: str = "") -> SyncResult:
        with self._lock(game_id):
            return self._sync(game_id, local_dir, registry_key, on_conflict, allow_pull=True)

    def after_exit(self, game_id: str, local_dir: str | Path,
                   on_conflict: Optional[ConflictResolver] = None, registry_key: str = "") -> SyncResult:
        with self._lock(game_id):
            return self._sync(game_id, local_dir, registry_key, on_conflict, allow_pull=False)

    def sync_if_safe(self, game_id: str, local_dir: str | Path, registry_key: str = "",
                     allow_pull: bool = True) -> Action:
        """Background sync (no game launch): upload when only this device changed, download when only the
        other one did (what's replaced is backed up, as always). Never asks: saves changed on both sides
        are left alone and reported as CONFLICT for the owner to decide. Returns what it did."""
        local_dir = _check_local_dir(local_dir, self.cloud_root)
        with self._lock(game_id):
            local = self._local_files(game_id, local_dir, registry_key)
            action = self.decide(_fingerprint_map(local), _fingerprint_map(self._cloud_files(game_id)),
                                 self._load_base(game_id))
            if action is Action.PUSHED:
                self._push(game_id, local_dir, registry_key, local)
            elif action is Action.PULLED:
                if not allow_pull:
                    return Action.NOTHING
                self._pull(game_id, local_dir, registry_key, local)
            return action

    # -- backups
    def list_backups(self, game_id: str) -> list[Backup]:
        """Newest first."""
        root = self._backup_root(game_id)
        out = []
        if root.is_dir():
            for d in root.iterdir():
                m = re.match(r"^(\d{8}-\d{6})-(.+?)(?:-\d+)?$", d.name)
                if d.is_dir() and m:
                    try:
                        when = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
                    except ValueError:
                        continue
                    out.append(Backup(d.name, when, m.group(2), d))
        return sorted(out, key=lambda b: _backup_order(b.name), reverse=True)

    def restore(self, game_id: str, backup_name: str, local_dir: str | Path, registry_key: str = "") -> SyncResult:
        """Put a backup back on this machine and upload it. The current saves are backed up first,
        so a restore can itself be undone."""
        local_dir = _check_local_dir(local_dir, self.cloud_root)
        with self._lock(game_id):
            src = _files(self._backup_root(game_id) / backup_name)
            if not src:
                raise FileNotFoundError(backup_name)
            self._backup(game_id, self._local_files(game_id, local_dir, registry_key), "local-before-restore",
                         protect=backup_name)
            self._write_local(src, local_dir, registry_key)
            local = self._local_files(game_id, local_dir, registry_key)
            return self._push(game_id, local_dir, registry_key, local, protect=backup_name)
