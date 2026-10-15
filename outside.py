"""
outside.py - starting other programs (games, Luna, Locale Emulator) cleanly from the packaged app.

The packaged Ikemen Reader.exe (PyInstaller) points Windows' DLL search at its own _internal folder and
adds Qt settings to its environment. Programs we start inherit both, so a program that ships its own
Python or Qt (LunaTranslator) loads ours instead and quits at once - and a game could pick up one of our
DLLs too. So every program we start goes through here: for that moment the DLL search is back to
Windows' normal one, and the environment is the one Windows gave us. (Running from source, `py app.py`,
none of this applies and nothing is changed.)
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import sys
import threading
from typing import Optional

FROZEN = bool(getattr(sys, "frozen", False))
_PACKAGING_VARS = ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH")
_lock = threading.Lock()


def environment(frozen: bool = FROZEN) -> Optional[dict[str, str]]:
    """The environment for a program we start (None = just inherit ours: nothing to remove)."""
    if not frozen:
        return None
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("_PYI") and k.upper() not in _PACKAGING_VARS}
    own = getattr(sys, "_MEIPASS", "")
    for key in [k for k in env if k.upper() == "PATH"]:
        env[key] = os.pathsep.join(p for p in env[key].split(os.pathsep)
                                   if p and not (own and os.path.normcase(os.path.normpath(p)) ==
                                                 os.path.normcase(os.path.normpath(own))))
    return env


@contextlib.contextmanager
def normal_dll_search(frozen: bool = FROZEN):
    """While inside: Windows' normal DLL search (inherited by programs started now). Ours is put back
    afterwards - Ikemen Reader itself still needs it."""
    if not frozen or sys.platform != "win32":
        yield
        return
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetDllDirectoryW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p]
    k32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    with _lock:                         # two starts at once must not put back each other's setting
        buf = ctypes.create_unicode_buffer(32768)
        had = k32.GetDllDirectoryW(len(buf), buf)
        k32.SetDllDirectoryW(None)
        try:
            yield
        finally:
            k32.SetDllDirectoryW(buf.value if had else None)


def popen(cmd: list[str], cwd: Optional[str] = None, creationflags: int = 0) -> subprocess.Popen:
    """subprocess.Popen for a program outside Ikemen Reader."""
    with normal_dll_search():
        return subprocess.Popen(cmd, cwd=cwd, env=environment(), creationflags=creationflags)


def _is_anki(proc, anki_exe: str) -> bool:
    """Is this process Anki? Older Anki is anki.exe itself; newer Anki's launcher (anki.exe) runs
    `pythonw -c "import aqt ... aqt.run()"` from its own folder (AnkiProgramFiles)."""
    try:
        exe = os.path.normcase(proc.exe() or "")
        if anki_exe and exe == os.path.normcase(os.path.abspath(anki_exe)):
            return True
        if os.path.basename(exe) in ("pythonw.exe", "python.exe") and "anki" in exe:
            return any("aqt" in part for part in proc.cmdline())
    except Exception:           # (gone, or not ours to look at)
        pass
    return False


def anki_window(anki_exe: str) -> Optional[int]:
    """The main window of a running Anki (the frontmost one if several), or None if Anki isn't open."""
    if sys.platform != "win32":
        return None
    import psutil
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    windows: list[tuple[int, int]] = []          # (hwnd, pid): visible, titled, top-level, not owned

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) and not user32.GetWindow(hwnd, 4):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            windows.append((hwnd, pid.value))
        return True

    user32.EnumWindows(cb, 0)
    checked: dict[int, bool] = {}
    for hwnd, pid in windows:                    # (in Windows' order: the frontmost window first)
        if pid not in checked:
            try:
                checked[pid] = _is_anki(psutil.Process(pid), anki_exe)
            except psutil.Error:
                checked[pid] = False
        if checked[pid]:
            return hwnd
    return None


def show_anki(anki_exe: str) -> bool:
    """If Anki is open, bring its main window to the front (restored if minimised) and return True.
    False: Anki isn't open - start it instead."""
    hwnd = anki_window(anki_exe)
    if hwnd is None:
        return False
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)               # SW_RESTORE
    user32.SetForegroundWindow(hwnd)             # (allowed: Ikemen Reader is in front, the owner just clicked)
    return True
