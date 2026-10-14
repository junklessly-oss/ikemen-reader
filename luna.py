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
import subprocess
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
    want = _norm(luna_exe)
    folder = os.path.dirname(want)
    for p in psutil.process_iter(["exe", "name"]):
        try:
            exe = p.info.get("exe")
            if exe and (_norm(exe) == want or
                        (_norm(exe).startswith(folder + os.sep) and "luna" in (p.info.get("name") or "").lower())):
                return True
        except psutil.Error:
            continue
    return False


def launch_game(luna_exe: str, uid: str) -> subprocess.Popen:
    return outside.popen([luna_exe, "--Exec", uid], cwd=os.path.dirname(luna_exe))


def open_alongside(luna_exe: str) -> None:
    if not running(luna_exe):
        outside.popen([luna_exe], cwd=os.path.dirname(luna_exe))
