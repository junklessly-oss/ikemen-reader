"""
taskicon.py - the themed icon on Ikemen Reader's pinned taskbar button, desktop and Start menu shortcuts
(owner, 2026-10-26).

Windows shows a shortcut's own icon (else its program's build-time icon), never the window's, so a theme that
recolours the logo left a pinned button / desktop shortcut in the old colours. Each themed icon is written once as
an .ico named by its colours + picture (Windows caches icons by file name: a new look = a new name) and every shortcut to
this .exe is pointed at it; the original look points them back at the .exe's own icon. Only Windows' "reload every
icon" notice makes the taskbar look again (tried on the PC 2026-10-26; the gentler ones were ignored), and it flickers
every icon on screen - so the app runs this once per change kept, not while editing. A shortcut given an icon of the
owner's own choosing is left alone. Shortcuts are changed through Windows' own IShellLink (ctypes, no extra
libraries), so Windows writes the file; each one's first state is kept in <icons>/originals.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import struct
from pathlib import Path
from typing import Optional

import lnk

SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)     # (as make_icon.py: every size Windows asks for)
KEEP_ICONS = 5                                          # themed .ico files kept (the newest; a shortcut may lag one)


def ico_bytes(images: list[tuple[int, bytes]]) -> bytes:
    """An .ico from (size, PNG data) pairs - a header, one entry per size, then the PNG data."""
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for size, data in images:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(data for _, data in images)


def icon_name(fill: str, outline: str, picture: bytes = b"") -> str:
    """The .ico's file name for a look: its colours + the picture itself (a redrawn Icon.png = new names, so Windows
    can't keep showing the old drawing from its cache)."""
    key = f"{fill.upper()}|{outline.upper()}|".encode() + hashlib.sha1(picture).digest()
    return "icon_" + hashlib.sha1(key).hexdigest()[:12] + ".ico"


def shortcut_folders() -> list[Path]:
    """Where Ikemen Reader's shortcuts can be: the pinned taskbar buttons, this user's and the shared desktop, this
    user's and the shared Start menu programs (with their subfolders)."""
    found = [Path(os.path.expandvars(r"%APPDATA%\Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"))]
    # CSIDL_DESKTOPDIRECTORY, CSIDL_COMMON_DESKTOPDIRECTORY, CSIDL_PROGRAMS, CSIDL_COMMON_PROGRAMS
    for csidl in (0x10, 0x19, 0x02, 0x17):
        buf = ctypes.create_unicode_buffer(260)
        try:
            if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
                found.append(Path(buf.value))
        except (AttributeError, OSError):
            pass
    return [f for f in found if f.is_dir()]


def _same(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _deep(folder: Path) -> bool:
    """A Start menu programs folder: shortcuts sit in subfolders too."""
    return folder.name.lower() == "programs" and folder.parent.name.lower() == "start menu"


def shortcuts_to(exe: str, folders: list[Path]) -> list[Path]:
    out = []
    for folder in folders:
        try:
            files = sorted(folder.rglob("*.lnk") if _deep(folder) else folder.glob("*.lnk"))
        except OSError:
            continue
        for f in files:
            try:
                target, _ = lnk.read(str(f))
            except (OSError, ValueError, struct.error):
                continue
            if target and _same(target, exe):
                out.append(f)
    return out


# -- Windows' IShellLink through its function table (COM without extra libraries)
class _GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(g))
    return g


_CLSID_SHELL_LINK = "{00021401-0000-0000-C000-000000000046}"
_IID_SHELL_LINK_W = "{000214F9-0000-0000-C000-000000000046}"
_IID_PERSIST_FILE = "{0000010B-0000-0000-C000-000000000046}"


def _call(obj: ctypes.c_void_p, index: int, restype, *args_and_types):
    """Method `index` of a COM object's function table (args_and_types: (ctype, value) pairs)."""
    table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *(t for t, _ in args_and_types))(table[index])
    return fn(obj, *(v for _, v in args_and_types))


class _Link:
    """One shortcut file, open for reading / changing its icon. Use as `with _Link(path) as s:`."""

    def __init__(self, path: Optional[Path] = None):
        self.path, self.link, self.file, self._uninit = path, ctypes.c_void_p(), ctypes.c_void_p(), False

    def __enter__(self) -> "_Link":
        ole32 = ctypes.oledll.ole32
        try:
            ole32.CoInitializeEx(None, 2)               # (apartment; S_FALSE = already: still balanced below)
            self._uninit = True
        except OSError:
            pass                                        # (this thread already uses the other kind: fine as it is)
        try:
            ole32.CoCreateInstance(ctypes.byref(_guid(_CLSID_SHELL_LINK)), None, 1,
                                   ctypes.byref(_guid(_IID_SHELL_LINK_W)), ctypes.byref(self.link))
            _call(self.link, 0, ctypes.HRESULT, (ctypes.POINTER(_GUID), ctypes.byref(_guid(_IID_PERSIST_FILE))),
                  (ctypes.POINTER(ctypes.c_void_p), ctypes.byref(self.file)))
            if self.path is not None:
                _call(self.file, 5, ctypes.HRESULT, (ctypes.c_wchar_p, str(self.path)), (ctypes.c_uint32, 0))
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_) -> None:
        for obj in (self.file, self.link):
            if obj:
                _call(obj, 2, ctypes.c_ulong)           # Release
        self.file, self.link = ctypes.c_void_p(), ctypes.c_void_p()
        if self._uninit:
            ctypes.windll.ole32.CoUninitialize()
            self._uninit = False

    def icon(self) -> tuple[str, int]:
        buf, index = ctypes.create_unicode_buffer(1024), ctypes.c_int()
        _call(self.link, 16, ctypes.HRESULT, (ctypes.c_wchar_p, buf), (ctypes.c_int, 1024),
              (ctypes.POINTER(ctypes.c_int), ctypes.byref(index)))
        return buf.value, index.value

    def set_icon(self, location: str, index: int = 0) -> None:
        _call(self.link, 17, ctypes.HRESULT, (ctypes.c_wchar_p, location), (ctypes.c_int, index))

    def set_target(self, target: str) -> None:
        _call(self.link, 20, ctypes.HRESULT, (ctypes.c_wchar_p, target))

    def save(self, path: Optional[Path] = None) -> None:
        _call(self.file, 6, ctypes.HRESULT, (ctypes.c_wchar_p, str(path or self.path)), (ctypes.c_int, 1))


def make_shortcut(path: Path, target: str) -> None:
    """A plain shortcut to `target` (the tests' stand-ins for a pinned button / desktop shortcut)."""
    with _Link() as s:
        s.set_target(target)
        s.save(path)


def icon_of(path: Path) -> tuple[str, int]:
    with _Link(path) as s:
        return s.icon()


def _notify(paths: list[Path]) -> None:
    shell32 = ctypes.windll.shell32
    for p in paths:
        shell32.SHChangeNotify(0x00002000, 0x0005, ctypes.c_wchar_p(str(p)), None)   # SHCNE_UPDATEITEM, SHCNF_PATHW
    shell32.SHChangeNotify(0x08000000, 0x0000, None, None)                            # SHCNE_ASSOCCHANGED


def _ours(icon: tuple[str, int], exe: str, icons: Path) -> bool:
    """The shortcut shows the .exe's own icon or one of ours - not one the owner chose for it."""
    loc = os.path.expandvars(icon[0])
    if not loc or _same(loc, exe):
        return True
    p = Path(loc)
    return p.name.startswith("icon_") and p.suffix.lower() == ".ico" and _same(str(p.parent), str(icons))


def update(exe: str, ico: Optional[bytes], name: str, icons: Path, folders: list[Path]) -> list[Path]:
    """Point every shortcut to `exe` in `folders` at this icon (written to icons/name once); ico None = the original
    look, back to the .exe's own icon. Returns the shortcuts changed (already right = left alone, not even touched;
    one we can't change - the shared desktop / Start menu need admin - is skipped, the rest still are)."""
    want, remade = ("", 0), False
    if ico is not None:
        icons.mkdir(parents=True, exist_ok=True)
        path = icons / name
        if not path.is_file() or path.read_bytes() != ico:
            remade = not path.exists()                  # (gone - e.g. the data folder was cleared: Windows looks again)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(ico)
            os.replace(tmp, path)
        else:
            os.utime(path)                              # (newest again: kept by the clean-up)
        want = (str(path), 0)
    changed = []
    for f in shortcuts_to(exe, folders):
        try:
            with _Link(f) as s:
                now = s.icon()
                if now == want:
                    if remade:
                        changed.append(f)
                    continue
                if not _ours(now, exe, icons):
                    continue                            # (an icon the owner chose for this shortcut: left as it is)
                first = icons / "originals" / f.name
                if not first.exists():                  # (each shortcut as it was before we ever changed it)
                    first.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, first)
                s.set_icon(*want)
                s.save()
            changed.append(f)
        except OSError:
            continue
    if changed:
        _notify(changed)
    _prune(icons, keep=want[0])
    return changed


def _prune(icons: Path, keep: str) -> None:
    try:
        files = sorted(icons.glob("icon_*.ico"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for p in files[KEEP_ICONS:]:
        if keep and _same(str(p), keep):
            continue
        try:
            p.unlink()
        except OSError:
            pass
