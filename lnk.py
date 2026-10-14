"""
lnk.py - find out which program a Windows shortcut (.lnk) points to, without extra libraries.

Reads the documented Shell Link format (MS-SHLLINK): the LinkInfo "local base path" for the
target, and the StringData "command line arguments". Luna's own desktop shortcuts point at
LunaTranslator.exe with "--Exec <game id>"; those are followed through Luna's game list to the
actual game .exe.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path
from typing import Optional

import luna

_HAS_IDLIST, _HAS_LINKINFO, _HAS_NAME, _HAS_RELPATH, _HAS_WORKDIR, _HAS_ARGS = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20
_UNICODE = 0x80


def read(path: str) -> tuple[Optional[str], str]:
    """(target path or None, arguments)."""
    data = Path(path).read_bytes()
    if len(data) < 76 or struct.unpack_from("<I", data, 0)[0] != 0x4C:
        return None, ""
    flags = struct.unpack_from("<I", data, 20)[0]
    pos = 76
    if flags & _HAS_IDLIST:
        pos += 2 + struct.unpack_from("<H", data, pos)[0]
    target = None
    if flags & _HAS_LINKINFO:
        size, header = struct.unpack_from("<II", data, pos)
        info_flags = struct.unpack_from("<I", data, pos + 8)[0]
        if info_flags & 1:                                   # VolumeIDAndLocalBasePath
            if header >= 0x24:                               # unicode offsets present
                off_u = struct.unpack_from("<I", data, pos + 28)[0]
                if off_u:
                    end = pos + off_u
                    while end + 2 <= len(data) and data[end:end + 2] != b"\0\0":   # (a damaged file has no end marker)
                        end += 2
                    target = data[pos + off_u:end].decode("utf-16-le", "replace")
            if not target:
                off = struct.unpack_from("<I", data, pos + 16)[0]
                raw = data[pos + off:data.index(b"\0", pos + off)]
                target = raw.decode("mbcs" if _has_mbcs() else "latin-1", "replace")
        pos += size
    args = ""
    unicode = bool(flags & _UNICODE)
    for bit in (_HAS_NAME, _HAS_RELPATH, _HAS_WORKDIR, _HAS_ARGS):
        if flags & bit:
            n = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            nbytes = n * 2 if unicode else n
            s = data[pos:pos + nbytes].decode("utf-16-le" if unicode else "latin-1", "replace")
            pos += nbytes
            if bit == _HAS_ARGS:
                args = s
    return target, args


def _has_mbcs() -> bool:
    try:
        "".encode("mbcs")
        return True
    except LookupError:
        return False


def resolve_game_exe(path: str) -> Optional[str]:
    """The game .exe behind a dropped file: the file itself if it's an .exe, the target of a
    shortcut, or - for Luna's shortcuts - the game Luna would launch."""
    p = Path(path)
    if p.suffix.lower() == ".exe":
        return str(p)
    if p.suffix.lower() != ".lnk":
        return None
    try:
        target, args = read(str(p))
    except (OSError, struct.error, ValueError):
        return None
    if not target or not target.lower().endswith(".exe"):
        return None
    m = re.search(r"--Exec\s+(\"[^\"]+\"|\S+)", args or "")
    if m and luna.kind(target) == "translator":
        uid = m.group(1).strip('"')
        game = luna.game_path_for_uid(target, uid)
        if game:
            return game
    return target
