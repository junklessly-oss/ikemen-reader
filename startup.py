"""
startup.py - "Launch Program on Startup": an entry in this Windows user's startup list
(HKEY_CURRENT_USER\\...\\Run, the list Task Manager's "Startup apps" shows and can switch off).

The registry is the only record, so the Settings checkbox always shows the real state.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "IkemenReader"


def command() -> str:
    """How Windows should start Ikemen Reader: the .exe once it's packaged, otherwise Python
    (pythonw, so no console window) running app.py."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    app = Path(__file__).resolve().with_name("app.py")
    return f'"{pythonw if pythonw.is_file() else exe}" "{app}"'


def _open(write: bool, key: str):
    import winreg
    access = winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE if write else winreg.KEY_QUERY_VALUE
    if write:
        return winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, access)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, access)


def current(key: str = RUN_KEY) -> Optional[str]:
    """The command in the startup list, or None if Ikemen Reader isn't in it."""
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with _open(False, key) as h:
            value, _ = winreg.QueryValueEx(h, VALUE)
            return value
    except OSError:
        return None


def is_enabled(key: str = RUN_KEY) -> bool:
    return current(key) is not None


def set_enabled(on: bool, key: str = RUN_KEY) -> None:
    if sys.platform != "win32":
        return
    import winreg
    with _open(True, key) as h:
        if on:
            winreg.SetValueEx(h, VALUE, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(h, VALUE)
            except FileNotFoundError:
                pass


def refresh(key: str = RUN_KEY) -> None:
    """If it's switched on, make sure it points at where Ikemen Reader is now (e.g. after moving it
    or switching to the packaged .exe)."""
    try:
        if is_enabled(key) and current(key) != command():
            set_enabled(True, key)
    except OSError:
        pass
