"""
app.py - the Ikemen Reader window.

    py app.py

The heavy lifting lives in sync_engine.py / library.py / launcher.py; this file is only the UI.
Playing runs on a background thread so the window stays responsive while a game is open.

Easy-to-tweak things live at the top: COLORS / LIGHTS (palette), and the SIZES & TIMING block.
All interface text lives in i18n.py.
"""
from __future__ import annotations

import html
import dataclasses
import math
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (QSizeF, QEvent, QMimeData, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread,
                            QTimer, Signal)
from PySide6.QtGui import (QCursor, QTextOption, QTextLayout, QAction, QColor, QDrag, QFont, QFontDatabase, QFontMetrics, QIcon, QImage,
                           QKeySequence, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
                           QRadialGradient)
from PySide6.QtWidgets import (QScrollArea, QTextBrowser, QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox,
                               QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QPushButton, QRadioButton, QSizePolicy, QSlider, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget)

import i18n
import lnk
import startup
import devsync
import guide
import luna
import regsync
import vndb
from i18n import tr
from launcher import PlayResult, finish_first_play, play, warm_up
from library import (SLOT_NUMBER, SLOTS, STATUSES, Game, Library, LocalSettings, MachinePaths,
                     default_app_dir, load_local_settings, other_slot, save_local_settings,
                     sort_games, this_computer)
from sync_engine import Action, ConflictInfo, SyncEngine, save_folder_problem
from titles import guess_title

# =========================================================================== look & feel
DARK = {
    "bg": "#3B3B3B",            # main area, dialogs
    "band": "#1C1C1C",          # top bar, bottom bar, status bar
    "input": "#2E2E2E",
    "border": "#4A4A4A",
    "button": "#4A4A4A",
    "button_hover": "#575757",
    "text": "#EDEDED",
    "muted": "#A6A6A6",
    "other_bg": "#333333",      # Other Device's fields
    "other_text": "#8C8C8C",
    "focus": "#BDBDBD",
    "select": "#F2F2F2",        # border around the selected cover
    "play": "#EDEDED",
    "play_text": "#1C1C1C",
    "play_hover": "#FFFFFF",
    "play_disabled": "#4A4A4A",
    "play_disabled_text": "#7A7A7A",
    "danger": "#E38B8B",
    "placeholder_top": "#555555",
    "placeholder_bottom": "#262626",
}
def _reverse(hex_colour: str) -> str:
    c = QColor(hex_colour)
    return QColor(255 - c.red(), 255 - c.green(), 255 - c.blue()).name().upper()


# light mode: every grey mirrored, so the same contrasts and "vibe", just reversed
LIGHT = {k: _reverse(v) for k, v in DARK.items()}
LIGHT["danger"] = "#B5413E"                      # a red that reads on light grey
APP_VERSION = "1.0.0"
BASE_LIGHTS = {"green": "#6FBF73", "blue": "#6FA8DC", "amber": "#E0B252", "red": "#E0605B", "grey": "#8C8C8C"}
LOGO_ORIGINAL = {"logo_fill": "#F0F0F0", "logo_outline": "#303030"}   # what Logo.png / Icon.png are drawn in

# the palette in use; apply_theme() switches them in place, so everything reads the current values
COLORS = dict(DARK)
LIGHTS = dict(BASE_LIGHTS)
LOGO = dict(LOGO_ORIGINAL)
ICONS = {"check": "", "chevron": ""}           # generated in the theme's colours (see _write_icons)

# custom theme editor: the main colours always show; the rest sit behind "Show All Colours"
THEME_MAIN = [
    ("basics", ["bg", "band", "button"]),
    ("text", ["text", "muted"]),
    ("highlights", ["play", "play_text", "select"]),
    ("logo", ["logo_fill", "logo_outline"]),
]
THEME_MORE = [
    ("fields", ["input", "other_bg", "other_text", "border", "focus"]),
    ("states", ["button_hover", "play_hover", "play_disabled", "play_disabled_text", "danger"]),
    ("covers", ["placeholder_top", "placeholder_bottom"]),
    ("lights", [f"light_{k}" for k in BASE_LIGHTS]),
]
THEME_GROUPS = THEME_MAIN + THEME_MORE


def theme_colors(theme: str, custom: Optional[dict] = None) -> dict:
    """Every colour role for a theme. Custom starts from Dark and applies the owner's choices."""
    base = dict(LIGHT if theme == "light" else DARK)
    base.update({f"light_{k}": v for k, v in BASE_LIGHTS.items()})
    base.update(LOGO_ORIGINAL)
    if theme == "custom" and custom:
        base.update({k: v.upper() for k, v in custom.items() if k in base})
    return base


def apply_theme(theme: str = "dark", custom: Optional[dict] = None) -> None:
    full = theme_colors(theme, custom)
    COLORS.clear()
    COLORS.update({k: full[k] for k in DARK})
    LIGHTS.clear()
    LIGHTS.update({k: full[f"light_{k}"] for k in BASE_LIGHTS})
    LOGO.update({k: full[k] for k in LOGO_ORIGINAL})
    _write_icons()


def _write_icons() -> None:
    """The tick and drop-down arrow, drawn in the theme's colours (the file name includes the colour so
    Qt doesn't reuse an old picture)."""
    d = default_app_dir() / "theme"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return                  # (the check box and arrow then use Qt's own look)
    tick, arrow = COLORS["band"], COLORS["text"]
    files = {
        "check": (f"check_{tick[1:]}.svg",
                  f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><path d="M3.5 8.5 '
                  f'L6.5 11.5 L12.5 4.5" fill="none" stroke="{tick}" stroke-width="2.2" stroke-linecap="round" '
                  f'stroke-linejoin="round"/></svg>'),
        "chevron": (f"chevron_{arrow[1:]}.svg",
                    f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 4.5 '
                    f'L6 8 L9.5 4.5" fill="none" stroke="{arrow}" stroke-width="1.6" stroke-linecap="round" '
                    f'stroke-linejoin="round"/></svg>'),
    }
    for key, (name, svg) in files.items():
        f = d / name
        if not f.exists():
            try:
                f.write_text(svg, "utf-8")
            except OSError:
                continue
        ICONS[key] = str(f)


# library background picture (drawn behind the covers by Backdrop)
BACKGROUND = {"path": "", "opacity": 0.35, "zoom": 1.0, "x": 0.5, "y": 0.5, "live": False}
_bg_cache: dict = {}
_bg_source: dict = {}          # the decoded picture (one), so dragging/zooming doesn't re-read the file


def apply_background(path: str, opacity: float, zoom: float = 1.0, x: float = 0.5, y: float = 0.5,
                     live: bool = False) -> None:
    """live=True while the owner is dragging/zooming: draw straight from a pre-shrunk copy of the picture
    (fast enough for every frame); live=False draws the full-quality version (built once)."""
    BACKGROUND.update(path=path or "", opacity=max(0.0, min(1.0, opacity)), zoom=max(1.0, min(4.0, zoom)),
                      x=max(0.0, min(1.0, x)), y=max(0.0, min(1.0, y)), live=live)
    app = QApplication.instance()
    if app:
        for w in app.topLevelWidgets():
            if hasattr(w, "backdrop"):
                w.backdrop.update()
                w.grid.viewport().update()


_bg_missing: dict[str, float] = {}       # picture that couldn't be opened -> when we last tried


def _bg_image(path: str) -> Optional[QImage]:
    img = _bg_source.get(path)
    if img is None:
        # a deleted / unplugged picture: don't retry on every redraw (dozens a second while animating)
        if time.monotonic() - _bg_missing.get(path, -1e9) < 5:
            return None
        img = QImage(path)
        if img.isNull():
            _bg_missing[path] = time.monotonic()
            return None
        _bg_missing.pop(path, None)
        _bg_source.clear()
        _bg_source[path] = img
    return img


_bg_working: dict = {}


def working_pixmap(path: str, area: QSize) -> Optional[tuple]:
    """(pixmap, scale): the picture shrunk once to about twice the library area (enough detail up to 2x
    zoom), used while dragging. scale = working pixels per original pixel."""
    img = _bg_image(path)
    if img is None or area.isEmpty():
        return None
    limit = 2 * max(area.width(), area.height())
    key = (path, limit)
    if key not in _bg_working:
        longest = max(img.width(), img.height())
        scale = min(1.0, limit / longest)
        small = img if scale >= 1.0 else img.scaled(round(img.width() * scale), round(img.height() * scale),
                                                     Qt.KeepAspectRatio, Qt.SmoothTransformation)
        _bg_working.clear()
        _bg_working[key] = (QPixmap.fromImage(small), small.width() / img.width())
    return _bg_working[key]


def background_pixmap(path: str, size: QSize, crop: Optional[tuple] = None) -> Optional[QPixmap]:
    """The picture filling `size`, cropped like a cover: zoom 1 = the largest area of the picture with
    that shape; (x, y) = its centre. crop defaults to the current background's."""
    if not path or size.isEmpty():
        return None
    zoom, cx, cy = crop if crop is not None else (BACKGROUND["zoom"], BACKGROUND["x"], BACKGROUND["y"])
    key = (path, size.width(), size.height(), round(zoom, 4), round(cx, 4), round(cy, 4))
    if key not in _bg_cache:
        img = _bg_image(path)
        if img is None:
            return None
        r, _, _ = crop_rect(img.width(), img.height(), zoom, cx, cy, aspect=size.width() / size.height())
        part = img.copy(r.toAlignedRect()).scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        if len(_bg_cache) > 6:
            _bg_cache.clear()
        _bg_cache[key] = QPixmap.fromImage(part)
    return _bg_cache[key]


_blend_cache: dict = {}


def blended_backdrop(size: QSize) -> Optional[QPixmap]:
    """The library background already mixed with the main colour at the chosen opacity, so any part
    of it can be copied straight onto the screen (cheap enough to redraw at animation speed)."""
    if not BACKGROUND["path"] or BACKGROUND["opacity"] <= 0 or size.isEmpty():
        return None
    key = (BACKGROUND["path"], round(BACKGROUND["opacity"], 3), COLORS["bg"], size.width(), size.height(),
           round(BACKGROUND["zoom"], 4), round(BACKGROUND["x"], 4), round(BACKGROUND["y"], 4))
    if key not in _blend_cache:
        pic = background_pixmap(BACKGROUND["path"], size)
        if pic is None:
            return None
        out = QPixmap(size)
        out.fill(QColor(COLORS["bg"]))
        p = QPainter(out)
        p.setOpacity(BACKGROUND["opacity"])
        p.drawPixmap(0, 0, pic)
        p.end()
        if len(_blend_cache) > 4:
            _blend_cache.clear()
        _blend_cache[key] = out
    return _blend_cache[key]


class Backdrop(QWidget):
    """The window's central area: the main colour, plus the library background picture (fixed in
    place behind the covers, between the top and bottom bars) at the chosen opacity."""

    def __init__(self, window: "MainWindow"):
        super().__init__(objectName="central")
        self.w = window

    def library_area(self) -> QRect:
        top = self.w.topbar.geometry().bottom() + 1 if hasattr(self.w, "topbar") else 0
        details = getattr(self.w, "details", None)
        bottom = details.geometry().top() if details is not None and details.isVisible() else self.height()
        return QRect(0, top, self.width(), max(0, bottom - top))

    def paint_into(self, p: QPainter, target: QRect, offset: QPoint) -> None:
        """Draw the backdrop for `target` (in the painter's coordinates); offset = where that painter's
        (0, 0) sits inside the library area."""
        if BACKGROUND["live"] and BACKGROUND["path"]:
            self._paint_live(p, target, offset)
            return
        pm = blended_backdrop(self.library_area().size())
        if pm is None:
            p.fillRect(target, QColor(COLORS["bg"]))
        else:
            p.drawPixmap(target, pm, target.translated(offset))

    def _paint_live(self, p: QPainter, target: QRect, offset: QPoint) -> None:
        """While dragging: scale the visible part of the pre-shrunk picture straight onto the screen."""
        area = self.library_area()
        p.fillRect(target, QColor(COLORS["bg"]))
        work = working_pixmap(BACKGROUND["path"], area.size())
        img = _bg_image(BACKGROUND["path"])
        if work is None or img is None or area.isEmpty():
            return
        pm, scale = work
        r, _, _ = crop_rect(img.width(), img.height(), BACKGROUND["zoom"], BACKGROUND["x"], BACKGROUND["y"],
                            aspect=area.width() / area.height())
        src = QRectF(r.x() * scale, r.y() * scale, r.width() * scale, r.height() * scale)
        p.save()
        p.setClipRect(target)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setOpacity(BACKGROUND["opacity"])
        p.drawPixmap(QRectF(-offset.x(), -offset.y(), area.width(), area.height()), pm, src)
        p.restore()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.fillRect(ev.rect(), QColor(COLORS["bg"]))
        area = self.library_area()
        part = ev.rect().intersected(area)
        if not part.isEmpty():
            self.paint_into(p, part, -area.topLeft())
        p.end()


class LibraryGrid(QListWidget):
    """The cover grid. It draws its own part of the backdrop (only where something changed), and
    redraws instead of sliding pixels when scrolling, so the background picture stays fixed."""

    def __init__(self, window: "MainWindow"):
        super().__init__(objectName="library")
        self.w = window
        self.viewport().setAutoFillBackground(False)
        self.viewport().setAttribute(Qt.WA_OpaquePaintEvent, True)   # we paint every pixel ourselves

    def paintEvent(self, ev) -> None:
        backdrop = getattr(self.w, "backdrop", None)
        if backdrop is not None:
            p = QPainter(self.viewport())
            origin = self.viewport().mapTo(backdrop, QPoint(0, 0)) - backdrop.library_area().topLeft()
            backdrop.paint_into(p, ev.rect(), origin)
            p.end()
        super().paintEvent(ev)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if BACKGROUND["path"]:
            self.viewport().update()          # keep the picture still while the covers scroll


# fonts: every font Ikemen Reader offers (family -> "builtin"/"user") and the choice per interface language
FONTS: dict[str, str] = {}
FONT_FILES: dict[str, str] = {}          # family -> its file, for fonts the owner added (shared by Ikemen Sync)
FONT_CHOICE = {"en": "Hiragino Maru Gothic ProN", "ja": "Hiragino Maru Gothic ProN"}
FONT_FALLBACKS = ["Hiragino Maru Gothic ProN", "Zen Maru Gothic", "Yu Gothic UI"]


def ui_families() -> list[str]:
    """The font order for the interface: the current language's font first, then the other language's
    (for characters the first one doesn't have), then the built-in fallbacks."""
    lang = i18n.language()
    order = [FONT_CHOICE.get(lang), FONT_CHOICE.get("ja" if lang == "en" else "en"), *FONT_FALLBACKS]
    out = []
    for f in order:
        if f and f not in out:
            out.append(f)
    return out


def ui_font(point_size: int = 10) -> QFont:
    f = QFont()
    f.setFamilies(ui_families())
    f.setPointSize(point_size)
    return f


def set_interface_language(lang: str) -> None:
    """Switch every text (and Qt's own dialogs, and the font order) to this language."""
    i18n.set_language(lang)
    set_qt_language(lang)
    restyle_all()                           # the current language's font goes first


def apply_fonts(font_en: str, font_ja: str) -> None:
    FONT_CHOICE["en"], FONT_CHOICE["ja"] = font_en or FONT_FALLBACKS[0], font_ja or FONT_FALLBACKS[0]


_recoloured: dict = {}


def themed_image(name: str, height: int) -> QImage:
    """Logo.png / Icon.png at a given pixel height, in the theme's logo colours. Both images are two-tone
    (light fill, dark outline, soft edges between), so each pixel is rebuilt from the two chosen colours
    according to where its brightness falls between the originals; transparency is kept, so edges stay soft."""
    key = (name, height, LOGO["logo_fill"], LOGO["logo_outline"])
    if key in _recoloured:
        return _recoloured[key]
    img = QImage(str(asset(name)))
    if img.isNull():
        return img
    img = img.scaledToHeight(height, Qt.SmoothTransformation).convertToFormat(QImage.Format_ARGB32)
    if LOGO != LOGO_ORIGINAL:
        img = _recolour(img, LOGO["logo_fill"], LOGO["logo_outline"])
    if len(_recoloured) > 40:
        _recoloured.clear()
    _recoloured[key] = img
    return img


def _recolour(img: QImage, fill: str, outline: str) -> QImage:
    """Fast two-colour recolour with Qt's own image operations: brightness -> a 256-entry colour table
    (outline ... fill), then the original transparency is put back."""
    f, o = QColor(fill), QColor(outline)
    lo, hi = QColor(LOGO_ORIGINAL["logo_outline"]).lightness(), QColor(LOGO_ORIGINAL["logo_fill"]).lightness()
    # brightness of each pixel, ignoring its transparency
    opaque = img.convertToFormat(QImage.Format_RGB32)
    gray = opaque.convertToFormat(QImage.Format_Grayscale8)
    indexed = gray.convertToFormat(QImage.Format_Indexed8, [QColor(i, i, i).rgb() for i in range(256)])
    table = []
    for i in range(256):
        s = max(0.0, min(1.0, (i - lo) / (hi - lo)))
        table.append(QColor(round(o.red() + (f.red() - o.red()) * s), round(o.green() + (f.green() - o.green()) * s),
                            round(o.blue() + (f.blue() - o.blue()) * s)).rgb())
    indexed.setColorTable(table)
    out = indexed.convertToFormat(QImage.Format_ARGB32)
    out.setAlphaChannel(img.convertToFormat(QImage.Format_Alpha8))
    return out


def app_icon() -> QIcon:
    return QIcon(QPixmap.fromImage(themed_image("Icon.png", 256)))


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours (1 = identical, 21 = black on white)."""
    def lum(h):
        c = QColor(h)
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


_qt_translator = None


def set_qt_language(lang: str) -> None:
    """Qt's own dialogs (the colour picker) in the interface language."""
    global _qt_translator
    from PySide6.QtCore import QLibraryInfo, QTranslator
    app = QApplication.instance()
    if app is None:
        return
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if lang == "ja":
        t = QTranslator(app)
        if t.load("qtbase_ja", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            app.installTranslator(t)
            _qt_translator = t


def apply_palette() -> None:
    """Qt's own base colours, in the theme's colours. Parts Qt draws itself and style sheets can't reach
    (the colour picker's brightness bar and its pointer, frames) then match everything else."""
    from PySide6.QtGui import QPalette
    app = QApplication.instance()
    if app is None:
        return
    c = {k: QColor(v) for k, v in COLORS.items()}
    pal = QPalette()
    for role, key in ((QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "input"),
                      (QPalette.AlternateBase, "other_bg"), (QPalette.Text, "text"), (QPalette.Button, "button"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "button_hover"),
                      (QPalette.HighlightedText, "text"), (QPalette.ToolTipBase, "input"), (QPalette.ToolTipText, "text"),
                      (QPalette.PlaceholderText, "other_text"), (QPalette.Light, "border"), (QPalette.Midlight, "border"),
                      (QPalette.Mid, "border"), (QPalette.Dark, "band"), (QPalette.Shadow, "band"),
                      (QPalette.BrightText, "text"), (QPalette.Link, "text")):
        pal.setColor(role, c[key])
    pal.setColor(QPalette.Disabled, QPalette.Text, c["other_text"])
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, c["other_text"])
    pal.setColor(QPalette.Disabled, QPalette.WindowText, c["other_text"])
    app.setPalette(pal)


def restyle_all() -> None:
    """After a theme change: new style sheet, and every open window refreshes what it drew itself."""
    app = QApplication.instance()
    apply_palette()
    app.setStyleSheet(build_style())
    app.setWindowIcon(app_icon())
    for w in app.topLevelWidgets():
        if hasattr(w, "restyle"):
            w.restyle()

# -- sizes & timing
COVER_W, COVER_H = 176, 248          # roughly the shape of a VN package
CARD_PAD_X, CARD_PAD_TOP, CARD_PAD_BOTTOM = 12, 12, 18
CARD_W, CARD_H = COVER_W + 2 * CARD_PAD_X, COVER_H + CARD_PAD_TOP + CARD_PAD_BOTTOM
GRID_SPACING = 6
HOVER_LIFT = 6                       # px the cover rises on hover
HOVER_GROW = 0.03                    # 3% bigger on hover
HOVER_MS = 150                       # ease in/out time
PULSE_CALM_S = 2.6                   # one breath when all is well
PULSE_BUSY_S = 1.0                   # quicker while checking/syncing
SCROLLBAR_FADE_MS = 200              # scroll bar fade in/out
SCROLLBAR_LINGER_S = 1.2             # stays visible this long after scrolling / leaving the edge
SCROLLBAR_EDGE_PX = 36               # mouse this close to the right edge shows it
LIGHT_R = 5.5
LOGO_H = 30

GAME_ID_ROLE = Qt.UserRole + 1          # item id: a game id, or FOLDER_PREFIX + folder id
FOLDER_PREFIX = "folder:"
MIME_GAME = "application/x-ikemen-game"  # a game being dragged inside the grid
FOLDER_PAD, FOLDER_GAP = 10, 8           # mosaic inside a folder tile
# when a folder holds games in different states, its light shows the most important one
STATE_PRIORITY = ["conflict", "syncing", "playing", "push", "pull", "checking", "detect", "unset", "empty", "synced"]


def is_folder(item_id: Optional[str]) -> bool:
    return bool(item_id) and item_id.startswith(FOLDER_PREFIX)

# sync state -> (light colour, pulse: "calm" / "busy" / "steady", bottom-bar text key)
STATE_LOOK = {
    "synced":   ("green", "calm", "sync.state.synced"),
    "push":     ("amber", "calm", "sync.state.push"),
    "pull":     ("blue", "calm", "sync.state.pull"),
    "conflict": ("red", "steady", "sync.state.conflict"),
    "empty":    ("grey", "calm", "sync.state.empty"),
    "unset":    ("grey", "steady", "sync.state.unset"),
    "detect":   ("grey", "calm", "sync.state.detect"),
    "checking": ("grey", "busy", "sync.state.checking"),
    "playing":  ("blue", "calm", "sync.state.playing"),
    "syncing":  ("blue", "busy", "sync.state.checking"),
}


def asset(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / "assets" / name


def _mix(a: str, b: str, amount: float) -> str:
    """A colour part of the way from a to b (0 = a, 1 = b)."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * amount), round(ca.green() + (cb.green() - ca.green()) * amount),
                  round(ca.blue() + (cb.blue() - ca.blue()) * amount)).name()


def build_style() -> str:
    c = COLORS
    if not ICONS["check"]:
        _write_icons()
    check = Path(ICONS["check"]).as_posix()
    chevron = Path(ICONS["chevron"]).as_posix()
    families = ", ".join('"' + f.replace('"', "") + '"' for f in ui_families())
    return f"""
* {{ font-family: {families}; font-size: 10pt; color: {c['text']}; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QWidget#central {{ background: {c['bg']}; }}
QListWidget {{ background: {c['bg']}; border: none; outline: none; }}
QListWidget#library {{ background: transparent; }}
QLineEdit, QComboBox {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 6px;
                       padding: 5px 8px; selection-background-color: {c['button_hover']}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {c['focus']}; }}
QComboBox#fontpick QAbstractItemView::item {{ padding: 5px 8px; min-height: 22px; }}
QScrollArea#settings_scroll, QWidget#settings_content {{ background: transparent; }}
QTextBrowser#guide {{ background: {c['bg']}; border: none; font-size: 10.5pt; }}
QComboBox:disabled {{ color: {c['other_text']}; background: {c['other_bg']}; border-color: {c['other_bg']}; }}
QSpinBox {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 3px 6px;
           selection-background-color: {c['button_hover']}; }}
QSpinBox:focus {{ border-color: {c['focus']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
QLineEdit[secondary="true"] {{ background: {c['other_bg']}; color: {c['other_text']}; border-color: {c['border']}; }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ top: 1px; }}
QComboBox QAbstractItemView {{ background: {c['input']}; border: 1px solid {c['border']};
                               selection-background-color: {c['button_hover']}; outline: none; }}
QPushButton {{ background: {c['button']}; border: 1px solid {c['button']}; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ background: {c['button_hover']}; border-color: {c['button_hover']}; }}
QPushButton:disabled {{ color: {c['other_text']}; background: {c['other_bg']}; border-color: {c['other_bg']}; }}
QPushButton#play {{ background: {c['play']}; color: {c['play_text']}; border: none; font-size: 14pt;
                    padding: 8px 30px; border-radius: 8px; }}
QPushButton#play:hover {{ background: {c['play_hover']}; }}
QPushButton#play:disabled {{ background: {c['play_disabled']}; color: {c['play_disabled_text']}; }}
QPushButton#danger {{ color: {c['danger']}; }}
QPushButton#seg {{ padding: 7px 22px; }}
QPushButton#seg:checked {{ background: {c['play']}; color: {c['play_text']}; border-color: {c['play']}; }}
QPushButton#swatch {{ border: 1px solid {c['border']}; border-radius: 11px; padding: 0; }}
QPushButton#swatch:hover {{ border-color: {c['focus']}; }}
QLineEdit#hex {{ background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 3px 6px;
                color: {c['muted']}; }}
QLineEdit#hex:hover {{ border-color: {c['border']}; }}
QLineEdit#hex:focus {{ border-color: {c['focus']}; background: {c['input']}; color: {c['text']}; }}
QFrame#divider {{ background: {c['border']}; border: none; }}
QPushButton#toggle {{ background: transparent; border: none; color: {_mix(c['muted'], c['text'], 0.85)}; padding: 6px 0;
                      text-align: left; text-decoration: underline; }}
QPushButton#toggle:hover {{ color: {c['text']}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget#scrollbody {{ background: {c['bg']}; }}
QPushButton#flat {{ background: transparent; border: none; color: {c['muted']}; padding: 4px 8px; }}
QPushButton#flat:hover {{ color: {c['text']}; }}
QPushButton#back {{ background: transparent; border: none; color: {c['muted']}; padding: 4px 6px; font-size: 11pt; }}
QPushButton#back:hover {{ color: {c['text']}; }}
QFrame#folderbar {{ background: transparent; }}
QFrame#band {{ background: {c['band']}; }}
QLabel#title {{ font-size: 16pt; }}
QLabel#muted, QLabel#hint {{ color: {c['muted']}; }}
QLabel#hint {{ font-size: 9pt; }}
QLabel#section {{ color: {c['muted']}; padding-top: 8px; }}
QLabel#banner {{ background: {c['input']}; border-radius: 6px; padding: 6px 10px; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{ border: 1px solid {c['border']}; border-radius: 4px; background: {c['input']}; }}
QCheckBox::indicator:checked {{ background: {c['select']}; border-color: {c['select']}; image: url({check}); }}
QRadioButton::indicator {{ border: 1px solid {c['border']}; border-radius: 9px; background: {c['input']}; }}
QRadioButton::indicator:checked {{ border-color: {c['select']}; background: qradialgradient(cx:0.5, cy:0.5,
    radius:0.5, fx:0.5, fy:0.5, stop:0 {c['select']}, stop:0.42 {c['select']}, stop:0.5 {c['input']}, stop:1 {c['input']}); }}
QMenu {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['button_hover']}; }}
QMenu::item:disabled {{ color: {c['other_text']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QStatusBar {{ background: {c['band']}; color: {c['muted']}; }}
QStatusBar::item {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {c['button_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; background: none; }}
QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['muted']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c['select']}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QToolTip {{ background: {c['input']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
QMessageBox QLabel {{ min-width: 320px; }}
"""


def open_folder(path: str) -> None:
    if path and Path(path).exists():
        try:
            os.startfile(path)  # type: ignore[attr-defined]  (Windows only)
        except OSError:
            pass                # Windows refused (no permission / no handler): nothing else to do


def dot(colour_key: str) -> str:
    return f'<span style="color:{LIGHTS[colour_key]}">●</span>'


def message(parent, text: str, title: str = "", buttons=(("ok", "OK"),), icon=None, dismiss: str = "",
            rich: bool = False) -> str:
    """A message with our own (translated) buttons. Returns the chosen key; closing the window counts
    as the last button. (Our own dialog rather than QMessageBox, which measures its text before our
    font is applied and then cuts it off instead of wrapping it.)"""
    d = QDialog(parent)
    d.setWindowTitle(title or tr("app.name"))
    lay = QVBoxLayout(d)
    lay.setContentsMargins(22, 20, 22, 16)
    lay.setSpacing(16)
    lab = QLabel(text)
    lab.setTextFormat(Qt.RichText if rich else Qt.PlainText)   # (plain: a "<" in a title stays a "<")
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lab.setMinimumWidth(360)
    lab.setMaximumWidth(480)
    lay.addWidget(lab)
    row = QHBoxLayout()
    row.addStretch()
    chosen = {"key": dismiss or buttons[-1][0]}     # closing the window = `dismiss` (or the last button)
    for i, (key, label) in enumerate(buttons):
        b = QPushButton(label)
        b.clicked.connect(lambda _=False, k=key: (chosen.update(key=k), d.accept()))
        if i == 0:
            b.setDefault(True)
        row.addWidget(b)
    lay.addLayout(row)
    d.setFixedSize(d.sizeHint())              # sized to the wrapped text
    d.exec()
    return chosen["key"]


def ask_yes_no(parent, text: str, yes: str, no: str) -> bool:
    return message(parent, text, buttons=[("yes", yes), ("no", no)]) == "yes"


def heading_message(parent, heading: str, body: str, buttons, dismiss: str = "") -> str:
    """A message with a bold heading above the text."""
    import html
    text = (f'<span style="font-size:13pt; font-weight:600">{html.escape(heading)}</span><br><br>'
            + html.escape(body).replace("\n", "<br>"))
    return message(parent, text, title=heading, buttons=buttons, dismiss=dismiss, rich=True)


def report_failure(parent, e: Exception) -> None:
    """Explain why an action couldn't be finished. (Nothing is ever half-applied silently.)"""
    if isinstance(e, KeyError):
        key = "error.game_gone"                    # removed meanwhile, e.g. on the other device
    elif isinstance(e, ValueError) and str(e) in i18n.S:
        key = str(e)                               # a reason we explain ourselves (title taken, unsafe folder...)
    else:
        key = "error.cloud_write"                  # a file in use, the cloud folder unavailable...
    message(parent, tr(key))


def guarded(fn):
    """For actions the owner starts: if the library / cloud folder can't be written right now, say so
    (and refresh the library, so what's shown is what's really there) instead of silently doing nothing."""
    import functools

    @functools.wraps(fn)
    def run(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except (OSError, KeyError, ValueError) as e:
            report_failure(self, e)
            main = self if hasattr(self, "refresh") else self.parent()
            if hasattr(main, "refresh"):
                try:
                    main.refresh()
                except Exception:              # noqa: BLE001 - showing the message matters more
                    pass
    return run


def confirm_remove(parent, title: str) -> bool:
    """"Are You Sure?" before a game leaves the library: Cancel (the safe default, also Enter/closing) | Confirm."""
    import html
    body = html.escape(tr("game.remove_confirm", title=title)).replace("\n", "<br>")
    text = f'<span style="font-size:13pt; font-weight:600">{html.escape(tr("game.remove_sure"))}</span><br><br>{body}'
    return message(parent, text, title=tr("game.remove_sure"),
                   buttons=[("no", tr("game.cancel")), ("yes", tr("game.remove_yes"))], dismiss="no", rich=True) == "yes"


# =========================================================================== background work
class Task(QThread):
    """Run fn() on a background thread; emit done(result) or failed(exception)."""
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self.fn())
        except Exception as e:  # noqa: BLE001 - reported to the UI
            self.failed.emit(e)


def sync_state(engine: SyncEngine, game: Game, machine: str) -> str:
    mine = game.paths_for(machine)
    if not mine.exe:
        return "unset"
    if not mine.save_dir:
        return "detect"
    action = engine.status(game.game_id, mine.save_dir, game.registry_key)
    if action is Action.NOTHING:
        d = Path(mine.save_dir)
        return "synced" if (d.is_dir() and any(d.iterdir())) or game.registry_key else "empty"
    return {Action.PUSHED: "push", Action.PULLED: "pull", Action.CONFLICT: "conflict"}[action]


class StatusChecker(QThread):
    """Fingerprints every game's saves off the UI thread. On the first run after opening, it also
    uploads saves made outside Ikemen Reader (only when that's safe: the cloud hasn't changed)."""
    done = Signal(dict, list)

    def __init__(self, engine: SyncEngine, games: list[Game], machine: str, upload: bool):
        super().__init__()
        self.engine, self.games, self.machine, self.upload = engine, games, machine, upload

    def run(self) -> None:
        states, uploaded = {}, []
        for g in self.games:
            try:
                s = sync_state(self.engine, g, self.machine)
                if s == "push" and self.upload:
                    mine = g.paths_for(self.machine)
                    if self.engine.push_if_safe(g.game_id, mine.save_dir, g.registry_key):
                        uploaded.append(g.title)
                        s = "synced"
                states[g.game_id] = s
            except (OSError, ValueError):
                states[g.game_id] = "unset"
        self.done.emit(states, uploaded)


class PlayWorker(QThread):
    """Runs launcher.play() (sync down, run, wait, sync up) without freezing the window."""
    status = Signal(str)
    conflict = Signal(object)        # ConflictInfo -> the window asks the owner, blocking this thread
    result = Signal(object)          # PlayResult
    phase = Signal(str)              # "syncing" / "playing" / "done"

    def __init__(self, game: Game, settings: LocalSettings, library: Library, engine: SyncEngine,
                 without: tuple[str, ...] = (), watch: bool = False):
        super().__init__()
        self.game, self.settings, self.library, self.engine = game, settings, library, engine
        self.without, self.watch = without, watch
        self.answer: Optional[str] = None

    def _ask(self, info: ConflictInfo) -> Optional[str]:
        self.answer = None
        self.conflict.emit(info)     # BlockingQueuedConnection: returns once the dialog is closed
        return self.answer

    def run(self) -> None:
        try:
            r = play(self.game, self.settings, self.library, self.engine, on_conflict=self._ask,
                     status=self.status.emit, on_phase=self.phase.emit, without=self.without, watch=self.watch)
        except Exception as e:  # noqa: BLE001 - never let a surprise kill the thread silently
            r = PlayResult(False, "play.couldnt_start")
            r.window_title = str(e)
        self.result.emit(r)


# =========================================================================== grid painting
ZOOM_MAX = 4.0


def crop_rect(iw: int, ih: int, zoom: float, cx: float, cy: float,
              aspect: float = COVER_W / COVER_H) -> tuple[QRectF, float, float]:
    """The part of an iw x ih image shown in a cover frame. zoom 1 = the largest frame-shaped area that
    fits (fills the frame, like Steam); (cx, cy) = centre of that area as 0..1 of the image, moved back
    inside the image if needed. Returns (source rect, clamped cx, clamped cy)."""
    if iw / ih > aspect:
        bw, bh = ih * aspect, ih
    else:
        bw, bh = iw, iw / aspect
    zoom = max(1.0, min(ZOOM_MAX, zoom))
    w, h = bw / zoom, bh / zoom
    x = min(max(cx * iw - w / 2, 0), iw - w)
    y = min(max(cy * ih - h / 2, 0), ih - h)
    return QRectF(x, y, w, h), (x + w / 2) / iw, (y + h / 2) / ih


class CoverCache:
    """Covers ready to draw. Only the most recently shown ones stay in memory (the grid only draws
    what's on screen), so a library of hundreds of games doesn't eat hundreds of MB."""
    MAX_COVERS = 120          # ~120 x 0.85 MB at most
    MAX_ORIGINALS = 4         # full-size images kept while cropping/zooming the same cover

    def __init__(self):
        from collections import OrderedDict
        self._images: "OrderedDict[tuple[str, float], QImage]" = OrderedDict()
        self._cache: "OrderedDict[tuple, QPixmap]" = OrderedDict()

    def get(self, path: Optional[Path], crop: tuple[float, float, float] = (1.0, 0.5, 0.5)) -> Optional[QPixmap]:
        if not path:
            return None
        try:
            src_key = (str(path), path.stat().st_mtime)
        except OSError:
            return None
        key = src_key + tuple(round(v, 4) for v in crop)
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            img = self._load(path, crop[0])
            if img is None:
                return None
            rect, _, _ = crop_rect(img.width(), img.height(), *crop)
            # extra resolution so it stays sharp when the cover grows on hover
            w, h = int(COVER_W * 2.2), int(COVER_H * 2.2)
            part = img.copy(rect.toAlignedRect()).scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self._cache[key] = QPixmap.fromImage(part)
            while len(self._cache) > self.MAX_COVERS:
                self._cache.popitem(last=False)
        return self._cache[key]

    def _load(self, path: Path, zoom: float) -> Optional[QImage]:
        """Decode the image only as large as needed for this zoom (big JPEGs decode much faster
        scaled down). Recently used decodes are kept for re-cropping."""
        from PySide6.QtGui import QImageReader
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        full = reader.size()
        if not full.isValid() or full.isEmpty():
            img = QImage(str(path))
            return None if img.isNull() else img
        base_h = min(full.height(), full.width() * COVER_H / COVER_W)    # height of the zoom-1 frame
        scale = min(1.0, COVER_H * 2.2 * max(1.0, zoom) / base_h)
        want = QSize(max(1, round(full.width() * scale)), max(1, round(full.height() * scale)))
        k = (str(path), want.width(), want.height())
        img = self._images.get(k)
        if img is None:
            if scale < 1.0:
                reader.setScaledSize(want)
            img = reader.read()
            if img.isNull():
                return None
            self._images[k] = img
            while len(self._images) > self.MAX_ORIGINALS:
                self._images.popitem(last=False)
        else:
            self._images.move_to_end(k)
        return img


def make_shadow(w: int, h: int, blur: int = 14) -> QPixmap:
    """A soft drop shadow, made by drawing a dark rounded rect small and scaling it up (cheap blur)."""
    pad = blur * 2
    small = QImage((w + pad * 2) // 4, (h + pad * 2) // 4, QImage.Format_ARGB32_Premultiplied)
    small.fill(Qt.transparent)
    p = QPainter(small)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(0, 0, 0, 200))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(pad / 4, pad / 4, w / 4, h / 4), 2, 2)
    p.end()
    big = small.scaled(w + pad * 2, h + pad * 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    big = big.scaled(big.width() // 2, big.height() // 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation) \
             .scaled(w + pad * 2, h + pad * 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return QPixmap.fromImage(big)


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)          # smoothstep: gentle start and finish


class CardDelegate(QStyledItemDelegate):
    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.w = window
        self.shadow = make_shadow(COVER_W, COVER_H)

    def sizeHint(self, option, index) -> QSize:
        return QSize(CARD_W, CARD_H)

    @staticmethod
    def cover_rect(rect: QRect, hover: float) -> QRectF:
        e = ease(hover)
        scale = 1 + HOVER_GROW * e
        w, h = COVER_W * scale, COVER_H * scale
        cx = rect.x() + CARD_W / 2
        top = rect.y() + CARD_PAD_TOP + COVER_H / 2 - h / 2 - HOVER_LIFT * e
        # whole pixels only: an edge that falls between pixels gets a faint light seam where the cover
        # and its hover fade are each softened separately
        left = round(cx - w / 2)
        return QRectF(left, round(top), round(cx + w / 2) - left, round(h))

    @staticmethod
    def light_center(cover: QRectF) -> QPointF:
        return QPointF(cover.right() - 14, cover.bottom() - 14)

    def _shadow(self, p: QPainter, cover: QRectF, e: float) -> None:
        if e > 0.01:     # grows and softens as the tile lifts
            p.setOpacity(0.55 * e)
            pad = 28
            sh = QRectF(cover.x() - pad, cover.y() - pad + 4 + 6 * e, cover.width() + pad * 2, cover.height() + pad * 2)
            p.drawPixmap(sh, self.shadow, QRectF(self.shadow.rect()))
            p.setOpacity(1.0)

    def _overlay(self, p: QPainter, option, cover: QRectF, e: float, title: str, line: str) -> None:
        """Title + a small line fading in at the bottom on hover (always light: it sits on a dark fade)."""
        # a pixel past the edges on purpose: when the cover grows on hover its edges fall between pixels,
        # and an exact-size fade would leave a thin line of the cover showing (the rounded clip trims it)
        band = QRectF(cover.x() - 1, cover.bottom() - cover.height() * 0.42, cover.width() + 2, cover.height() * 0.42 + 1)
        g = QLinearGradient(band.topLeft(), band.bottomLeft())
        g.setColorAt(0, QColor(0, 0, 0, 0))
        g.setColorAt(1, QColor(0, 0, 0, int(215 * e)))
        p.fillRect(band, g)
        p.setOpacity(e)
        text_w = cover.width() - 24 - 22         # leave room for the light
        f = QFont(option.font)
        fm_small = QFontMetrics(f)
        y = cover.bottom() - 12
        if line:
            p.setPen(QColor(DARK["muted"]))
            p.setFont(f)
            p.drawText(QRectF(cover.x() + 12, y - fm_small.height(), text_w, fm_small.height()),
                       Qt.AlignLeft | Qt.AlignVCenter, line)
        else:
            y += fm_small.height() + 4          # title sits at the bottom on its own
        if title:
            ft = QFont(option.font)
            ft.setPointSize(11)
            fm = QFontMetrics(ft)
            p.setFont(ft)
            p.setPen(QColor(DARK["text"]))
            lines = self._wrap(title, fm, text_w, 2)
            top = y - fm_small.height() - 4 - fm.height() * len(lines)
            for i, l in enumerate(lines):
                p.drawText(QRectF(cover.x() + 12, top + i * fm.height(), text_w, fm.height()),
                           Qt.AlignLeft | Qt.AlignVCenter, l)
        p.setOpacity(1.0)

    def _ring(self, p: QPainter, cover: QRectF) -> None:
        # the ring overlaps the cover's edge by half a pixel (same corner centre as the cover's
        # rounding), so no sliver of background or soft dark edge shows between them
        p.setPen(QPen(QColor(COLORS["select"]), 3))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(cover.adjusted(-1, -1, 1, 1), 9, 9)

    _placeholders: dict = {}

    def _placeholder(self, title: str, font: QFont) -> QPixmap:
        """A cover without art (grey gradient + title), drawn once and reused: laying out the title text
        every frame is the most expensive part of drawing the grid."""
        key = (title, COLORS["placeholder_top"], COLORS["placeholder_bottom"], COLORS["text"], font.family())
        pm = self._placeholders.get(key)
        if pm is None:
            scale = 2                                   # extra detail for when it grows on hover
            pm = QPixmap(COVER_W * scale, COVER_H * scale)
            q = QPainter(pm)
            q.setRenderHint(QPainter.Antialiasing)
            q.setRenderHint(QPainter.TextAntialiasing)
            q.scale(scale, scale)
            rect = QRectF(0, 0, COVER_W, COVER_H)
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0, QColor(COLORS["placeholder_top"]))
            grad.setColorAt(1, QColor(COLORS["placeholder_bottom"]))
            q.fillRect(rect, grad)
            f = QFont(font)
            f.setPointSize(13)
            q.setFont(f)
            q.setPen(QColor(COLORS["text"]))
            q.drawText(rect.adjusted(16, 16, -16, -16), Qt.AlignCenter | Qt.TextWordWrap, title)
            q.end()
            if len(self._placeholders) > 400:
                self._placeholders.clear()
            self._placeholders[key] = pm
        return pm

    def _clipped(self, p: QPainter, cover: QRectF, e: float, draw) -> None:
        """Draw a tile's contents (draw(painter, rect)) inside its rounded outline. While it's hovered,
        the contents (cover + dark fade + text) are first painted together on a separate layer and then
        cut out once: cutting out each piece separately leaves faint light pixels along the rounded
        corners, where every piece is softened on its own."""
        clip = QPainterPath()
        clip.addRoundedRect(cover, 8, 8)
        p.save()
        if e <= 0.01:
            p.setClipPath(clip)
            draw(p, cover)
        else:
            dev = p.device()
            dpr = dev.devicePixelRatioF() if dev is not None else 1.0
            layer = QPixmap(max(1, math.ceil(cover.width() * dpr)), max(1, math.ceil(cover.height() * dpr)))
            layer.setDevicePixelRatio(dpr)
            layer.fill(Qt.transparent)
            lp = QPainter(layer)
            lp.setRenderHint(QPainter.Antialiasing)
            lp.setRenderHint(QPainter.SmoothPixmapTransform)
            lp.setRenderHint(QPainter.TextAntialiasing)
            draw(lp, QRectF(0, 0, cover.width(), cover.height()))
            lp.end()
            p.setClipPath(clip)
            p.drawPixmap(cover, layer, QRectF(layer.rect()))
        p.restore()

    def _paint_folder(self, p: QPainter, option, iid: str) -> None:
        fid = iid[len(FOLDER_PREFIX):]
        games = self.w.folder_games.get(fid, [])
        e = ease(self.w.hover.get(iid, 0.0))
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cover = self.cover_rect(option.rect, self.w.hover.get(iid, 0.0))
        self._shadow(p, cover, e)

        def draw(q: QPainter, rect: QRectF) -> None:
            q.fillRect(rect, QColor(COLORS["button"]))
            # 2 x 2 mosaic of the first covers inside
            inner = rect.adjusted(FOLDER_PAD, FOLDER_PAD, -FOLDER_PAD, -FOLDER_PAD)
            mw, mh = (inner.width() - FOLDER_GAP) / 2, (inner.height() - FOLDER_GAP) / 2
            for i, g in enumerate(games[:4]):
                r = QRectF(inner.x() + (i % 2) * (mw + FOLDER_GAP), inner.y() + (i // 2) * (mh + FOLDER_GAP), mw, mh)
                mclip = QPainterPath()
                mclip.addRoundedRect(r, 5, 5)
                q.save()
                q.setClipPath(mclip, Qt.IntersectClip)
                pm = self.w.covers.get(self.w.library.artwork_path(g), (g.crop_zoom, g.crop_x, g.crop_y))
                if pm:
                    q.drawPixmap(r, pm, QRectF(pm.rect()))
                else:
                    grad = QLinearGradient(r.topLeft(), r.bottomLeft())
                    grad.setColorAt(0, QColor(COLORS["placeholder_top"]))
                    grad.setColorAt(1, QColor(COLORS["placeholder_bottom"]))
                    q.fillRect(r, grad)
                q.restore()
            if e > 0.01:
                n = len(games)
                count = tr("folder.count_one") if n == 1 else tr("folder.count_many", n=n)
                self._overlay(q, option, rect, e, self.w.folder_name(fid), count)

        self._clipped(p, cover, e, draw)
        if option.state & QStyle.State_Selected:
            self._ring(p, cover)
        self._paint_light(p, self.light_center(cover), self.w.light_state(iid))
        p.restore()

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        gid = index.data(GAME_ID_ROLE)
        if is_folder(gid):
            self._paint_folder(p, option, gid)
            return
        game: Optional[Game] = self.w.games.get(gid)
        if game is None:
            return
        e = ease(self.w.hover.get(gid, 0.0))
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        selected = bool(option.state & QStyle.State_Selected)
        cover = self.cover_rect(option.rect, self.w.hover.get(gid, 0.0))
        self._shadow(p, cover, e)
        pm = self.w.covers.get(self.w.library.artwork_path(game), (game.crop_zoom, game.crop_x, game.crop_y))

        def draw(q: QPainter, rect: QRectF) -> None:
            if pm:
                q.drawPixmap(rect, pm, QRectF(pm.rect()))
            else:
                ph = self._placeholder(game.title, option.font)
                q.drawPixmap(rect, ph, QRectF(ph.rect()))
            # hover overlay: title + playtime fade in at the bottom (Steam style)
            if e > 0.01:
                play_line = i18n.playtime(game.total_playtime) if game.total_playtime else tr("card.never")
                if not self.w.settings.show_stats:
                    play_line = ""                      # statistics hidden: the reading status only
                if game.status:
                    play_line += ("  ·  " if play_line else "") + tr(f"status.{game.status}")
                if self.w.quiet(gid):
                    play_line = ""
                # placeholders already show the title in the middle
                self._overlay(q, option, rect, e, game.title if pm else "", play_line)

        self._clipped(p, cover, e, draw)
        if selected:
            self._ring(p, cover)
        self._paint_light(p, self.light_center(cover), self.w.light_state(gid))
        if gid == self.w._dim_gid and self.w.drag_dim > 0:
            self._fade_into_background(p, option.rect, 0.75 * self.w.drag_dim)
        p.restore()

    def _fade_into_background(self, p: QPainter, rect: QRect, amount: float) -> None:
        """The picked-up game, faded in place: the background (picture and all) drawn back over it."""
        backdrop = getattr(self.w, "backdrop", None)
        if backdrop is None:
            return
        vp = self.w.grid.viewport()
        origin = vp.mapTo(backdrop, QPoint(0, 0)) - backdrop.library_area().topLeft()
        p.save()
        p.setOpacity(amount)
        backdrop.paint_into(p, rect.intersected(vp.rect()), origin)
        p.restore()

    @staticmethod
    def _wrap(text: str, fm: QFontMetrics, width: float, max_lines: int) -> list[str]:
        """Wrap to max_lines, ellipsis on overflow. English breaks between words; Japanese (no spaces)
        can break between any two characters."""
        import re as _re
        # pieces: a run of non-space, non-Japanese characters is one word; Japanese characters stand alone
        cjk = "\u3000-\u303f\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uff00-\uffef"
        pieces = _re.findall(rf"[^\s{cjk}]+|\s+|.", text)
        lines, cur, i = [], "", 0
        while i < len(pieces):
            piece = pieces[i]
            if cur and fm.horizontalAdvance(cur + piece) > width:
                lines.append(cur.rstrip())
                cur = ""
                if len(lines) == max_lines:
                    rest = "".join(pieces[i:])
                    lines[-1] = fm.elidedText(lines[-1] + " " + rest.lstrip(), Qt.ElideRight, int(width))
                    return lines
                if piece.isspace():
                    i += 1
                continue
            if not cur and fm.horizontalAdvance(piece) > width:   # one very long word: split it
                cut = 1
                while cut < len(piece) and fm.horizontalAdvance(piece[:cut + 1]) <= width:
                    cut += 1
                pieces[i:i + 1] = [piece[:cut], piece[cut:]]
                piece = pieces[i]
            cur += piece
            i += 1
        lines.append(cur.rstrip())
        return lines

    def _paint_light(self, p: QPainter, c: QPointF, state: str) -> None:
        colour_key, pulse, _ = STATE_LOOK.get(state, STATE_LOOK["checking"])
        paint_light(p, c, colour_key, pulse, self.w.settings.pulse)


def paint_light(p: QPainter, c: QPointF, colour_key: str, pulse: str, pulsing_on: bool, r: float = LIGHT_R) -> None:
    """A sync light: a dot with a soft halo that breathes. All lights share one clock, so they pulse
    in step. pulse: "calm" / "busy" (quicker) / "steady" (no pulse)."""
    col = QColor(LIGHTS[colour_key])
    s = 0.0
    if pulsing_on and pulse != "steady":
        period = PULSE_BUSY_S if pulse == "busy" else PULSE_CALM_S
        s = (1 - math.cos(2 * math.pi * (time.monotonic() % period) / period)) / 2   # 0..1, all in step
    halo_r = r * (1.6 + 1.2 * s)
    g = QRadialGradient(c, halo_r)
    hc = QColor(col)
    hc.setAlpha(int(40 + 90 * s))
    g.setColorAt(0, hc)
    hc.setAlpha(0)
    g.setColorAt(1, hc)
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawEllipse(c, halo_r, halo_r)
    core = QColor(col).lighter(100 + int(18 * s))
    p.setPen(QPen(QColor(0, 0, 0, 150), 1.5))   # dark ring so it reads on light covers too
    p.setBrush(core)
    p.drawEllipse(c, r, r)


class SyncLight(QWidget):
    """The pulsing light next to the top bar's Synced / Syncing... text."""

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.w = window
        self.colour, self.pulse = "green", "calm"
        self.setFixedSize(26, 26)          # room for the halo at its widest (2.8 x the dot)

    def set_state(self, colour: str, pulse: str) -> None:
        self.colour, self.pulse = colour, pulse
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_light(p, QPointF(self.width() / 2, self.height() / 2), self.colour, self.pulse,
                    self.w.settings.pulse, r=4.5)
        p.end()


# =========================================================================== cover crop editor
class CropView(QWidget):
    """The cover preview in the game settings page: drag to move, wheel to zoom, double-click to reset."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(COVER_W, COVER_H)
        self.img: Optional[QImage] = None
        self.zoom, self.cx, self.cy = 1.0, 0.5, 0.5
        self._drag: Optional[QPointF] = None
        self.setToolTip(tr("game.crop_tip"))

    def set_image(self, path, crop: tuple[float, float, float] = (1.0, 0.5, 0.5)) -> None:
        img = QImage(str(path)) if path else QImage()
        self.img = None if img.isNull() else img
        self.zoom, self.cx, self.cy = crop
        self._clamp()
        self.setCursor(Qt.OpenHandCursor if self.img else Qt.ArrowCursor)
        self.update()

    def crop(self) -> tuple[float, float, float]:
        return self.zoom, self.cx, self.cy

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(1.0, min(ZOOM_MAX, zoom))
        self._clamp()
        self.update()
        self.changed.emit()

    def _clamp(self) -> None:
        if self.img:
            _, self.cx, self.cy = crop_rect(self.img.width(), self.img.height(), self.zoom, self.cx, self.cy)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 8, 8)
        p.setClipPath(clip)
        if self.img:
            src, _, _ = crop_rect(self.img.width(), self.img.height(), self.zoom, self.cx, self.cy)
            p.drawImage(QRectF(self.rect()), self.img, src)
        else:
            g = QLinearGradient(0, 0, 0, self.height())
            g.setColorAt(0, QColor(COLORS["placeholder_top"]))
            g.setColorAt(1, QColor(COLORS["placeholder_bottom"]))
            p.fillRect(self.rect(), g)
            p.setPen(QColor(COLORS["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, tr("game.no_cover"))
        p.end()

    def mousePressEvent(self, ev) -> None:
        if self.img and ev.button() == Qt.LeftButton:
            self._drag = ev.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev) -> None:
        if self._drag is None or not self.img:
            return
        d = ev.position() - self._drag
        self._drag = ev.position()
        src, _, _ = crop_rect(self.img.width(), self.img.height(), self.zoom, self.cx, self.cy)
        # dragging right shows more of the left side, like moving a photo under a frame
        self.cx -= d.x() * (src.width() / self.width()) / self.img.width()
        self.cy -= d.y() * (src.height() / self.height()) / self.img.height()
        self._clamp()
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, _ev) -> None:
        self._drag = None
        if self.img:
            self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, _ev) -> None:
        if self.img:
            self.zoom, self.cx, self.cy = 1.0, 0.5, 0.5
            self.update()
            self.changed.emit()

    def wheelEvent(self, ev) -> None:
        if self.img:
            self.set_zoom(self.zoom * (1.1 if ev.angleDelta().y() > 0 else 1 / 1.1))


# =========================================================================== small widgets
def browse_row(edit: QLineEdit, on_browse, label: str) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    b = QPushButton(label)
    b.clicked.connect(on_browse)
    row.addWidget(b)
    return row


def section(text: str) -> QLabel:
    lab = QLabel(text, objectName="section")
    lab.setIndent(0)        # Qt otherwise indents styled labels by half an "x", out of line with the text below
    return lab


def hint(text: str) -> QLabel:
    lab = QLabel(text, objectName="hint")
    lab.setWordWrap(True)
    return lab


class SearchBox(QLineEdit):
    """220 px wide when there's room; gives way (down to 120 px) when the top bar gets crowded."""

    def sizeHint(self) -> QSize:
        return QSize(220, super().sizeHint().height())


class StarButton(QPushButton):
    """A button with the logo's star in front of the text, centred between the button's left edge
    and the first letter (the gap on both sides of the star is the same, whatever the language)."""
    STAR = 19          # icon size
    GAP = 11           # space on each side of the star
    NUDGE = 1          # Icon.png has a transparent pixel round the star; measured so both gaps match

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.refresh_icon()
        # room for gap + star + gap before the text; the text starts right after it
        self.setStyleSheet(f"padding-left: {2 * self.GAP + self.STAR - self.NUDGE}px;")

    def refresh_icon(self) -> None:
        self._star = QPixmap.fromImage(themed_image("Icon.png", self.STAR * 4))   # sharp on high-DPI screens
        self.update()

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        y = (self.height() - self.STAR) // 2
        p.drawPixmap(QRect(self.GAP, y, self.STAR, self.STAR), self._star)
        p.end()


class DragGhost(QWidget):
    """The cover that follows the mouse while a game is dragged. Our own see-through window (not Windows'
    drag picture, which can't change mid-drag), so it can fade almost away over the trash can."""
    NORMAL, OVER_TRASH = 0.85, 0.1

    def __init__(self, pm: QPixmap):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pm = pm
        self.setFixedSize(pm.size() / pm.devicePixelRatio())
        self.opacity = self.target = self.NORMAL
        self._t = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)

    def follow(self, global_pos: QPoint) -> None:
        self.move(global_pos - QPoint(self.width() // 2, self.height() // 2))

    def set_faded(self, on: bool) -> None:
        target = self.OVER_TRASH if on else self.NORMAL
        if target != self.target:
            self.target = target
            self._t = time.monotonic()
            self._timer.start()

    def _step(self) -> None:
        now = time.monotonic()
        dt, self._t = now - self._t, now
        self.opacity += (self.target - self.opacity) * min(1.0, dt * 16)     # a quick, smooth fade
        if abs(self.target - self.opacity) < 0.01:
            self.opacity = self.target
            self._timer.stop()
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(self.opacity)
        p.drawPixmap(self.rect(), self.pm)
        p.end()


class EscWatcher(QObject):
    """While a game is being dragged: Esc (pressed anywhere in the app) calls `on_esc`."""

    def __init__(self, on_esc: Callable):
        super().__init__()
        self.on_esc = on_esc

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() in (QEvent.KeyPress, QEvent.ShortcutOverride) and ev.key() == Qt.Key_Escape:
            if ev.type() == QEvent.KeyPress:
                self.on_esc()
            ev.accept()
            return True
        return False


class TrashButton(QPushButton):
    """The trash can icon itself (Trash.png, recoloured with the theme like the star), no button box.
    Drop a game on it (or click it) to remove the selected game.
    Mouse over: rises, grows a little and gives one short wiggle. A game held over it: grows more and
    keeps rocking gently. (Rocks around its base, like a real bin.)"""
    ICON = 34
    HOVER_SCALE, HOT_SCALE = 1.08, 1.2
    WIGGLE_DEG, WIGGLE_HZ, WIGGLE_S = 9.0, 7.0, 0.5       # the one-off wiggle on mouse over
    ROCK_DEG, ROCK_HZ = 6.0, 4.5                           # the steady rocking while a game is held over it

    def __init__(self):
        super().__init__(objectName="trash")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(self.ICON + 18, self.ICON + 18)       # room to grow and rock without being cut off
        self._hot = False
        self._hover = False
        self._scale = 1.0
        self._wiggle_from = -1.0                                # when the hover wiggle started (-1 = none)
        self._t = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self.refresh_icon()

    def refresh_icon(self) -> None:
        self._icon = QPixmap.fromImage(themed_image("Trash.png", self.ICON * 3))   # sharp on high-DPI screens
        self.update()

    def _live(self) -> bool:
        return self.isEnabled() or self._hot

    def _animate(self) -> None:
        if not self._timer.isActive():
            self._t = time.monotonic()
            self._timer.start()

    def set_hot(self, on: bool) -> None:
        if self._hot != on:
            self._hot = on
            self._animate()

    def enterEvent(self, ev) -> None:
        super().enterEvent(ev)
        self._hover = True
        if self.isEnabled():
            self._wiggle_from = time.monotonic()
        self._animate()

    def leaveEvent(self, ev) -> None:
        super().leaveEvent(ev)
        self._hover = False
        self._animate()

    def _target_scale(self) -> float:
        if self._hot:
            return self.HOT_SCALE
        return self.HOVER_SCALE if (self._hover and self.isEnabled()) else 1.0

    def _angle(self, now: float) -> float:
        if self._hot:
            return self.ROCK_DEG * math.sin(2 * math.pi * self.ROCK_HZ * now)
        if self._wiggle_from >= 0:
            age = now - self._wiggle_from
            if age < self.WIGGLE_S:
                fade = 1 - age / self.WIGGLE_S
                return self.WIGGLE_DEG * fade * fade * math.sin(2 * math.pi * self.WIGGLE_HZ * age)
            self._wiggle_from = -1.0
        return 0.0

    def _step(self) -> None:
        now = time.monotonic()
        dt, self._t = now - self._t, now
        target = self._target_scale()
        self._scale += (target - self._scale) * min(1.0, dt * 14)          # ease towards the target size
        if abs(target - self._scale) < 0.002:
            self._scale = target
        if self._scale == target and not self._hot and self._wiggle_from < 0:
            self._timer.stop()                                              # still: stop redrawing
        self.update()

    def paintEvent(self, _ev) -> None:                          # only the icon: no button background
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        if not self._live():
            p.setOpacity(0.3)
        elif not (self._hot or self._hover):
            p.setOpacity(0.85)
        s = self.ICON * self._scale
        lift = (self._scale - 1.0) * 10                         # rises a little as it grows
        base = QPointF(self.width() / 2, (self.height() + self.ICON) / 2 - lift)
        p.translate(base)
        p.rotate(self._angle(time.monotonic()) if self._live() else 0.0)
        p.drawPixmap(QRectF(-s / 2, -s, s, s), self._icon, QRectF(self._icon.rect()))
        p.end()

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == QEvent.EnabledChange:
            self._animate()


class WobbleBox(TrashButton):
    """A button-like box that moves exactly like the trash can, but does nothing when clicked.
    rock=True: grows and keeps rocking while the mouse is over it (the trash can with a game held over it).
    rock=False: rises and gives one wiggle when the mouse arrives (the trash can's normal hover).
    Pivots around its centre (a wide box swinging round its base would sweep too far)."""

    def __init__(self, text: str, rock: bool):
        super().__init__()
        self.setAcceptDrops(False)
        self.setCursor(Qt.ArrowCursor)
        self.text_, self.rock = text, rock
        self.box_font = ui_font(13)
        fm = QFontMetrics(self.box_font)
        self.box = QSizeF(fm.horizontalAdvance(text) + 56, fm.height() + 26)
        # room to grow (x1.2) and tilt without being cut off
        self.setFixedSize(int(self.box.width() * 1.2) + 24, int(self.box.height() * 1.2) + 44)

    def refresh_icon(self) -> None:          # no icon; just repaint (theme changes)
        self.update()

    def enterEvent(self, ev) -> None:
        if self.rock:
            QWidget.enterEvent(self, ev)
            self.set_hot(True)
        else:
            super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        if self.rock:
            QWidget.leaveEvent(self, ev)
            self.set_hot(False)
        else:
            super().leaveEvent(ev)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        lift = (self._scale - 1.0) * 10
        p.translate(self.width() / 2, self.height() / 2 - lift)
        p.rotate(self._angle(time.monotonic()))
        p.scale(self._scale, self._scale)
        r = QRectF(-self.box.width() / 2, -self.box.height() / 2, self.box.width(), self.box.height())
        p.setPen(QPen(QColor(COLORS["border"]), 1))
        p.setBrush(QColor(COLORS["button_hover"] if (self._hot or self._hover) else COLORS["button"]))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QColor(COLORS["text"]))
        p.setFont(self.box_font)
        p.drawText(r, Qt.AlignCenter, self.text_)
        p.end()


class EasterEgg(QWidget):
    """Click the logo: the window goes dark with two boxes in the middle. Clicking anywhere else
    (or Esc) brings the library back."""
    DARK = 175            # how dark the cover gets (0-255)

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.setGeometry(window.rect())
        self.setFocusPolicy(Qt.StrongFocus)
        self._alpha, self._target = 0.0, float(self.DARK)
        self.boxes = [WobbleBox(f"Ver. {APP_VERSION}", rock=False), WobbleBox(tr("egg.title"), rock=True)]   # top, bottom
        for b in self.boxes:
            b.setParent(self)
        self._place()
        window.installEventFilter(self)          # follow the window's size
        self._t = time.monotonic()
        self._fade = QTimer(self)
        self._fade.setInterval(16)
        self._fade.timeout.connect(self._step)
        self.show()
        self.raise_()
        self.setFocus()
        self._fade.start()

    def _place(self) -> None:
        a, b = self.boxes
        gap = -18                                # the boxes' own margins already leave space between them
        total = a.height() + gap + b.height()
        y = (self.height() - total) // 2
        a.move((self.width() - a.width()) // 2, y)
        b.move((self.width() - b.width()) // 2, y + a.height() + gap)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.parent() and ev.type() == QEvent.Resize:
            self.setGeometry(self.parent().rect())
            self._place()
        return False

    def _step(self) -> None:
        now = time.monotonic()
        dt, self._t = now - self._t, now
        self._alpha += (self._target - self._alpha) * min(1.0, dt * 14)
        if abs(self._target - self._alpha) < 1:
            self._alpha = self._target
            self._fade.stop()
            if self._target == 0:                # faded away: gone
                self.parent().removeEventFilter(self)
                self.deleteLater()
                return
        self.update()

    def dismiss(self) -> None:
        if self._target != 0:
            self._target = 0.0
            for b in self.boxes:
                b.hide()
            self._t = time.monotonic()
            self._fade.start()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, int(self._alpha)))
        p.end()

    def mousePressEvent(self, ev) -> None:      # (the boxes take their own clicks, so this is "elsewhere")
        self.dismiss()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(ev)


class Logo(QLabel):
    clicked = Signal()

    def __init__(self, height: int = LOGO_H):
        super().__init__()
        self._h = height
        self.refresh()

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(ev)

    def refresh(self) -> None:
        dpr = 2.0
        img = themed_image("Logo.png", int(self._h * dpr))
        if img.isNull():
            self.setText(tr("app.name"))
            return
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self.setPixmap(pm)


# =========================================================================== dialogs
class ThemeDialog(QDialog):
    """Custom theme: every colour role with a swatch and a hex box; changes preview live."""

    def __init__(self, colors: dict, preview: Callable[[dict], None], parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QScrollArea
        self.setWindowTitle(tr("theme.title"))
        self.setMinimumSize(430, 560)
        self.resize(450, 620)
        self.colors = dict(colors)
        self.preview = preview
        self.rows: dict[str, tuple] = {}
        # redraw the whole window only once changes pause (dragging in the colour picker stays smooth)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(lambda: self.preview(self.colors))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel(tr("theme.start_from"), objectName="muted"))
        top.addStretch()
        for name in ("dark", "light"):
            b = QPushButton(tr(f"theme.{name}"), objectName="seg")
            b.setToolTip(tr("theme.start_tip"))
            b.clicked.connect(lambda _=False, n=name: self._load(theme_colors(n)))
            top.addWidget(b)
        lay.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget(objectName="scrollbody")
        col = QVBoxLayout(body)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(0)
        col.addWidget(self._group_block(THEME_MAIN))
        self.more = self._group_block(THEME_MORE)
        self.more.hide()
        col.addWidget(self.more)
        # the toggle always sits last: under the main colours, or under everything once expanded
        self.more_btn = QPushButton(tr("theme.more"), objectName="toggle")
        self.more_btn.clicked.connect(self._toggle_more)
        col.addSpacing(6)
        col.addWidget(self.more_btn)
        col.addStretch()
        self.body = body
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)
        self.scroll = scroll

        self.warning = QLabel(objectName="hint")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color: {COLORS['danger']};")
        lay.addWidget(self.warning)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton(tr("game.cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("game.save"))
        save.setDefault(True)
        save.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addLayout(btns)
        self._load(self.colors)
        QTimer.singleShot(0, self._fit)

    def _fit(self) -> None:
        """Make the window exactly tall enough for the main colours (no scrolling slack); the extra
        colours scroll."""
        spare = self.body.sizeHint().height() - self.scroll.viewport().height()
        screen = self.screen().availableGeometry().height() if self.screen() else 900
        self.resize(self.width(), min(self.height() + spare, int(screen * 0.9)))

    def _group_block(self, groups) -> QWidget:
        box = QWidget()
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(0, 1)
        row = 0
        for group, keys in groups:
            head = section(tr(f"theme.g_{group}"))
            head.setContentsMargins(0, 10 if row else 2, 0, 2)
            grid.addWidget(head, row, 0, 1, 3)
            row += 1
            for k in keys:
                grid.addWidget(QLabel(tr(f"theme.c_{k}")), row, 0)
                sw = QPushButton(objectName="swatch")
                sw.setFixedSize(22, 22)
                sw.setCursor(Qt.PointingHandCursor)
                sw.setToolTip(tr("theme.swatch_tip"))
                sw.clicked.connect(lambda _=False, k=k: self._pick(k))
                hx = QLineEdit(objectName="hex")
                hx.setFixedWidth(84)
                hx.setMaxLength(7)
                hx.setToolTip(tr("theme.hex_tip"))
                hx.editingFinished.connect(lambda k=k: self._typed(k))
                grid.addWidget(sw, row, 1)
                grid.addWidget(hx, row, 2)
                self.rows[k] = (sw, hx)
                row += 1
        return box

    def done(self, r) -> None:
        self._preview_timer.stop()
        if r:
            self.preview(self.colors)      # make sure the last change is applied
        super().done(r)

    def _toggle_more(self) -> None:
        show = not self.more.isVisible()
        self.more.setVisible(show)
        self.more_btn.setText(tr("theme.less" if show else "theme.more"))
        if show:      # bring the newly shown colours into view
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(
                self.more.mapTo(self.scroll.widget(), self.more.rect().topLeft()).y() - 8))

    def _load(self, colors: dict) -> None:
        self.colors = dict(colors)
        for k in self.rows:
            self._show(k)
        self._changed()

    def _show(self, k: str) -> None:
        sw, hx = self.rows[k]
        sw.setStyleSheet(f"background: {self.colors[k]}; border-radius: 11px;")
        hx.setText(self.colors[k])

    def _changed(self) -> None:
        # update continuously while dragging (at most every 80 ms), not only once it stops
        if not self._preview_timer.isActive():
            self._preview_timer.start()
        c = self.colors
        weak = [tr(f"theme.where_{w}") for w, (fg, bg) in {
            "main": (c["text"], c["bg"]), "bars": (c["text"], c["band"]),
            "buttons": (c["text"], c["button"]), "play": (c["play_text"], c["play"])}.items()
            if contrast(fg, bg) < 3.0]
        sep = "、" if i18n.language() == "ja" else ", "
        self.warning.setText(tr("theme.low_contrast", where=sep.join(weak)) if weak else "")

    def _set(self, k: str, hex_colour: str) -> None:
        self.colors[k] = QColor(hex_colour).name().upper()
        self._show(k)
        self._changed()

    def _typed(self, k: str) -> None:
        text = self.rows[k][1].text().strip()
        if not text.startswith("#"):
            text = "#" + text
        if QColor.isValidColorName(text) and len(text) == 7:
            self._set(k, text)
        else:
            self._show(k)                     # not a colour: put the old value back

    def _pick(self, k: str) -> None:
        from PySide6.QtWidgets import QColorDialog
        before = self.colors[k]
        d = QColorDialog(QColor(before), self)
        d.setWindowTitle(tr("theme.pick"))
        d.currentColorChanged.connect(lambda c: self._set(k, c.name()))   # live while choosing
        if d.exec():
            self._set(k, d.currentColor().name())
        else:
            self._set(k, before)


class FontSample(QWidget):
    """The font preview: lays the sample out itself and measures where the letters really land (some fonts
    draw swashes and accents far outside their line), then grows to fit, so nothing is ever cut off."""
    PAD = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.text, self.font_ = "", QFont()
        self._lines: list = []
        self._shift = QPointF()

    def set_sample(self, family: str, text: str) -> None:
        self.font_ = QFont()
        self.font_.setFamilies([family, *FONT_FALLBACKS])
        self.font_.setPointSize(13)
        self.text = text
        self._relayout()

    def _build(self, wrap: float) -> tuple:
        layouts, y = [], 0.0
        ink = QRectF()
        for para in self.text.split("\n"):
            tl = QTextLayout(para, self.font_)
            opt = QTextOption()
            opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
            tl.setTextOption(opt)
            tl.beginLayout()
            while True:
                line = tl.createLine()
                if not line.isValid():
                    break
                line.setLineWidth(wrap)
                line.setPosition(QPointF(0, y))
                y += line.height()
            tl.endLayout()
            for run in tl.glyphRuns():
                ink = ink.united(run.boundingRect())                    # the letters' real outline
            layouts.append(tl)
            y += 4                                                      # a little air between the lines
        box = QRectF(0, 0, wrap, y - 4).united(ink)
        return layouts, box

    def _relayout(self) -> None:
        avail = max(50.0, self.width() - 2 * self.PAD)
        layouts, box = self._build(avail)
        over = box.width() - avail
        if over > 0.5:                                                  # letters lean past the edge: wrap narrower
            layouts, box = self._build(max(50.0, avail - over))
        self._lines = layouts
        self._shift = QPointF(self.PAD - box.left(), self.PAD - box.top())
        self.setFixedHeight(int(box.height() + 2 * self.PAD + 1))
        self.update()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._relayout()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(COLORS["input"]))
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        p.setPen(QColor(COLORS["text"]))
        for tl in self._lines:
            tl.draw(p, self._shift)
        p.end()


class FontDialog(QDialog):
    """Pick the font for one interface language, from the built-in fonts and any the owner added."""

    def __init__(self, lang: str, current: str, parent=None):
        super().__init__(parent)
        self.lang = lang
        self.setWindowTitle(tr(f"font.title_{lang}"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        lay.addWidget(hint(tr(f"font.hint_{lang}")))
        row = QHBoxLayout()
        self.combo = QComboBox(objectName="fontpick")
        self.combo.setItemDelegate(QStyledItemDelegate(self.combo))    # honours each item's own font
        self.combo.currentIndexChanged.connect(self._show_sample)
        row.addWidget(self.combo, 1)
        add = QPushButton(tr("font.add"))
        add.clicked.connect(self._add)
        row.addWidget(add)
        lay.addLayout(row)
        self.sample = FontSample()
        lay.addWidget(self.sample)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton(tr("game.cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("game.save"))
        save.setDefault(True)
        save.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addSpacing(4)
        lay.addLayout(btns)
        self._fill(current)

    def _fill(self, select: str) -> None:
        self.combo.blockSignals(True)
        self.combo.clear()
        names = sorted(FONTS, key=lambda f: (FONTS[f] != "builtin", f.lower()))
        if select and select not in names:
            names.insert(0, select)              # chosen before but its file is gone: still show it
        for fam in names:
            label = f"{fam}  ({tr('font.builtin' if FONTS.get(fam) == 'builtin' else 'font.user')})"
            self.combo.addItem(label, fam)
            f = QFont()
            f.setFamilies([fam, *FONT_FALLBACKS])
            self.combo.setItemData(self.combo.count() - 1, f, Qt.FontRole)   # each name in its own font
        self.combo.setCurrentIndex(max(0, self.combo.findData(select)))
        self.combo.blockSignals(False)
        self._show_sample()

    def family(self) -> str:
        return self.combo.currentData() or FONT_FALLBACKS[0]

    def _show_sample(self, *_):
        self.sample.set_sample(self.family(), tr("font.sample_en") + "\n" + tr("font.sample_ja"))
        self.adjustSize()                       # the window grows or shrinks with the sample

    @guarded
    def _add(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("font.pick"), "", tr("font.files"))
        if not path:
            return
        fam = add_user_font(path)
        if fam is None:
            message(self, tr("font.bad"))
            return
        self._fill(fam)


class GuideDialog(QDialog):
    """Help and Guidance: a short how-to for every part of Ikemen Reader, in the interface language."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("guide.title"))
        self.resize(640, 680)
        self.setMinimumSize(420, 360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(12)
        text = QTextBrowser(objectName="guide")
        text.setOpenExternalLinks(False)
        text.setHtml(guide.guide_html(i18n.language(), COLORS, LIGHTS))
        lay.addWidget(text, 1)
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton(tr("guide.close"))
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)


class BgPreview(QWidget):
    """The background preview: drag to move the picture, wheel to zoom, double-click to reset (like
    cropping a cover). Shaped like the library area, so what you see is what you get."""
    changed = Signal()

    def __init__(self, size: QSize, parent=None):
        super().__init__(parent)
        self.setFixedSize(size)
        self.path, self.opacity = "", 0.35
        self.zoom, self.cx, self.cy = 1.0, 0.5, 0.5
        self._drag: Optional[QPointF] = None
        self.setToolTip(tr("bg.crop_tip"))

    def set_state(self, path: str, opacity: float, zoom: float, cx: float, cy: float) -> None:
        self.path, self.opacity, self.zoom, self.cx, self.cy = path, opacity, zoom, cx, cy
        self._clamp()
        self.setCursor(Qt.OpenHandCursor if path else Qt.ArrowCursor)
        self.update()

    def _img(self) -> Optional[QImage]:
        return _bg_image(self.path) if self.path else None

    def _clamp(self) -> None:
        img = self._img()
        if img is not None:
            _, self.cx, self.cy = crop_rect(img.width(), img.height(), self.zoom, self.cx, self.cy,
                                            aspect=self.width() / self.height())

    def set_zoom(self, zoom: float) -> None:
        self.zoom = max(1.0, min(4.0, zoom))
        self._clamp()
        self.update()
        self.changed.emit()

    def paintEvent(self, _ev) -> None:
        s = self.size()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        frame = QRectF(0.5, 0.5, s.width() - 1, s.height() - 1)
        clip = QPainterPath()
        clip.addRoundedRect(frame, 8, 8)
        p.save()
        p.setClipPath(clip)
        p.fillRect(self.rect(), QColor(COLORS["bg"]))
        img = self._img()
        if img is not None:
            r, _, _ = crop_rect(img.width(), img.height(), self.zoom, self.cx, self.cy, aspect=s.width() / s.height())
            p.setOpacity(self.opacity)
            p.drawImage(QRectF(self.rect()), img, r)
            p.setOpacity(1.0)
            n, gap = 5, 12                                          # a row of covers, centred
            ch = min(82.0, s.height() * 0.42)
            cw = ch * COVER_W / COVER_H
            x0 = (s.width() - (n * cw + (n - 1) * gap)) / 2
            y0 = (s.height() - ch) / 2
            for i in range(n):
                cr = QRectF(x0 + i * (cw + gap), y0, cw, ch)
                g = QLinearGradient(cr.topLeft(), cr.bottomLeft())
                g.setColorAt(0, QColor(COLORS["placeholder_top"]))
                g.setColorAt(1, QColor(COLORS["placeholder_bottom"]))
                p.setBrush(g)
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(cr, 5, 5)
        else:
            p.setPen(QColor(COLORS["muted"]))
            p.setFont(ui_font(10))                                  # the app's font
            p.drawText(QRectF(28, 0, s.width() - 56, s.height()), Qt.AlignCenter | Qt.TextWordWrap, tr("bg.none"))
        p.restore()
        p.setPen(QPen(QColor(COLORS["border"]), 1))           # the same thin rounded border as other boxes
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(frame, 8, 8)
        p.end()

    def mousePressEvent(self, ev) -> None:
        if self.path and ev.button() == Qt.LeftButton:
            self._drag = ev.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, ev) -> None:
        img = self._img()
        if self._drag is None or img is None:
            return
        d = ev.position() - self._drag
        self._drag = ev.position()
        r, _, _ = crop_rect(img.width(), img.height(), self.zoom, self.cx, self.cy, aspect=self.width() / self.height())
        # dragging right shows more of the left side, like moving a photo under a frame
        self.cx -= d.x() * (r.width() / self.width()) / img.width()
        self.cy -= d.y() * (r.height() / self.height()) / img.height()
        self._clamp()
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, _ev) -> None:
        self._drag = None
        if self.path:
            self.setCursor(Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, _ev) -> None:
        if self.path:
            self.zoom, self.cx, self.cy = 1.0, 0.5, 0.5
            self.update()
            self.changed.emit()

    def wheelEvent(self, ev) -> None:
        if self.path:
            self.set_zoom(self.zoom * (1.1 if ev.angleDelta().y() > 0 else 1 / 1.1))


class BackgroundDialog(QDialog):
    """Choose a library background picture, its opacity and crop; previews in the box and, live, in the
    window behind."""

    def __init__(self, path: str, opacity: float, preview: Callable, parent=None,
                 crop: tuple = (1.0, 0.5, 0.5), area: Optional[QSize] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("bg.title"))
        self.path, self.opacity, self.preview = path or "", opacity, preview
        self.zoom, self.cx, self.cy = crop
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        # the preview takes the library area's shape (within sensible limits)
        w = 380
        aspect = (area.width() / area.height()) if area and not area.isEmpty() else 16 / 9
        self.box = BgPreview(QSize(w, int(max(150, min(260, w / aspect)))))
        self.box.changed.connect(self._cropped)
        lay.addWidget(self.box, alignment=Qt.AlignHCenter)
        row = QHBoxLayout()
        self.choose = QPushButton()
        self.choose.clicked.connect(self._choose)
        self.remove = QPushButton(tr("bg.remove"), objectName="danger")
        self.remove.clicked.connect(self._remove)
        row.addWidget(self.choose)
        row.addWidget(self.remove)
        row.addStretch()
        lay.addLayout(row)
        # opacity and zoom: shown once there's a picture
        self.sliders = QWidget()
        grid = QGridLayout(self.sliders)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setHorizontalSpacing(10)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(round(opacity * 100))
        self.slider.valueChanged.connect(self._slid)
        self.pct = QLabel(objectName="muted")
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(100, 400)
        self.zoom_slider.setToolTip(tr("bg.crop_tip"))
        self.zoom_slider.valueChanged.connect(lambda v: self.box.set_zoom(v / 100))
        self.zoom_val = QLabel(objectName="muted")
        for i, (name, slider, value) in enumerate(((tr("bg.zoom"), self.zoom_slider, self.zoom_val),
                                                   (tr("bg.opacity"), self.slider, self.pct))):
            grid.addWidget(QLabel(name), i, 0)
            grid.addWidget(slider, i, 1)
            value.setFixedWidth(42)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(value, i, 2)
        grid.setColumnStretch(1, 1)
        lay.addWidget(self.sliders)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton(tr("game.cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("game.save"))
        save.setDefault(True)
        save.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(save)
        lay.addSpacing(4)
        lay.addLayout(btns)
        # while dragging/zooming the window behind follows every move (fast mode); ~0.15 s after the
        # last move it is redrawn once at full quality
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(150)
        self._settle.timeout.connect(self._push_preview)
        self._refresh()

    def crop(self) -> tuple:
        return self.zoom, self.cx, self.cy

    def _push_preview(self) -> None:
        self.preview(self.path, self.opacity, self.zoom, self.cx, self.cy)

    def _changed(self) -> None:
        self.preview(self.path, self.opacity, self.zoom, self.cx, self.cy, True)    # fast, every move
        self._settle.start()                                                         # sharp when it stops

    def _refresh(self) -> None:
        has = bool(self.path)
        self.choose.setText(tr("bg.change" if has else "bg.choose"))
        self.remove.setVisible(has)
        self.sliders.setVisible(has)        # the sliders only once there's a picture
        self.pct.setText(f"{round(self.opacity * 100)}%")
        self.box.set_state(self.path, self.opacity, self.zoom, self.cx, self.cy)
        self.zoom, self.cx, self.cy = self.box.zoom, self.box.cx, self.box.cy
        self._show_zoom()
        self._push_preview()
        self.adjustSize()

    def _show_zoom(self) -> None:
        self.zoom_slider.blockSignals(True)
        self.zoom_slider.setValue(round(self.zoom * 100))
        self.zoom_slider.blockSignals(False)
        self.zoom_val.setText(f"{self.zoom:.1f}×")

    def _cropped(self) -> None:
        self.zoom, self.cx, self.cy = self.box.zoom, self.box.cx, self.box.cy
        self._show_zoom()
        self._changed()

    def _choose(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, tr("bg.choose"), "", tr("file.images"))
        if not f:
            return
        if QImage(f).isNull():
            message(self, tr("bg.bad_image"))
            return
        self.path = str(Path(f))
        self.zoom, self.cx, self.cy = 1.0, 0.5, 0.5          # a new picture starts uncropped
        self._refresh()

    def _remove(self) -> None:
        self.path = ""
        self.zoom, self.cx, self.cy = 1.0, 0.5, 0.5
        self._refresh()

    def _slid(self, v: int) -> None:
        self.opacity = v / 100
        self.pct.setText(f"{v}%")
        self.box.set_state(self.path, self.opacity, self.zoom, self.cx, self.cy)
        self._changed()

    def done(self, r) -> None:
        self._settle.stop()
        if r:
            self._push_preview()                     # leave it at full quality
        super().done(r)


class SettingsDialog(QDialog):
    def __init__(self, current: Optional[LocalSettings], parent=None, welcome: bool = False):
        super().__init__(parent)
        self._i18n: list[Callable] = []          # re-applies every text when the language is switched
        self._tx(self, "settings.welcome" if welcome else "settings.title", "setWindowTitle")
        self.setMinimumWidth(580)
        # the settings scroll when the screen is too short for them; Cancel / Save stay fixed below
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._content = QWidget(objectName="settings_content")
        lay = QVBoxLayout(self._content)
        lay.setContentsMargins(22, 18, 22, 8)
        self._scroll = QScrollArea(objectName="settings_scroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._content)
        outer.addWidget(self._scroll, 1)
        if welcome:
            lay.addWidget(Logo(44), alignment=Qt.AlignLeft)
            lay.addSpacing(6)

        lay.addWidget(self._tx(section(""), "settings.language"))
        self.lang = QComboBox()
        self.lang.addItem(tr("lang.en"), "en")
        self.lang.addItem(tr("lang.ja"), "ja")
        for i, key in enumerate(("lang.en", "lang.ja")):
            self._i18n.append(lambda i=i, key=key: self.lang.setItemText(i, tr(key)))
        self._orig_lang = current.language if current else i18n.language()
        self.lang.setCurrentIndex(1 if self._orig_lang == "ja" else 0)
        self.lang.currentIndexChanged.connect(self._switch_language)    # the whole window follows at once
        lay.addWidget(self.lang)

        # Font: one per interface language ("日本語" is always written in Japanese)
        self.fonts = {"en": current.font_en if current else FONT_FALLBACKS[0],
                      "ja": current.font_ja if current else FONT_FALLBACKS[0]}
        lay.addSpacing(4)
        pair = QHBoxLayout()                   # Font | Ikemen Sync, each heading centred over its buttons
        for heading, buttons in (("settings.font", [(f"font.btn_{l}", lambda _=False, l=l: self._choose_font(l))
                                                     for l in ("en", "ja")]),
                                 ("settings.isync", [("isync.button", lambda _=False: self._ikemen_sync())]),
                                 ("settings.about", [("guide.button", lambda _=False: GuideDialog(self).exec())])):
            col = QVBoxLayout()
            col.setSpacing(6)
            head = self._tx(section(""), heading)
            head.setAlignment(Qt.AlignHCenter)
            col.addWidget(head)
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addStretch()
            for key, slot in buttons:
                b = self._tx(QPushButton(), key)
                b.clicked.connect(slot)
                row.addWidget(b)
                if key == "isync.button":
                    self._tx(b, "isync.tip", "setToolTip")
            row.addStretch()
            col.addLayout(row)
            pair.addLayout(col, 1)
        lay.addLayout(pair)
        lay.addSpacing(14)
        lay.addLayout(self._divider())
        lay.addSpacing(3)                      # + the heading's own top space = the same visible gap as above

        lay.addWidget(self._tx(section(""), "settings.device"))
        self.device = QComboBox()
        for slot in SLOTS:
            self.device.addItem(tr("settings.device_n", n=SLOT_NUMBER[slot]), slot)
            self._i18n.append(lambda i=self.device.count() - 1, n=SLOT_NUMBER[slot]:
                              self.device.setItemText(i, tr("settings.device_n", n=n)))
        if current:
            self.device.setCurrentIndex(SLOTS.index(current.machine_name))
        lay.addWidget(self.device)
        lay.addWidget(self._tx(hint(""), "settings.device_hint"))

        lay.addWidget(self._tx(section(""), "settings.cloud"))
        self.cloud = QLineEdit(current.cloud_root if current else "")
        self.cloud.setPlaceholderText(r"C:\Users\You\OneDrive\VNSaves")
        lay.addLayout(self._browse_row(self.cloud, self._pick_cloud))
        lay.addWidget(self._tx(hint(""), "settings.cloud_hint"))

        lay.addWidget(self._tx(section(""), "settings.le"))
        self.le = QLineEdit(current.le_path if current else "")
        self._tx(self.le, "settings.le_placeholder", "setPlaceholderText")
        lay.addLayout(self._browse_row(self.le, self._pick_le))

        lay.addWidget(self._tx(section(""), "settings.luna"))
        self.luna = QLineEdit(current.luna_path if current else "")
        self._tx(self.luna, "settings.luna_placeholder", "setPlaceholderText")
        lay.addLayout(self._browse_row(self.luna, self._pick_luna))
        lay.addWidget(self._tx(hint(""), "settings.luna_hint"))

        # a faint line: what follows is personalisation
        lay.addSpacing(14)
        lay.addLayout(self._divider())
        lay.addSpacing(3)                      # + the heading's own top space = the same visible gap as above

        lay.addWidget(self._tx(section(""), "settings.accessibility"))
        lay.addSpacing(4)
        self.pulse = self._tx(QCheckBox(), "settings.pulse")
        self.pulse.setChecked(current.pulse if current else True)
        self.startup = self._tx(QCheckBox(), "settings.startup")
        self.startup.setChecked(startup.is_enabled())        # the real state of Windows' startup list
        self.vndb = self._tx(QCheckBox(), "settings.vndb")
        self.vndb.setChecked(current.vndb if current else True)
        self.vndb_safe = self._tx(QCheckBox(), "settings.vndb_safe")
        self.vndb_safe.setChecked(current.vndb_safe_only if current else False)
        lay.addWidget(self.pulse)
        lay.addWidget(self.startup)
        lay.addWidget(self.vndb)
        lay.addWidget(self.vndb_safe)

        # Background, then Theme: Light / Dark / Custom (the one in use stays highlighted)
        self.bg_path = current.bg_image if current else ""
        self.bg_opacity = current.bg_opacity if current else 0.35
        self.bg_crop = (current.bg_zoom, current.bg_x, current.bg_y) if current else (1.0, 0.5, 0.5)
        self._orig_bg = (self.bg_path, self.bg_opacity, *self.bg_crop)
        lay.addSpacing(4)
        lay.addWidget(self._tx(section(""), "settings.background_head"))
        lay.addSpacing(6)
        bg_row = QHBoxLayout()
        bg_btn = self._tx(QPushButton(), "settings.background")
        bg_btn.clicked.connect(self._choose_background)
        bg_row.addWidget(bg_btn)
        bg_row.addStretch()
        lay.addLayout(bg_row)
        lay.addSpacing(4)
        lay.addWidget(self._tx(section(""), "settings.display"))
        lay.addSpacing(6)
        self.theme = current.theme if current else "dark"
        self.custom = dict(current.custom_colors) if current else {}
        self._orig_theme, self._orig_custom = self.theme, dict(self.custom)
        seg = QHBoxLayout()
        seg.setSpacing(6)
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        self.theme_btns = {}
        for name in ("dark", "light", "custom"):
            b = self._tx(QPushButton(objectName="seg"), f"theme.{name}")
            b.setCheckable(True)
            if name == "custom":
                self._tx(b, "theme.custom_tip", "setToolTip")
            b.clicked.connect(lambda _=False, n=name: self._choose_theme(n))
            self.theme_group.addButton(b)
            self.theme_btns[name] = b
            seg.addWidget(b)
        seg.addStretch()
        lay.addLayout(seg)
        self.theme_btns[self.theme].setChecked(True)

        row = QHBoxLayout()
        row.addStretch()
        cancel = self._tx(QPushButton(), "settings.cancel")
        cancel.clicked.connect(self.reject)
        save = self._tx(QPushButton(), "settings.save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        row.setContentsMargins(22, 10, 22, 18)
        row.setSpacing(lay.spacing())          # the usual gap between the two buttons
        outer.addLayout(row)
        self.current = current
        self.sort_filter = (current.sort, current.status_filter) if current else ("recent", "all")
        self.result_settings: Optional[LocalSettings] = None

    def _tx(self, obj, key: str, setter: str = "setText", **kw):
        """Set a text now, and again whenever the language is switched."""
        f = lambda: getattr(obj, setter)(tr(key, **kw))
        f()
        self._i18n.append(f)
        return obj

    def _browse_row(self, edit: QLineEdit, on_browse) -> QHBoxLayout:
        row = browse_row(edit, on_browse, "")
        self._tx(row.itemAt(row.count() - 1).widget(), "game.browse")
        return row

    def _switch_language(self, *_):
        """Preview the chosen language everywhere at once (Save keeps it, Cancel switches back)."""
        set_interface_language(self.lang.currentData())
        for f in self._i18n:
            f()
        main = self.parent()
        if hasattr(main, "preview_language"):
            main.preview_language()
        self.adjustSize()
        self._fit_height()

    @staticmethod
    def _screen_room(widget: QWidget) -> QRect:
        screen = widget.screen() or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _fit_height(self) -> None:
        """As tall as all the settings need (with the hint texts wrapped at the real width), but never
        taller than the screen: then the settings scroll and Cancel / Save stay in view."""
        width = max(self.width(), self.minimumWidth())
        content = self._content.layout().totalHeightForWidth(width)
        buttons = self.layout().itemAt(1).sizeHint().height()
        room = self._screen_room(self)
        frame = self.frameGeometry().height() - self.geometry().height() if self.isVisible() else 32
        h = min(content + buttons, room.height() - max(frame, 32) - 8)
        self.setMinimumHeight(min(320, h))
        self.resize(width, h)
        if self.isVisible():                    # keep the whole window on the screen
            top = self.frameGeometry().top()
            bottom = top + h + max(frame, 32)
            if bottom > room.bottom() or top < room.top():
                self.move(self.x(), max(room.top(), room.bottom() - h - max(frame, 32)))

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._fit_height()

    def _preview(self, theme: str, custom: dict) -> None:
        apply_theme(theme, custom)
        restyle_all()

    def _choose_theme(self, name: str) -> None:
        if name == "custom":
            start = theme_colors("custom", self.custom) if self.custom else theme_colors(self.theme)
            d = ThemeDialog(start, lambda c: self._preview("custom", c), self)
            if d.exec():
                self.theme, self.custom = "custom", d.colors
                self._persist(theme="custom", custom_colors=dict(self.custom))
                self._orig_theme, self._orig_custom = self.theme, dict(self.custom)
            # cancelled: keep whatever was chosen before (and its look)
            self.theme_btns[self.theme].setChecked(True)
            self._preview(self.theme, self.custom)
            return
        self.theme = name
        self._preview(name, self.custom)

    @guarded
    def _ikemen_sync(self) -> None:
        """Device Settings Sync: copy the other device's look and preferences onto this one."""
        cloud, slot = self.cloud.text().strip(), self.device.currentData()
        other = other_slot(slot)
        n = SLOT_NUMBER[other]
        if not cloud or not Path(cloud).is_dir():
            message(self, tr("isync.no_cloud"))
            return
        shared = devsync.read(cloud, other)
        if shared is None:
            message(self, tr("isync.none", n=n))
            return
        when = shared.get("shared_at")
        when = i18n.datetime_str(when) if isinstance(when, (int, float)) else "?"
        if heading_message(self, tr("isync.confirm_title", n=n), tr("isync.confirm", n=n, when=when),
                           [("no", tr("settings.cancel")), ("yes", tr("isync.go"))], dismiss="no") != "yes":
            return
        base = self.current or LocalSettings(slot, cloud)
        new, files = devsync.apply(shared, cloud, other, base)
        missing = []
        for fam, path in files["fonts"].items():         # fonts the other device added: add them here too
            if fam not in FONTS:
                add_user_font(path)
        for lang in ("en", "ja"):
            fam = getattr(new, f"font_{lang}")
            if fam not in FONTS:                         # its font file didn't come across: keep ours
                missing.append(fam)
                setattr(new, f"font_{lang}", self.fonts[lang])
        bg = ""
        if files["background"]:
            import shutil
            folder = default_app_dir() / "background"
            folder.mkdir(parents=True, exist_ok=True)
            bg = str(folder / f"background_{int(time.time())}{Path(files['background']).suffix.lower()}")
            try:
                shutil.copy2(files["background"], bg)
            except OSError:
                bg = self.bg_path
        # show it all in this window ...
        self.fonts = {"en": new.font_en, "ja": new.font_ja}
        self.theme, self.custom = new.theme, dict(new.custom_colors)
        self.theme_btns[self.theme].setChecked(True)
        self.bg_path, self.bg_opacity = bg, new.bg_opacity
        self.bg_crop = (new.bg_zoom, new.bg_x, new.bg_y)
        self.bg_path = self._store_background()          # keeps the new picture, tidies older ones
        self.pulse.setChecked(new.pulse)
        self.vndb.setChecked(new.vndb)
        self.vndb_safe.setChecked(new.vndb_safe_only)
        self.sort_filter = (new.sort, new.status_filter)
        self.lang.setCurrentIndex(1 if new.language == "ja" else 0)   # switches the language at once
        # ... and save it straight away (like the theme and background windows)
        changes = dict(language=new.language, font_en=new.font_en, font_ja=new.font_ja, pulse=new.pulse,
                       vndb=new.vndb, vndb_safe_only=new.vndb_safe_only, sort=new.sort,
                       status_filter=new.status_filter, theme=new.theme, custom_colors=dict(new.custom_colors),
                       bg_image=self.bg_path, bg_opacity=new.bg_opacity, bg_zoom=new.bg_zoom, bg_x=new.bg_x,
                       bg_y=new.bg_y, show_stats=new.show_stats)
        if self.current is not None:
            self._persist(**changes)
            self._orig_theme, self._orig_custom = self.theme, dict(self.custom)
            self._orig_bg = (self.bg_path, self.bg_opacity, *self.bg_crop)
            self._orig_lang = new.language
            main = self.parent()
            if hasattr(main, "apply_settings"):
                main.apply_settings(self.current)          # fonts, theme, background, sort and filter
        else:
            apply_fonts(new.font_en, new.font_ja)
            apply_background(self.bg_path, self.bg_opacity, *self.bg_crop)
            self._preview(self.theme, self.custom)
        note = tr("isync.done", n=n)
        if missing:
            note += "\n\n" + tr("isync.font_missing", fonts=", ".join(missing))
        message(self, note)

    @staticmethod
    def _divider() -> QHBoxLayout:
        """A faint, thin centred line between groups of settings."""
        row = QHBoxLayout()
        line = QFrame(objectName="divider")
        line.setFixedHeight(1)
        row.addStretch(1)
        row.addWidget(line, 6)
        row.addStretch(1)
        return row

    def _choose_font(self, lang: str) -> None:
        d = FontDialog(lang, self.fonts[lang], self)
        if d.exec():
            self.fonts[lang] = d.family()
            self._persist(**{f"font_{lang}": self.fonts[lang]})
            apply_fonts(self.fonts["en"], self.fonts["ja"])
            restyle_all()

    def _persist(self, **changes) -> None:
        """Save some settings straight away (Save in a sub-window shouldn't also need Save in Settings).
        Cancel in Settings then keeps them too. (Nothing to save into yet during first-time setup.)"""
        if self.current is None:
            return
        saved = load_local_settings() or self.current
        for k, v in changes.items():
            setattr(saved, k, v)
            setattr(self.current, k, v)          # the main window's settings object stays in step
        save_local_settings(saved)
        if hasattr(self.parent(), "share_settings"):
            self.parent().share_settings()       # so the other device can copy them (Ikemen Sync)

    def _choose_background(self) -> None:
        main = self.parent()
        area = main.backdrop.library_area().size() if hasattr(main, "backdrop") else None
        d = BackgroundDialog(self.bg_path, self.bg_opacity, apply_background, self, crop=self.bg_crop, area=area)
        if d.exec():
            self.bg_path, self.bg_opacity, self.bg_crop = d.path, d.opacity, d.crop()
            if self.current is not None:
                self.bg_path = self._store_background()
                z, x, y = self.bg_crop
                self._persist(bg_image=self.bg_path, bg_opacity=round(self.bg_opacity, 2),
                              bg_zoom=round(z, 4), bg_x=round(x, 4), bg_y=round(y, 4))
                self._orig_bg = (self.bg_path, self.bg_opacity, *self.bg_crop)
        apply_background(self.bg_path, self.bg_opacity, *self.bg_crop)   # cancelled: back to the earlier choice

    def reject(self) -> None:
        self._preview(self._orig_theme, self._orig_custom)          # always undo any preview
        apply_background(*self._orig_bg)
        if i18n.language() != self._orig_lang:
            set_interface_language(self._orig_lang)
            if hasattr(self.parent(), "preview_language"):
                self.parent().preview_language()
        super().reject()

    def _store_background(self) -> str:
        """Keep our own copy of the picture (so moving or deleting the original doesn't matter) and
        tidy up pictures no longer used."""
        folder = default_app_dir() / "background"
        if not self.bg_path:
            keep = None
        elif Path(self.bg_path).parent == folder:
            keep = Path(self.bg_path)
        else:
            import shutil
            folder.mkdir(parents=True, exist_ok=True)
            keep = folder / f"background_{int(time.time())}{Path(self.bg_path).suffix.lower()}"
            try:
                shutil.copy2(self.bg_path, keep)
            except OSError:
                keep = Path(self.bg_path)
        if folder.is_dir():
            for f in folder.iterdir():
                if f != keep:
                    try:
                        f.unlink()
                    except OSError:
                        pass
        return str(keep) if keep else ""

    def _pick_cloud(self):
        start = self.cloud.text() or os.environ.get("OneDrive", str(Path.home()))
        d = QFileDialog.getExistingDirectory(self, tr("settings.pick_cloud"), start)
        if d:
            self.cloud.setText(str(Path(d)))

    def _pick_le(self):
        f, _ = QFileDialog.getOpenFileName(self, tr("settings.pick_le"), self.le.text(), "LEProc.exe (LEProc.exe);;*.exe")
        if f:
            self.le.setText(str(Path(f)))

    def _pick_luna(self):
        f, _ = QFileDialog.getOpenFileName(self, tr("settings.pick_luna"), self.luna.text(), "*.exe")
        if f:
            self.luna.setText(str(Path(f)))

    @guarded
    def _save(self):
        cloud, le, lp = self.cloud.text().strip(), self.le.text().strip(), self.luna.text().strip()
        if not cloud:
            message(self, tr("settings.need_cloud"))
            return
        for path in (le, lp):
            if path and not Path(path).is_file():
                message(self, tr("settings.bad_file", path=path))
                return
        if lp and luna.is_admin_build(lp):
            message(self, tr("settings.luna_admin"))
            return
        try:
            Path(cloud).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            message(self, tr("settings.bad_cloud", error=e))
            return
        slot = self.device.currentData()
        # safeguard: is that device number already used by another computer?
        lib = Library(cloud)
        owner = lib.slot_owner(slot)
        if owner and owner.lower() != this_computer().lower():
            n, other = SLOT_NUMBER[slot], SLOT_NUMBER[other_slot(slot)]
            choice = message(self, tr("settings.device_taken", n=n, computer=owner, other=other),
                             buttons=[("other", tr("settings.device_n", n=other)),
                                      ("keep", tr("settings.device_taken_keep", n=n)),
                                      ("cancel", tr("settings.cancel"))])
            if choice == "cancel":
                return
            if choice == "other":
                slot = other_slot(slot)
        lib.claim_slot(slot)
        bg_image = self._store_background()
        if self.startup.isChecked() != startup.is_enabled():
            try:
                startup.set_enabled(self.startup.isChecked())
            except OSError:
                pass
        self.result_settings = LocalSettings(
            slot, cloud, le, lp, self.lang.currentData(), self.pulse.isChecked(), self.vndb.isChecked(),
            self.vndb_safe.isChecked(), *self.sort_filter,
            theme=self.theme, custom_colors=dict(self.custom), bg_image=bg_image,
            font_en=self.fonts["en"], font_ja=self.fonts["ja"],
            show_stats=self.current.show_stats if self.current else True,
            bg_opacity=round(self.bg_opacity, 2), bg_zoom=round(self.bg_crop[0], 4),
            bg_x=round(self.bg_crop[1], 4), bg_y=round(self.bg_crop[2], 4))
        save_local_settings(self.result_settings)
        self.accept()


def _fetch_bytes(url: str) -> bytes:
    return vndb.fetch(url, timeout=15)          # kept-open connections + remembered for the session


def _download_cover(url: str) -> str:
    d = default_app_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
    return vndb.download(url, str(d / f"vndb_{int(time.time() * 1000)}{ext}"))


_LIVE_TASKS: set = set()          # every background task still running, whoever started it


def wait_for_tasks(seconds: float) -> bool:
    """Give running background tasks up to `seconds` to finish (at exit). True if all did."""
    end = time.monotonic() + seconds
    for t in list(_LIVE_TASKS):
        t.wait(max(0, int((end - time.monotonic()) * 1000)))
    return not any(t.isRunning() for t in _LIVE_TASKS)


class TaskOwner:
    """Mixin: run work in the background. A task belongs to the program, not to the window that
    started it: closing the window never waits for it (no freeze) and never deletes it mid-run
    (which would make Qt abort the program); if the window is gone, the result is simply dropped."""

    def _run(self, fn, done, failed=None):
        import shiboken6
        if not hasattr(self, "_tasks"):
            self._tasks = []
        t = Task(fn)
        owner = self

        def deliver(callback):
            def call(value):
                if callback is not None and shiboken6.isValid(owner):
                    callback(value)
            return call
        t.done.connect(deliver(done))
        t.failed.connect(deliver(failed))

        def finished():
            _LIVE_TASKS.discard(t)
            if t in owner._tasks:
                owner._tasks.remove(t)
        t.finished.connect(finished)
        _LIVE_TASKS.add(t)
        self._tasks.append(t)
        t.start()

    def _wait_tasks(self, seconds: float = 3.0):
        end = time.monotonic() + seconds
        for t in list(getattr(self, "_tasks", [])):
            t.wait(max(0, int((end - time.monotonic()) * 1000)))


class CoverPickerDialog(TaskOwner, QDialog):
    """All of a VN's covers on VNDB as thumbnails; pick one."""

    def __init__(self, title: str, options: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("vndb.pick_cover"))
        self.setMinimumSize(640, 560)
        self.choice = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        body = QLabel(tr("vndb.pick_body", n=len(options), title=html.escape(title)))
        body.setWordWrap(True)
        lay.addWidget(body)
        self.list = QListWidget()
        self.list.setViewMode(QListWidget.IconMode)
        self.list.setResizeMode(QListWidget.Adjust)
        self.list.setMovement(QListWidget.Static)
        self.list.setIconSize(QSize(COVER_W * 3 // 4, COVER_H * 3 // 4))
        self.list.setGridSize(QSize(COVER_W * 3 // 4 + 24, COVER_H * 3 // 4 + 52))
        self.list.setWordWrap(True)
        self.list.setStyleSheet(f"QListWidget {{ background: {COLORS['input']}; border-radius: 6px; padding: 6px; }}"
                                f"QListWidget::item {{ border-radius: 6px; padding: 4px; color: {COLORS['muted']}; }}"
                                f"QListWidget::item:selected {{ background: {COLORS['button_hover']}; color: {COLORS['text']}; }}")
        self.list.itemDoubleClicked.connect(lambda _: self._use())
        blank = QPixmap(COVER_W * 3 // 4, COVER_H * 3 // 4)
        blank.fill(QColor(COLORS["other_bg"]))
        for c in options:
            label = tr(f"vndb.cover_{c.kind}")
            if c.released:
                label += f"\n{c.released}"
            it = QListWidgetItem(QIcon(blank), label)
            it.setData(Qt.UserRole, c)
            it.setToolTip(c.release or label)
            it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
            self.list.addItem(it)
            self._run(lambda url=(c.thumb_url or c.url): _fetch_bytes(url), lambda data, item=it: self._thumb(item, data))
        self.list.setCurrentRow(0)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(tr("game.cancel"))
        cancel.clicked.connect(self.reject)
        use = QPushButton(tr("vndb.use_selected"))
        use.setDefault(True)
        use.clicked.connect(self._use)
        row.addWidget(cancel)
        row.addWidget(use)
        lay.addLayout(row)

    def done(self, r):
        super().done(r)          # (background tasks finish on their own; see TaskOwner)

    def _thumb(self, item: QListWidgetItem, data: bytes):
        img = QImage.fromData(data)
        if img.isNull():
            return
        size = self.list.iconSize()
        r, _, _ = crop_rect(img.width(), img.height(), 1.0, 0.5, 0.5)    # same framing as the grid
        item.setIcon(QIcon(QPixmap.fromImage(img.copy(r.toAlignedRect()).scaled(
            size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))))

    def _use(self):
        it = self.list.currentItem()
        if it:
            self.choice = it.data(Qt.UserRole)
            self.accept()


class VndbDialog(TaskOwner, QDialog):
    """Search VNDB and pick a cover (and optionally the title)."""

    def __init__(self, query: str, parent=None, safe_only: bool = False):
        super().__init__(parent)
        self.safe_only = safe_only
        self.setWindowTitle(tr("vndb.title"))
        self.setMinimumSize(560, 520)
        self.choice: Optional[vndb.Match] = None
        self.cover_file: Optional[str] = None
        self.use_title = False
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.q = QLineEdit(query)
        self.q.returnPressed.connect(self.search)
        # search as you type: a moment after typing pauses (Enter / Search still search at once)
        self._gen = 0                       # which search the list is showing; older answers are ignored
        self._typing = QTimer(self)
        self._typing.setSingleShot(True)
        self._typing.setInterval(300)
        self._typing.timeout.connect(lambda: self.search(typed=True))
        self.q.textEdited.connect(lambda _: self._typing.start())
        go = QPushButton(tr("vndb.search"))
        go.clicked.connect(self.search)
        row.addWidget(self.q, 1)
        row.addWidget(go)
        lay.addLayout(row)
        self.info = hint("")
        lay.addWidget(self.info)
        self.list = QListWidget()
        self.list.setIconSize(QSize(64, 90))
        self.list.setSpacing(4)
        self.list.setStyleSheet(f"QListWidget {{ background: {COLORS['input']}; border-radius: 6px; }}"
                                f"QListWidget::item {{ padding: 4px; border-radius: 6px; }}"
                                f"QListWidget::item:selected {{ background: {COLORS['button_hover']}; }}")
        self.list.itemDoubleClicked.connect(lambda _: self._use(True))
        self.list.currentItemChanged.connect(self._prefetch)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(tr("game.cancel"))
        cancel.clicked.connect(self.reject)
        only = QPushButton(tr("vndb.use_cover"))
        only.clicked.connect(lambda: self._use(False))
        both = QPushButton(tr("vndb.use"))
        both.setDefault(True)
        both.clicked.connect(lambda: self._use(True))
        for b in (cancel, only, both):
            row.addWidget(b)
        lay.addLayout(row)
        if query.strip():
            QTimer.singleShot(0, self.search)

    def done(self, r):
        super().done(r)          # (background tasks finish on their own; see TaskOwner)

    def search(self, typed: bool = False):
        self._typing.stop()
        q = self.q.text().strip()
        if typed and not (len(q) >= 2 or any(not c.isascii() for c in q)):
            return                          # a single Latin letter would match half of VNDB
        if not q:
            return
        self._gen += 1
        gen = self._gen
        self.info.setText(tr("vndb.searching"))
        self._run(lambda: vndb.search(q, limit=10, safe_only=self.safe_only),
                  lambda matches: self._show(matches, gen),
                  lambda e: self.info.setText(tr("vndb.error")) if gen == self._gen else None)

    def _show(self, matches: list, gen: int = 0):
        if gen and gen != self._gen:
            return                          # an older search answered late: the newer one wins
        self.list.clear()
        self.info.setText("" if matches else tr("vndb.none"))
        for m in matches:
            text = m.display_title + (f"\n{m.title}" if m.original and m.title != m.original else "")
            text += f"\n{m.released}" if m.released else ""
            if not m.image_url:
                text += "  " + tr("vndb.no_safe_cover")
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, m)
            it.setSizeHint(QSize(0, 98))
            self.list.addItem(it)
            if m.image_url:
                self._run(lambda url=(m.thumb_url or m.image_url): _fetch_bytes(url),
                          lambda data, item=it, g=self._gen: self._thumb(item, data) if g == self._gen else None)
        if matches:
            self.list.setCurrentRow(0)

    def _prefetch(self, *_):
        """While you look at the results, fetch the selected one's cover list, so "Use" responds at once."""
        it = self.list.currentItem()
        m = it.data(Qt.UserRole) if it else None
        if m is not None and m.image_url:
            self._run(lambda: vndb.covers(m.vndb_id, safe_only=self.safe_only), lambda _: None, lambda _: None)

    @staticmethod
    def _thumb(item: QListWidgetItem, data: bytes):
        img = QImage.fromData(data)
        if not img.isNull():
            item.setIcon(QIcon(QPixmap.fromImage(img).scaled(64, 90, Qt.KeepAspectRatio, Qt.SmoothTransformation)))

    def _use(self, with_title: bool):
        it = self.list.currentItem()
        if not it:
            return
        m: vndb.Match = it.data(Qt.UserRole)
        if not m.image_url and not with_title:
            return
        self.choice, self.use_title = m, with_title
        self.info.setText(tr("vndb.loading_covers"))
        self._run(lambda: vndb.covers(m.vndb_id, safe_only=self.safe_only), self._got_covers,
                  lambda e: self._got_covers([vndb.Cover(m.image_url, "main", "", "")] if m.image_url else []))

    def _got_covers(self, options: list):
        m = self.choice
        if not options:
            if self.use_title:
                self.accept()          # no safe cover at all: just the title
            else:
                self.info.setText(tr("vndb.no_safe_cover"))
            return
        url = options[0].url
        if len(options) > 1:
            picker = CoverPickerDialog(m.display_title, options, self)
            if not picker.exec() or not picker.choice:
                self.info.setText("")
                return
            url = picker.choice.url
        self.info.setText(tr("vndb.downloading"))
        self._run(lambda: _download_cover(url), self._downloaded, lambda e: self.info.setText(tr("vndb.error")))

    def _downloaded(self, path: str):
        self.cover_file = path
        self.accept()


class GameDialog(TaskOwner, QDialog):
    """Add or edit a game: title, cover, launch options, reading status, and paths for This Device
    (with Browse) and the Other Device (greyed, text only)."""

    def __init__(self, library: Library, settings: LocalSettings, game: Optional[Game] = None,
                 exe: str = "", parent=None):
        super().__init__(parent)
        self.library, self.settings, self.game = library, settings, game
        self.machine = settings.machine_name
        self.setWindowTitle(tr("game.title_edit") if game else tr("game.title_add"))
        self.setMinimumWidth(720)
        self.art_src: Optional[str] = None
        self.art_from = "user"
        self.remove_art = False
        self.vndb_id = game.vndb_id if game else ""
        self.removed = False
        self.saved_id: Optional[str] = None
        self.title_locked = game.title_locked if game else False
        self.manual_title = game.manual_title if game else ""      # restored when Automatic is turned off
        self._suggestion: Optional[tuple] = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(20)
        # -- cover on the left
        left = QVBoxLayout()
        self.cover = CropView()
        self.cover.changed.connect(self._crop_changed)
        left.addWidget(self.cover)
        zrow = QHBoxLayout()
        self.zoom_label = QLabel(tr("game.zoom"), objectName="hint")
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(100, int(ZOOM_MAX * 100))
        self.zoom.setToolTip(tr("game.crop_tip"))
        self.zoom.valueChanged.connect(lambda v: self.cover.set_zoom(v / 100))
        zrow.addWidget(self.zoom_label)
        zrow.addWidget(self.zoom, 1)
        left.addLayout(zrow)
        b = QPushButton(tr("game.cover"))
        b.clicked.connect(self._pick_art)
        left.addWidget(b)
        self.remove_art_btn = QPushButton(tr("game.remove_cover"))
        self.remove_art_btn.clicked.connect(self._remove_art)
        left.addWidget(self.remove_art_btn)
        b = QPushButton(tr("game.vndb"))
        b.clicked.connect(self._open_vndb)
        left.addWidget(b)
        left.addStretch()
        outer.addLayout(left)
        self._show_cover(library.artwork_path(game) if game else None,
                         (game.crop_zoom, game.crop_x, game.crop_y) if game else (1.0, 0.5, 0.5))

        # -- fields on the right
        lay = QVBoxLayout()
        outer.addLayout(lay, 1)
        self.banner = QLabel(objectName="banner")
        self.banner.setWordWrap(True)
        self.banner_btn = QPushButton(tr("game.vndb_use"))
        self.banner_btn.clicked.connect(self._use_suggestion)
        self.banner_box = QWidget()
        brow = QHBoxLayout(self.banner_box)
        brow.setContentsMargins(0, 0, 0, 0)
        brow.addWidget(self.banner, 1)
        brow.addWidget(self.banner_btn)
        self.banner_box.hide()
        lay.addWidget(self.banner_box)

        lay.addWidget(section(tr("game.title")))
        trow = QHBoxLayout()
        self.title = QLineEdit(game.title if game else (guess_title(exe) if exe else ""))
        self.title.textEdited.connect(self._title_typed)
        trow.addWidget(self.title, 1)
        self.auto_btn = QPushButton(objectName="flat")      # an on/off switch for automatic titles
        self.auto_btn.clicked.connect(self._title_auto)
        trow.addWidget(self.auto_btn)
        lay.addLayout(trow)
        self._update_auto_btn()

        # launch options + status
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.addWidget(section(tr("game.launch")), 0, 0)
        grid.addWidget(section(tr("game.status")), 0, 1)
        self.use_luna = QCheckBox(tr("game.use_luna"))
        self.use_le = QCheckBox(tr("game.use_le"))
        self.use_luna.setChecked(bool(game and game.use_luna))
        self.use_le.setChecked(bool(game and game.use_le))
        grid.addWidget(self.use_luna, 1, 0)
        grid.addWidget(self.use_le, 2, 0)
        self.status = QComboBox()
        for s in STATUSES:
            self.status.addItem(tr(f"status.{s or 'none'}"), s)
        self.status.setCurrentIndex(STATUSES.index(game.status) if game else 0)
        grid.addWidget(self.status, 1, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.luna_note = hint(tr("game.luna_note"))
        lay.addWidget(self.luna_note)
        self.use_luna.toggled.connect(self.luna_note.setVisible)
        self.luna_note.setVisible(self.use_luna.isChecked())

        # paths: This Device first, Other Device below (greyed)
        self.fields: dict[str, tuple[QLineEdit, QLineEdit]] = {}
        for slot in (self.machine, other_slot(self.machine)):
            here = slot == self.machine
            lay.addWidget(section(tr("game.this_device") if here else tr("game.other_device")))
            p = game.paths_for(slot) if game else MachinePaths(exe if here else "", "")
            g = QGridLayout()
            exe_e, save_e = QLineEdit(p.exe), QLineEdit(p.save_dir)
            g.addWidget(QLabel(tr("game.exe")), 0, 0)
            g.addWidget(exe_e, 0, 1)
            g.addWidget(QLabel(tr("game.saves")), 1, 0)
            g.addWidget(save_e, 1, 1)
            if here:
                save_e.setPlaceholderText(tr("game.saves_auto"))
                be = QPushButton(tr("game.browse"))
                be.clicked.connect(lambda _=False, e=exe_e: self._pick_exe(e))
                bs = QPushButton(tr("game.browse"))
                bs.clicked.connect(lambda _=False, s=save_e: self._pick_save(s))
                g.addWidget(be, 0, 2)
                g.addWidget(bs, 1, 2)
            else:
                for e in (exe_e, save_e):
                    e.setProperty("secondary", True)
                    e.setToolTip(tr("game.other_tip"))
            lay.addLayout(g)
            self.fields[slot] = (exe_e, save_e)

        lay.addWidget(section(tr("game.registry")))
        self.registry = QLineEdit(game.registry_key if game else "")
        self.registry.setPlaceholderText(r"HKEY_CURRENT_USER\Software\...")
        lay.addWidget(self.registry)
        lay.addWidget(hint(tr("game.registry_hint")))
        lay.addStretch()

        row = QHBoxLayout()
        if game:
            rm = QPushButton(tr("game.remove"), objectName="danger")
            rm.clicked.connect(self._remove)
            row.addWidget(rm)
        row.addStretch()
        cancel = QPushButton(tr("game.cancel"))
        cancel.clicked.connect(self.reject)
        save = QPushButton(tr("game.save"))
        save.setDefault(True)
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        lay.addSpacing(8)
        lay.addLayout(row)

        # adding with "fetch covers from VNDB" on: look it up straight away
        if not game and settings.vndb and self.title.text().strip():
            self._auto_vndb(self.title.text().strip())

    def done(self, r):
        super().done(r)          # (background tasks finish on their own; see TaskOwner)

    # -- title
    def _title_typed(self, _text: str):
        self.title_locked = True
        self._update_auto_btn()

    def _title_auto(self):
        """Toggle automatic titles.
        On:  remember the current (own) title, then show the automatic one straight away - the game's
             window title from its last play, or (never played) the best guess from its .exe / folder.
        Off: bring the remembered own title back (or, if there never was one, keep the current
             title fixed)."""
        if self.title_locked:
            self.manual_title = self.title.text().strip()
            self.title_locked = False
            exe = self.fields[self.machine][0].text().strip()
            auto = (self.game.auto_title if self.game else "") or (guess_title(exe) if exe else "")
            if auto:
                self.title.setText(auto)
        else:
            self.title_locked = True
            if self.manual_title:
                self.title.setText(self.manual_title)
        self._update_auto_btn()

    def _update_auto_btn(self):
        on = not self.title_locked
        self.auto_btn.setText(("✓ " if on else "") + tr("game.title_auto"))
        self.auto_btn.setToolTip(tr("game.title_auto_on_tip" if on else "game.title_auto_off_tip"))
        # lit when on, dimmed when the owner has typed their own title
        self.auto_btn.setStyleSheet(f"color: {COLORS['text'] if on else COLORS['muted']};")

    # -- cover
    def _show_cover(self, path, crop=(1.0, 0.5, 0.5)) -> None:
        """Show a cover (a new one starts uncropped). The crop controls only appear when there is one."""
        self.cover.set_image(path, crop)
        has = self.cover.img is not None
        self.remove_art_btn.setVisible(has)
        self.zoom.setVisible(has)
        self.zoom_label.setVisible(has)
        self.zoom.blockSignals(True)
        self.zoom.setValue(int(self.cover.zoom * 100))
        self.zoom.blockSignals(False)

    def _crop_changed(self) -> None:
        self.zoom.blockSignals(True)
        self.zoom.setValue(int(round(self.cover.zoom * 100)))
        self.zoom.blockSignals(False)

    def _pick_art(self):
        f, _ = QFileDialog.getOpenFileName(self, tr("file.pick_cover"), "", tr("file.images"))
        if f and QImage(f).isNull():
            message(self, tr("bg.bad_image"))          # damaged, or not really an image
            return
        if f:
            self.art_src, self.art_from, self.remove_art = f, "user", False
            self._show_cover(f)
            self.banner_box.hide()

    def _remove_art(self):
        self.art_src, self.remove_art = None, True
        self._show_cover(None)
        self.banner_box.hide()

    def _open_vndb(self):
        d = VndbDialog(self.title.text().strip(), self, safe_only=self.settings.vndb_safe_only)
        if d.exec() and d.choice:
            self._apply_vndb(d.choice, d.cover_file, d.use_title)

    def _apply_vndb(self, m: vndb.Match, cover_file: Optional[str], use_title: bool):
        if cover_file:
            self.art_src, self.art_from, self.remove_art = cover_file, "vndb", False
            self._show_cover(cover_file)
        if use_title:
            self.title.setText(m.display_title)
            self.title_locked = True
            self._update_auto_btn()
        self.vndb_id = m.vndb_id
        self.banner_box.hide()

    def _auto_vndb(self, query: str):
        def work():
            for m in vndb.search(query, limit=3, safe_only=self.settings.vndb_safe_only):
                if m.image_url:
                    return m, _download_cover(m.image_url)
            return None
        self._run(work, self._suggest)

    def _suggest(self, found):
        if not found or self.art_src:
            return
        m, path = found
        self._suggestion = (m, path)
        self.banner.setText(tr("game.vndb_suggest", title=m.display_title))
        self.banner.setToolTip(m.title)
        pm = QPixmap(path)
        if not pm.isNull():
            self.banner_btn.setIcon(QIcon(pm.scaled(36, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            self.banner_btn.setIconSize(QSize(24, 34))
        self.banner_box.show()

    def _use_suggestion(self):
        if self._suggestion:
            m, path = self._suggestion
            self._apply_vndb(m, path, use_title=True)

    # -- paths
    def _pick_exe(self, edit: QLineEdit):
        f, _ = QFileDialog.getOpenFileName(self, tr("file.pick_exe"), edit.text(), tr("file.programs"))
        if f:
            exe = lnk.resolve_game_exe(f) or f
            edit.setText(str(Path(exe)))
            if not self.title.text().strip() or not self.title_locked:
                self.title.setText(guess_title(exe))

    def _pick_save(self, edit: QLineEdit):
        exe = self.fields[self.machine][0].text()
        start = edit.text() or (str(Path(exe).parent) if exe else os.environ.get("APPDATA", ""))
        d = QFileDialog.getExistingDirectory(self, tr("file.pick_saves"), start)
        if d:
            edit.setText(str(Path(d)))

    def _shared_save_folder(self, save_dir: str) -> Optional[Game]:
        """Another game whose save folder on This Device is the same as, inside, or around this one."""
        n = os.path.normcase(os.path.abspath(save_dir)).rstrip("\\/")
        for g in self.library.list_games():
            if self.game and g.game_id == self.game.game_id:
                continue
            o = g.paths_for(self.machine).save_dir
            if o:
                o = os.path.normcase(os.path.abspath(o)).rstrip("\\/")
                if o == n or o.startswith(n + os.sep) or n.startswith(o + os.sep):
                    return g
        return None

    @guarded
    def _remove(self):
        if confirm_remove(self, self.game.title):
            self.library.remove(self.game.game_id)
            self.removed = True
            self.accept()

    def _save(self):
        paths = {slot: MachinePaths(e.text().strip(), s.text().strip()) for slot, (e, s) in self.fields.items()}
        mine = paths[self.machine]
        if mine.exe and not Path(mine.exe).is_file():
            message(self, tr("game.exe_missing", path=mine.exe))
            return
        if mine.save_dir:
            problem = save_folder_problem(mine.save_dir, self.settings.cloud_root)
            if problem:
                message(self, tr(problem))
                return
            other = self._shared_save_folder(mine.save_dir)
            if other and not ask_yes_no(self, tr("error.save_shared", title=other.title),
                                        tr("error.use_anyway"), tr("game.cancel")):
                return
        reg = self.registry.text().strip()
        if reg:
            try:
                reg = regsync.display(reg)
            except regsync.RegistryKeyNotAllowed:
                message(self, tr("error.registry"))
                return
        zoom, cx, cy = self.cover.crop()
        if self.title_locked:
            self.manual_title = self.title.text().strip()     # the own title, for toggling back later
        extra = dict(use_luna=self.use_luna.isChecked(), use_le=self.use_le.isChecked(),
                     status=self.status.currentData(), title_locked=self.title_locked,
                     manual_title=self.manual_title,
                     registry_key=reg, vndb_id=self.vndb_id,
                     crop_zoom=round(zoom, 4), crop_x=round(cx, 4), crop_y=round(cy, 4))
        try:
            if self.game:
                g = self.library.edit(self.game.game_id, self.title.text(), paths, self.art_src,
                                      artwork_from=self.art_from, **extra)
                if self.remove_art and not self.art_src:
                    g = self.library.clear_artwork(g.game_id)
            else:
                g = self.library.add_game(self.title.text(), self.machine, artwork_src=self.art_src,
                                          artwork_from=self.art_from if self.art_src else "", **extra)
                g = self.library.edit(g.game_id, g.title, {s: p for s, p in paths.items() if p.exe or p.save_dir})
        except (ValueError, KeyError, OSError) as e:
            report_failure(self, e)
            return
        self.saved_id = g.game_id
        self.accept()


class NameDialog(QDialog):
    """Ask for a folder name."""

    def __init__(self, title: str, name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.addWidget(section(tr("folder.name")))
        self.edit = QLineEdit(name)
        self.edit.selectAll()
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(tr("game.cancel"))
        cancel.clicked.connect(self.reject)
        ok = QPushButton(tr("game.save"))
        ok.setDefault(True)
        ok.clicked.connect(lambda: self.accept() if self.edit.text().strip() else None)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addSpacing(8)
        lay.addLayout(row)

    def name(self) -> str:
        return self.edit.text().strip()


class ConflictDialog(QDialog):
    def __init__(self, title: str, info: ConflictInfo, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("conflict.title"))
        self.setMinimumWidth(480)
        self.choice: Optional[str] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        body = QLabel(tr("conflict.body", title=html.escape(title)))
        body.setWordWrap(True)
        lay.addWidget(body)
        newer = "local" if (info.local_mtime or 0) >= (info.cloud_mtime or 0) else "cloud"
        for key, label, t in (("local", tr("conflict.local"), info.local_mtime),
                              ("cloud", tr("conflict.cloud"), info.cloud_mtime)):
            when = i18n.datetime_str(t) if t else tr("conflict.none")
            b = QPushButton(f"{label}\n{tr('conflict.last_saved', date=when)}" +
                            (f"   {tr('conflict.newer')}" if key == newer else ""))
            b.setStyleSheet("text-align: left; padding: 10px 14px;")
            if key == newer:
                b.setDefault(True)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            lay.addWidget(b)
        lay.addWidget(hint(tr("conflict.backup_note")))
        later = QPushButton(tr("conflict.later"))
        later.clicked.connect(self.reject)
        lay.addWidget(later)

    def _pick(self, key: str):
        self.choice = key
        self.accept()


class DetectDialog(QDialog):
    """After a first play without a save folder: confirm where the game saves."""

    def __init__(self, game: Game, candidates: list, app_dir: Path, parent=None, cloud_root: str = ""):
        super().__init__(parent)
        self.cloud_root = cloud_root
        self.setWindowTitle(tr("detect.title"))
        self.setMinimumWidth(620)
        self.game = game
        self.save_dir: Optional[str] = None
        self.registry_key = ""
        from sync_engine import safe_game_id
        self._registry_only_dir = str(app_dir / "registry-only" / safe_game_id(game.game_id))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        folders = [c for c in candidates if c.kind == "folder"]
        regs = [c for c in candidates if c.kind == "registry"]
        n_options = len(folders) if folders else (1 if regs else 0)
        key = "detect.none_body" if not candidates else ("detect.body_one" if n_options == 1 else "detect.body_many")
        body = QLabel(tr(key, title=html.escape(game.title)))
        body.setWordWrap(True)
        lay.addWidget(body)
        self.group = QButtonGroup(self)
        for i, c in enumerate(folders):
            rb = QRadioButton(f"{c.path}\n{tr('detect.files', n=c.files)}")
            rb.setProperty("path", c.path)
            rb.setChecked(i == 0)
            self.group.addButton(rb)
            lay.addWidget(rb)
        if regs and not folders:
            rb = QRadioButton(tr("detect.registry_only"))
            rb.setProperty("path", self._registry_only_dir)
            rb.setChecked(True)
            self.group.addButton(rb)
            lay.addWidget(rb)
        self.reg_box = None
        if regs:
            self.reg_box = QCheckBox(tr("detect.registry", key=regs[0].path))
            self.reg_box.setToolTip(tr("detect.registry_tip"))
            self.reg_box.setChecked(not folders)
            self.reg_box.setProperty("key", regs[0].path)
            lay.addWidget(self.reg_box)
        row = QHBoxLayout()
        other = QPushButton(tr("detect.browse"))
        other.clicked.connect(self._browse)
        row.addWidget(other)
        row.addStretch()
        later = QPushButton(tr("detect.later"))
        later.clicked.connect(self.reject)
        row.addWidget(later)
        if candidates:
            use = QPushButton(tr("detect.use"))
            use.setDefault(True)
            use.clicked.connect(self._use)
            row.addWidget(use)
        lay.addSpacing(8)
        lay.addLayout(row)

    def _registry_choice(self) -> str:
        return self.reg_box.property("key") if self.reg_box and self.reg_box.isChecked() else ""

    def _ok(self, path: str) -> bool:
        problem = save_folder_problem(path, self.cloud_root)
        if problem:
            message(self, tr(problem))
            return False
        return True

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, tr("file.pick_saves"), os.environ.get("APPDATA", ""))
        if d and self._ok(str(Path(d))):
            self.save_dir = str(Path(d))
            self.registry_key = self._registry_choice()
            self.accept()

    def _use(self):
        b = self.group.checkedButton()
        self.save_dir = b.property("path") if b else None
        self.registry_key = self._registry_choice()
        if self.save_dir and not self._ok(self.save_dir):
            self.save_dir = None
            return
        if self.save_dir:
            try:
                Path(self.save_dir).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                report_failure(self, e)
                return
            self.accept()


class RestoreDialog(QDialog):
    def __init__(self, game: Game, engine: SyncEngine, save_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("restore.title"))
        self.setMinimumSize(520, 420)
        self.game, self.engine, self.save_dir = game, engine, save_dir
        self.restored = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        body = QLabel(tr("restore.body", title=html.escape(game.title)))
        body.setWordWrap(True)
        lay.addWidget(body)
        self.list = QListWidget()
        self.list.setStyleSheet(f"QListWidget {{ background: {COLORS['input']}; border-radius: 6px; padding: 4px; }}"
                                f"QListWidget::item {{ padding: 8px; border-radius: 4px; }}"
                                f"QListWidget::item:selected {{ background: {COLORS['button_hover']}; }}")
        backups = engine.list_backups(game.game_id)
        for b in backups:
            key = f"restore.reason.{b.reason}"
            reason = tr(key) if key in i18n.S else b.reason
            it = QListWidgetItem(f"{i18n.datetime_str(b.when.timestamp())}   ·   {reason}")
            it.setData(Qt.UserRole, b)
            self.list.addItem(it)
        if backups:
            self.list.setCurrentRow(0)
        lay.addWidget(self.list, 1)
        if not backups:
            lay.addWidget(hint(tr("restore.none")))
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton(tr("game.cancel"))
        cancel.clicked.connect(self.reject)
        go = QPushButton(tr("restore.restore"))
        go.setEnabled(bool(backups))
        go.clicked.connect(self._restore)
        row.addWidget(cancel)
        row.addWidget(go)
        lay.addLayout(row)

    def _restore(self):
        it = self.list.currentItem()
        if not it:
            return
        b = it.data(Qt.UserRole)
        if not ask_yes_no(self, tr("restore.confirm", title=self.game.title,
                                   date=i18n.datetime_str(b.when.timestamp())),
                          tr("restore.restore"), tr("game.cancel")):
            return
        try:
            self.engine.restore(self.game.game_id, b.name, self.save_dir, self.game.registry_key)
        except (OSError, ValueError) as e:
            report_failure(self, e if isinstance(e, ValueError) and str(e) in i18n.S else OSError())
            return
        self.restored = True
        self.accept()


# =========================================================================== main window
class MainWindow(TaskOwner, QMainWindow):
    def __init__(self, settings: LocalSettings):
        super().__init__()
        self.setWindowIcon(app_icon())
        self.resize(1200, 800)
        self.setMinimumHeight(520)          # room for the bars and at least one row of covers
        self.setAcceptDrops(True)
        self.covers = CoverCache()
        self.games: dict[str, Game] = {}
        self.sync_states: dict[str, str] = {}
        self.hover: dict[str, float] = {}       # game_id -> 0..1 hover progress
        self.hovered: Optional[str] = None
        self.worker: Optional[PlayWorker] = None
        self.checker: Optional[StatusChecker] = None
        self._recheck = False
        self._first_check = True
        self.playing_id: Optional[str] = None
        self.play_phase = "done"
        self.current_folder: Optional[str] = None     # folder id being viewed, None = the library
        self.folder_names: dict[str, str] = {}
        self.folder_games: dict[str, list[Game]] = {}
        self._press: Optional[tuple] = None           # (position, game id) for starting a drag
        self._ghost: Optional[DragGhost] = None       # the cover following the mouse while a game is dragged
        self._dragging: Optional[str] = None
        self._dim_gid: Optional[str] = None           # the picked-up game, faded in its place
        self.drag_dim = 0.0                           # 0..1 how far it has faded

        central = Backdrop(self)
        self.backdrop = central
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- top bar
        top = QFrame(objectName="band")
        self.topbar = top
        tl = QHBoxLayout(top)
        tl.setContentsMargins(16, 10, 16, 10)
        tl.setSpacing(10)
        self.logo = Logo()
        self.logo.clicked.connect(self.easter_egg)
        tl.addWidget(self.logo)
        tl.addSpacing(10)
        self.search = SearchBox()
        self.search.setMinimumWidth(120)
        self.search.setMaximumWidth(220)
        self.search.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.search.textChanged.connect(self._apply_filter)
        tl.addWidget(self.search)
        self.sort = QComboBox()
        self.sort.currentIndexChanged.connect(self._sort_changed)
        tl.addWidget(self.sort)
        self.filter = QComboBox()
        self.filter.currentIndexChanged.connect(self._filter_changed)
        tl.addWidget(self.filter)
        self.stats_btn = QPushButton()
        self.stats_btn.clicked.connect(self._toggle_stats)
        tl.addWidget(self.stats_btn)
        # the sync status sits centred between the drop-downs and the buttons
        tl.addStretch(1)
        status = QHBoxLayout()                 # light + text kept together (the light's box holds its glow)
        status.setSpacing(0)
        self.sync_light = SyncLight(self)
        status.addWidget(self.sync_light)
        self.sync_label = QLabel()
        self.sync_label.linkActivated.connect(self._show_next_conflict)
        status.addWidget(self.sync_label)
        # the light's box keeps ~9 px of empty room (for its glow) left of the dot; the same space after
        # the text keeps what you see (dot + text) exactly centred between the drop-downs and the buttons
        status.addSpacing(9)
        tl.addLayout(status)
        tl.addStretch(1)
        self.add_btn = StarButton()                              # the star from the logo
        self.add_btn.clicked.connect(self.add_game)
        self.settings_btn = StarButton()
        self.settings_btn.clicked.connect(self.open_settings)
        tl.addWidget(self.add_btn)
        tl.addWidget(self.settings_btn)
        root.addWidget(top)

        # -- folder bar (only inside a folder): back to the library + the folder's name
        self.folderbar = QFrame(objectName="folderbar")
        fb = QHBoxLayout(self.folderbar)
        fb.setContentsMargins(14, 10, 16, 0)
        self.back_btn = QPushButton(objectName="back")
        self.back_btn.clicked.connect(self.close_folder)
        self.back_btn.setAcceptDrops(True)
        self.back_btn.installEventFilter(self)
        fb.addWidget(self.back_btn)
        self.folder_title = QLabel(objectName="title")
        self.folder_title.setTextFormat(Qt.PlainText)
        fb.addSpacing(6)
        fb.addWidget(self.folder_title, 1)
        self.folderbar.hide()
        root.addWidget(self.folderbar)

        # -- grid
        self.grid = LibraryGrid(self)
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setGridSize(QSize(CARD_W + GRID_SPACING, CARD_H + GRID_SPACING))
        self.grid.setSpacing(0)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setMouseTracking(True)
        self.grid.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.grid.verticalScrollBar().setSingleStep(24)
        # scroll bar: hidden until needed (fades in on scroll / near the right edge / hover)
        self.sb_alpha = 0.0
        self._sb_until = 0.0
        self._sb_hover = False
        sb = self.grid.verticalScrollBar()
        sb.valueChanged.connect(lambda _: self._show_scrollbar())
        sb.installEventFilter(self)
        self._style_scrollbar()
        self.grid.setViewportMargins(14, 8, 10, 8)         # 14 + the cover's own inset = 29 px, the same as between covers
        self.grid.setItemDelegate(CardDelegate(self))
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.grid.itemDoubleClicked.connect(lambda _: self.activate_selected())
        self.grid.currentItemChanged.connect(lambda *_: self._update_details())
        self.grid.itemSelectionChanged.connect(self._update_details)
        self.grid.viewport().installEventFilter(self)
        self.grid.viewport().setAcceptDrops(True)
        root.addWidget(self.grid, 1)

        self.empty = QLabel(alignment=Qt.AlignCenter, objectName="muted")
        self.empty.setWordWrap(True)
        self.empty.setAcceptDrops(True)
        self.empty.installEventFilter(self)
        self.empty.hide()
        root.addWidget(self.empty, 1)

        # -- bottom bar
        self.details = QFrame(objectName="band")
        dl = QHBoxLayout(self.details)
        # the status line below makes the dark band 22 px taller; shift the contents down by half of
        # that so they sit centred in the whole band (14 + 11 above, 14 - 11 below)
        dl.setContentsMargins(22, 25, 22, 3)
        dl.setSpacing(12)
        self.play_btn = QPushButton(objectName="play")
        self.play_btn.clicked.connect(lambda: self.activate_selected())
        dl.addWidget(self.play_btn)
        dl.addSpacing(10)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.d_title = QLabel(objectName="title")
        self.d_title.setTextFormat(Qt.PlainText)
        self.d_sync = QLabel()                 # sync status, then playtime and last played, on one line
        for w in (self.d_title, self.d_sync):
            w.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            info.addWidget(w)
        dl.addLayout(info, 1)
        self.d_status = QComboBox()
        self.d_status.currentIndexChanged.connect(self._status_changed)
        dl.addWidget(self.d_status)
        self.saves_btn = QPushButton()
        self.saves_btn.clicked.connect(self._open_saves)
        self.edit_btn = QPushButton()
        self.edit_btn.clicked.connect(self.edit_selected)
        self.trash_btn = TrashButton()
        self.trash_btn.clicked.connect(lambda: self.remove_game(self.selected_id() or ""))
        self.trash_btn.installEventFilter(self)       # games can be dropped on it
        dl.addWidget(self.saves_btn)
        dl.addWidget(self.edit_btn)
        dl.addWidget(self.trash_btn)
        root.addWidget(self.details)
        # create the status line now (Qt makes it on the first message otherwise): the band then
        # always has the same height, so the bar's contents stay centred and nothing jumps
        self.statusBar().setSizeGripEnabled(False)

        refresh = QAction(self)
        refresh.setShortcut(QKeySequence.Refresh)
        refresh.triggered.connect(self.refresh)
        self.addAction(refresh)
        for key in (Qt.Key_Escape, Qt.Key_Backspace, QKeySequence.Back):
            back = QAction(self)
            back.setShortcut(QKeySequence(key))
            back.triggered.connect(self.close_folder)
            self.addAction(back)
        enter = QAction(self)
        enter.setShortcuts([QKeySequence(Qt.Key_Return), QKeySequence(Qt.Key_Enter)])
        enter.triggered.connect(self.activate_selected)
        self.grid.addAction(enter)

        # animation: hover easing + breathing lights
        self.anim = QTimer(self)
        self.anim.setInterval(16)
        self.anim.timeout.connect(self._tick)
        self._last_tick = time.monotonic()

        # live refresh: notice what the other device changes in the cloud folder (checked in the background)
        self._live_sig = None
        self._live_checking = False
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(4000)
        self._live_timer.timeout.connect(self._live_poll)
        self.apply_settings(settings)
        self._run(warm_up, lambda _: None)          # makes the first Play react instantly
        self._live_timer.start()

    def restyle(self) -> None:
        """Redraw everything that isn't covered by the style sheet after a theme change."""
        if not hasattr(self, "anim"):
            return
        self.logo.refresh()
        self.add_btn.refresh_icon()
        self.settings_btn.refresh_icon()
        self.trash_btn.refresh_icon()
        self.setWindowIcon(app_icon())
        self._style_scrollbar()
        self.grid.setItemDelegate(CardDelegate(self))     # fresh shadow etc.
        self._update_sync_label()
        self._update_details()
        self.grid.viewport().update()
        self.sync_light.update()

    # ------------------------------------------------------------------ text
    def preview_language(self) -> None:
        """The language was switched in Settings (not saved yet): re-label everything."""
        self.retranslate()
        self._update_sync_label()
        self._update_details()
        if not self.games:
            self.empty.setText(tr("grid.no_cloud", path=self.settings.cloud_root)
                               if not Path(self.settings.cloud_root).is_dir() else tr("grid.empty"))
        self.grid.viewport().update()
        self._styled_fonts = ui_families()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("app.name"))
        self.search.setPlaceholderText(tr("top.search"))
        self.add_btn.setText(tr("top.add"))
        self.settings_btn.setText(tr("top.settings"))
        self.play_btn.setText(tr("bar.play"))
        self.saves_btn.setText(tr("bar.open_saves"))
        self.edit_btn.setText(tr("bar.edit"))
        self.back_btn.setText(tr("folder.back"))
        self.back_btn.setToolTip(tr("folder.back_tip"))
        self.trash_btn.setToolTip(tr("bar.trash_tip"))
        self.stats_btn.setText(tr("top.hide_stats" if self.settings.show_stats else "top.show_stats"))
        self.stats_btn.setToolTip(tr("top.stats_tip"))
        for combo, items, current in (
                (self.sort, [(tr(f"sort.{s}"), s) for s in ("recent", "hours", "title")], self.settings.sort),
                (self.filter, [(tr("filter.all"), "all")] + [(tr(f"status.{s}"), s) for s in STATUSES[1:]],
                 self.settings.status_filter),
                (self.d_status, [(tr(f"status.{s or 'none'}"), s) for s in STATUSES], None)):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)   # resize when the language changes
            combo.blockSignals(True)
            combo.clear()
            for label, data in items:
                combo.addItem(label, data)
            if current is not None:
                combo.setCurrentIndex(max(0, combo.findData(current)))
            combo.blockSignals(False)
            combo.ensurePolished()                          # measure with the real font and padding
            if combo in (self.sort, self.filter):
                # the top bar's two drop-downs: just as wide as their longest choice needs (leaving room for the
                # statistics button). Qt itself says how much of the width its text area gets.
                from PySide6.QtWidgets import QStyleOptionComboBox
                opt = QStyleOptionComboBox()
                combo.initStyleOption(opt)
                field = combo.style().subControlRect(QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxEditField, combo)
                chrome = combo.width() - field.width()          # padding, border and the arrow's space
                fm = combo.fontMetrics()
                longest = max(fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count()))
                combo.setFixedWidth(longest + chrome + 6)       # 6 px to spare
            else:
                # Qt keeps an old minimum otherwise; +12 because its estimate is a few px short with our arrow
                combo.setFixedWidth(combo.sizeHint().width() + 12)
        for b in (self.add_btn, self.settings_btn, self.saves_btn, self.edit_btn, self.play_btn, self.stats_btn):
            b.updateGeometry()

    # ------------------------------------------------------------------ data
    def apply_settings(self, settings: LocalSettings) -> None:
        self.settings = settings
        i18n.set_language(settings.language)
        set_qt_language(settings.language)
        apply_background(settings.bg_image, settings.bg_opacity, settings.bg_zoom, settings.bg_x, settings.bg_y)
        fonts_before = ui_families()
        apply_fonts(settings.font_en, settings.font_ja)
        full = theme_colors(settings.theme, settings.custom_colors)
        if COLORS != {k: full[k] for k in DARK} or LOGO != {k: full[k] for k in LOGO_ORIGINAL} \
                or LIGHTS != {k: full[f"light_{k}"] for k in BASE_LIGHTS}:
            apply_theme(settings.theme, settings.custom_colors)
            restyle_all()
        elif ui_families() != fonts_before or getattr(self, "_styled_fonts", None) != ui_families():
            restyle_all()                       # a different font (or language, which changes the order)
        self._styled_fonts = ui_families()
        self.app_dir = default_app_dir()
        self.library = Library(settings.cloud_root)
        self.engine = SyncEngine(settings.cloud_root, self.app_dir, computers=self.library.known_computers)
        try:
            owner = self.library.slot_owner(settings.machine_name)
            if owner is None or owner.lower() == this_computer().lower():
                self.library.claim_slot(settings.machine_name)
        except OSError:
            pass
        self.retranslate()
        self.refresh()
        self._update_anim()
        self.share_settings()

    def easter_egg(self) -> None:
        if self.findChild(EasterEgg) is None:
            EasterEgg(self)

    def share_settings(self) -> None:
        """Copy this device's personal settings into the cloud folder for Ikemen Sync (in the background:
        a big background picture or font file can take a moment the first time)."""
        s = dataclasses.replace(self.settings, custom_colors=dict(self.settings.custom_colors))
        fonts = dict(FONT_FILES)

        def work():
            if Path(s.cloud_root).is_dir():
                devsync.publish(s, this_computer(), fonts)

        def failed(e: Exception) -> None:                    # never in the way, but noted for later
            try:
                import traceback
                with open(self.app_dir / "share_error.log", "a", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "".join(traceback.format_exception(e)) + "\n")
            except OSError:
                pass
        self._run(work, lambda _: None, failed)

    def refresh(self) -> None:
        """Re-read the library from the cloud folder, then rebuild the grid."""
        games = self.library.list_games() if Path(self.settings.cloud_root).is_dir() else []
        self.games = {g.game_id: g for g in games}
        self.folder_names = self.library.list_folders() if games else {}
        self._rebuild()
        if not Path(self.settings.cloud_root).is_dir():
            self.empty.setText(tr("grid.no_cloud", path=self.settings.cloud_root))
        else:
            self.empty.setText(tr("grid.empty"))
        self.empty.setVisible(not games)
        self.grid.setVisible(bool(games))
        self.details.setVisible(bool(games))
        self._check_sync()
        try:
            self._live_sig = self.library.change_signature()
        except OSError:
            self._live_sig = None

    def _live_poll(self) -> None:
        if self._live_checking:
            return
        self._live_checking = True
        library = self.library

        def done(sig) -> None:
            self._live_checking = False
            if library is not self.library or sig == self._live_sig:
                return                              # (settings changed meanwhile, or nothing new)
            if self._ghost is not None or self.worker is not None or QApplication.activePopupWidget() is not None \
                    or QApplication.activeModalWidget() is not None:
                return                              # mid-drag / a game running / a menu or window open: next time
            self.refresh()
            self.statusBar().showMessage(tr("status.live_update"), 6000)

        def failed(_e) -> None:
            self._live_checking = False
        self._run(library.change_signature, done, failed)

    def _check_sync(self) -> None:
        if self.checker and self.checker.isRunning():
            self._recheck = True  # one at a time; check again when the running one finishes
            return
        self._recheck = False
        for gid in self.games:
            self.sync_states.setdefault(gid, "checking")
        self.checker = StatusChecker(self.engine, list(self.games.values()), self.settings.machine_name,
                                     upload=self._first_check)
        self._first_check = False
        self.checker.done.connect(self._sync_checked)
        self.checker.finished.connect(self._update_sync_label)  # isRunning() is reliably False by then
        self.checker.start()
        self._update_details()
        self._update_sync_label()

    def _sync_checked(self, states: dict, uploaded: list) -> None:
        self.sync_states = dict(states)
        if uploaded:                                # our own upload: not a change from the other device
            try:
                self._live_sig = self.library.change_signature()
            except OSError:
                pass
        if uploaded:
            sep = "、" if i18n.language() == "ja" else ", "
            self.statusBar().showMessage(tr("status.startup_uploaded", games=sep.join(uploaded)), 10000)
        self.grid.viewport().update()
        self._update_details()
        self._update_sync_label()
        if self._recheck:
            self._check_sync()

    def folder_name(self, fid: str) -> str:
        return self.folder_names.get(fid) or tr("folder.new_name")

    def _rebuild(self, select: Optional[str] = None) -> None:
        """Fill the grid: the library (games + folder tiles), or the open folder's games. While
        searching or filtering, games inside folders are shown directly so nothing is hidden."""
        current = select or self.selected_id()
        games = list(self.games.values())
        q = self.search.text().strip().lower()
        status = self.settings.status_filter
        sort = self.settings.sort

        self.folder_games = {}
        for g in sort_games(games, sort):
            if g.folder:
                self.folder_games.setdefault(g.folder, []).append(g)
        if self.current_folder and self.current_folder not in self.folder_games:
            self.current_folder = None                    # it was emptied or removed
        flat = bool(q) or status != "all"

        def keep(g: Game) -> bool:
            return (not q or q in g.title.lower()) and (status == "all" or g.status == status)

        if self.current_folder:
            entries = [("game", g) for g in self.folder_games[self.current_folder] if keep(g)]
        elif flat:
            entries = [("game", g) for g in sort_games(games, sort) if keep(g)]
        else:
            entries = [("game", g) for g in games if not g.folder]
            entries += [("folder", fid) for fid in self.folder_games]
            entries.sort(key=lambda e: self._sort_key(e))

        scroll = self.grid.verticalScrollBar().value()       # keep your place (a refresh mustn't jump to the top)
        self.grid.clear()
        for kind, obj in entries:
            it = QListWidgetItem()
            iid = obj.game_id if kind == "game" else FOLDER_PREFIX + obj
            it.setData(GAME_ID_ROLE, iid)
            it.setData(Qt.DisplayRole, obj.title if kind == "game" else self.folder_name(obj))
            it.setSizeHint(QSize(CARD_W, CARD_H))
            self.grid.addItem(it)
            if iid == current:
                self.grid.setCurrentItem(it)
                it.setSelected(True)
        if current is None:
            self.grid.clearSelection()          # nothing was selected: keep it that way
        if select is None:
            self.grid.doItemsLayout()
            self.grid.verticalScrollBar().setValue(scroll)

        inside = self.current_folder is not None
        self.folderbar.setVisible(inside)
        if inside:
            self.folder_title.setText(self.folder_name(self.current_folder))
        self._update_details()

    def _sort_key(self, entry) -> tuple:
        from library import title_sort_key
        kind, obj = entry
        if kind == "game":
            last, hours, title = obj.last_played, obj.total_playtime, obj.title
        else:
            gs = self.folder_games[obj]
            last = max(g.last_played for g in gs)
            hours = sum(g.total_playtime for g in gs)
            title = self.folder_name(obj)
        if self.settings.sort == "hours":
            return (-hours, title_sort_key(title))
        if self.settings.sort == "title":
            return (title_sort_key(title),)
        return (-last, title_sort_key(title))

    def selected_id(self) -> Optional[str]:
        items = self.grid.selectedItems()
        return items[0].data(GAME_ID_ROLE) if items else None

    def clear_selection(self) -> None:
        self.grid.clearSelection()
        self.grid.setCurrentItem(None)
        self._update_details()

    def selected_folder(self) -> Optional[str]:
        iid = self.selected_id()
        return iid[len(FOLDER_PREFIX):] if is_folder(iid) else None

    def selected(self) -> Optional[Game]:
        return self.games.get(self.selected_id() or "")

    def open_folder(self, fid: str) -> None:
        self.current_folder = fid
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._rebuild()
        self.grid.scrollToTop()

    def close_folder(self) -> None:
        egg = self.findChild(EasterEgg)
        if egg is not None:                         # Esc on the logo's dark screen: back to the library
            egg.dismiss()
            return
        if self._ghost is not None:                 # Esc while dragging a game: put it back
            self._end_game_drag(QCursor.pos(), drop=False)
            return
        if self.current_folder:
            fid, self.current_folder = self.current_folder, None
            self._rebuild(select=FOLDER_PREFIX + fid)
        else:
            self.clear_selection()

    def activate_selected(self) -> None:
        """Double-click / Enter / the big button: open a folder, or play a game."""
        fid = self.selected_folder()
        if fid:
            self.open_folder(fid)
        else:
            self.play_selected()

    def quiet(self, gid: str) -> bool:
        """No playtime / reading status while the game is running, or before it has any save data
        (the "Save at least once!" state): only the message matters then."""
        return gid == self.playing_id or self.sync_states.get(gid) == "detect"

    def light_state(self, gid: str) -> str:
        if is_folder(gid):
            states = {self.light_state(g.game_id) for g in self.folder_games.get(gid[len(FOLDER_PREFIX):], [])}
            return next((s for s in STATE_PRIORITY if s in states), "synced")
        if gid == self.playing_id:
            return "syncing" if self.play_phase == "syncing" else "playing"
        return self.sync_states.get(gid, "checking")

    # ------------------------------------------------------------------ sort / filter
    def _apply_filter(self) -> None:
        self._rebuild()

    def _toggle_stats(self) -> None:
        """Hide Statistics / Show Statistics: playtime and last played, on the covers and the bottom bar."""
        self.settings.show_stats = not self.settings.show_stats
        self.stats_btn.setText(tr("top.hide_stats" if self.settings.show_stats else "top.show_stats"))
        self._save_view_choice()                 # remembered for next time (and shared by Ikemen Sync)
        self.grid.viewport().update()
        self._update_details()

    def _save_view_choice(self) -> None:
        try:
            save_local_settings(self.settings)
        except OSError:
            return              # locked for a moment: it's saved with the next change anyway
        self.share_settings()

    def _sort_changed(self, _i: int) -> None:
        self.settings.sort = self.sort.currentData() or "recent"
        self._save_view_choice()
        self.refresh()

    def _filter_changed(self, _i: int) -> None:
        self.settings.status_filter = self.filter.currentData() or "all"
        self._save_view_choice()
        self._apply_filter()

    @guarded
    def _status_changed(self, _i: int) -> None:
        g = self.selected()
        if g is None:
            return
        s = self.d_status.currentData() or ""
        if s != g.status:
            self.games[g.game_id] = self.library.update(g.game_id, status=s)
            if self.settings.status_filter != "all":
                self._rebuild()

    # ------------------------------------------------------------------ bottom bar / top status
    def _update_details(self) -> None:
        fid = self.selected_folder()
        if fid:
            n = len(self.folder_games.get(fid, []))
            self.d_title.setText(self.folder_name(fid))
            count = tr("folder.count_one") if n == 1 else tr("folder.count_many", n=n)
            self.d_sync.setText(f'<span style="color:{COLORS["muted"]}">{html.escape(count)}</span>')
            self.d_sync.setToolTip("")
            self.play_btn.setText(tr("bar.open"))
            self.play_btn.setEnabled(True)
            self.play_btn.setToolTip("")
            self.edit_btn.setText(tr("bar.rename"))
            self.edit_btn.setEnabled(True)
            self.trash_btn.setEnabled(False)          # folders aren't removed this way
            self.saves_btn.hide()
            self.d_status.hide()
            return
        self.saves_btn.show()
        self.edit_btn.setText(tr("bar.edit"))
        g = self.selected()
        busy = self.worker is not None
        for w in (self.play_btn, self.edit_btn, self.saves_btn, self.d_status):
            w.setEnabled(g is not None)
        self.trash_btn.setEnabled(g is not None and not busy)
        if g is None:                               # nothing selected: an empty bar, buttons greyed out
            for lab in (self.d_title, self.d_sync):
                lab.setText("")
            self.d_sync.setToolTip("")
            self.play_btn.setText(tr("bar.play"))
            self.play_btn.setToolTip("")
            self.d_status.setVisible(True)
            return
        self.d_title.setText(g.title)
        parts = [tr("bar.played", time=i18n.playtime(g.total_playtime)) if g.total_playtime else tr("card.never")]
        if g.last_played:
            parts.append(tr("bar.last_played", date=i18n.date(g.last_played)))
        colour_key, _, text_key = STATE_LOOK[self.light_state(g.game_id)]
        sep = "&nbsp;&nbsp;·&nbsp;&nbsp;"
        quiet = self.quiet(g.game_id)
        extra = "" if (quiet or not self.settings.show_stats) else f'<span style="color:{COLORS["muted"]}">{sep}{sep.join(html.escape(s) for s in parts)}</span>'
        main = f"{dot(colour_key)} {html.escape(tr(text_key))}"
        # safety net for small windows: drop the playtime part before the sync message gets cut off
        if extra and self._html_width(main + extra) > self.d_sync.width() > 0:
            extra = ""
        self.d_sync.setText(main + extra)
        tip = text_key.replace("sync.state.", "sync.tip.")
        self.d_sync.setToolTip(tr(tip) if tip in i18n.S else "")
        self.d_status.setVisible(not quiet)
        self.d_status.blockSignals(True)
        self.d_status.setCurrentIndex(max(0, self.d_status.findData(g.status)))
        self.d_status.blockSignals(False)

        mine = g.paths_for(self.settings.machine_name)
        ready = bool(mine.exe)
        self.play_btn.setText(tr("bar.playing") if g.game_id == self.playing_id else tr("bar.play"))
        self.play_btn.setEnabled(ready and not busy)
        self.saves_btn.setEnabled(bool(mine.save_dir) and Path(mine.save_dir).is_dir())
        self.play_btn.setToolTip(tr("bar.tip_busy") if busy else ("" if ready else tr("bar.tip_no_exe")))

    def _html_width(self, text: str) -> int:
        from PySide6.QtGui import QTextDocument
        doc = QTextDocument()
        doc.setDefaultFont(self.d_sync.font())
        doc.setDocumentMargin(0)
        doc.setHtml(text)
        return int(doc.idealWidth()) + 2

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._update_details()          # re-check whether the playtime still fits

    def _update_sync_label(self) -> None:
        """The one-line status in the top bar. Details (device, cloud folder) live in Settings."""
        checking = self.checker is not None and self.checker.isRunning()
        conflicts = sum(1 for s in self.sync_states.values() if s == "conflict")
        tip = ""
        if self.play_phase == "syncing" or checking:
            text = tr("top.syncing")
            self.sync_light.set_state("blue", "busy")
        elif conflicts:
            label = tr("top.attention_one") if conflicts == 1 else tr("top.attention_many", n=conflicts)
            text = (f'<a href="conflict" style="color:{COLORS["text"]}; text-decoration:none">'
                    f"{label}</a>")
            tip = tr("top.attention_tip")
            self.sync_light.set_state("red", "steady")
        else:
            text = tr("top.synced")
            self.sync_light.set_state("green", "calm")
        self.sync_label.setText(text)
        self.sync_label.setToolTip(tip)
        self.sync_light.setToolTip(tip)

    def _show_next_conflict(self, _link: str = "") -> None:
        """Select the next game whose saves need a decision (cycles through them on repeated clicks)."""
        self.search.clear()
        if self.settings.status_filter != "all":
            self.filter.setCurrentIndex(0)
        from library import sort_games as _sort
        order = [g.game_id for g in _sort(list(self.games.values()), self.settings.sort)]
        conflicts = [gid for gid in order if self.sync_states.get(gid) == "conflict"]
        if not conflicts:
            return
        cur = self.selected_id()
        nxt = conflicts[(conflicts.index(cur) + 1) % len(conflicts)] if cur in conflicts else conflicts[0]
        self._select(nxt)

    # ------------------------------------------------------------------ animation
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if not hasattr(self, "anim"):       # still building the window
            return False
        t = ev.type()
        if obj is self.grid.verticalScrollBar():
            if t == QEvent.Enter:
                self._sb_hover = True
                self._show_scrollbar()
            elif t == QEvent.Leave:
                self._sb_hover = False
                self._show_scrollbar()
        # dragging a game inside the grid: onto another game (new folder), a folder (add to it),
        # or the "Library" button (take it out of its folder)
        if t in (QEvent.DragEnter, QEvent.DragMove, QEvent.DragLeave, QEvent.Drop) and (
                t == QEvent.DragLeave or ev.mimeData().hasFormat(MIME_GAME)):
            return self._game_drag_event(obj, ev)
        if obj is self.grid.viewport():
            if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
                it = self.grid.itemAt(ev.position().toPoint())
                iid = it.data(GAME_ID_ROLE) if it else None
                self._press = (ev.position().toPoint(), iid) if iid and not is_folder(iid) else None
                if it is None:                      # clicked the background, not a cover
                    self.clear_selection()
                    return True
            elif t == QEvent.MouseButtonRelease:
                self._press = None
                if self._ghost is not None:                     # let go of a dragged game
                    self._end_game_drag(ev.globalPosition().toPoint(), drop=True)
                    return True
            elif t == QEvent.MouseMove:
                pos = ev.position().toPoint()
                if self._ghost is not None:                     # dragging a game (the grid keeps the mouse)
                    self._move_game_drag(ev.globalPosition().toPoint())
                    return True
                if self._press and ev.buttons() & Qt.LeftButton and self.worker is None and \
                        (pos - self._press[0]).manhattanLength() >= QApplication.startDragDistance():
                    gid = self._press[1]
                    self._press = None
                    self._start_game_drag(gid)
                    return True
                it = self.grid.itemAt(pos)
                self._set_hovered(it.data(GAME_ID_ROLE) if it else None)
                if pos.x() > self.grid.viewport().width() - SCROLLBAR_EDGE_PX:
                    self._show_scrollbar()
            elif t == QEvent.Leave:
                self._set_hovered(None)
        if t in (QEvent.DragEnter, QEvent.DragMove):
            if self._dropped_paths(ev) and self.worker is None:
                ev.acceptProposedAction()
            else:
                ev.ignore()
            return True
        if t == QEvent.Drop:
            self.dropEvent(ev)
            return True
        return super().eventFilter(obj, ev)

    def _start_game_drag(self, gid: str) -> None:
        g = self.games.get(gid)
        if g is None:
            return
        # a small copy of the cover follows the mouse (see-through: DragGhost sets the opacity)
        dpr = self.devicePixelRatioF()
        w, h = COVER_W // 2, COVER_H // 2
        img = QPixmap(round(w * dpr), round(h * dpr))
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 6, 6)
        p.setClipPath(clip)
        pm = self.covers.get(self.library.artwork_path(g), (g.crop_zoom, g.crop_x, g.crop_y))
        if pm:
            p.drawPixmap(QRectF(0, 0, w, h), pm, QRectF(pm.rect()))
        else:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, QColor(COLORS["placeholder_top"]))
            grad.setColorAt(1, QColor(COLORS["placeholder_bottom"]))
            p.fillRect(QRectF(0, 0, w, h), grad)
        p.end()
        self._dragging = gid
        self._dim_gid = gid
        self._ghost = DragGhost(img)
        self._ghost.follow(QCursor.pos())
        self._ghost.show()
        QApplication.setOverrideCursor(Qt.ClosedHandCursor)
        self._esc = EscWatcher(lambda: self._end_game_drag(QCursor.pos(), drop=False))
        QApplication.instance().installEventFilter(self._esc)
        self._update_anim()                           # (fades the picked-up game)
        # if the button is let go somewhere we never hear about it (a Windows prompt, the lock screen),
        # put the game back instead of leaving the floating cover on screen
        self._drag_watch = QTimer(self)
        self._drag_watch.setInterval(150)
        self._drag_watch.timeout.connect(self._check_drag_button)
        self._drag_watch.start()

    def _check_drag_button(self) -> None:
        if self._ghost is not None and not (QApplication.mouseButtons() & Qt.LeftButton):
            self._end_game_drag(QCursor.pos(), drop=False)

    def _drag_hit(self, gp: QPoint) -> tuple:
        """What a dragged game is over: ("trash", None), ("back", None), ("grid", target or None) or (None, None)."""
        def over(w) -> bool:
            return w.isVisible() and w.rect().contains(w.mapFromGlobal(gp))
        if over(self.trash_btn) and self.worker is None:
            return "trash", None
        if self.current_folder and over(self.back_btn):
            return "back", None
        vp = self.grid.viewport()
        if over(vp):
            return "grid", self._drop_target(vp.mapFromGlobal(gp))
        return None, None

    def _move_game_drag(self, gp: QPoint) -> None:
        self._ghost.follow(gp)
        kind, target = self._drag_hit(gp)
        self.trash_btn.set_hot(kind == "trash")
        self._ghost.set_faded(kind == "trash")          # almost see-through over the trash can
        self._set_hovered(target if kind == "grid" else None)   # the target lifts, so you can see where it'll go

    def _end_game_drag(self, gp: QPoint, drop: bool) -> None:
        gid = self._dragging
        kind, target = self._drag_hit(gp) if drop else (None, None)
        if self._ghost is None:
            return
        self._drag_watch.stop()
        QApplication.instance().removeEventFilter(self._esc)
        self._ghost.hide()
        self._ghost.deleteLater()
        self._ghost = None
        self._dragging = None
        QApplication.restoreOverrideCursor()
        self.trash_btn.set_hot(False)
        self._set_hovered(None)
        self._update_anim()                           # (the picked-up game fades back in)
        if not gid or kind is None:
            return
        if kind == "trash":
            QTimer.singleShot(0, lambda: self.remove_game(gid))
        elif kind == "back":
            self._drop_game(gid, None, True)
        elif target is not None:
            self._drop_game(gid, target, False)

    def _drop_target(self, pos) -> Optional[str]:
        """What a dragged game would land on at pos: a folder tile, or another game."""
        it = self.grid.itemAt(pos)
        iid = it.data(GAME_ID_ROLE) if it else None
        dragged = getattr(self, "_dragging", None)
        if not iid or iid == dragged:
            return None
        if not is_folder(iid) and self.current_folder:
            return None                 # inside a folder there's nothing to make
        return iid

    def _game_drag_event(self, obj, ev) -> bool:
        t = ev.type()
        on_back = obj is self.back_btn
        if obj is self.trash_btn:
            return self._trash_drag_event(ev)
        if t == QEvent.DragLeave:
            self._set_hovered(None)
            return True
        gid = bytes(ev.mimeData().data(MIME_GAME)).decode("utf-8")
        if on_back:
            ok = bool(self.current_folder)
            target = None
        else:
            target = self._drop_target(ev.position().toPoint())
            ok = target is not None
            self._set_hovered(target)               # the target lifts, so you can see where it'll go
        if t == QEvent.DragEnter:
            # always accept entering the grid: refusing here would stop Qt sending the moves that
            # follow (a drag starts over the game being dragged, which isn't a valid target itself)
            if on_back and not ok:
                ev.ignore()
            else:
                ev.acceptProposedAction()
            return True
        if t == QEvent.DragMove:
            ev.acceptProposedAction() if ok else ev.ignore()
            return True
        # drop
        self._set_hovered(None)
        if not ok:
            ev.ignore()
            return True
        ev.acceptProposedAction()
        QTimer.singleShot(0, lambda: self._drop_game(gid, target, on_back))
        return True

    def _trash_drag_event(self, ev) -> bool:
        """A game held over / dropped on the trash button: the same "Are You Sure?" as the menu."""
        t = ev.type()
        if t == QEvent.DragLeave:
            self.trash_btn.set_hot(False)
            return True
        gid = bytes(ev.mimeData().data(MIME_GAME)).decode("utf-8")
        ok = gid in self.games and self.worker is None
        if t in (QEvent.DragEnter, QEvent.DragMove):
            self.trash_btn.set_hot(ok)
            ev.acceptProposedAction() if ok else ev.ignore()
            return True
        self.trash_btn.set_hot(False)                 # dropped
        if not ok:
            ev.ignore()
            return True
        ev.acceptProposedAction()
        QTimer.singleShot(0, lambda: self.remove_game(gid))   # after the drag has finished
        return True

    @guarded
    def _drop_game(self, gid: str, target: Optional[str], on_back: bool) -> None:
        if gid not in self.games or gid == target:
            return                 # the game vanished meanwhile (e.g. removed on the other device), or itself
        if on_back:
            self.library.set_folder(gid, "")
            self.refresh()
            return
        if is_folder(target):
            fid = target[len(FOLDER_PREFIX):]
            self.library.set_folder(gid, fid)
            self.refresh()
            self._select_item(target)
            return
        other = self.games.get(target)
        if other is None or self.library.get(target) is None:
            return
        if other.folder:                        # (while searching) joining that game's folder
            self.library.set_folder(gid, other.folder)
            self.refresh()
            return
        fid = self.library.create_folder(tr("folder.new_name"))
        self.library.set_folder(target, fid)
        self.library.set_folder(gid, fid)
        self.refresh()
        self._select_item(FOLDER_PREFIX + fid)

    def _select_item(self, iid: str) -> None:
        for i in range(self.grid.count()):
            if self.grid.item(i).data(GAME_ID_ROLE) == iid:
                self.grid.setCurrentRow(i)
                self.grid.item(i).setSelected(True)
                self.grid.scrollToItem(self.grid.item(i))
                return

    def _set_hovered(self, gid: Optional[str]) -> None:
        if gid != self.hovered:
            self.hovered = gid
            self._update_anim()

    def _show_scrollbar(self) -> None:
        self._sb_until = time.monotonic() + SCROLLBAR_LINGER_S
        self._update_anim()

    def _style_scrollbar(self) -> None:
        a = self.sb_alpha
        c, h = QColor(COLORS["border"]), QColor(COLORS["button_hover"])
        c.setAlphaF(a)
        h.setAlphaF(a)
        self.grid.verticalScrollBar().setStyleSheet(
            f"QScrollBar::handle:vertical {{ background: {c.name(QColor.HexArgb)}; }}"
            f"QScrollBar::handle:vertical:hover {{ background: {h.name(QColor.HexArgb)}; }}")

    def _sb_target(self) -> float:
        return 1.0 if (self._sb_hover or time.monotonic() < self._sb_until) else 0.0

    def _update_anim(self) -> None:
        needed = (self.settings.pulse or self.hovered is not None or bool(self.hover)
                  or self.sb_alpha > 0 or self._sb_target() > 0   # keep ticking until it has faded out
                  or self._ghost is not None or self.drag_dim > 0)
        if needed and not self.anim.isActive():
            self._last_tick = time.monotonic()
            self.anim.start()
        elif not needed and self.anim.isActive():
            self.anim.stop()
            self.grid.viewport().update()

    def _tick(self) -> None:
        now = time.monotonic()
        step = (now - self._last_tick) * 1000 / HOVER_MS
        self._last_tick = now
        changed = False
        moving: set[str] = set()                 # tiles whose hover lift changed this frame
        for gid in set(self.hover) | ({self.hovered} if self.hovered else set()):
            cur = self.hover.get(gid, 0.0)
            target = 1.0 if gid == self.hovered else 0.0
            new = min(target, cur + step) if target > cur else max(target, cur - step)
            if new != cur:
                changed = True
                moving.add(gid)
            if new <= 0:
                self.hover.pop(gid, None)
            else:
                self.hover[gid] = new
        dim_target = 1.0 if self._ghost is not None else 0.0
        if self.drag_dim != dim_target:
            d = (step * HOVER_MS) / 120                  # a quick fade, ~0.12 s
            self.drag_dim = min(dim_target, self.drag_dim + d) if dim_target > self.drag_dim \
                else max(dim_target, self.drag_dim - d)
            if self._dim_gid:
                moving.add(self._dim_gid)
            changed = True
        elif self.drag_dim == 0 and self._ghost is None:
            self._dim_gid = None
        target = self._sb_target()
        if self.sb_alpha != target:
            d = step * HOVER_MS / SCROLLBAR_FADE_MS
            self.sb_alpha = min(target, self.sb_alpha + d) if target > self.sb_alpha else max(target, self.sb_alpha - d)
            self._style_scrollbar()
            changed = True
        # redraw only what actually moves: a lifting tile (with its shadow) and the small squares
        # around the breathing lights - never the whole grid, which would cost far more with many games
        from PySide6.QtGui import QRegion
        region = QRegion()
        view = self.grid.viewport().rect()
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            r = self.grid.visualItemRect(it)
            if not r.intersects(view):
                continue
            iid = it.data(GAME_ID_ROLE)
            if iid in moving:
                region += r.adjusted(-30, -30, 30, 36)
            elif self.settings.pulse:
                c = CardDelegate.light_center(CardDelegate.cover_rect(r, self.hover.get(iid, 0.0)))
                rad = int(LIGHT_R * 2.9) + 2
                region += QRect(int(c.x()) - rad, int(c.y()) - rad, 2 * rad + 1, 2 * rad + 1)
        if not region.isEmpty():
            self.grid.viewport().update(region)
        if self.settings.pulse:
            self.sync_light.update()
        if not changed:
            self._update_anim()   # stops the timer when nothing moves and pulsing is off

    # ------------------------------------------------------------------ adding games
    @staticmethod
    def _dropped_paths(ev) -> list[str]:
        md = ev.mimeData()
        if not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls()
                if u.isLocalFile() and u.toLocalFile().lower().endswith((".exe", ".lnk"))]

    def dragEnterEvent(self, ev) -> None:
        if self._dropped_paths(ev) and self.worker is None:
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev) -> None:
        paths = self._dropped_paths(ev)
        if paths and self.worker is None:
            ev.acceptProposedAction()
            QTimer.singleShot(0, lambda: self._add_files(paths))   # let the drag finish first

    def add_game(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, tr("file.pick_exe"), "", tr("file.programs"))
        if files:
            self._add_files(files)

    def _add_files(self, files: list[str]) -> None:
        last = None
        for f in files:
            exe = lnk.resolve_game_exe(f)
            if not exe or not Path(exe).is_file():
                message(self, tr("game.not_exe") + f"\n{f}")
                continue
            existing = self.library.find_by_exe(exe)
            machine = self.settings.machine_name
            if existing and existing.paths_for(machine).exe:
                message(self, tr("game.already_added", title=existing.title))
                last = existing.game_id
                continue
            match = existing or self._added_on_other_device(exe)
            if match is not None:
                # the same game, added on the other device: set that one up here (one shared save slot)
                choice = message(self, tr("add.existing", title=match.title), title=tr("game.title_add"),
                                 buttons=[("setup", tr("add.set_up")), ("new", tr("add.as_new")),
                                          ("cancel", tr("game.cancel"))], dismiss="cancel")
                if choice == "cancel":
                    continue
                if choice == "setup":
                    try:
                        game = self.library.set_paths(match.game_id, machine, exe=exe)
                    except (OSError, KeyError) as e:
                        report_failure(self, e)
                        continue
                    GameDialog(self.library, self.settings, game, parent=self).exec()   # e.g. its save folder
                    last = match.game_id
                    continue
            d = GameDialog(self.library, self.settings, exe=exe, parent=self)
            if d.exec() and d.saved_id:
                last = d.saved_id
        self.refresh()
        if last:
            self._select(last)

    def _added_on_other_device(self, exe: str) -> Optional[Game]:
        """A game the other device added that isn't set up here yet and looks like this .exe (same file
        name there, or the same title)."""
        name = Path(exe).name.casefold()
        guess = guess_title(exe).strip().casefold()
        machine = self.settings.machine_name
        for g in self.library.list_games():
            if g.paths_for(machine).exe:
                continue
            names = {Path(p.exe.replace("\\", "/")).name.casefold() for p in g.paths.values() if p.exe}
            if name in names or (guess and guess in (g.title.casefold(), g.auto_title.casefold())):
                return g
        return None

    def _select(self, gid: str) -> None:
        """Select a game (opening its folder if it's in one)."""
        g = self.games.get(gid)
        if g is not None:
            want = g.folder or None
            if self.search.text().strip() == "" and self.settings.status_filter == "all" and want != self.current_folder:
                self.current_folder = want
                self._rebuild(select=gid)
        for i in range(self.grid.count()):
            if self.grid.item(i).data(GAME_ID_ROLE) == gid:
                self.grid.setCurrentRow(i)
                self.grid.item(i).setSelected(True)
                self.grid.scrollToItem(self.grid.item(i))
                return

    # ------------------------------------------------------------------ actions
    def edit_selected(self) -> None:
        fid = self.selected_folder()
        if fid:
            self.rename_folder(fid)
            return
        g = self.selected()
        if g is None:
            return
        d = GameDialog(self.library, self.settings, self.library.get(g.game_id) or g, parent=self)
        if d.exec():
            self.refresh()

    @guarded
    def rename_folder(self, fid: str) -> None:
        d = NameDialog(tr("folder.rename_title"), self.folder_name(fid), self)
        if d.exec():
            self.library.rename_folder(fid, d.name())
            self.refresh()

    @guarded
    def remove_folder(self, fid: str) -> None:
        if ask_yes_no(self, tr("folder.remove_confirm", name=self.folder_name(fid)),
                      tr("menu.remove_folder"), tr("game.cancel")):
            self.library.delete_folder(fid)
            if self.current_folder == fid:
                self.current_folder = None
            self.refresh()

    @guarded
    def move_to_folder(self, gid: str, fid: Optional[str]) -> None:
        """fid None = a new folder (asks for its name); "" = out of its folder."""
        if fid is None:
            d = NameDialog(tr("menu.new_folder").rstrip(".…"), tr("folder.new_name"), self)
            if not d.exec():
                return
            fid = self.library.create_folder(d.name())
        self.library.set_folder(gid, fid)
        self.refresh()

    def _open_saves(self) -> None:
        g = self.selected()
        if g:
            open_folder(g.paths_for(self.settings.machine_name).save_dir)

    def open_settings(self) -> None:
        if self.worker:
            message(self, tr("settings.close_game"))
            return
        d = SettingsDialog(self.settings, self)
        if d.exec() and d.result_settings:
            self._first_check = True
            self.apply_settings(d.result_settings)

    def restore_selected(self) -> None:
        g = self.selected()
        if g is None:
            return
        mine = g.paths_for(self.settings.machine_name)
        if not mine.save_dir:
            return
        d = RestoreDialog(g, self.engine, mine.save_dir, self)
        if d.exec() and d.restored:
            self.statusBar().showMessage(tr("restore.done"), 8000)
            self.refresh()

    def play_selected(self, without: tuple[str, ...] = (), watch: bool = False) -> None:
        g = self.selected()
        if g is None or self.worker is not None:
            return
        if not g.paths_for(self.settings.machine_name).exe:
            self.edit_selected()
            return
        self.playing_id = g.game_id
        self.worker = PlayWorker(self.library.get(g.game_id) or g, self.settings, self.library, self.engine,
                                 without, watch)
        self.worker.status.connect(self._play_status)
        self.worker.conflict.connect(self._ask_conflict, Qt.BlockingQueuedConnection)
        self.worker.result.connect(self._play_finished)
        self.worker.phase.connect(self._on_phase)
        self.worker.start()
        self._update_details()

    def _play_status(self, key: str) -> None:
        if key == "status.launching":
            # the bottom bar already says "Playing Now..."; keep the status line clear while playing
            self.statusBar().clearMessage()
        else:
            self.statusBar().showMessage(tr(key))

    def _on_phase(self, phase: str) -> None:
        self.play_phase = phase
        self._update_sync_label()
        self._update_details()

    def _conflict_choice(self, info: ConflictInfo) -> Optional[str]:
        g = self.games.get(info.game_id)
        d = ConflictDialog(g.title if g else info.game_id, info, self)
        d.exec()
        return d.choice

    def _ask_conflict(self, info: ConflictInfo) -> None:
        choice = self._conflict_choice(info)
        if self.worker:
            self.worker.answer = choice

    def _play_finished(self, r: PlayResult) -> None:
        worker = self.worker
        worker.wait()
        self.worker = None
        gid = self.playing_id
        self.playing_id = None
        self.play_phase = "done"
        game = self.library.get(gid) if gid else None
        if not r.launched:
            self.statusBar().showMessage(tr(r.message))
            self.refresh()
            self._launch_problem(r, worker.without)
            return
        msg = tr(r.message) if r.message else ""
        self.statusBar().showMessage(tr("status.played_for", msg=msg, time=i18n.playtime(r.seconds)), 15000)
        if r.new_title:
            self.statusBar().showMessage(tr("status.title_updated", title=r.new_title), 15000)
        self.refresh()
        self.raise_()
        self.activateWindow()
        if game and not game.paths_for(self.settings.machine_name).save_dir:
            self._confirm_detection(game, r)
            self.refresh()
        if game and self.settings.vndb and not game.artwork and game.artwork_from != "removed" and r.window_title:
            self._auto_cover(game.game_id, r.new_title or game.title)

    def _launch_problem(self, r: PlayResult, already_without: tuple[str, ...]) -> None:
        buttons = []
        if r.message == "play.luna_running":
            buttons.append(("retry", tr("play.retry")))
        if r.retry_without == "watch":
            buttons.append(("watch", tr("play.watch")))
        if r.retry_without == "luna":
            buttons.append(("without_luna", tr("play.retry_without_luna")))
        elif r.retry_without == "le":
            buttons.append(("without_le", tr("play.retry_without_le")))
        if r.message in ("play.luna_missing", "play.le_missing"):
            buttons.append(("settings", tr("play.go_settings")))
        buttons.append(("cancel", tr("game.cancel")))
        choice = message(self, tr(r.message), tr("play.title"), buttons)
        if choice == "retry":
            self.play_selected(already_without)
        elif choice == "watch":
            self.play_selected(tuple(already_without), watch=True)
        elif choice == "without_luna":
            self.play_selected(tuple(already_without) + ("luna",))
        elif choice == "without_le":
            self.play_selected(tuple(already_without) + ("le",))
        elif choice == "settings":
            self.open_settings()

    @guarded
    def _confirm_detection(self, game: Game, r: PlayResult) -> None:
        d = DetectDialog(game, r.candidates, self.app_dir, self, cloud_root=self.settings.cloud_root)
        if d.exec() and d.save_dir:
            res = finish_first_play(game, self.settings, self.library, self.engine, d.save_dir,
                                    d.registry_key, self._conflict_choice)
            self.statusBar().showMessage(tr(res.message), 10000)

    def _auto_cover(self, gid: str, title: str) -> None:
        """After a first play: if VNDB has an exact title match with a safe cover, use it."""
        def work():
            for m in vndb.search(title, limit=5, safe_only=self.settings.vndb_safe_only):
                if m.image_url and vndb.exact(m, title):
                    return m, _download_cover(m.image_url)
            return None

        def done(found):
            g = self.library.get(gid)
            if found and g and not g.artwork and g.artwork_from != "removed":
                m, path = found
                try:
                    self.library.set_artwork(gid, path, source="vndb")
                    self.library.update(gid, vndb_id=m.vndb_id)
                except (OSError, KeyError, ValueError):
                    return             # a bonus, not something to bother the owner about; tried again next time
                self.statusBar().showMessage(tr("status.cover_found"), 8000)
                self.refresh()
        self._run(work, done, lambda _: None)

    def _context_menu(self, pos) -> None:
        it = self.grid.itemAt(pos)
        if it is None:
            return
        self.grid.setCurrentItem(it)
        fid = self.selected_folder()
        m = QMenu(self)
        if fid:
            m.addAction(tr("menu.open_folder_tile"), lambda: self.open_folder(fid))
            m.addAction(tr("menu.rename_folder"), lambda: self.rename_folder(fid))
            m.addSeparator()
            m.addAction(tr("menu.remove_folder"), lambda: self.remove_folder(fid))
            m.exec(self.grid.viewport().mapToGlobal(pos))
            return
        g = self.selected()
        mine = g.paths_for(self.settings.machine_name)
        a = m.addAction(tr("menu.play"), lambda: self.play_selected())
        a.setEnabled(self.play_btn.isEnabled())
        m.addAction(tr("menu.edit"), self.edit_selected)
        sm = m.addMenu(tr("menu.status"))
        for s in STATUSES:
            act = sm.addAction(tr(f"status.{s or 'none'}"), lambda s=s: self._set_status(g.game_id, s))
            act.setCheckable(True)
            act.setChecked(g.status == s)
        fm = m.addMenu(tr("menu.add_to_folder"))
        others = sorted((f for f in self.folder_games if f != g.folder), key=lambda f: self.folder_name(f).lower())
        for f in others:
            fm.addAction(self.folder_name(f), lambda f=f: self.move_to_folder(g.game_id, f))
        if others:
            fm.addSeparator()
        fm.addAction(tr("menu.new_folder"), lambda: self.move_to_folder(g.game_id, None))
        if g.folder:
            m.addAction(tr("menu.remove_from_folder"), lambda: self.move_to_folder(g.game_id, ""))
        m.addSeparator()
        a = m.addAction(tr("menu.restore"), self.restore_selected)
        a.setEnabled(bool(mine.save_dir) and self.worker is None)
        a = m.addAction(tr("menu.open_saves"), lambda: open_folder(mine.save_dir))
        a.setEnabled(bool(mine.save_dir))
        a = m.addAction(tr("menu.open_game"), lambda: open_folder(str(Path(mine.exe).parent)))
        a.setEnabled(bool(mine.exe))
        m.addSeparator()
        a = m.addAction(tr("game.remove"), lambda: self.remove_game(g.game_id))
        a.setEnabled(self.worker is None)             # not while a game is running or syncing
        m.exec(self.grid.viewport().mapToGlobal(pos))

    @guarded
    def remove_game(self, gid: str) -> None:
        g = self.games.get(gid)
        if g is None or self.worker is not None:
            return
        if confirm_remove(self, g.title):
            self.library.remove(gid)
            self.clear_selection()
            self.refresh()

    @guarded
    def _set_status(self, gid: str, status: str) -> None:
        self.library.update(gid, status=status)
        self.refresh()

    def closeEvent(self, e) -> None:
        if self.worker and self.play_phase == "syncing":
            message(self, tr("close.syncing"))      # stopping mid-sync could leave saves half-uploaded
            e.ignore()
            return
        if self.worker:
            g = self.games.get(self.playing_id)
            if not ask_yes_no(self, tr("close.body", title=g.title if g else ""), tr("close.title"),
                              tr("game.cancel")):
                e.ignore()
                return
            os._exit(0)  # the game keeps running; nothing of ours is mid-write while we only watch it
        if self.checker:
            self.checker.wait(5000)       # (it may be uploading saves made outside Ikemen Reader)
        e.accept()                        # other background work: see wait_for_tasks() in main()


def user_fonts_dir() -> Path:
    return default_app_dir() / "fonts"


def _register_font(path: Path, source: str) -> list[str]:
    fid = QFontDatabase.addApplicationFont(str(path))
    families = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
    for fam in families[:1]:                 # the main family name (some files also list a weight-named one)
        if fam not in FONTS:
            FONTS[fam] = source
        if source == "user":
            FONT_FILES.setdefault(fam, str(path))
    return families


def load_fonts() -> None:
    """The built-in fonts, then every font the owner added (kept in %APPDATA%\\IkemenReader\\fonts)."""
    for name in ("Font.otf", "ZenMaruGothic-Regular.ttf"):
        f = asset(name)
        if f.is_file():
            _register_font(f, "builtin")
    d = user_fonts_dir()
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if f.suffix.lower() in (".ttf", ".otf", ".ttc"):
                _register_font(f, "user")


def add_user_font(src: str) -> Optional[str]:
    """Copy a font file into our fonts folder and load it. Returns its family name, or None."""
    import shutil
    d = user_fonts_dir()
    d.mkdir(parents=True, exist_ok=True)
    dest = d / Path(src).name
    n = 1
    while dest.exists() and dest.read_bytes() != Path(src).read_bytes():
        n += 1
        dest = d / f"{Path(src).stem}_{n}{Path(src).suffix}"
    if not dest.exists():
        shutil.copy2(src, dest)
    families = _register_font(dest, "user")
    if not families:
        try:
            dest.unlink()
        except OSError:
            pass
        return None
    return families[0]


def install_error_net() -> None:
    """Last line of defence: an error nothing else caught is written to error.log (in the app folder) and
    explained in one short message, instead of an action silently doing nothing."""
    import traceback
    log = default_app_dir() / "error.log"
    showing = {"now": False}

    def hook(kind, value, tb):
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "".join(traceback.format_exception(kind, value, tb)) + "\n")
        except OSError:
            pass
        app = QApplication.instance()
        if app is None or showing["now"] or QThread.currentThread() is not app.thread():
            return                              # (only the main thread may show windows)
        showing["now"] = True
        try:
            message(app.activeWindow(), tr("error.unexpected", path=str(log)))
        finally:
            showing["now"] = False
    sys.excepthook = hook


INSTANCE_KEY = "IkemenReader-" + (os.environ.get("USERNAME") or "user")


def hand_over_to_running_copy() -> bool:
    """If Ikemen Reader is already open (for example it started with Windows, then was opened again),
    ask that copy to show itself and return True: two copies could sync the same saves at once."""
    from PySide6.QtNetwork import QLocalSocket
    s = QLocalSocket()
    s.connectToServer(INSTANCE_KEY)
    if s.waitForConnected(300):
        s.write(b"show")
        s.waitForBytesWritten(300)
        s.disconnectFromServer()
        return True
    return False


def listen_for_second_copies(window: QWidget) -> None:
    """Answer hand_over_to_running_copy(): bring this window to the front."""
    from PySide6.QtNetwork import QLocalServer
    QLocalServer.removeServer(INSTANCE_KEY)     # a leftover from a copy that didn't close cleanly
    server = QLocalServer(window)

    def show():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()
    server.newConnection.connect(show)
    server.listen(INSTANCE_KEY)


def main() -> None:
    if sys.platform == "win32":
        import ctypes
        try:   # own taskbar icon/group instead of Python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("IkemenReader.App")
        except (AttributeError, OSError):
            pass
    app = QApplication(sys.argv)
    app.setApplicationName("Ikemen Reader")
    if hand_over_to_running_copy():
        return                                  # already open: that window comes to the front instead
    install_error_net()
    load_fonts()
    startup.refresh()          # if it starts with Windows, keep that entry pointing at this copy
    settings = load_local_settings()
    if settings:
        apply_theme(settings.theme, settings.custom_colors)
        set_qt_language(settings.language)
        i18n.set_language(settings.language)
        apply_fonts(settings.font_en, settings.font_ja)
        apply_background(settings.bg_image, settings.bg_opacity, settings.bg_zoom, settings.bg_x, settings.bg_y)
    else:
        apply_theme("dark")
    app.setWindowIcon(app_icon())
    apply_palette()
    app.setStyleSheet(build_style())
    if settings is not None:
        i18n.set_language(settings.language)
    if settings is None or not settings.cloud_root:
        d = SettingsDialog(None, welcome=True)
        if not d.exec():
            return
        settings = d.result_settings
    w = MainWindow(settings)
    listen_for_second_copies(w)
    w.show()
    code = app.exec()
    if w.checker is not None and w.checker.isRunning() and not w.checker.wait(3000):
        os._exit(code)           # still uploading a big save folder: stop cleanly (it carries on next time)
    if not wait_for_tasks(3):
        os._exit(code)           # a slow download is still going: leave without waiting for it
                                 # (everything we write is swapped in whole, so stopping is safe)
    sys.exit(code)


if __name__ == "__main__":
    main()
