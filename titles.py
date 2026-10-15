"""
titles.py - turn whatever a game calls itself into a clean library title.

Sources, best first: the game's window title (read while it runs), the title stored in the .exe,
the game folder's name, the .exe's name. Obvious add-ons at the END are removed, in English or
Japanese: version numbers and edition tags ("ver1.02", "Complete Edition", "体験版", "【DL版】").
Real subtitles ("―櫻の森の下を歩む―") are kept.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

_EDITIONS_EN = [
    r"complete edition", r"trial edition", r"trial version", r"trial", r"demo version", r"demo",
    r"steam edition", r"steam version", r"dl edition", r"download edition", r"download version",
    r"remastered", r"remaster", r"hd remaster", r"hd edition", r"director'?s cut", r"full version",
    r"standard edition", r"limited edition", r"deluxe edition", r"definitive edition",
    r"english version", r"japanese version", r"jp version", r"en version",
]
_EDITIONS_JA = [
    r"完全版", r"体験版", r"製品版", r"通常版", r"初回限定版", r"初回版", r"限定版", r"豪華版",
    r"廉価版", r"普及版", r"DL版", r"ＤＬ版", r"ダウンロード版", r"パッケージ版", r"リマスター版",
    r"リマスター", r"HDリマスター", r"新装版", r"英語版", r"日本語版", r"Steam版", r"ｓｔｅａｍ版",
    r"デモ版", r"お試し版", r"ベータ版", r"β版",
]
_VERSION = r"(?:ver(?:sion)?\.?\s*|v\.?\s*)?\d+(?:[._]\d+)+[a-z]?|ver(?:sion)?\.?\s*\d+[a-z]?|v\d+[a-z]?"
_TAG = "|".join(_EDITIONS_EN + _EDITIONS_JA + [_VERSION])
_OPEN, _CLOSE = r"[\[\(（【〔「『<＜]", r"[\]\)）】〕」』>＞]"
_SEPCHARS = "-–—_:：・|｜/／~～"

_BRACKETED = re.compile(rf"{_OPEN}\s*(?:{_TAG})(?:\s*[ /・]\s*(?:{_TAG}))*\s*{_CLOSE}\s*$", re.IGNORECASE)
# an edition tag between dashes, as in file names: "大穢-完全版-" (a real subtitle like "-前編-" stays)
_DASHED = re.compile(rf"[-－~～―]\s*(?:{_TAG})\s*[-－~～―]\s*$", re.IGNORECASE)
_WRAPPED = re.compile(r"^\s*([『「【〔\[(（<＜])(.+)([』」】〕\])）>＞])\s*$")
_PAIRS = {"『": "』", "「": "」", "【": "】", "〔": "〕", "[": "]", "(": ")", "（": "）", "<": ">", "＜": "＞"}
_BARE = re.compile(rf"(?:{_TAG})\s*$", re.IGNORECASE)
_JA_TAG = re.compile(rf"(?:{'|'.join(_EDITIONS_JA)})\s*$", re.IGNORECASE)


def _cut(t: str, start: int) -> str:
    """Remove t[start:], plus a separator in front of it - but only one that stands apart
    ("Title - Complete Edition"), never a subtitle's own closing dash ("-the out of infinity-")."""
    head = t[:start].rstrip()
    if len(head) >= 2 and head[-1] in _SEPCHARS and head[-2].isspace():
        head = head[:-1].rstrip()
    return head


def clean_title(raw: str) -> str:
    """Strip trailing version numbers / edition tags (repeatedly), keep everything else."""
    t = " ".join((raw or "").split())
    m = _WRAPPED.match(t)
    if m and _PAIRS.get(m.group(1)) == m.group(3) and not re.search(r"[『「【〔\[(（<＜』」】〕\])）>＞]", m.group(2)):
        t = m.group(2).strip()               # 『つくとり』 -> つくとり (brackets round the whole title only)
    for _ in range(6):
        new = t
        m = _BRACKETED.search(new) or _DASHED.search(new)
        if m:
            new = _cut(new, m.start())
        else:
            m = _BARE.search(new)
            # a bare tag must stand apart (space / bracket before it); Japanese tags may be attached
            if m and m.start() > 0 and (new[m.start() - 1].isspace() or new[m.start() - 1] in "】)）]」』"
                                       or _JA_TAG.fullmatch(new[m.start():])):
                new = _cut(new, m.start())
        if new == t or not new:
            break
        t = new
    return t or (raw or "").strip()


def exe_product_name(exe: str) -> Optional[str]:
    """ProductName / FileDescription from the .exe's version info (Windows only). Many VNs leave
    these empty, which is why the window title is preferred."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    ver = ctypes.WinDLL("version")
    size = ver.GetFileVersionInfoSizeW(exe, None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(exe, 0, size, buf):
        return None
    ptr, length = ctypes.c_void_p(), wintypes.UINT()
    langs = []
    if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(length)) and length.value:
        arr = ctypes.cast(ptr, ctypes.POINTER(wintypes.WORD * (length.value // 2))).contents
        langs = [(arr[i], arr[i + 1]) for i in range(0, len(arr) - 1, 2)]
    langs += [(0x0411, 0x04B0), (0x0411, 0x03A4), (0x0409, 0x04B0), (0x0409, 0x04E4)]
    for field in ("ProductName", "FileDescription"):
        for lang, cp in langs:
            q = f"\\StringFileInfo\\{lang:04x}{cp:04x}\\{field}"
            if ver.VerQueryValueW(buf, q, ctypes.byref(ptr), ctypes.byref(length)) and length.value > 1:
                s = ctypes.wstring_at(ptr, length.value - 1).strip()
                if s and s.lower() not in ("game", "application", "launcher"):
                    return s
    return None


# what game engines write into the .exe instead of the game's name ("AdvGame Module", "codeX RScript",
# "TVP(KIRIKIRI) Z core / Scripting Platform for Win32", "Stuff スクリプトエンジン", "AVGEngineV2")
_ENGINE = re.compile(r"engine|エンジン|module|script|platform|\bcore\b|kirikiri|krkr|\btvp|siglus|nscripter|"
                     r"\bbgi\b|ren'?py|tyrano|unity|launcher|runtime|\bplayer\b|^(?:game|application|launcher)$",
                     re.IGNORECASE)
# folder / .exe names that say nothing about which game it is
_GENERIC = {"game", "games", "bin", "data", "system", "program", "x64", "x86", "win", "windows", "start",
            "launcher", "launch", "setup", "config", "play", "main", "bgi", "update", "patch", "app",
            "siglusengine", "nscripter", "kirikiri", "krkr", "tyrano", "renpy", "player", "runtime", "exe"}
_JA = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")


def is_engine_name(name: str) -> bool:
    return bool(_ENGINE.search(name or ""))


def _usable(name: str) -> bool:
    n = (name or "").strip()
    return bool(n) and n.casefold() not in _GENERIC and len(n) >= 2 and not re.fullmatch(r"[a-z]:\\?", n, re.I)


def _flat(name: str) -> str:
    return re.sub(r"[\s\-_・.]", "", name.casefold())


def guess_title(exe: str) -> str:
    """Best title available before the game has ever run. The .exe's own name for itself when it is a
    real title (engines often put their own name there instead); otherwise the better of the game
    folder's name and the .exe's name: the Japanese one if only one is Japanese, the fuller one if one is
    a shortened form of the other ("Reli" / "Relirium"), else the folder. A folder called Data, Game...
    is skipped in favour of the folder above it."""
    name = exe_product_name(exe)
    if name and not is_engine_name(name):
        return clean_title(name)
    p = Path(exe)
    folder = p.parent
    while folder.name and not _usable(folder.name) and folder.parent != folder:
        folder = folder.parent
    candidates = [clean_title(n) for n in (folder.name, p.stem) if _usable(n)]
    candidates = [c for c in candidates if _usable(c)]
    if not candidates:
        return clean_title(p.stem)
    if len(candidates) == 1:
        return candidates[0]
    a, b = candidates                                       # folder, exe name
    ja_a, ja_b = bool(_JA.search(a)), bool(_JA.search(b))
    if ja_a != ja_b:
        return a if ja_a else b
    fa, fb = _flat(a), _flat(b)
    if fa != fb and fa in fb:
        return b
    if fa != fb and fb in fa:
        return a
    return a


def usable_window_title(title: str) -> bool:
    """Ignore empty or obviously non-title windows (consoles, error boxes, launchers' default names)."""
    t = title.strip()
    if len(t) < 2:
        return False
    bad = ("default ime", "msctfime", "error", "エラー", "warning", "警告", "cmd.exe", "console",
           "setup", "install", "unity", "directx", "opengl")
    return not any(t.lower() == b or t.lower().startswith(b + " ") for b in bad)
