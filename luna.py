"""
luna.py - launching games through Luna (LunaTranslator, or a standalone LunaHook program).

LunaTranslator keeps a game list in  <its folder>\\userconfig\\savegamedata_<version>.json ; element
[1] maps a game ID ("uid") to {"gamepath": ..., "title": ...}. Its own "create desktop shortcut"
feature runs  LunaTranslator.exe --Exec <uid> , which starts that game with the hook attached
from the start - exactly what we do. Game IDs differ per computer, so they're looked up by the
game's .exe path on THIS device every time, never stored in the shared library.

A standalone LunaHook program has no game list and can't be told to launch a game, so for it we
just open it (if it isn't already open) and start the game ourselves; the owner attaches as usual.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import psutil

import outside


def kind(luna_exe: str) -> str:
    """"translator" (can launch games itself) or "hook" (open alongside)."""
    p = Path(luna_exe)
    if p.name.lower().startswith("lunatranslator") or _game_list_file(p) is not None:
        return "translator"
    return "hook"


def is_admin_build(luna_exe: str) -> bool:
    return "_admin" in Path(luna_exe).stem.lower()


def _game_list_file(luna_exe: Path) -> Optional[Path]:
    cfg = luna_exe.parent / "userconfig"
    try:
        files = sorted(cfg.glob("savegamedata_*.json"), key=lambda f: f.stat().st_mtime, reverse=True) if cfg.is_dir() else []
    except OSError:                    # a file vanished while looking (Luna rewriting it)
        return None
    return files[0] if files else None


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def find_uid(luna_exe: str, game_exe: str) -> Optional[str]:
    """Luna's ID for this game, or None if the game isn't in Luna's list yet."""
    f = _game_list_file(Path(luna_exe))
    if f is None:
        return None
    try:
        data = json.loads(f.read_text("utf-8"))
        games = data[1] if isinstance(data, list) else data
    except (OSError, ValueError, IndexError, KeyError):
        return None
    if not isinstance(games, dict):    # a format we don't know (a future Luna version)
        return None
    want = _norm(game_exe)
    for uid, g in games.items():
        if isinstance(g, dict) and isinstance(g.get("gamepath"), str) and g["gamepath"] and _norm(g["gamepath"]) == want:
            return uid
    return None


def game_path_for_uid(luna_exe: str, uid: str) -> Optional[str]:
    """The game .exe that Luna's game `uid` points to (used to follow Luna's desktop shortcuts)."""
    f = _game_list_file(Path(luna_exe))
    if f is None:
        return None
    try:
        data = json.loads(f.read_text("utf-8"))
        games = data[1] if isinstance(data, list) else data
        g = games.get(uid)
    except (OSError, ValueError, IndexError, KeyError, AttributeError):
        return None
    return g.get("gamepath") if isinstance(g, dict) else None


def running(luna_exe: str) -> bool:
    """Is this Luna program already open?"""
    return bool(_luna_pids(luna_exe))


def _luna_pids(luna_exe: str) -> set[int]:
    """The programs of this Luna that are open (its main program and its helpers in the same folder). Uses the
    launcher's program list (asked through Windows directly and remembered): psutil's own look at every program
    held Python long enough to stutter the window."""
    from launcher import _scan
    want = _norm(luna_exe)
    folder = os.path.dirname(want)
    out = set()
    for p in _scan():
        exe = p.get("exe")
        if exe and (_norm(exe) == want or
                    (_norm(exe).startswith(folder + os.sep) and "luna" in os.path.basename(exe).lower())):
            out.add(p["pid"])
    return out


def _windowed_pids() -> set[int]:
    """Programs with a visible window (where a game would be)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    out: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            out.add(pid.value)
        return True
    user32.EnumWindows(cb, 0)
    return out


def idle(luna_exe: str) -> bool:
    """Is this Luna open and CERTAINLY not busy with a game (so closing it can't interrupt anything)? Only True when
    sure (owner: 95-100 % or not at all):
    - nothing it started is running (a game Luna starts is its child program - a fact Windows records), and
    - no program with a window has anything from Luna's folder loaded (attaching to a running game, Luna puts its
      hook - LunaHook32/64.dll - inside it; any file from its folder counts, so a renamed hook is still seen), and
    - every such program could be looked into: one that can't (run as administrator) might hide a hooked game, so
      then it's "not sure" -> False."""
    return busy_only_with(luna_exe, None)


def busy_only_with(luna_exe: str, game_dir: Optional[str]) -> bool:
    """idle() with one allowance: Luna may be busy with THIS game (its programs = whatever runs from game_dir) and
    nothing else. game_dir None = no allowance (certainly idle). Same certainty rules as idle()."""
    if sys.platform != "win32":
        return False
    from launcher import _scan
    want = _norm(luna_exe)
    folder = os.path.dirname(want) + os.sep
    allowed = (_norm(str(game_dir)).rstrip(os.sep) + os.sep) if game_dir else None

    def this_game(exe: Optional[str]) -> bool:
        return bool(allowed and exe and _norm(exe).startswith(allowed))
    try:
        procs = _scan()
    except Exception:                            # noqa: BLE001 - couldn't list programs: not sure
        return False
    exes = {p["pid"]: p.get("exe") for p in procs}
    pids = {p["pid"] for p in procs if p.get("exe") and (_norm(p["exe"]) == want or (
        _norm(p["exe"]).startswith(folder) and "luna" in os.path.basename(p["exe"]).lower()))}
    if not pids:
        return False
    own = {os.getpid()}
    try:
        for p in procs:
            if p.get("ppid") in pids and p["pid"] not in pids:
                exe = p.get("exe")
                if not exe or not (_norm(exe).startswith(folder) or this_game(exe)):
                    return False                          # Luna started something else: a (different) game
        for pid in _windowed_pids() - pids - own:
            try:
                maps = psutil.Process(pid).memory_maps(grouped=True)
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:                          # (access denied: can't tell what's inside)
                return False
            if any(_norm(m.path).startswith(folder) for m in maps) and not this_game(exes.get(pid)):
                return False                              # Luna's hook is inside another program
    except psutil.Error:
        return False
    return True


def close(luna_exe: str, wait_s: float = 6.0) -> bool:
    """Ask this Luna to close (like its own X) and wait for it; True once it's gone."""
    import time
    from launcher import _ask_to_close
    pids = _luna_pids(luna_exe)
    _ask_to_close(pids)
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        if not _luna_pids(luna_exe):
            return True
        time.sleep(0.2)
    return False


def open_alongside(luna_exe: str) -> None:
    if not running(luna_exe):
        outside.popen([luna_exe], cwd=os.path.dirname(luna_exe))
