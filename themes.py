"""
themes.py - saved custom themes, shared by both devices through the cloud folder.

A saved theme is a snapshot of the whole look: every colour, the library background (picture, opacity,
crop; none = no background) and the English / Japanese fonts. Pictures and added font files are copied
next to the list, so the other device has them too:

    <library root>/themes/themes.json      {"themes": {id: {name, colors, font_en, font_ja, bg, fonts, saved_at}}}
    <library root>/themes/pictures/<id>.<ext>
    <library root>/themes/fonts/<font file>
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from library import _atomic_write

FOLDER = "themes"


def folder(root: str) -> Path:
    return Path(root) / FOLDER


def _read(root: str, strict: bool = False) -> dict:
    """The saved themes by id. strict (before writing): a list that exists but can't be read raises, so it's never
    replaced by a near-empty one (every theme would be lost); only a missing list counts as empty."""
    path = folder(root) / "themes.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        if strict:
            raise OSError(f"{path} couldn't be read")
        return {}
    themes = data.get("themes") if isinstance(data, dict) else None
    return {k: v for k, v in themes.items() if isinstance(v, dict)} if isinstance(themes, dict) else {}


def _write(root: str, themes: dict) -> None:
    _atomic_write(folder(root) / "themes.json", json.dumps({"themes": themes}, ensure_ascii=False, indent=1))


def load(root: str) -> list[dict]:
    """Every saved theme (each with its "id"), oldest first."""
    out = [{**t, "id": k} for k, t in _read(root).items()]
    return sorted(out, key=lambda t: (t.get("saved_at") or 0, t["id"]))


def add(root: str, name: str, colors: dict, font_en: str, font_ja: str, bg: Optional[tuple],
        font_files: dict) -> str:
    """Save a theme. bg = (picture, opacity, zoom, x, y) or None; font_files = family -> file for fonts the
    owner added (built-in fonts need no copy). Returns its id."""
    tid = uuid.uuid4().hex[:12]
    base = folder(root)
    entry = {"name": name, "colors": dict(colors), "font_en": font_en, "font_ja": font_ja, "bg": None,
             "fonts": {}, "saved_at": time.time()}
    if bg and bg[0] and Path(bg[0]).is_file():
        pic = base / "pictures" / f"{tid}{Path(bg[0]).suffix.lower()}"
        pic.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bg[0], pic)
        entry["bg"] = {"file": pic.name, "opacity": bg[1], "zoom": bg[2], "x": bg[3], "y": bg[4]}
    for fam in {font_en, font_ja}:
        src = font_files.get(fam)
        if src and Path(src).is_file():
            dest = base / "fonts" / Path(src).name
            dest.parent.mkdir(parents=True, exist_ok=True)
            n = 1
            while dest.exists() and dest.read_bytes() != Path(src).read_bytes():   # (another font, same file name)
                n += 1
                dest = dest.with_name(f"{Path(src).stem}_{n}{Path(src).suffix}")
            if not dest.exists():
                shutil.copy2(src, dest)
            entry["fonts"][fam] = dest.name
    themes = _read(root, strict=True)         # (read again just before writing: the other device may have added one)
    themes[tid] = entry
    _write(root, themes)
    return tid


def rename(root: str, tid: str, name: str) -> None:
    themes = _read(root, strict=True)
    if tid in themes:
        themes[tid]["name"] = name
        _write(root, themes)


def delete(root: str, tid: str) -> None:
    """Remove a theme, its picture, and font files no other theme uses."""
    themes = _read(root, strict=True)
    gone = themes.pop(tid, None)
    if gone is None:
        return
    _write(root, themes)
    base = folder(root)
    if isinstance(gone.get("bg"), dict):
        try:
            (base / "pictures" / gone["bg"]["file"]).unlink()
        except (OSError, KeyError, TypeError):
            pass
    used = {f for t in themes.values() for f in (t.get("fonts") or {}).values()}
    for f in (gone.get("fonts") or {}).values():
        if f not in used:
            try:
                (base / "fonts" / f).unlink()
            except OSError:
                pass


def merge(solo_root: str, cloud_root: str, at_start: dict) -> None:
    """Back to Dual: themes saved on this computer while solo join the shared list (with their pictures and
    fonts), ones renamed here take the new name, ones deleted here leave it. at_start = id -> name when solo began."""
    mine, shared = _read(solo_root), _read(cloud_root, strict=True)
    out = {k: dict(v) for k, v in shared.items()}
    for tid, t in mine.items():
        if tid in out:
            if t.get("name") != at_start.get(tid, t.get("name")):
                out[tid]["name"] = t.get("name")                   # renamed here
        elif tid not in at_start:                                   # saved here (not one deleted elsewhere meanwhile)
            files = [("pictures", t["bg"].get("file"))] if isinstance(t.get("bg"), dict) else []
            files += [("fonts", f) for f in (t.get("fonts") or {}).values()]
            for sub, name in files:
                src, dest = folder(solo_root) / sub / str(name), folder(cloud_root) / sub / str(name)
                if name and src.is_file() and not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
            out[tid] = t
    if out != shared:
        _write(cloud_root, out)
    for tid in at_start:
        if tid not in mine and tid in out:                          # deleted here
            delete(cloud_root, tid)


def picture(root: str, theme: dict) -> Optional[Path]:
    """The theme's background picture in the cloud (None: no background, or it's missing)."""
    bg = theme.get("bg")
    if not isinstance(bg, dict) or not bg.get("file"):
        return None
    p = folder(root) / "pictures" / bg["file"]
    return p if p.is_file() else None


def font_file(root: str, theme: dict, family: str) -> Optional[Path]:
    name = (theme.get("fonts") or {}).get(family)
    p = folder(root) / "fonts" / name if name else None
    return p if p and p.is_file() else None


def local_copies(root: str, cache: Path, saved: list[dict], protect: frozenset = frozenset()) -> list[dict]:
    """Copy each theme's picture and font files into `cache` on this computer (only what's new or changed), so
    showing and picking themes never waits on the cloud folder. Returns, per theme, {"theme", "picture" (local path
    or ""), "fonts" {family: local path}}. Files no theme uses any more are tidied away, except those in `protect`
    (a theme being tried right now, deleted meanwhile: Save still keeps its look). (Run in the background.)"""
    out, keep = [], set()

    def copy(src: Optional[Path], sub: str) -> str:
        if src is None:
            return ""
        dest = cache / sub / src.name
        try:
            st = src.stat()
            if not (dest.is_file() and dest.stat().st_size == st.st_size and dest.stat().st_mtime == st.st_mtime):
                dest.parent.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_name(dest.name + ".part")
                shutil.copy2(src, tmp)
                tmp.replace(dest)
        except OSError:
            return str(dest) if dest.is_file() else ""
        keep.add(dest)
        return str(dest)

    for t in saved:
        fonts = {}
        for fam in (t.get("fonts") or {}):
            local = copy(font_file(root, t, fam), "fonts")
            if local:
                fonts[fam] = local
        out.append({"theme": t, "picture": copy(picture(root, t), "pictures"), "fonts": fonts})
    for sub in ("pictures", "fonts"):
        d = cache / sub
        if d.is_dir():
            for f in d.iterdir():
                if f not in keep and str(f) not in protect:
                    try:
                        f.unlink()
                    except OSError:
                        pass
    return out
