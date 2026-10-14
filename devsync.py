"""
devsync.py - "Ikemen Sync": copy the look and preferences from the other device.

Each device shares its personal settings (theme, fonts, background, language, ...) into
<cloud>/device_settings/<slot>/ whenever Ikemen Reader opens or its settings are saved.
"Device Settings Sync" then reads the other device's copy. Things that only make sense on one computer
(which device it is, the cloud folder, Locale Emulator / Luna locations, startup) are never shared.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from library import LocalSettings, _atomic_write

FOLDER = "device_settings"
SHARED = ("language", "font_en", "font_ja", "pulse", "vndb", "vndb_safe_only", "sort", "status_filter",
          "theme", "custom_colors", "bg_opacity", "bg_zoom", "bg_x", "bg_y", "show_stats")


def device_dir(cloud_root: str, slot: str) -> Path:
    return Path(cloud_root) / FOLDER / slot


def _same_file(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
    except OSError:
        return False
    return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)


def _copy_if_changed(src: Path, dest: Path) -> None:
    if not _same_file(src, dest):
        tmp = dest.with_name(f"{dest.name}.{threading.get_ident()}.tmp")
        shutil.copy2(src, tmp)                 # keeps the modified time, so unchanged files aren't re-copied
        tmp.replace(dest)


_PUBLISHING = threading.Lock()                 # one share at a time (opening the app + a quick settings change)


def publish(settings: LocalSettings, computer: str, font_files: dict[str, str]) -> None:
    """Share this device's personal settings (and the files they need) into the cloud folder.
    font_files: family -> font file, for fonts the owner added (built-in ones exist on every device)."""
    with _PUBLISHING:
        _publish(settings, computer, font_files)


def _publish(settings: LocalSettings, computer: str, font_files: dict[str, str]) -> None:
    d = device_dir(settings.cloud_root, settings.machine_name)
    d.mkdir(parents=True, exist_ok=True)
    data = {k: getattr(settings, k) for k in SHARED}
    keep = {"settings.json"}
    bg = Path(settings.bg_image) if settings.bg_image else None
    if bg is not None and bg.is_file():
        name = "background" + bg.suffix.lower()
        _copy_if_changed(bg, d / name)
        data["background"] = name
        keep.add(name)
    else:
        data["background"] = ""
    fonts = {}
    for fam in {settings.font_en, settings.font_ja}:
        f = Path(font_files[fam]) if fam in font_files else None
        if f is not None and f.is_file():
            (d / "fonts").mkdir(exist_ok=True)
            _copy_if_changed(f, d / "fonts" / f.name)
            fonts[fam] = f.name
    data["fonts"] = fonts
    data["computer"] = computer
    old = read(settings.cloud_root, settings.machine_name)
    unchanged = old is not None and {k: v for k, v in old.items() if k != "shared_at"} == data
    previous = old.get("shared_at") if old else None
    data["shared_at"] = previous if unchanged and isinstance(previous, (int, float)) else time.time()
    unchanged = unchanged and isinstance(previous, (int, float))
    if not unchanged:
        _atomic_write(d / "settings.json", json.dumps(data, ensure_ascii=False, indent=2))
    # tidy up what's no longer used (an old background picture, fonts no longer chosen)
    for f in d.iterdir():
        if f.is_file() and f.name not in keep and not f.name.endswith(".tmp"):
            try:
                f.unlink()
            except OSError:
                pass
    if (d / "fonts").is_dir():
        for f in (d / "fonts").iterdir():
            if f.name not in fonts.values():
                try:
                    f.unlink()
                except OSError:
                    pass


def read(cloud_root: str, slot: str) -> Optional[dict]:
    """The settings a device has shared, or None if it hasn't (or the file is damaged)."""
    try:
        data = json.loads((device_dir(cloud_root, slot) / "settings.json").read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def apply(shared: dict, cloud_root: str, slot: str, mine: LocalSettings) -> tuple[LocalSettings, dict]:
    """This device's settings with the other device's personal ones on top (nothing is saved here).
    Returns (settings, files): files = {"background": path or "", "fonts": {family: path}} inside the
    cloud folder, for the caller to copy in. Missing or wrong-typed values keep this device's own."""
    import dataclasses
    d = device_dir(cloud_root, slot)
    changes = {}
    for k in SHARED:
        if k not in shared:
            continue
        v, cur = shared[k], getattr(mine, k)
        if type(cur) is float and type(v) is int:
            v = float(v)
        if type(v) is type(cur):               # (exact type: True must not pass as a number, or 1 as a bool)
            changes[k] = v
    if "custom_colors" in changes:              # colour codes only, like the settings file
        import re
        changes["custom_colors"] = {k: v for k, v in changes["custom_colors"].items()
                                    if isinstance(k, str) and isinstance(v, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", v)}
    for k in ("bg_opacity", "bg_zoom", "bg_x", "bg_y"):
        if k in changes and not (changes[k] == changes[k] and abs(changes[k]) < 1e6):   # NaN / infinity
            del changes[k]
    result = dataclasses.replace(mine, **changes)
    result.bg_opacity = min(1.0, max(0.0, result.bg_opacity))
    result.bg_zoom = min(4.0, max(1.0, result.bg_zoom))
    result.bg_x, result.bg_y = min(1.0, max(0.0, result.bg_x)), min(1.0, max(0.0, result.bg_y))

    def plain(name) -> bool:                    # a file name inside the shared folder, nothing else
        return isinstance(name, str) and name not in ("", ".", "..") and Path(name).name == name
    bg = shared.get("background")
    fonts = shared.get("fonts") if isinstance(shared.get("fonts"), dict) else {}
    files = {"background": str(d / bg) if plain(bg) and (d / bg).is_file() else "",
             "fonts": {fam: str(d / "fonts" / name) for fam, name in fonts.items()
                       if isinstance(fam, str) and plain(name) and (d / "fonts" / name).is_file()}}
    return result, files
