"""
app.py - the Ikemen Reader window.

    py app.py

The heavy lifting lives in sync_engine.py / library.py / launcher.py; this file is only the UI.
Playing runs on a background thread so the window stays responsive while a game is open.

Easy-to-tweak things live at the top: COLORS / LIGHTS (palette), and the SIZES & TIMING block.
All interface text lives in i18n.py.
"""
from __future__ import annotations

import ctypes
import html
import dataclasses
import math
import os
import re
import sys
import threading
import time
import zlib
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import (QAbstractNativeEventFilter, QSizeF, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread,
                            QTimer, QUrl, Signal)
from PySide6.QtGui import (QCursor, QTextCursor, QPalette, QTextOption, QTextLayout, QAction, QColor, QFont, QFontDatabase, QFontMetrics, QFontMetricsF, QIcon, QImage,
                           QKeySequence, QLinearGradient, QPainter, QPainterPath, QPaintEvent, QPen, QPixmap, QRegion,
                           QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractScrollArea, QScrollArea, QTextBrowser, QAbstractItemView, QApplication, QButtonGroup, QGraphicsOpacityEffect, QCheckBox, QComboBox,
                               QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QTextEdit, QStackedWidget,
                               QProgressBar, QPushButton, QRadioButton, QSizePolicy, QSlider, QStyle,
                               QProxyStyle, QStatusBar, QStyledItemDelegate, QStyleOption, QStyleOptionButton, QStyleOptionViewItem, QVBoxLayout,
                               QWidget)

import shiboken6

import i18n
import lnk
import startup
import devsync
import themes
import guide
import luna
import outside
import presence
import regsync
import updater
import vndb
from i18n import tr
import detect
import launcher
from launcher import PlayResult, finish_first_play, play, warm_up
from library import (SLOT_NUMBER, SLOTS, STATUSES, Game, Library, LocalSettings, MachinePaths,
                     default_app_dir, load_local_settings, other_slot, save_local_settings,
                     matches_names, matches_search, solo_dir, sort_games, start_solo_library, this_computer,
                     backup_solo_library, merge_solo, solo_joins)
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
APP_VERSION = "2.0.0"
BASE_LIGHTS = {"green": "#6FBF73", "blue": "#6FA8DC", "amber": "#E0B252", "red": "#E0605B", "grey": "#8C8C8C",
               "lavender": "#A98BD9", "yellow": "#E6D95A"}    # (yellow: a pale lemon, kept clear of the golden amber)
# folder lights: one slice per colour inside, most important first (clockwise from 12 o'clock)
LIGHT_ORDER = ["red", "yellow", "amber", "blue", "lavender", "green", "grey"]   # (yellow: needs you, like red)
# bookmark ribbons on the covers, one colour per reading status (muted gold / silver / bronze / rose)
BASE_MARKS = {"finished": "#C9AB5A", "reading": "#B2B7BE", "plan": "#B5885E", "dropped": "#C48D96",
              "border": "#1C1C1C"}        # (and the thin border round every bookmark)
LOGO_ORIGINAL = {"logo_fill": "#F0F0F0", "logo_outline": "#303030"}   # what Logo.png / Icon.png are drawn in

# the palette in use; apply_theme() switches them in place, so everything reads the current values
COLORS = dict(DARK)
LIGHTS = dict(BASE_LIGHTS)
MARKS = dict(BASE_MARKS)
LOGO = dict(LOGO_ORIGINAL)
ICONS = {"check": "", "chevron": "", "chevron_up": "", "chevron_right": "", "tick": ""}   # theme colours (_write_icons)

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
    ("bookmarks", [f"mark_{k}" for k in BASE_MARKS]),
]


def theme_colors(theme: str, custom: Optional[dict] = None) -> dict:
    """Every colour role for a theme. Custom starts from Dark and applies the owner's choices."""
    base = dict(LIGHT if theme == "light" else DARK)
    base.update({f"light_{k}": v for k, v in BASE_LIGHTS.items()})
    base.update({f"mark_{k}": v for k, v in BASE_MARKS.items()})
    base.update(LOGO_ORIGINAL)
    if theme == "light":
        # the logo, star, trash, bookmark and window icon inverted - a dark fill with a light outline - so they stand
        # out on the light bars (owner, 2026-10-17; the same pair as Discord's light picture)
        base.update(logo_fill=LOGO_ORIGINAL["logo_outline"], logo_outline=LOGO_ORIGINAL["logo_fill"])
    if theme == "custom" and custom:
        base.update({k: v.upper() for k, v in custom.items() if k in base})
    return base


def apply_theme(theme: str = "dark", custom: Optional[dict] = None) -> None:
    full = theme_colors(theme, custom)
    COLORS.clear()
    COLORS.update({k: full[k] for k in DARK})
    LIGHTS.clear()
    LIGHTS.update({k: full[f"light_{k}"] for k in BASE_LIGHTS})
    MARKS.clear()
    MARKS.update({k: full[f"mark_{k}"] for k in BASE_MARKS})
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
        # (a drop-down whose list will open upwards shows its arrow pointing up)
        "chevron_up": (f"chevron_up_{arrow[1:]}.svg",
                       f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 7.5 '
                       f'L6 4 L9.5 7.5" fill="none" stroke="{arrow}" stroke-width="1.6" stroke-linecap="round" '
                       f'stroke-linejoin="round"/></svg>'),
        # (the reading status in use, in the right-click menu: the check box's tick, in the menu's text colour)
        "tick": (f"tick_{arrow[1:]}.svg",
                 f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><path d="M3.5 8.5 '
                 f'L6.5 11.5 L12.5 4.5" fill="none" stroke="{arrow}" stroke-width="2" stroke-linecap="round" '
                 f'stroke-linejoin="round"/></svg>'),
        # (a menu item that opens another menu: the same arrow, pointing the way it opens)
        "chevron_right": (f"chevron_right_{arrow[1:]}.svg",
                          f'<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12"><path d="M4.5 2.5 '
                          f'L8 6 L4.5 9.5" fill="none" stroke="{arrow}" stroke-width="1.6" stroke-linecap="round" '
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


_SCROLL_CURSORS: dict = {}


def scroll_cursor(way: int) -> QCursor:
    """Wheel-press scrolling's pointer: the drop-downs' chevron, one up + one down, in the text colour with a thin
    band-coloured edge (readable over any cover). way = -1 / 1: that one bright, the other dimmed; 0: both."""
    key = (way, COLORS["text"], COLORS["band"])
    if key not in _SCROLL_CURSORS:
        size, s = 32, 1.75                         # (the 12-unit chevron drawn 1.75x: ~12 px wide)
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        c = size / 2
        for d, lift in ((-1, -7.0), (1, 7.0)):     # chevron centres 7 px above / below the hot spot
            y, h = c + lift, 1.75 * s * d          # (tip 1.75 units from the centre line, pointing the way)
            path = QPainterPath(QPointF(c - 3.5 * s, y - h))
            path.lineTo(c, y + h)
            path.lineTo(c + 3.5 * s, y - h)
            p.setOpacity(1.0 if way in (0, d) else 0.35)
            for colour, width in ((COLORS["band"], 1.6 * s + 2.4), (COLORS["text"], 1.6 * s)):
                p.setPen(QPen(QColor(colour), width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.drawPath(path)
        p.end()
        _SCROLL_CURSORS[key] = QCursor(pm, size // 2, size // 2)
    return _SCROLL_CURSORS[key]


class _AutoScroll(QObject):
    """Pressing the mouse wheel scrolls like a browser (owner, 2026-10-20): move the pointer above / below where it was
    pressed and the library keeps scrolling that way, faster the further away. Held and moved: letting go stops it;
    a quick click: it runs until the next click, the wheel or Esc. Counts as a glide (the strips draw it, no hover)."""

    def __init__(self, grid: QAbstractScrollArea):
        super().__init__(grid)
        self.grid = grid
        self.active = False
        self.clock = VsyncClock(self)
        self.clock.timeout.connect(self._step)

    def _begin(self) -> None:
        g = self.grid
        g._end_glide()
        g._glide_clock_ready()
        g._glide_to = self.pos                     # (everything else treats it as a glide)
        g._rehover_timer().stop()
        g.w._set_hovered(None)                     # (a cover lifted under the pointer settles: no hover meanwhile)

    def _end(self) -> None:
        self.grid._heading = 0
        self.grid._end_glide()

    def _move(self, dy: float) -> None:
        g, sb = self.grid, self.grid.verticalScrollBar()
        g._heading = 1 if dy > 0 else -1           # (_glide_to is where it is: the strip ahead needs the way)
        g._glide_to = self.pos
        g._gliding = True
        try:
            sb.setValue(round(self.pos))
        finally:
            g._gliding = False

    def start(self) -> None:
        sb = self.grid.verticalScrollBar()
        self.active, self.moved = True, False
        self.anchor = QCursor.pos().y()
        self.pos = float(sb.value())
        self.started = time.perf_counter()
        self.last = frame_time()
        self._begin()
        self.way, self.rest_since = 0, None
        QApplication.setOverrideCursor(scroll_cursor(0))
        QApplication.instance().installEventFilter(self)
        self.clock.start()

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        self.clock.stop()
        QApplication.instance().removeEventFilter(self)
        QApplication.restoreOverrideCursor()
        self._end()

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t == QEvent.MouseButtonRelease and ev.button() == Qt.MiddleButton:
            if self.moved or time.perf_counter() - self.started > AUTO_SCROLL_CLICK_S:
                self.stop()                        # held and used: letting go ends it
            return True
        if t in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.Wheel):
            self.stop()
            return True
        # a key comes as ShortcutOverride first (Esc never got here as a KeyPress: the window's Esc shortcut took it).
        # Any key stops it; Esc does only that (accepted: no going back out of a folder), others then work as usual
        # (an arrow key's selection and this both moved the view, back and forth)
        if t in (QEvent.ShortcutOverride, QEvent.KeyPress):
            self.stop()
            if ev.key() == Qt.Key_Escape:
                ev.accept()
                return True
        if t == QEvent.ApplicationDeactivate:
            self.stop()
        return False

    def _step(self) -> None:
        g, sb = self.grid, self.grid.verticalScrollBar()
        if not self.active or not g.isVisible():
            self.stop()
            return
        now = frame_time()                         # (the frame's own time: even steps - see frame_time)
        dt, self.last = min(0.05, now - self.last), max(self.last, now)
        if dt <= 0:
            return                                 # (a second step for the same frame)
        dy = QCursor.pos().y() - self.anchor
        over = abs(dy) - AUTO_SCROLL_DEAD
        way = 0 if over <= 0 else (1 if dy > 0 else -1)
        # (the pointer brightens the way it's scrolling; passing through the middle on the way to the other side
        # goes straight across - both lit again only after a real rest there)
        if way:
            self.rest_since = None
        elif self.way:
            self.rest_since = self.rest_since or now
            if now - self.rest_since < AUTO_SCROLL_REST_S:
                way = self.way
        if way != self.way:
            self.way = way
            QApplication.changeOverrideCursor(scroll_cursor(way))
        if over <= 0:
            return
        self.moved = True
        self.pos += math.copysign(over ** 1.2 * AUTO_SCROLL_SPEED, dy) * dt
        self.pos = min(float(sb.maximum()), max(float(sb.minimum()), self.pos))
        self._move(dy)


class _AreaAutoScroll(_AutoScroll):
    """Wheel-press scrolling for every other scrolling area (owner, 2026-10-20): Settings, Edit, Custom Theme, the
    guide, drop-down lists... The same pointer, speeds and ways to stop as the library's; drawn by SmoothWheel's
    solid-background sliding (see SmoothWheel._solid)."""

    def _begin(self) -> None:
        area = self.grid
        if isinstance(area, QAbstractItemView) and area.verticalScrollMode() != QAbstractItemView.ScrollPerPixel:
            top = area.indexAt(QPoint(4, 4))       # (row by row it could only jump: by pixel, the same row on top)
            area.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
            if top.isValid():
                area.scrollTo(top, QAbstractItemView.PositionAtTop)
            self.pos = float(area.verticalScrollBar().value())
        sb = area.verticalScrollBar()
        sw = _SMOOTH_WHEEL
        if sw is not None:
            sw.glides.pop(sb, None)
            sw._solid(sb, True)

    def _end(self) -> None:
        if _SMOOTH_WHEEL is not None and shiboken6.isValid(self.grid):
            _SMOOTH_WHEEL._solid(self.grid.verticalScrollBar(), False)

    def _move(self, dy: float) -> None:
        sw = _SMOOTH_WHEEL
        if sw is not None:
            sw._setting = True                     # (not "moved elsewhere": nothing to cancel)
        try:
            self.grid.verticalScrollBar().setValue(round(self.pos))
        finally:
            if sw is not None:
                sw._setting = False


class LibraryGrid(QListWidget):
    """The cover grid. It draws its own part of the backdrop (only where something changed), and
    redraws instead of sliding pixels when scrolling, so the background picture stays fixed."""

    def __init__(self, window: "MainWindow"):
        super().__init__(objectName="library")
        self.w = window
        self.viewport().setAutoFillBackground(False)
        self.viewport().setAttribute(Qt.WA_OpaquePaintEvent, True)   # we paint every pixel ourselves
        # No input method (IME) for the grid: nothing is typed into it (browsers / Steam only connect the IME to real
        # text boxes). Tried first for the refocus freeze (2026-10-20) - not its cause (that was the title-bar press:
        # KeepsTitleBar._caption_pressed), kept as harmless.
        self.setAttribute(Qt.WA_InputMethodEnabled, False)
        self._layer = {}
        self._layer_missing = set()
        self._layer_arrived = set()

    bare = False              # True while taking a snapshot of the covers alone (folder animation)

    # Mouse-wheel scrolling glides (owner, 2026-10-19: Steam's library and browsers do; a notch jumped 72 px). A
    # notch moves a TARGET and the view follows a curve to it, like Chromium (Steam): eases in from rest, and a
    # notch arriving mid-glide starts a new curve from where the view IS at the speed it HAS - so stacked notches
    # build into one push instead of a shove each (the first version chased the target exponentially: fastest on
    # the first frame, a kick per notch, a long sub-pixel tail). Curve = cubic Hermite (start position + speed ->
    # target at rest). Touchpads / free-spinning wheels send small steps, not whole notches: those scroll directly
    # as before. Anything else moving the view (scroll bar, keyboard, a rebuild, dragging a game to the edge) ends
    # the glide on the spot.
    _glide_to: Optional[float] = None
    _glide_from = 0.0         # the curve: from position, starting speed (px/s), start time, length (s)
    _glide_v0 = 0.0
    _glide_t0 = 0.0
    _glide_len = 0.2
    _gliding = False          # (True while the glide itself moves the scroll bar)
    _glide_prev = None        # the last frame's (whole-pixel step, curve speed) - see _glide_step
    _heading = 0              # wheel-press scrolling: +1 down / -1 up (a glide goes towards _glide_to)

    def notch_px(self) -> float:
        """How far one wheel notch goes: Chromium's 100 px for Windows' usual 3 lines (follows the Windows setting)."""
        return QApplication.wheelScrollLines() * WHEEL_LINE_PX

    def event(self, ev) -> bool:
        # Qt's item views repaint their WHOLE viewport when the window gains / loses focus (selected items can look
        # different in an inactive window) - here every cover plus the still picture remade: a skipped frame on every
        # switch to / from the window (owner, 2026-10-20, "minor stutter when tabbing in"). Our covers look the same
        # either way, so that step is skipped (the scroll area's own handling still runs).
        if ev.type() in (QEvent.WindowActivate, QEvent.WindowDeactivate):
            return QAbstractScrollArea.event(self, ev)
        return super().event(ev)

    # (the same whole-viewport repaint comes again with the grid's keyboard focus leaving / returning with the window)
    def focusInEvent(self, ev) -> None:
        if ev.reason() == Qt.ActiveWindowFocusReason:
            QAbstractScrollArea.focusInEvent(self, ev)
        else:
            super().focusInEvent(ev)

    def focusOutEvent(self, ev) -> None:
        if ev.reason() == Qt.ActiveWindowFocusReason:
            QAbstractScrollArea.focusOutEvent(self, ev)
        else:
            super().focusOutEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MiddleButton:
            if not hasattr(self, "_auto"):
                self._auto = _AutoScroll(self)
            if not self._auto.active and self.verticalScrollBar().maximum() > 0:
                self._auto.start()                 # (nothing to scroll: nothing happens, no cursor, no eaten click)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def auto_scrolling(self) -> bool:
        return hasattr(self, "_auto") and self._auto.active

    def wheelEvent(self, ev) -> None:
        d = ev.angleDelta().y()
        if WHEEL_LOG:
            WHEEL_LOG.append(f"{time.perf_counter():.4f} wheel {ev.angleDelta().y()} {ev.angleDelta().x()} "
                             f"pixel {ev.pixelDelta().y()} mods {int(ev.modifiers().value)} phase {ev.phase()}")
        # a mouse wheel glides however finely it reports: high-resolution / free-spinning wheels send parts of a
        # notch (not multiples of 120) some of the time, and those used to jump (2026-10-20). Touchpads (pixel deltas or a scroll phase) keep Qt's own scrolling.
        touchpad = not ev.pixelDelta().isNull() or ev.phase() != Qt.NoScrollPhase
        if not d or ev.angleDelta().x() or ev.modifiers() or touchpad:
            self._end_glide()
            super().wheelEvent(ev)             # touchpad / sideways / with a key held: as before
            return
        sb = self.verticalScrollBar()
        base = self._glide_to if self._glide_to is not None else float(sb.value())
        self._aim(base - d / 120 * self.notch_px(), WHEEL_GLIDE_S)
        ev.accept()

    def _glide_state(self, now: float) -> tuple[float, float, float]:
        """(position, speed px/s, progress 0..1) on the current curve at `now`."""
        L = self._glide_len
        s = min(1.0, max(0.0, (now - self._glide_t0) / L))
        p0, m, p1 = self._glide_from, self._glide_v0 * L, self._glide_to
        s2, s3 = s * s, s * s * s
        pos = (2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * m + (3 * s2 - 2 * s3) * p1
        vel = ((6 * s2 - 6 * s) * p0 + (3 * s2 - 4 * s + 1) * m + (6 * s - 6 * s2) * p1) / L
        return pos, vel, s

    def _aim(self, target: float, length: float) -> None:
        """Start a curve to `target` from where the view is now, keeping its current speed."""
        sb = self.verticalScrollBar()
        target = min(float(sb.maximum()), max(float(sb.minimum()), target))
        now = time.perf_counter()
        if self._glide_to is not None:
            pos, vel, _ = self._glide_state(now)
        else:
            pos, vel = float(sb.value()), 0.0
        dist = target - pos
        if abs(dist) < 0.5:                   # (already there - at an end of the list: land exactly on it)
            if self._glide_to is not None:
                self._gliding = True
                try:
                    sb.setValue(round(target))
                finally:
                    self._gliding = False
            self._end_glide()
            return
        if vel * dist > 0:                    # already heading there: shorten so the curve never overshoots
            length = min(length, 3 * dist / vel)
        elif vel * dist < 0:
            vel = 0.0                         # turned round: straight back, not on the old way first (owner: rubbery)
        self._glide_from, self._glide_v0, self._glide_t0 = pos, vel, now
        self._glide_len = max(length, 0.05)
        was_idle = self._glide_to is None
        self._glide_to = target
        if was_idle:
            self._rehover_timer().stop()
            self._glide_clock_ready().start()

    def _rehover_timer(self) -> QTimer:
        if not hasattr(self, "_rehover"):
            self._rehover = QTimer(self, singleShot=True, interval=HOVER_AFTER_SCROLL_MS)
            self._rehover.timeout.connect(self.w.scrolled)
        return self._rehover

    def scroll_settling(self) -> bool:
        """A wheel glide runs, or ended moments ago (the hover waits: see _end_glide)."""
        return self._glide_to is not None or (hasattr(self, "_rehover") and self._rehover.isActive())

    def _glide_clock_ready(self) -> "VsyncClock":
        if not hasattr(self, "_glide_clock"):
            self._glide_clock = VsyncClock(self)
            self._glide_clock.timeout.connect(self._glide_step)
            self.verticalScrollBar().valueChanged.connect(self._scrolled_elsewhere)
        return self._glide_clock

    def _glide_step(self) -> None:
        if self._glide_to is None:
            self._glide_clock.stop()
            return
        sb = self.verticalScrollBar()
        lo, hi = float(sb.minimum()), float(sb.maximum())
        if not lo <= self._glide_to <= hi:                   # the range changed (a rebuild): aim at the new edge
            self._aim(self._glide_to, self._glide_len)
            if self._glide_to is None:
                return
        pos, vel, s = self._glide_state(frame_time())         # (where it should be when this frame shows)
        done = s >= 1.0
        if WHEEL_LOG:
            WHEEL_LOG.append(f"{time.perf_counter():.4f} step pos={pos:.1f} s={s:.2f}")
        cur = sb.value()
        want = round(self._glide_to if done else min(hi, max(lo, pos)))
        step, prev = want - cur, self._glide_prev
        # each frame's whole-pixel step follows the curve's speed: never smaller than the last while it speeds up,
        # never bigger while it slows down - rounding each frame's place on its own made 5, 4, 6 / 3, 4 wobbles
        # (owner, 2026-10-24). Only within a pixel of the curve, so it still lands exactly where it would.
        if not done and prev is not None and step and prev[0] and (step > 0) == (prev[0] > 0):
            last = abs(prev[0])
            low, high = (last, last + 1) if abs(vel) > abs(prev[1]) else (max(1, last - 1), last)  # (a pixel at a time)
            even = math.copysign(min(high, max(low, abs(step))), step)
            if abs(cur + even - pos) <= 1.5 and lo <= cur + even <= hi:
                step = int(even)
        self._glide_prev = (step, vel)
        self._gliding = True
        try:
            sb.setValue(cur + step)
        finally:
            self._gliding = False
        if done:
            self._end_glide()

    def _scrolled_elsewhere(self, _value: int) -> None:
        if not self._gliding:
            self._end_glide()

    def _end_glide(self) -> None:
        self._glide_prev = None
        if self._glide_to is not None:
            self._glide_to = None
            self._glide_clock_ready().stop()
            if self._layer and self._missing_in_view():
                # covers on screen still loading: the strips stay in charge until they've arrived (normal drawing
                # would read them all at once right now - a stutter just as the glide settles)
                self._layer_hold = time.perf_counter()
            elif self._layer:
                self._hand_back()
            # the cover under the pointer lifts once scrolling has really stopped (or the mouse moves): lifting at
            # every glide's end made covers pop up and drop between quick flicks - noisy, and each one had to be
            # drawn live through the next flick (owner, 2026-10-19: the pointer over a cover added lots of stress)
            self._rehover_timer().start()
            if WHEEL_LOG:
                WHEEL_LOG.f.flush()

    # While a wheel glide runs, the covers are drawn ONCE into strips of the whole grid's picture (LAYER_TILE px tall,
    # covers only: no background, no lights) and each frame just slides them over the fixed background, then draws
    # the lights and any cover still settling from a hover on top - like a browser (Steam) scrolls a finished page.
    # Redrawing every cover each frame (and the lights' still picture on top of it, as the hovered cover changed)
    # took longer than a 143 Hz refresh: frames went out unevenly (owner, 2026-10-19: "the stutter is horrific").
    # Only while gliding and nothing else moves the covers; anything changing them drops the strips (redrawn when
    # next needed), and the glide's end hands back to normal drawing. The hover doesn't follow the scroll meanwhile
    # (browsers don't either): the cover under the pointer lifts when the glide ends.
    _layer: dict = {}                    # strip number -> QPixmap (content y = number x LAYER_TILE)
    _layer_skip: frozenset = frozenset()  # covers left out of the strips (drawn live: settling from a hover)
    _keep_layer = False                   # the next normal repaint leaves the strips alone (the glide's last one)

    def layer_active(self) -> bool:
        w = self.w
        # (a dragged game's edge scroll glides too: the picked-up cover is left out of the strips and drawn live -
        # fading - and the drop line goes on top; owner 2026-10-24: as smooth as the wheel)
        return ((self._glide_to is not None or self._layer_hold) and not self.bare
                and self.isVisible()
                and w._landing is None and not w.slides
                and not w._search_moving() and not w.rumble and w.no_results == 0
                and w.bookmark_alpha in (0.0, 1.0) and not BACKGROUND["live"])

    def _drop_layer(self, *_args) -> None:
        self._layer = {}
        self._rows = {}
        self._layer_missing = set()
        self._layer_arrived = set()
        self._layer_hold = 0.0
        if hasattr(self, "w"):
            self._prebuild_timer().start(LAYER_QUIET_MS)  # (made again once things are quiet)

    def _layer_tile(self, k: int) -> QPixmap:
        """Strip k of the covers (made now if it isn't there yet)."""
        tile = self._layer.get(k)
        if tile is not None:
            return tile
        vp = self.viewport()
        dpr = vp.devicePixelRatioF()
        width, top = vp.width(), k * LAYER_TILE - self.verticalScrollBar().value()   # (its top, in viewport terms)
        tile = QPixmap(math.ceil(width * dpr), math.ceil(LAYER_TILE * dpr))
        tile.setDevicePixelRatio(dpr)
        tile.fill(Qt.transparent)
        self._draw_strip(tile, top, QRect(0, top, width, LAYER_TILE))
        self._layer[k] = tile
        return tile

    def _sync_skip(self) -> frozenset:
        """The covers left out of the strips = those lifted / settling now; a change patches just their spots."""
        w = self.w
        dim = getattr(w, "_dim_gid", None)      # (the picked-up game: drawn live, faded, its light with it)
        skip = frozenset(gid for gid, h in w.hover.items() if h > 0) | ({w.hovered} if w.hovered else frozenset()) \
            | ({dim} if dim else frozenset())
        if skip != self._layer_skip:
            changed, self._layer_skip = skip ^ self._layer_skip, skip
            self._patch_layer(changed)
        return skip

    def _layer_keep(self) -> tuple[int, int]:
        """The strips worth having: LAYER_AHEAD screens above and below the view (in strip numbers)."""
        v, h = self.verticalScrollBar().value(), self.viewport().height()
        last = (self.verticalScrollBar().maximum() + h - 1) // LAYER_TILE
        return (max(0, (v - LAYER_AHEAD * h) // LAYER_TILE),
                min(last, (v + h + LAYER_AHEAD * h) // LAYER_TILE))

    def _prebuild(self) -> None:
        """While nothing happens: make the strips around the view, one per step, so a glide starts with them all
        ready and never draws one mid-frame (that was most of the late frames while flicking quickly)."""
        w = self.w
        if (not self.isVisible() or w.isMinimized() or self._glide_to is not None or self.bare
                or w.slides or w._search_moving() or w.rumble):
            return
        self._sync_skip()
        lo, hi = self._layer_keep()
        todo = [k for k in range(lo, hi + 1) if k not in self._layer]
        v = self.verticalScrollBar().value() // LAYER_TILE
        todo.sort(key=lambda k: abs(k - v))                # nearest first
        vp_top = self.verticalScrollBar().value()
        for k in todo:
            # only strips whose covers are already in memory: decoding one here would freeze an idle frame. The
            # others' covers load in the background; their arrival redraws the grid, which starts this again
            missing = [iid for i in self.visible_range(QRect(0, k * LAYER_TILE - vp_top - 40, self.viewport().width(),
                                                             LAYER_TILE + 80))
                       if not w.cover_ready(iid := self.model().index(i, 0).data(GAME_ID_ROLE))]
            if missing:
                for iid in missing:
                    w.want_cover(iid)
                continue
            self._layer_tile(k)
            self._prebuild_timer().start(LAYER_STEP_MS)    # (the next one shortly)
            return

    def _prebuild_timer(self) -> QTimer:
        if not hasattr(self, "_prebuilder"):
            self._prebuilder = QTimer(self, singleShot=True)
            self._prebuilder.timeout.connect(self._prebuild)
        return self._prebuilder

    def _draw_strip(self, tile: QPixmap, top: int, part: QRect, only=None) -> None:
        """Draw the covers reaching into `part` (viewport terms) into a strip whose top is at `top`, lights left
        out; the strip is cleared there first. (A whole strip, or just one cover's cell - `only`, its index: a hover
        began / settled, or its picture arrived.)"""
        q = QPainter(tile)
        q.translate(0, -top)
        q.setClipRect(part)
        q.setCompositionMode(QPainter.CompositionMode_Clear)
        q.fillRect(part, Qt.transparent)
        q.setCompositionMode(QPainter.CompositionMode_SourceOver)
        delegate = self.itemDelegate()
        delegate._skip_light = True
        # a cover not loaded yet is NOT read here (reading + decoding it held a frame up to ~0.2 s when scrolling
        # fast through a big library - faster than the background loading): its placeholder goes in for now, the
        # cover loads in the background and is patched in when it arrives (covers_arrived), like Steam does
        covers = self.w.covers
        covers.no_decode = True
        # a still cover draws inside its own cell (cover, ring, bookmark; the lights and a lifted cover's shadow
        # aren't in the strips), and cells never overlap: one cover's cell needs just that cover (patching every
        # cover of the rows around it made each one arriving mid-scroll cost ~4 ms)
        if only is not None:
            todo = [only]
        else:
            rows = (self.model().index(i, 0) for i in self.visible_range(part.adjusted(-40, -40, 40, 40)))
            todo = [index for index in rows if self.visualRect(index).intersects(part)]
        try:
            for index in todo:
                iid = index.data(GAME_ID_ROLE)
                if iid in self._layer_skip:
                    continue
                if not self.w.cover_ready(iid):
                    self._layer_missing.add(iid)
                    self.w.want_cover(iid)
                delegate.paint(q, self._item_option(index), index)
        finally:
            covers.no_decode = False
            delegate._skip_light = False
            q.end()

    _layer_missing: set = set()           # covers drawn as placeholders in the strips, until they've loaded

    def covers_arrived(self) -> None:
        """Covers finished loading in the background: those drawn as placeholders in the strips are patched in."""
        if self._glide_to is None and not self._prebuild_timer().isActive():
            self._prebuild_timer().start(LAYER_STEP_MS)    # (strips skipped for covers still loading: now possible)
        if not self._layer_missing:
            return
        ready = {iid for iid in self._layer_missing if self.w.cover_ready(iid)}
        if ready:
            self._layer_missing -= ready
            if self._layer:
                if self._glide_to is None:
                    self._patch_layer(ready)
                else:
                    self._layer_arrived |= ready       # (gliding: a few a frame - _paint_layer)
                self.viewport().update()
            if self._layer_hold and self._glide_to is None and not self._missing_in_view():
                self._hand_back()

    _layer_hold = 0.0                     # a glide ended with covers still loading: the strips stay until then (its time)

    def _missing_in_view(self) -> bool:
        """Is any cover on screen not in memory (still loading, or pushed out of the cover memory since its strip
        was made - a long fast run draws hundreds)? Those are asked for in the background."""
        missing = False
        # (a little above the view too: normal drawing asks Qt about GRID_TOP_SHIFT higher - see _paint_part)
        for i in self.visible_range(self.viewport().rect().adjusted(0, -GRID_TOP_SHIFT - 40, 0, 40)):
            iid = self.model().index(i, 0).data(GAME_ID_ROLE)
            if iid in self._layer_missing or not self.w.cover_ready(iid):
                self._layer_missing.add(iid)
                self.w.want_cover(iid)
                missing = True
        return missing

    def _hand_back(self) -> None:
        """Gliding is over: normal drawing takes over (the strips are kept for the next flick)."""
        self._layer_hold = 0.0
        if self._layer_arrived:            # (covers that arrived in the last frames: patched now, or the next flick
            arrived, self._layer_arrived = self._layer_arrived, set()      # would show their placeholders)
            self._patch_layer(arrived)
        self._still = None                 # (normal drawing takes over: everything drawn fresh)
        self._keep_layer = True            # (that one repaint changes nothing: the strips stay for the next flick)
        self.viewport().update()

    def _patch_arrived(self) -> None:
        """Gliding: covers that arrived are patched in a few at a time (a batch of ten at once held one frame up)."""
        t0 = time.perf_counter()
        while self._layer_arrived and time.perf_counter() - t0 < LAYER_PATCH_BUDGET_S:
            self._patch_layer({self._layer_arrived.pop()})

    def _patch_layer(self, ids) -> None:
        """Covers whose hover began / settled: only their spots in the strips are drawn again (a whole new set of
        strips in one frame was the hitch the owner saw with the pointer over a cover)."""
        vp, v = self.viewport(), self.verticalScrollBar().value()
        for gid in ids:
            index = self._index_of(gid)
            if index is None:
                continue
            spot = self.visualRect(index)                  # (all a still cover draws: see _draw_strip)
            for k in range((spot.top() + v) // LAYER_TILE, (spot.bottom() + v) // LAYER_TILE + 1):
                tile = self._layer.get(k)
                if tile is not None:
                    top = k * LAYER_TILE - v
                    self._draw_strip(tile, top, spot.intersected(QRect(0, top, vp.width(), LAYER_TILE)), index)

    _rows: dict = {}                      # game / folder id -> its row (checked on use, made again when out of date)

    def _index_of(self, gid: str):
        """The model index showing `gid` (None if it isn't shown). Patching looked for it among five screens of
        covers each time - ~1 ms per cover arriving while scrolling fast through a big library."""
        model = self.model()
        row = self._rows.get(gid)
        if row is not None and row < model.rowCount() and model.index(row, 0).data(GAME_ID_ROLE) == gid:
            return model.index(row, 0)
        if row is None and self._rows:
            return None                    # (made since the last change - _drop_layer - and it isn't shown)
        self._rows = {model.index(i, 0).data(GAME_ID_ROLE): i for i in range(model.rowCount())}
        row = self._rows.get(gid)
        return model.index(row, 0) if row is not None else None

    def _item_option(self, index) -> QStyleOptionViewItem:
        opt = QStyleOptionViewItem()
        opt.rect = self.visualRect(index)
        opt.font = self.font()
        opt.state = QStyle.State_Enabled | (QStyle.State_Selected if self.selectionModel()
                                            and self.selectionModel().isSelected(index) else QStyle.State_None)
        return opt

    def _paint_layer(self) -> None:
        w, vp, v = self.w, self.viewport(), self.verticalScrollBar().value()
        skip = self._sync_skip()
        first, last = v // LAYER_TILE, (v + vp.height() - 1) // LAYER_TILE
        ahead = None                                       # one strip ahead each frame, the way it's going
        if self._glide_to is not None:
            k = last + 1 if (self._heading > 0 if self._heading else self._glide_to >= v) else first - 1
            if 0 <= k <= self._layer_keep()[1] and k not in self._layer:
                ahead = k                                  # (none past the end: it was made and dropped every frame)
        if self._layer_arrived and ahead is None:
            self._patch_arrived()                          # (not in the same frame as a new strip: one or the other)
        p = QPainter(vp)
        backdrop = getattr(w, "backdrop", None)
        if backdrop is not None:
            backdrop.paint_into(p, vp.rect(), vp.mapTo(backdrop, QPoint(0, 0)) - backdrop.library_area().topLeft())
        else:
            p.fillRect(vp.rect(), QColor(COLORS["bg"]))
        for k in range(first, last + 1):
            p.drawPixmap(0, k * LAYER_TILE - v, self._layer_tile(k))
        # the strip ahead (spreads the drawing out), and none kept far away
        if ahead is not None:
            self._layer_tile(ahead)
        elif self._glide_to is None and self._layer_hold and time.perf_counter() - self._layer_hold > LAYER_HOLD_MAX_S:
            self._hand_back()                              # (covers that never arrive don't keep it forever)
        lo, hi = self._layer_keep()
        for k in [k for k in self._layer if k < lo or k > hi]:
            del self._layer[k]
        delegate = self.itemDelegate()
        p.setRenderHint(QPainter.Antialiasing)
        vis = self.visible_range()
        delegate._skip_light = True
        w.covers.no_decode = True                          # (one still loading: its placeholder, like the strips)
        try:
            for i in vis:                                  # covers settling from a hover: drawn as usual, live
                index = self.model().index(i, 0)
                iid = index.data(GAME_ID_ROLE)
                if iid in skip:
                    # (the picked-up game with its light, faded together - exactly as normal drawing does)
                    delegate._skip_light = iid != w._dim_gid
                    delegate.paint(p, self._item_option(index), index)
                    delegate._skip_light = True
        finally:
            w.covers.no_decode = False
            delegate._skip_light = False
        for i in vis:                                      # the lights, live (they breathe)
            index = self.model().index(i, 0)
            iid = index.data(GAME_ID_ROLE)
            centre = CardDelegate.light_center(CardDelegate.cover_rect(self.visualRect(index), w.hover.get(iid, 0.0)))
            if iid == w._dim_gid and iid in skip:
                continue                                   # (drawn with its cover above, faded with it)
            if is_folder(iid):
                paint_light(p, centre, w.folder_colours(iid[len(FOLDER_PREFIX):]), "calm", w.settings.pulse)
            elif iid in w.games:
                delegate._paint_light(p, centre, w.light_state(iid), iid)
        if self.marker is not None:                        # (a dragged game's drop line)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS["select"]))
            p.drawRoundedRect(self.marker, 2, 2)
        p.end()

    def dataChanged(self, *args) -> None:
        self._drop_layer()
        super().dataChanged(*args)

    def selectionChanged(self, *args) -> None:
        self._drop_layer()
        super().selectionChanged(*args)

    def rowsInserted(self, *args) -> None:
        self._drop_layer()
        super().rowsInserted(*args)

    def rowsAboutToBeRemoved(self, *args) -> None:
        self._drop_layer()
        super().rowsAboutToBeRemoved(*args)

    def reset(self) -> None:
        self._drop_layer()
        super().reset()

    def visible_range(self, view: Optional[QRect] = None) -> range:
        """The items in view (or in `view`, viewport coordinates). They're laid out in reading order, so two binary
        searches find them: the animations ask every frame, and walking all of a big library each time cost ~4 ms
        at 600 games."""
        n, view = self.count(), view if view is not None else self.viewport().rect()
        rect = lambda i: self.visualRect(self.model().index(i, 0))
        lo, hi = 0, n
        while lo < hi:                                  # the first item reaching into view
            mid = (lo + hi) // 2
            lo, hi = (mid + 1, hi) if rect(mid).bottom() < view.top() else (lo, mid)
        first, hi = lo, n
        while lo < hi:                                  # the first item below the view
            mid = (lo + hi) // 2
            lo, hi = (mid + 1, hi) if rect(mid).top() <= view.bottom() else (lo, mid)
        return range(first, lo)

    # Lights-only frames (the breathing sync lights, ~60 a second): the covers don't change, so they're copied
    # from a picture of the grid made without its lights, and only the lights are drawn on top. Redrawing the
    # covers every frame cost ~6-9 ms a frame on the owner's PC, which games starting / closing tipped into hitches.
    light_frame: Optional[dict] = None   # set by MainWindow._tick: {"region": QRegion, "lights": [...], "live": QRegion}
    painted_all = False                  # most of the grid was just drawn the usual way (lights included)
    _still: Optional[QPixmap] = None     # the grid without lights (made when first needed)
    _still_dirty = None                  # QRegion of it that's out of date (redrawn there, only there, when next used)

    def _patch_still(self, region) -> None:
        """Redraw just this part of the still picture (lights left out), the rest as it was."""
        delegate = self.itemDelegate()
        delegate._skip_light = True
        self._making_still = True
        try:
            for r in region:
                # each piece reaches GRID_TOP_SHIFT higher: Qt picks which covers to draw by where IT thinks they
                # are (that much higher than where we draw them), so a thin strip near a cover's bottom edge came
                # back as bare backdrop - the dark rectangles under the lights while a folder was hovered (its
                # region splits the light squares into such strips). Redrawing a little more is harmless.
                r = r.adjusted(0, -(GRID_TOP_SHIFT + 2), 0, 0).intersected(self.viewport().rect())
                self.viewport().render(self._still, r.topLeft(), QRegion(r))
        finally:
            delegate._skip_light = False
            self._making_still = False

    def _still_size(self) -> QSize:
        vp = self.viewport()
        dpr = vp.devicePixelRatioF()
        # (math.floor(x + 0.5), not round(): Python rounds 802.5 to 802, so at 125 % it never matched and was
        # remade every frame)
        return QSize(max(1, math.floor(vp.width() * dpr + 0.5)), max(1, math.floor(vp.height() * dpr + 0.5)))

    def prepare_still(self, skip=None) -> bool:
        """Bring the still picture up to date. Called BEFORE a lights-only frame is asked for, never while
        painting: render() inside paintEvent loses that frame's drawing (the lights froze, and under the
        easter egg's dark cover each lost frame darkened their squares again until they were black).
        skip: a part that frame draws live anyway (a hover moving): left out of date for now."""
        if self.bare or not self.isVisible():
            return False
        if self._still is None or self._still.size() != self._still_size():
            self._still, self._still_dirty = self._make_still(), None
        elif self._still_dirty is not None and not self._still_dirty.isEmpty():
            todo = self._still_dirty.subtracted(skip) if skip is not None else self._still_dirty
            if not todo.isEmpty():
                self._patch_still(todo)          # (only what changed since: a hover, a cover loading...)
            self._still_dirty = self._still_dirty.intersected(skip) if skip is not None else None
        return True

    def _make_still(self) -> Optional[QPixmap]:
        vp = self.viewport()
        dpr = vp.devicePixelRatioF()
        size = self._still_size()
        pm = QPixmap(size.width(), size.height())
        pm.setDevicePixelRatio(dpr)
        delegate = self.itemDelegate()
        delegate._skip_light = True
        self._making_still = True
        try:
            vp.render(pm)
        finally:
            delegate._skip_light = False
            self._making_still = False
        return pm

    def paintEvent(self, ev) -> None:
        frame, self.light_frame = self.light_frame, None
        t = time.perf_counter()
        layer = not getattr(self, "_making_still", False) and self.layer_active()
        if layer:
            self._paint_layer()                            # gliding: the covers' strips slide (see layer_active)
        else:
            self._paint_normal(ev, frame)
        if WHEEL_LOG:
            WHEEL_LOG.append(f"{t:.4f} paint {'layer' if layer else 'normal'} {(time.perf_counter() - t) * 1000:.2f} ms "
                             f"v={self.verticalScrollBar().value()} glide={self._glide_to is not None} "
                             f"why={self._layer_blocker() if self._glide_to is not None and not layer else ''}")

    def _layer_blocker(self) -> str:
        w = self.w
        checks = {"bare": self.bare, "hidden": not self.isVisible(), "landing": w._landing is not None,
                  "slides": bool(w.slides), "search": w._search_moving(), "rumble": bool(w.rumble),
                  "no_results": w.no_results != 0,
                  "bookmark": w.bookmark_alpha not in (0.0, 1.0), "bg_live": bool(BACKGROUND["live"])}
        return ",".join(k for k, v in checks.items() if v)

    def _paint_normal(self, ev, frame) -> None:
        # the wheel's strips stay for the next flick only while nothing but the lights / a hover is drawn (a hover's
        # cover is patched in when the next glide starts); anything else drawn may have changed a cover
        # (drawing the lights' still picture changes no cover either: it dropped the strips right after every glide,
        # so each flick began by rebuilding them - an 8-16 ms first frame, every time; owner's launch check 2026-10-20)
        making_still = getattr(self, "_making_still", False)
        if not self._keep_layer and not making_still \
                and not (frame is not None and ev.region().subtracted(frame["region"]).isEmpty()):
            self._drop_layer()
        if not making_still:
            self._keep_layer = False
        live = frame.get("live") if frame is not None else None
        if frame is not None and not getattr(self, "_making_still", False) and not self.bare \
                and self._still is not None and self._still.size() == self._still_size() \
                and (self._still_dirty is None or (self._still_dirty.subtracted(live) if live is not None
                                                   else self._still_dirty).isEmpty()) \
                and ev.region().subtracted(frame["region"]).isEmpty():
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            k = self._still.devicePixelRatio()
            for r in (ev.region().subtracted(live) if live is not None else ev.region()):
                p.drawPixmap(QRectF(r), self._still, QRectF(r.x() * k, r.y() * k, r.width() * k, r.height() * k))
            delegate = self.itemDelegate()
            for centre, iid in frame["lights"]:
                if is_folder(iid):
                    paint_light(p, centre, self.w.folder_colours(iid[len(FOLDER_PREFIX):]), "calm", self.w.settings.pulse)
                else:
                    delegate._paint_light(p, centre, self.w.light_state(iid), iid)
            p.end()
            if live is not None:
                # A hover moving: just that part drawn the usual way (_paint_part, the very same drawing as a normal
                # repaint - Qt picks which covers to draw there). Qt redraws every cover inside the box around
                # everything that changed: with the lights spread over the grid that was all of them, ~4.5 ms a
                # hover frame at 143 Hz (owner's PC, 2026-10-19). Its part of the still picture is out of date now.
                part = ev.region().intersected(live)
                if not part.isEmpty():
                    self._paint_part(QPaintEvent(part))
                    self._still_dirty = (self._still_dirty or QRegion()) | part
            return
        if not getattr(self, "_making_still", False):
            r = ev.region().boundingRect()
            vr = self.viewport().rect()
            if r.width() * r.height() > 0.5 * vr.width() * vr.height():
                self.painted_all = True                   # (see MainWindow._tick)
        if not getattr(self, "_making_still", False) and self._still is not None:
            # something else was drawn: that part of the picture is out of date (patched when next used); a change
            # over most of the grid (scrolling, a search...) just makes it again
            dirty = (self._still_dirty or QRegion()) | ev.region()
            area = sum(r.width() * r.height() for r in dirty)
            if area > 0.5 * vr.width() * vr.height():
                self._still, self._still_dirty = None, None
            else:
                self._still_dirty = dirty
        self._paint_part(ev)
        if self.marker is not None and not self.bare:
            p = QPainter(self.viewport())
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(COLORS["select"]))
            p.drawRoundedRect(self.marker, 2, 2)
            p.end()

    def _paint_part(self, ev) -> None:
        backdrop = getattr(self.w, "backdrop", None)
        if backdrop is not None:
            p = QPainter(self.viewport())
            if not self.bare:
                origin = self.viewport().mapTo(backdrop, QPoint(0, 0)) - backdrop.library_area().topLeft()
                backdrop.paint_into(p, ev.rect(), origin)
            for pm, rects, opacity, scale, shift in (self.w.leaving_frame() if not self.bare else []):
                # covers search / filter changes took away, fading out (each change's own picture)
                dpr = pm.devicePixelRatio()
                p.setOpacity(opacity)
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                for r in rects:
                    if r.translated(shift).intersects(ev.rect()):
                        c = QRectF(r.translated(shift)).center()
                        to = QRectF(0, 0, r.width() * scale, r.height() * scale)
                        to.moveCenter(c)
                        p.drawPixmap(to, pm, QRectF(r.x() * dpr, r.y() * dpr, r.width() * dpr, r.height() * dpr))
                p.setOpacity(1.0)
            # arriving covers, beneath the ones gliding past them (also in snapshots: a cover taken away while
            # still arriving then fades out from how it looked, instead of vanishing)
            if self.w.appearing:
                delegate = self.itemDelegate()
                delegate.under_pass = True
                try:
                    for i in self.visible_range():
                        index = self.model().index(i, 0)
                        if index.data(GAME_ID_ROLE) in self.w.appearing:
                            opt = QStyleOptionViewItem()
                            opt.rect = self.visualRect(index)
                            opt.font = self.font()
                            opt.state = QStyle.State_Enabled | (QStyle.State_Selected if self.selectionModel()
                                                                and self.selectionModel().isSelected(index) else QStyle.State_None)
                            if opt.rect.intersects(ev.rect().adjusted(-40, -40, 40, 40)):
                                delegate.paint(p, opt, index)
                finally:
                    delegate.under_pass = False
            if self.w.no_results > 0 and not self.bare:  # nothing matches the search / filter, or no games yet
                f = QFont(self.font())
                f.setPointSizeF(f.pointSizeF() * 2.7)
                p.setFont(f)
                c = QColor(COLORS["muted"])
                k = ease(self.w.no_results)
                c.setAlphaF(0.7 * k)
                p.setPen(c)
                # placed on the grid itself, not its viewport: the viewport's margins change while results come
                # and go (scroll bar shown / hidden, covers re-centred) and the text moved with them. A little
                # above the middle (the optical centre: the true centre looks low), rising into place as it fades in
                r = self.rect()
                centre = self.viewport().mapFrom(self, QPoint(r.center().x(), r.top() + round(r.height() * NO_RESULTS_Y)))
                box = QRect(0, 0, max(200, round(r.width() * 0.8)), 1200)   # (a long sentence wraps, centred)
                box.moveCenter(centre + QPoint(0, round(NO_RESULTS_RISE * (1 - k))))
                # (a quoted button name never splits across lines: "Add Game")
                text = re.sub(r'"[^"]*"', lambda m: m.group(0).replace(" ", "\u00a0"), tr(self.w.no_results_key))
                p.drawText(box, Qt.AlignCenter | Qt.TextWordWrap, text)
            p.end()
        # Qt picks the covers to draw by where IT places them (GRID_TOP_SHIFT higher than they're drawn): asked about
        # the same area shifted too, a strip at a cover's foot gets that cover (else only the background was drawn
        # there - a dark box under a light when a cover below was selected; owner, 2026-10-18). The painting itself
        # stays inside the real area.
        region = ev.region()
        super().paintEvent(QPaintEvent(region.united(region.translated(0, -GRID_TOP_SHIFT))))

    def resizeEvent(self, ev) -> None:
        self._drop_layer()
        super().resizeEvent(ev)
        if hasattr(self.w, "grid"):
            self.w._fit_grid()

    marker: Optional[QRectF] = None   # where a dragged cover would go (a thin line between two covers)

    def set_marker(self, rect: Optional[QRectF]) -> None:
        if rect != self.marker:
            for r in (self.marker, rect):
                if r is not None:
                    self.viewport().update(r.adjusted(-3, -3, 3, 3).toAlignedRect())
            self.marker = rect
            # (the line is drawn on top of the covers, never into the strips: redrawing its spot changes no cover.
            # Dropping them each time it moved meant a dragged game's edge scroll began by building them all in its
            # first frame - an 8 ms hitch, owner 2026-10-24: "abrupt start")
            self._keep_layer = True

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        if self.layer_active():
            self._still = None                 # (gliding: remade when the glide ends - moving it is wasted work)
        elif self._still is not None and (dx or dy):
            # Qt moved the covers on screen and draws only the strip it uncovered: the lights-only frames' picture
            # moves the same way (the strip is patched in when next used). It used to stay put: small squares of
            # what was there before the scroll showed around the lights (without a background picture)
            k = self._still.devicePixelRatio()
            self._still.scroll(round(dx * k), round(dy * k), self._still.rect())
            if self._still_dirty is not None:
                self._still_dirty.translate(dx, dy)
        self.w.scrolled()
        if BACKGROUND["path"] or self.layer_active():
            self.viewport().update()          # keep the picture still while the covers scroll

    # every cell GRID_TOP_SHIFT lower than Qt places it: drawing, clicks and our own code all go through these
    def visualRect(self, index) -> QRect:
        r = super().visualRect(index)
        return r.translated(0, GRID_TOP_SHIFT) if r.isValid() else r

    def indexAt(self, pos: QPoint):
        """The cover under pos - the cover itself only, not the rest of its cell (the space below it and around it):
        hovering or clicking under a cover counted as on it (owner, 2026-10-18). Hover, clicks, double-clicks and
        right-clicks all ask here. (Its resting place: a lifted cover doesn't run away from the mouse.)"""
        index = super().indexAt(pos - QPoint(0, GRID_TOP_SHIFT))
        if index.isValid() and not CardDelegate.cover_rect(self.visualRect(index), 0).contains(QPointF(pos)):
            return self.model().index(-1, 0)
        return index

    def setSelection(self, rect: QRect, flags) -> None:
        # a click selects through here, by where Qt places the cells: shifted like indexAt, or a click near the foot
        # of a cover chose the cell below it - a game in the row hidden under the bottom bar (owner, 2026-10-18)
        super().setSelection(rect.translated(0, -GRID_TOP_SHIFT), flags)

    def updateGeometries(self) -> None:
        super().updateGeometries()
        sb = self.verticalScrollBar()
        sb.setRange(0, max(0, self.contentsSize().height() + GRID_END_EXTRA - self.viewport().height()))

    def _showing(self, index) -> Optional[int]:
        """The scroll position that brings this item into view (None: it's in view already)."""
        r = self.visualRect(index)
        if not r.isValid():
            return None
        sb = self.verticalScrollBar()
        cover_top = r.top() + CARD_PAD_TOP - ROW_GAP       # (with the usual gap above / below it)
        cover_bottom = r.bottom() - (CARD_PAD_BOTTOM + GRID_SPACING) + ROW_GAP
        if cover_top < 0:
            return sb.value() + cover_top
        if cover_bottom > self.viewport().height():
            return sb.value() + cover_bottom - self.viewport().height()
        return None

    def scrollTo(self, index, hint=QAbstractItemView.EnsureVisible) -> None:
        to = self._showing(index)
        if to is not None:
            self.verticalScrollBar().setValue(to)

    def glide_to_show(self, index) -> None:
        """Like scrollTo, but gliding there like a wheel notch (a new folder made far from the view: the view jumped
        - owner, 2026-10-19, "it snaps back")."""
        to = self._showing(index)
        if to is None:
            return
        dist = abs(to - self.verticalScrollBar().value())     # longer trips take a little longer, like a browser's
        self._aim(float(to), min(0.45, WHEEL_GLIDE_S + dist / 4000))

    def scrollToItem(self, item, hint=QAbstractItemView.EnsureVisible) -> None:
        self.scrollTo(self.indexFromItem(item), hint)    # (Qt's own skips the shift above)


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
    # the window looks the same focused or not: make the unfocused colours exactly the focused ones. When they
    # differ (Windows fills the roles we don't set), Qt redraws the WHOLE window every time focus moves - which on
    # the owner's PC landed as a hitch right as a game's window took focus, and again as ours took it back
    for r in QPalette.ColorRole:
        if r != QPalette.NColorRoles:
            pal.setBrush(QPalette.Inactive, r, pal.brush(QPalette.Active, r))
    app.setPalette(pal)


def restyle_all() -> None:
    """After a theme change: new style sheet, and every open window refreshes what it drew itself."""
    app = QApplication.instance()
    apply_palette()
    root = _SHEET_ROOT[0]
    (root if root is not None else app).setStyleSheet(build_style())
    app.setWindowIcon(app_icon())
    for w in app.topLevelWidgets():
        if w.isWindow() and w.isVisible() and w.windowType() in (Qt.Window, Qt.Dialog):
            set_border(w)                      # (Windows 11: the border follows the theme)
        if hasattr(w, "restyle"):
            w.restyle()


_SHEET_ROOT: list = [None]


def discard(d: QWidget) -> None:
    """Delete a closed window NOW. A window opened with a parent stays as its hidden child until deleted: every
    Settings / Edit / theme / colour window opened since start was restyled again on every theme change (owner,
    2026-10-20: the theme windows were "comically slow", worse the longer the program ran)."""
    if shiboken6.isValid(d):
        shiboken6.delete(d)


def sheet_on(root: Optional[QWidget]) -> None:
    """While Custom Theme is open the style sheet sits on the outermost window (its windows, the colour picker and
    every drop-down / pop-up inside are its children and inherit it) instead of on the whole app: each live colour
    step re-styles in ~52 ms instead of ~131 (measured, main window + Settings + Custom Theme open; the preview
    stepped at ~7 a second). Pixel-identical. None: back on the app (Custom Theme closed)."""
    app = QApplication.instance()
    old, _SHEET_ROOT[0] = _SHEET_ROOT[0], root
    css = build_style()
    if root is not None:
        root.setStyleSheet(css)
        app.setStyleSheet("")
    elif old is not None:
        app.setStyleSheet(css)
        old.setStyleSheet("")

# -- sizes & timing
COVER_W, COVER_H = 176, 248          # roughly the shape of a VN package
CARD_PAD_X, CARD_PAD_TOP, CARD_PAD_BOTTOM = 12, 12, 18
GRID_SPACING = 6
GRID_GAP = 2 * CARD_PAD_X + GRID_SPACING     # the space between two covers, and at the left/right edges
ROW_GAP = CARD_PAD_BOTTOM + GRID_SPACING + CARD_PAD_TOP   # the space between two rows of covers
# the first row sits a whole row gap below the top bar (room to lift on hover with the selection outline),
# and the last one as far above the bottom bar: Qt's grid can't pad its top, so LibraryGrid shifts every cell
GRID_TOP_SHIFT = ROW_GAP - CARD_PAD_TOP
GRID_END_EXTRA = GRID_TOP_SHIFT + ROW_GAP - (CARD_PAD_BOTTOM + GRID_SPACING)
# covers grow with the window (Steam style): COVER_W x COVER_H is the smallest size; the grid works out
# how many columns fit at that size and then stretches the covers so the row is filled exactly
GRID = {"cover_w": float(COVER_W), "cover_h": float(COVER_H)}
HOVER_LIFT = 6                       # px the cover rises on hover
HOVER_GROW = 0.03                    # 3% bigger on hover
WHEEL_GLIDE_S = 0.2                  # mouse-wheel glide: one notch's curve from rest (shorter when already moving)
WHEEL_LINE_PX = 100 / 3              # per wheel "line" (Chromium: 100 px a notch at Windows' default 3 lines)
HOVER_AFTER_SCROLL_MS = 250          # wheel scrolling: the cover under a still pointer lifts this long after it stops
AUTO_SCROLL_DEAD = 6                 # wheel-press scrolling: px from the press point before it moves
AUTO_SCROLL_SPEED = 6.0              # ... speed = (px past that) ^ 1.2 x this, px/s (100 px away ~1,200 px/s)
AUTO_SCROLL_CLICK_S = 0.35           # ... let go sooner without moving = a click: keeps running until the next click
AUTO_SCROLL_REST_S = 0.15            # ... back in the middle this long before both chevrons light (a quick reverse skips it)
LAYER_TILE = 256                     # wheel glides: the covers' picture is made in strips this tall (LibraryGrid._layer)
LAYER_AHEAD = 2                      # ... kept ready this many screens above and below the view
LAYER_QUIET_MS = 400                 # ... made this long after the last change ...
LAYER_STEP_MS = 12                   # ... one at a time, this far apart (never a long freeze)
LAYER_HOLD_MAX_S = 10.0              # ... a glide's end waits at most this long for covers still loading on screen (a
#                                      safety net: the strips show the right picture meanwhile; a weak PC can take > 2 s)
LAYER_PATCH_BUDGET_S = 0.0015        # ... gliding: at most this long a frame patching in covers that have just loaded
FLAT_TILES = 64                      # finished (rounded) covers kept ready for repaints (CardDelegate._flat; ~15 MB)
HOVER_MS = 150                       # ease in/out time
SLIDE_S = 0.22                       # covers sliding aside into their new places after a drop
# search / filter: nothing slides. A cover whose place changes fades out where it was (with the ones taken away)
# and fades back in at its new place; covers that keep their place stay still. OUT first, then IN, so two pictures
# never blend in one place; and the grid only changes once typing pauses for SEARCH_PAUSE_MS
SEARCH_PAUSE_MS = 120
VIEW_SAVE_DELAY_MS = 900             # sort / filter / view choices are saved once the fade has finished
LEAVE_S = 0.16                       # OUT: fade + shrink this long ...
LEAVE_SHRINK = 0.12
ARRIVE_WAIT = 0.1                    # ... IN starts this much after a change that takes covers away ...
APPEAR_S = 0.36                      # ... and takes this long (fade + grow + rise) ...
APPEAR_GROW = 0.1                    # ... starting this much smaller ...
APPEAR_RISE = 8                      # ... and this many px lower ...
APPEAR_STAGGER = 0.025               # ... each one a little after the one before it (in grid order) ...
APPEAR_STAGGER_MAX = 0.15            # ... the last at most this much later
ARRIVE_CLEAR = 0.12                  # (a place is clear once whatever was leaving it is fainter than this)
MAX_LEAVING_GROUPS = 4               # leaving pictures from earlier changes still finishing
NO_RESULTS_OUT_S = 0.12              # "No Results Found" fades out quickly when covers come back (they wait) ...
NO_RESULTS_S = 0.3                   # ... and fades in (once the last cover has gone) ...
NO_RESULTS_RISE = 8                  # ... rising this many px into place ...
NO_RESULTS_Y = 0.44                  # ... at this height of the library (the optical centre); it goes at once
PULSE_CALM_S = 2.6                   # one breath when all is well
PULSE_BUSY_S = 1.0                   # quicker while checking/syncing
# Settings' faint lines: px of room (above, below) the line itself, by line, language and My Device - so each has
# exactly the same clear space above and below it, the same for every line (owner, 2026-10-18; measured on the screen:
# the texts' own shapes differ by language and by what's shown in each mode)
SETTINGS_RULES = {
    "top":   {("en", False): (14, 7), ("ja", False): (14, 7), ("en", True): (14, 7), ("ja", True): (14, 8)},
    "cloud": {("en", False): (25, 18), ("ja", False): (24, 19)},   # (+11 each side: room for the open This Is list)
    "anki":  {("en", False): (14, 7), ("ja", False): (12, 7), ("en", True): (14, 7), ("ja", True): (12, 7)},
}
MENU_TICK, MENU_TICK_GAP = 14, 12     # the tick after the reading status in use (right-click menu): size, room before it
SUBMENU_GAP = 3                      # px between a menu and its submenu (as LIST_GAP: the drop-down lists)
MENU_SHADOW = 5                      # px right of / below a menu for its shadow (= _ListShadow.SIZE)
LIGHT_FADE_S = 0.22                  # a light changing colour cross-fades this long (owner: 0.45 felt slow)
OPEN_SETTLE_S = 0.35                 # lights keep their first look this long after the window appears (see _paint_light)
BAR_XFADE_S = 0.2                    # the Read button's cross-fade, old look -> new look (BarFader)
BAR_OUT_S, BAR_IN_S = 0.15, 0.2      # every other piece of the bar that changed: fades out, then in (BarFader)
PRESENCE_CHECK_MS = 2000             # Discord presence: what the window is doing, checked this often
PRESENCE_AWAY_S = 300                # ...and "Away" after this long without using Ikemen Reader (owner: 5 minutes)
INSTANT_CLOSE_S = 5                 # a game that closes within this long of appearing "closed right after starting"
ICON_DIM = 0.4                     # the bar's icon buttons when greyed (trash) or off (bookmarks): one dimness
BAR_WAKE_S = 0.22                    # nothing selected <-> selected: the whole bar blends as one, over this long
COUNT_UP_SETTLE_S = 0.25           # after the bar's cross-fade: the old total sits still this long, then rolls up
COUNT_UP_WAIT_S = 15                # after a session: how long the count-up waits for the re-read (Google Drive busy)
SYNC_SHOW_AFTER_S = 0.3              # a sync around a session shows "Syncing" only if it takes longer than this
PREFETCH_QUIET_MS = 500              # covers are prepared in the background once nothing has changed this long
SCROLLBAR_FADE_MS = 200              # scroll bar fade in/out
RECHECK_POLLS = 15                   # re-check every game's saves every 15 live polls (~1 min)
MENU_LINE_INSET = 8                  # right-click menu dividers stop this far from the menu's sides (MenuLines)
BUTTON_GAP = 8                       # between the buttons at the bottom of every window
STOP_PATIENCE_MS = 6000             # Stop: how long a game gets to close before "force it?"
SCROLLBAR_LINGER_S = 0.6             # stays visible this long after scrolling / leaving the edge
SCROLLBAR_EDGE_PX = 36               # mouse this close to the right edge shows it
DRAG_SCROLL_ZONE = 70                # dragging a game this close to the grid's top/bottom scrolls it...
DRAG_SCROLL_MIN, DRAG_SCROLL_MAX = 2, 60   # ...this many px per frame, faster the further you push
DRAG_SCROLL_EASE_S = 0.16          # ... its speed eases towards the new one over about this long
DRAG_SCROLL_BRAKE = 5000            # ... and slows into the library's end at this rate (px/s²)
DRAG_AIM_SPEED = 8                  # let go scrolling faster than this: the game goes back (rows race past, nothing to aim at)
LIGHT_R = 5.5
LOGO_H = 30


class _WheelLog:
    """IKEMEN_DEBUG_WHEEL=1: every wheel event, glide step and grid paint -> %APPDATA%\\IkemenReader\\wheel.log."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w", encoding="utf-8", buffering=1 << 16)
        import atexit
        atexit.register(self.f.flush)

    def append(self, line: str) -> None:
        self.f.write(line + "\n")


WHEEL_LOG = _WheelLog(Path(os.environ.get("APPDATA", ".")) / "IkemenReader" / "wheel.log") \
    if os.environ.get("IKEMEN_DEBUG_WHEEL") else None

GAME_ID_ROLE = Qt.UserRole + 1          # item id: a game id, or FOLDER_PREFIX + folder id
FOLDER_PREFIX = "folder:"
MIME_GAME = "application/x-ikemen-game"  # a game being dragged inside the grid
FOLDER_PAD, FOLDER_GAP = 10, 8           # mosaic inside a folder tile
# when a folder holds games in different states, its light shows the most important one
STATE_PRIORITY = ["conflict", "syncing", "starting", "playing", "push", "pull", "checking", "failed", "detect", "unset", "empty",
                  "solo", "synced"]


def is_folder(item_id: Optional[str]) -> bool:
    return bool(item_id) and item_id.startswith(FOLDER_PREFIX)

# sync state -> (light colour, pulse: "calm" / "busy" / "steady", bottom-bar text key)
STATE_LOOK = {
    "synced":   ("green", "calm", "sync.state.synced"),
    "solo":     ("lavender", "calm", "sync.state.solo"),     # in sync; the other device hasn't set it up yet
    "push":     ("amber", "calm", "sync.state.push"),
    "pull":     ("blue", "calm", "sync.state.pull"),
    "conflict": ("red", "steady", "sync.state.conflict"),
    "empty":    ("grey", "calm", "sync.state.empty"),
    "unset":    ("yellow", "steady", "sync.state.unset"),    # the only one of these that needs you to do something
    "detect":   ("grey", "calm", "sync.state.detect"),
    "checking": ("grey", "busy", "sync.state.checking"),
    "failed":   ("grey", "calm", "sync.state.failed"),       # couldn't be read just now (a file in use, Drive busy)
    "playing":  ("blue", "calm", "sync.state.playing"),
    "starting": ("blue", "busy", "sync.state.starting"),
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
    chevron_up = Path(ICONS["chevron_up"] or ICONS["chevron"]).as_posix()
    chevron_right = Path(ICONS["chevron_right"] or ICONS["chevron"]).as_posix()
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
QFrame#notes_box {{ background: transparent; }}
QLabel#notes_title {{ color: {c['text']}; font-size: 20pt; }}
QTextEdit#notes {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 6px;
                       padding: 6px 8px; font-size: 11pt; selection-background-color: {c['button_hover']}; }}
QComboBox#fontpick QAbstractItemView::item {{ padding: 5px 5px; min-height: 22px; }}
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
QComboBox[opens_up="true"]::down-arrow {{ image: url({chevron_up}); }}
QComboBox[opens_up="true"]::down-arrow:on {{ top: -1px; }}
QComboBoxPrivateContainer {{ background: transparent; border: none; }}
QProgressBar {{ background: {c['input']}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background: {c['play']}; border-radius: 4px; }}
QComboBox QAbstractItemView {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 6px;
                               padding: 4px 0px; selection-background-color: {c['button_hover']}; outline: none; }}
QComboBox QAbstractItemView::item {{ padding: 1px 5px 3px 5px; }}   /* (the words start where the box's do) */
QPushButton {{ background: {c['button']}; border: 1px solid {c['button']}; border-radius: 6px; padding: 6px 14px; }}
QPushButton:hover {{ background: {c['button_hover']}; border-color: {c['button_hover']}; }}
QPushButton:disabled {{ color: {c['other_text']}; background: {c['other_bg']}; border-color: {c['other_bg']}; }}
QPushButton#play {{ background: {c['play']}; color: {c['play_text']}; border: none; font-size: 17.5pt;
                    padding: 6px 32px; margin: 4px 3px 2px 3px; border-radius: 10px; }}
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
QLineEdit#title_edit {{ font-size: 16pt; padding: 2px 0 1px 0; }}
QLineEdit#title_edit:focus {{ border-color: {c['focus']}; }}
QLabel#muted, QLabel#hint {{ color: {c['muted']}; }}
QLabel#hint {{ font-size: 9pt; }}
QLabel#section {{ color: {c['muted']}; padding-top: 8px; }}
QDialog#settings_dialog QLabel#section {{ padding-top: 5px; }}   /* a little tighter: Settings fits the screen */
QLabel#banner {{ background: {c['input']}; border-radius: 6px; padding: 6px 10px; }}
QCheckBox, QRadioButton {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; }}
QCheckBox::indicator {{ border: 1px solid {c['border']}; border-radius: 4px; background: {c['input']}; }}
QCheckBox::indicator:checked {{ background: {c['select']}; border-color: {c['select']}; image: url({check}); }}
QRadioButton::indicator {{ border: 1px solid {c['border']}; border-radius: 9px; background: {c['input']}; }}
QRadioButton::indicator:checked {{ border-color: {c['select']}; background: qradialgradient(cx:0.5, cy:0.5,
    radius:0.5, fx:0.5, fy:0.5, stop:0 {c['select']}, stop:0.42 {c['select']}, stop:0.5 {c['input']}, stop:1 {c['input']}); }}
QMenu {{ background: {c['input']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 4px;
         margin: 0px {MENU_SHADOW}px {MENU_SHADOW}px 0px; }}
QMenu::right-arrow {{ image: url({chevron_right}); width: 12px; height: 12px; right: 6px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {c['button_hover']}; }}
QMenu::item:disabled {{ color: {c['other_text']}; }}
QMenu#ticks::item {{ padding: 6px {20 + MENU_TICK + MENU_TICK_GAP}px 6px 20px; }}
QMenu::separator {{ height: 1px; background: transparent; margin: 4px 0px; }}
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
QWidget#smooth_tip {{ background: {c['input']}; border: 1px solid {c['border']}; }}
QLabel#smooth_tip_text {{ color: {c['text']}; padding: 4px; background: transparent; }}
QMessageBox QLabel {{ min-width: 320px; }}
"""


def suggest_save(settings: LocalSettings, exe: str, title: str,
                 other: Optional[tuple[str, str]]) -> Optional[detect.Suggestion]:
    """Where this game probably saves on This Device (see detect.suggest_save_folder). Slow-ish: run it
    in the background."""
    if not exe.strip():
        return None
    game_dir = str(Path(exe).parent)
    skip = [settings.cloud_root, str(default_app_dir())] + [str(Path(p).parent) for p in (settings.luna_path, settings.le_path, settings.anki_path) if p]
    usable = lambda p: save_folder_problem(str(p), settings.cloud_root) is None and not any(
        os.path.normcase(str(p)).startswith(os.path.normcase(s)) for s in skip if s)
    return detect.suggest_save_folder(game_dir, [title, Path(exe).stem, Path(game_dir).name], other, usable, skip)


def _save_folder_usable(settings: LocalSettings) -> Callable[[str], bool]:
    skip = [settings.cloud_root, str(default_app_dir())] + [str(Path(p).parent) for p in (settings.luna_path, settings.le_path, settings.anki_path) if p]
    return lambda p: save_folder_problem(str(p), settings.cloud_root) is None and not any(
        os.path.normcase(str(p)).startswith(os.path.normcase(s)) for s in skip if s)


def certain_saves(settings: LocalSettings, games: list[Game]) -> dict[str, str]:
    """{game id: save folder} for games set up here with an empty save folder whose place on this device follows
    for certain from the other device's (detect.certain_from_other_device). Never a folder another game here
    already uses (same, inside or around). Touches the disk: run it in the background."""
    me, other = settings.machine_name, other_slot(settings.machine_name)
    usable = _save_folder_usable(settings)
    norm = lambda p: os.path.normcase(os.path.abspath(p)).rstrip("\\/")
    taken = [norm(g.paths_for(me).save_dir) for g in games if g.paths_for(me).save_dir]
    out: dict[str, str] = {}
    for g in games:
        here, there = g.paths_for(me), g.paths_for(other)
        if not here.exe or here.save_dir or not there.save_dir:
            continue
        p = detect.certain_from_other_device(str(Path(here.exe).parent), there.exe, there.save_dir)
        if p is None or not usable(p):
            continue
        n = norm(str(p))
        if any(t == n or t.startswith(n + os.sep) or n.startswith(t + os.sep) for t in taken):
            continue
        out[g.game_id] = str(p)
        taken.append(n)
    return out


def open_folder(path: str) -> None:
    if path and Path(path).exists():
        watch = ExplorerAppearsDrawn.start()
        try:
            os.startfile(path)  # type: ignore[attr-defined]  (Windows only)
        except OSError:
            watch.stop()        # Windows refused (no permission / no handler): nothing else to do


EXPLORER_WATCH_S = 3.0          # how long after asking for a folder a new Explorer window is looked for
EXPLORER_WAIT_MAX_S = 1.5       # a hidden Explorer window is shown after this, whatever it looks like
EXPLORER_SAMPLE_S = 0.025       # how often its picture is read while it's hidden
_EXPLORER_WATCHES: set = set()  # watchers whose hook is live (see ExplorerAppearsDrawn.start)
_EXPLORER_HIDING: set = set()   # watchers with an Explorer window still hidden (show_hidden_explorer_windows)


class ExplorerAppearsDrawn:
    """Open Game Folder / Open Save Folder: Explorer's new window faded in half-drawn - a white box for ~100 ms
    before its dark folder list (owner, 2026-10-19; filmed one screen read per refresh). It's another program's
    window: Windows refuses to cloak it (access denied), but lets us switch off its fade and make it see-through.
    A WinEvent hook (out of context: delivered to this thread's message loop) catches the window as it's CREATED,
    still hidden: fade off + layered with alpha 0. A background thread then reads its picture (PrintWindow with
    PW_RENDERFULLCONTENT works while it's see-through) and shows it once two readings in a row are identical (and,
    in dark mode, not mostly white) - it appears at once, fully drawn. Measured here: white 92 % at show, dark and
    still ~125 ms later. Its own style is put back afterwards (fade on again for closing, not layered)."""

    def __init__(self):
        self.hook = None
        self.windows: dict = {}             # hidden for now: hwnd -> its extended style before we touched it
        self.lock = threading.Lock()

    @classmethod
    def start(cls) -> "ExplorerAppearsDrawn":
        self = cls()
        if sys.platform != "win32":
            return self
        try:
            from ctypes import wintypes
            u = ctypes.WinDLL("user32")      # (own function objects: argtypes set here touch nobody else's)
            dwm = ctypes.WinDLL("dwmapi")
            proc_type = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, ctypes.c_long,
                                           ctypes.c_long, wintypes.DWORD, wintypes.DWORD)
            u.SetWinEventHook.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, proc_type,
                                          wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
            u.SetWinEventHook.restype = wintypes.HANDLE
            u.UnhookWinEvent.argtypes = [wintypes.HANDLE]
            u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            u.GetParent.argtypes = [wintypes.HWND]
            u.GetParent.restype = wintypes.HWND
            u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
            u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
            u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
            u.IsWindow.argtypes = [wintypes.HWND]
            u.IsWindowVisible.argtypes = [wintypes.HWND]
            u.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
            u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            self.u, self.dwm = u, dwm
            dark = _apps_use_dark_mode()

            def on_event(hook, event, hwnd, obj, child, thread, when):
                if obj != 0 or not hwnd or hwnd in self.windows or u.GetParent(hwnd) or u.IsWindowVisible(hwnd):
                    return
                name = ctypes.create_unicode_buffer(32)
                u.GetClassNameW(hwnd, name, 32)
                if name.value != "CabinetWClass":
                    return
                style = u.GetWindowLongW(hwnd, -20)
                with self.lock:
                    self.windows[hwnd] = style
                    _EXPLORER_HIDING.add(self)
                self._fade(hwnd, False)
                u.SetWindowLongW(hwnd, -20, style | 0x00080000)               # WS_EX_LAYERED
                u.SetLayeredWindowAttributes(hwnd, 0, 0, 2)                    # LWA_ALPHA, see-through
                threading.Thread(target=self._reveal_when_drawn, args=(hwnd, dark), daemon=True).start()

            self.proc = proc_type(on_event)                                    # (kept alive while hooked)
            # EVENT_OBJECT_CREATE only, other programs only (WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS)
            for older in list(_EXPLORER_WATCHES):        # (one at a time: a quick second click replaces the first)
                older.stop()
            self.hook = u.SetWinEventHook(0x8000, 0x8000, None, self.proc, 0, 0, 0x0002)
            if self.hook:
                # kept here until stopped: Windows calls self.proc for as long as the hook lives, and a watcher
                # thrown away meanwhile crashed the program on the next window created (found 2026-10-19)
                _EXPLORER_WATCHES.add(self)
                QTimer.singleShot(int(EXPLORER_WATCH_S * 1000), self.stop)
        except (OSError, AttributeError):
            pass
        return self

    def stop(self) -> None:
        if self.hook:
            self.u.UnhookWinEvent(self.hook)
            self.hook = None
        _EXPLORER_WATCHES.discard(self)

    def _fade(self, hwnd, on: bool) -> None:
        value = ctypes.c_int(0 if on else 1)
        self.dwm.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(value), ctypes.sizeof(value))   # TRANSITIONS_FORCEDISABLED

    def _reveal_when_drawn(self, hwnd, dark: bool) -> None:
        start, last = time.perf_counter(), None
        try:
            while time.perf_counter() - start < EXPLORER_WAIT_MAX_S and self.u.IsWindow(hwnd):
                time.sleep(EXPLORER_SAMPLE_S)
                if not self.u.IsWindowVisible(hwnd):
                    continue                    # (not shown yet: nothing drawn to read)
                look = self._look(hwnd)
                if look is not None and look == last and not (dark and look[0] > 0.5):
                    break
                last = look
        finally:
            self._show(hwnd)

    def _show(self, hwnd) -> None:
        """Seen again, its own style back (not layered, its fade on for closing). Once only (the reveal thread, or
        closing Ikemen Reader meanwhile)."""
        with self.lock:
            style = self.windows.pop(hwnd, None)
            if not self.windows:
                _EXPLORER_HIDING.discard(self)
        if style is not None and self.u.IsWindow(hwnd):
            self.u.SetLayeredWindowAttributes(hwnd, 0, 255, 2)
            self.u.SetWindowLongW(hwnd, -20, style)
            self._fade(hwnd, True)

    def _look(self, hwnd):
        """(share of near-white points, a fingerprint of the picture) - read from the window itself, though it's
        see-through - or None if it can't be read."""
        from ctypes import wintypes
        r = wintypes.RECT()
        self.u.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 20 or h <= 50:
            return None
        gdi, BIH = _explorer_gdi()
        head = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        dc = gdi.CreateCompatibleDC(None)
        bmp = gdi.CreateDIBSection(dc, ctypes.byref(head), 0, ctypes.byref(bits), None, 0)
        if not dc or not bmp or not bits.value:
            if bmp:
                gdi.DeleteObject(bmp)
            if dc:
                gdi.DeleteDC(dc)
            return None
        try:
            gdi.SelectObject(dc, bmp)
            if not self.u.PrintWindow(hwnd, dc, 2):                           # PW_RENDERFULLCONTENT
                return None
            data = ctypes.string_at(bits, w * h * 4)
        finally:
            gdi.DeleteObject(bmp)
            gdi.DeleteDC(dc)
        points = light = 0
        for y in range(40, h - 10, 12):                                        # (below the title bar)
            row = y * w * 4
            for x in range(10, w - 10, 12):
                i = row + x * 4
                points += 1
                light += data[i] > 200 and data[i + 1] > 200 and data[i + 2] > 200
        return round(light / max(1, points), 3), zlib.crc32(data)


_GDI: list = []


def _explorer_gdi():
    """gdi32 with the argument types ExplorerAppearsDrawn._look needs, and the bitmap header type - set up once."""
    if not _GDI:
        from ctypes import wintypes
        gdi = ctypes.WinDLL("gdi32")         # (own function objects: argtypes set here touch nobody else's)
        gdi.CreateCompatibleDC.restype = wintypes.HDC
        gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi.CreateDIBSection.restype = wintypes.HBITMAP
        gdi.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                         wintypes.HANDLE, wintypes.DWORD]
        gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi.SelectObject.restype = wintypes.HGDIOBJ
        gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi.DeleteDC.argtypes = [wintypes.HDC]

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]
        _GDI[:] = [gdi, BIH]
    return _GDI[0], _GDI[1]


def show_hidden_explorer_windows() -> None:
    """Closing: an Explorer window still hidden while it draws (a folder opened a moment before) is shown now -
    the thread that would show it ends with the program, and the window would stay invisible."""
    for watcher in list(_EXPLORER_HIDING):
        for hwnd in list(watcher.windows):
            watcher._show(hwnd)


def _apps_use_dark_mode() -> bool:
    """Windows' own app colour setting (Settings > Personalisation > Colours): Explorer follows it."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


_BACK_KEYS = ("cancel", "no", "later")          # "don't do it" buttons: always on the far left


def message(parent, text: str, title: str = "", buttons=(("ok", "OK"),), icon=None, dismiss: str = "",
            rich: bool = False, done_when: Optional[Callable[[], bool]] = None) -> str:
    """A message with our own (translated) buttons. Returns the chosen key; closing the window counts
    as the last button. (Our own dialog rather than QMessageBox, which measures its text before our
    font is applied and then cuts it off instead of wrapping it.) done_when: checked a few times a second -
    once it's true the message closes by itself and returns "done" (e.g. the game it's about was closed)."""
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
    row.setSpacing(BUTTON_GAP)
    row.addStretch()
    chosen = {"key": dismiss or buttons[-1][0]}     # closing the window = `dismiss` (or the last button)
    # buttons are listed most important first; shown Cancel far left, the main action far right
    backs = [b for b in buttons if b[0] in _BACK_KEYS]
    shown = backs + [b for b in reversed(buttons) if b[0] not in _BACK_KEYS]
    # Enter = the safe "don't" button when there is one (Cancel / Later / Keep Open...), else the first listed. Set
    # outright: left to Qt, Enter went to whichever button had the focus (the leftmost) - the same in effect, but
    # by accident (found 2026-10-17)
    enter = backs[0][0] if backs else buttons[0][0]
    default_button = None
    for key, label in shown:
        b = QPushButton(label)
        b.setAutoDefault(False)                    # (focus mustn't move Enter elsewhere)
        b.clicked.connect(lambda _=False, k=key: (chosen.update(key=k), d.accept()))
        if key == enter:
            b.setDefault(True)
            default_button = b
        row.addWidget(b)
    lay.addLayout(row)
    d.setFixedSize(d.sizeHint())              # sized to the wrapped text
    if default_button is not None:
        default_button.setFocus()
    if done_when is not None:
        watch = QTimer(d)
        watch.setInterval(400)

        def check() -> None:
            try:
                over = done_when()
            except Exception:                 # (a failed check just doesn't close it)
                over = False
            if over and d.isVisible():
                chosen.update(key="done")
                d.accept()
        watch.timeout.connect(check)
        watch.start()
    d.exec()
    return chosen["key"]


def ask_yes_no(parent, text: str, yes: str, no: str, title: str = "") -> bool:
    """yes / no: no far left - and on Enter, and closing the window (see message)."""
    return message(parent, text, title, buttons=[("yes", yes), ("no", no)], dismiss="no") == "yes"


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


def confirm_remove(parent, game: Game, machine: str, solo: bool = False) -> Optional[str]:
    """"Are You Sure?" before a game leaves the library. Set up on the other device too: Cancel | Remove From Both
    Devices | Remove From This Device (owner, 2026-10-17); otherwise Cancel | Confirm. Cancel is the safe default (also
    Enter / closing). Returns "here", "both" or None. solo: always Cancel | Confirm, and only from this computer (the
    other one keeps it - owner's plan)."""
    import html
    shared = not solo and any(p.exe for m, p in game.paths.items() if m != machine)
    key = "game.remove_choose" if shared else "game.remove_confirm"
    body = tr(key, title=game.title)
    if game.notes.strip():                       # (they live in the game file: they go with it)
        body += "\n" + tr("game.remove_notes_both" if shared else "game.remove_notes")
    body = html.escape(body).replace("\n", "<br>")
    text = f'<span style="font-size:13pt; font-weight:600">{html.escape(tr("game.remove_sure"))}</span><br><br>{body}'
    buttons = ([("here", tr("game.remove_here")), ("both", tr("game.remove_both")), ("no", tr("game.cancel"))]
               if shared else [("here", tr("game.remove_yes")), ("no", tr("game.cancel"))])
    choice = message(parent, text, title=tr("game.remove_sure"), buttons=buttons, dismiss="no", rich=True)
    return None if choice == "no" else choice


def remove_game_from(library: Library, game_id: str, machine: str, how: str) -> dict:
    """Carry out confirm_remove's answer; returns what Undo needs."""
    return library.remove(game_id) if how == "both" else library.remove_here(game_id, machine)


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

    def _run(self, fn, done, failed=None, low=False):
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
        # low: background work the window must never wait for (cover loading): on a weak or busy PC Windows gives
        # the window its turn first - on one core, loading covers while flinging through a big library held
        # scroll frames up to ~170 ms (the loader's time slices)
        t.start(QThread.Priority.LowPriority if low else QThread.Priority.InheritPriority)
        return t


def sync_state(engine: SyncEngine, game: Game, machine: str) -> str:
    mine = game.paths_for(machine)
    if not mine.exe:
        return "unset"
    if not mine.save_dir:
        return "detect"
    action = engine.status(game.game_id, mine.save_dir, game.registry_key)
    if action is Action.NOTHING:
        d = Path(mine.save_dir)
        if not ((d.is_dir() and any(d.iterdir())) or game.registry_key):
            return "empty"
        return "synced" if game.paths_for(other_slot(machine)).exe else "solo"
    return {Action.PUSHED: "push", Action.PULLED: "pull", Action.CONFLICT: "conflict"}[action]


class StatusChecker(QThread):
    """Fingerprints every game's saves off the UI thread, and syncs what can be synced without asking
    (silent sync): uploads saves only this device changed, downloads saves only the other device changed.
    Saves changed on both sides are left for the owner. Never touches the game being read (skip), and
    never downloads into a game that is open outside Ikemen Reader (it would write its old saves back)."""
    done = Signal(dict, list, list)

    def __init__(self, engine: SyncEngine, games: list[Game], machine: str, skip: set):
        super().__init__()
        self.engine, self.games, self.machine, self.skip = engine, games, machine, set(skip)

    def run(self) -> None:
        states, uploaded, downloaded = {}, [], []
        for g in self.games:
            try:
                s = sync_state(self.engine, g, self.machine)
                if s in ("push", "pull") and g.game_id not in self.skip:
                    mine = g.paths_for(self.machine)
                    open_now = s == "pull" and launcher.already_running(Path(mine.exe).parent)
                    if not open_now:
                        did = self.engine.sync_if_safe(g.game_id, mine.save_dir, g.registry_key)
                        if did is Action.PUSHED:
                            uploaded.append(g.title)
                        elif did is Action.PULLED:
                            downloaded.append(g.title)
                        s = sync_state(self.engine, g, self.machine)
                states[g.game_id] = s
            except (OSError, ValueError):
                states[g.game_id] = "failed"        # (it IS set up: "Not Set Up Yet" was the wrong answer)
        self.done.emit(states, uploaded, downloaded)


class SyncWorker(QThread):
    """Sync chosen games now, without launching them (saves changed on both devices: the owner picks
    which to keep in the usual window, asked through a blocking signal like PlayWorker)."""
    conflict = Signal(object)
    finished_all = Signal(list)          # [(title, i18n key)]

    def __init__(self, engine: SyncEngine, games: list[Game], machine: str):
        super().__init__()
        self.engine, self.games, self.machine = engine, games, machine
        self.answer: Optional[str] = None

    def _ask(self, info: ConflictInfo) -> Optional[str]:
        self.answer = None
        self.conflict.emit(info)         # BlockingQueuedConnection: returns once the window is closed
        return self.answer

    def run(self) -> None:
        out = []
        for g in self.games:
            mine = g.paths_for(self.machine)
            try:
                if mine.exe and launcher.already_running(Path(mine.exe).parent):
                    out.append((g.title, "sync.skipped_running"))
                    continue
                r = self.engine.before_launch(g.game_id, mine.save_dir, self._ask, g.registry_key)
                out.append((g.title, r.message))
            except (OSError, ValueError):
                out.append((g.title, "sync.failed"))
        self.finished_all.emit(out)


class PlayWorker(QThread):
    """Runs launcher.play() (sync down, run, wait, sync up) without freezing the window."""
    status = Signal(str)
    conflict = Signal(object)        # ConflictInfo -> the window asks the owner, blocking this thread
    result = Signal(object)          # PlayResult
    phase = Signal(str)              # "syncing" / "starting" / "playing" / "done"

    def __init__(self, game: Game, settings: LocalSettings, library: Library, engine: SyncEngine,
                 without: tuple[str, ...] = (), watch: bool = False):
        super().__init__()
        self.game, self.settings, self.library, self.engine = game, settings, library, engine
        self.without, self.watch = without, watch
        self.answer: Optional[str] = None
        self.stop_pressed = threading.Event()     # Stop: stop waiting for the game / ask it to close
        self.force_close = threading.Event()      # the owner agreed to end the game at once

    @staticmethod
    def _take(ev: threading.Event) -> bool:
        """True once per press (the launcher acts on each press once)."""
        if ev.is_set():
            ev.clear()
            return True
        return False

    def _ask(self, info: ConflictInfo) -> Optional[str]:
        self.answer = None
        self.conflict.emit(info)     # BlockingQueuedConnection: returns once the dialog is closed
        return self.answer

    def run(self) -> None:
        try:
            r = play(self.game, self.settings, self.library, self.engine, on_conflict=self._ask,
                     status=self.status.emit, on_phase=self.phase.emit, without=self.without, watch=self.watch,
                     stop=lambda: self._take(self.stop_pressed), force=lambda: self._take(self.force_close))
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


def merge_order(full: list, shown: list) -> list:
    """A view's new order put into the whole order: the items shown take the places shown items had (in their new
    order); items the view hides keep theirs (rearranging This Device mustn't move the other device's games)."""
    full = list(dict.fromkeys(full))
    seen = set(shown)
    full += [i for i in shown if i not in set(full)]
    it = iter(shown)
    return [next(it) if i in seen else i for i in full]


class CoverCache:
    """Covers ready to draw. Only the most recently shown ones stay in memory (the grid only draws
    what's on screen), so a library of hundreds of games doesn't eat hundreds of MB."""
    MAX_COVERS = 120          # ~120 x 0.85 MB at most
    KEEP_DRAWN = 48           # the covers drawn last are never dropped for ones prepared ahead (> a screenful)
    MAX_ORIGINALS = 4         # full-size images kept while cropping/zooming the same cover
    no_decode = False         # True: a cover not yet in memory isn't read now (get returns None)

    def __init__(self):
        from collections import OrderedDict
        self._images: "OrderedDict[tuple[str, float], QImage]" = OrderedDict()
        self._cache: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        # a cover file's modified time, remembered: covers are drawn dozens of times a second, and asking
        # the cloud drive (Google Drive's G:) each time made everything lag, badly while it was busy
        self._mtimes: dict[str, Optional[float]] = {}
        self._ready: dict[tuple, QImage] = {}      # decoded in the background, not yet on screen

    def forget_files(self) -> None:
        """The library was reloaded: look at the cover files again (a cover may have been changed)."""
        self._mtimes.clear()

    def get(self, path: Optional[Path], crop: tuple[float, float, float] = (1.0, 0.5, 0.5)) -> Optional[QPixmap]:
        if not path:
            return None
        sp = str(path)
        if sp not in self._mtimes:
            if self.no_decode:
                return None                            # (not even a look at the file: it may be on the cloud drive)
            try:
                self._mtimes[sp] = path.stat().st_mtime
            except OSError:
                self._mtimes[sp] = None
        mtime = self._mtimes[sp]
        if mtime is None:
            return None
        key = self._key(sp, mtime, crop)
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            part = self._ready.pop(key, None)          # prepared in the background (see prepare)
            if part is None and self.no_decode:
                return None                            # (a wheel glide: the placeholder for now - LibraryGrid)
            if part is None:
                img = self._load(path, crop[0])
                if img is None:
                    return None
                part = self._cut(img, crop)
            self._cache[key] = QPixmap.fromImage(part)
            while len(self._cache) > self.MAX_COVERS:
                self._cache.popitem(last=False)
            self._trim_ready()
        return self._cache[key]

    def ready(self, path: Optional[Path], crop) -> bool:
        """Can this cover be drawn right now without reading or decoding anything?"""
        if not path:
            return True
        sp = str(path)
        if sp not in self._mtimes:
            return False
        mtime = self._mtimes[sp]
        if mtime is None:
            return True                                 # (no file: nothing to decode)
        key = self._key(sp, mtime, crop)
        return key in self._cache or key in self._ready

    def _trim_ready(self, evict_drawn: bool = False) -> None:
        """Covers drawn + covers waiting stay within MAX_COVERS (the least wanted waiting ones go first).
        evict_drawn (covers prepared just around the view): the covers drawn longest ago make room for them first
        (scrolled far away; the last KEEP_DRAWN stay: what's on screen) - once a big library had filled the
        memory, every cover prepared ahead was dropped at once, and decoded while drawing when scrolled to."""
        while evict_drawn and len(self._cache) + len(self._ready) > self.MAX_COVERS and len(self._cache) > self.KEEP_DRAWN:
            self._cache.popitem(last=False)
        while self._ready and len(self._cache) + len(self._ready) > self.MAX_COVERS:
            self._ready.pop(next(reversed(self._ready)))

    @staticmethod
    def _key(sp: str, mtime: float, crop) -> tuple:
        # by FILE NAME + modified time, not the full path: My Device's library is a copy (same names, same times),
        # so switching modes keeps every decoded cover - decoding them again froze the switch's fade for 120 ms
        return (os.path.basename(sp), mtime) + tuple(round(v, 4) for v in crop)

    @staticmethod
    def _cut(img: QImage, crop) -> QImage:
        rect, _, _ = crop_rect(img.width(), img.height(), *crop)
        # extra resolution so it stays sharp when the cover grows on hover
        w, h = int(COVER_W * 2.2), int(COVER_H * 2.2)
        return img.copy(rect.toAlignedRect()).scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def cached(self) -> frozenset:
        return frozenset(self._cache) | frozenset(self._ready)

    @classmethod
    def prepare(cls, wanted: list, skip: frozenset = frozenset()) -> list:
        """In a background thread: decode, crop and scale covers [(path, crop)] -> [(path, mtime, crop, QImage)]
        (those in skip = cached() are ready already).
        Scrolling to covers not seen yet then costs only the upload to the screen, not a decode (~80 ms froze
        the first scroll). Touches nothing shared: hand the result to accept() on the window's thread."""
        out = []
        for path, crop in wanted:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if cls._key(str(path), mtime, crop) in skip:
                continue
            img = cls._decode(path, crop[0])
            if img is not None:
                out.append((path, mtime, crop, cls._cut(img, crop)))
        return out

    def accept(self, prepared: list, urgent: bool = False, ahead: bool = False) -> None:
        """urgent: covers about to be drawn (a search lets them in): kept even past the budget, until drawn.
        ahead: the covers around the view (see MainWindow._prefetch_ahead): wanted before the rest, and older drawn
        covers make room for them."""
        for path, mtime, crop, part in prepared:
            sp = str(path)
            if self._mtimes.setdefault(sp, mtime) != mtime:
                continue                               # the file changed meanwhile: decoded again when drawn
            key = self._key(sp, mtime, crop)
            if key not in self._cache:
                self._ready[key] = part                # (in the order wanted: most wanted first)
                if urgent:
                    self._ready = {key: self._ready.pop(key), **self._ready}   # (trimmed last)
        if ahead:                                      # (in front of the rest, in their own order: nearest first)
            keys = [k for k in (self._key(str(pa), m, c) for pa, m, c, _ in prepared) if k in self._ready]
            self._ready = {**{k: self._ready[k] for k in keys}, **self._ready}
        if not urgent:
            self._trim_ready(evict_drawn=ahead)

    @staticmethod
    def _decode(path: Path, zoom: float) -> Optional[QImage]:
        """Read the image only as large as needed for this zoom (big JPEGs decode much faster scaled down)."""
        from PySide6.QtGui import QImageReader
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        full = reader.size()
        if not full.isValid() or full.isEmpty():
            img = QImage(str(path))
            return None if img.isNull() else img
        base_h = min(full.height(), full.width() * COVER_H / COVER_W)    # height of the zoom-1 frame
        scale = min(1.0, COVER_H * 2.2 * max(1.0, zoom) / base_h)
        if scale < 1.0:
            reader.setScaledSize(QSize(max(1, round(full.width() * scale)), max(1, round(full.height() * scale))))
        img = reader.read()
        return None if img.isNull() else img

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


def in_order(items: list, key, order: list[str]) -> list:
    """items in the owner's own order: the ones it lists in its order, then any it doesn't know yet (new
    games, games just moved here) at the end, in the order they came."""
    pos = {k: i for i, k in enumerate(order)}
    known = sorted((it for it in items if key(it) in pos), key=lambda it: pos[key(it)])
    return known + [it for it in items if key(it) not in pos]


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)          # smoothstep: gentle start and finish


def ease_cross(t: float) -> float:
    """For cross-fades (two looks on top of each other): smootherstep - as gentle at both ends as ease(), but through
    the middle quicker, where old and new show half and half (two labels overlapping read muddy)."""
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (6 * t - 15) + 10)


_CJK = "　-〿぀-ヿ㐀-鿿豈-﫿＀-￯"     # Japanese characters
_JOINERS = "†‡・/\\-–—_~～×:：&+"          # a title may break after these ("CROSS†" / "CHANNEL")
_J = re.escape(_JOINERS)
_TITLE_PIECES = re.compile(rf"[^\s{_CJK}{_J}]+[{_J}]*|[{_J}]+|\s+|.")
# Japanese line breaking (kinsoku): these never start a line / never end one
_NO_START = set("、。，．,.・:：;；!！?？ー～〜)）]］}｝」』】〕〉》’”ゝゞヽヾぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ々")
_NO_END = set("(（[［{｛「『【〔〈《‘“")


def has_japanese(text: str) -> bool:
    return re.search(rf"[{_CJK}]", text) is not None


def wrap_title(text: str, fm: QFontMetrics, width: float, max_lines: int) -> list[str]:
    """Wrap a title to max_lines, with an ellipsis if it still doesn't fit. English breaks between words
    (or after a joining symbol like † or ・), and splits a word only when the word alone is wider than the
    line. Japanese can break between any two characters, except that no line starts with 、。ー」 or a
    small kana, and none ends with 「『（ (the character moves to the next line with its neighbour)."""
    pieces = _TITLE_PIECES.findall(text)
    lines: list[str] = []
    cur: list[str] = []
    i = 0
    while i < len(pieces):
        piece = pieces[i]
        if cur and fm.horizontalAdvance("".join(cur) + piece) > width:
            carry: list[str] = []
            if not piece.isspace() and len(cur) > 1 and not cur[-1].isspace() and \
                    (piece[0] in _NO_START or cur[-1][-1] in _NO_END):
                carry = [cur.pop()]                  # keep the pair together on the next line
            lines.append("".join(cur).rstrip())
            cur = carry
            if len(lines) == max_lines:
                rest = "".join(carry + pieces[i:]).lstrip()
                lines[-1] = fm.elidedText(lines[-1] + ("" if has_japanese(rest[:1]) else " ") + rest,
                                          Qt.ElideRight, int(width))
                return lines
            if piece.isspace():
                i += 1
            continue
        if not cur and not piece.isspace() and fm.horizontalAdvance(piece) > width:   # one very long word
            cut = 1
            while cut < len(piece) and fm.horizontalAdvance(piece[:cut + 1]) <= width:
                cut += 1
            pieces[i:i + 1] = [piece[:cut], piece[cut:]]
            piece = pieces[i]
        if not cur and piece.isspace():
            i += 1                                   # no line starts with a space
            continue
        cur.append(piece)
        i += 1
    if cur:
        lines.append("".join(cur).rstrip())
    return lines


_FITTED: dict = {}      # fit_title's answers: (title, font, width, lines) -> (font, lines)
_ADVANCES: dict = {}    # text widths: (text, font) -> px


def fit_title(text: str, font: QFont, width: float, max_lines: int) -> tuple[QFont, list[str]]:
    """The title's font and lines (remembered: a hovered cover asked every frame, measuring each word again -
    most of a scroll frame's text work at 143 Hz, owner's PC, 2026-10-19)."""
    key = (text, font.key(), width, max_lines)
    hit = _FITTED.get(key)
    if hit is None:
        if len(_FITTED) > 2000:
            _FITTED.clear()
        hit = _FITTED[key] = _fit_title(text, font, width, max_lines)
    return hit


def advance(font: QFont, text: str) -> float:
    """How wide this text is in this font (remembered, like fit_title)."""
    key = (text, font.key())
    w = _ADVANCES.get(key)
    if w is None:
        if len(_ADVANCES) > 4000:
            _ADVANCES.clear()
        w = _ADVANCES[key] = QFontMetricsF(font).horizontalAdvance(text)
    return w


def _fit_title(text: str, font: QFont, width: float, max_lines: int) -> tuple[QFont, list[str]]:
    """A Japanese title whose last line would hold only one or two characters is set one size smaller when that
    saves the line (a big jump in size would look odd, so only one)."""
    fm = QFontMetrics(font)
    lines = wrap_title(text, fm, width, max_lines)
    if has_japanese(text):
        natural = wrap_title(text, fm, width, 99)
        if len(natural) > 1 and len(natural[-1].strip()) <= 2:
            smaller = QFont(font)
            smaller.setPointSizeF(font.pointSizeF() - 1)
            sfm = QFontMetrics(smaller)
            if len(wrap_title(text, sfm, width, 99)) < len(natural):
                return smaller, wrap_title(text, sfm, width, max_lines)
    return font, lines


# =========================================================================== statistics (playtime / last read)
# Drawn as "runs": ("t", text, font, colour) / ("i", icon kind, colour, size) / ("g", gap). Numbers bright, units
# small and muted, a clock / calendar icon in front, each icon centred on the digits so everything sits level.
_STAT_ICONS: dict = {}


def stat_icon(kind: str, colour: str, size: int, dpr: float = 1.0, sweep: float = 0.0) -> QPixmap:
    """A thin line icon: "clock" (time read) or "calendar" (last read), drawn on a 16-unit grid.
    sweep: the clock's long hand turned on by that many degrees (the count-up after a session; not cached)."""
    key = (kind, colour, size, dpr)
    pm = None if sweep else _STAT_ICONS.get(key)
    if pm is None:
        pm = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.Antialiasing)
        q.scale(size / 16, size / 16)
        pen = QPen(QColor(colour), 1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        q.setPen(pen)
        q.setBrush(Qt.NoBrush)
        if kind == "clock":
            q.drawEllipse(QPointF(8, 8), 6.2, 6.2)
            a = math.radians(sweep)
            tip = QPointF(8 + 3.4 * math.sin(a), 8 - 3.4 * math.cos(a))
            q.drawPolyline([tip, QPointF(8, 8), QPointF(10.4, 9.6)])
        else:
            q.drawRoundedRect(QRectF(2, 3.2, 12, 10.8), 1.6, 1.6)
            q.drawLine(QPointF(2, 6.6), QPointF(14, 6.6))
            q.drawLine(QPointF(5.2, 1.8), QPointF(5.2, 4.4))
            q.drawLine(QPointF(10.8, 1.8), QPointF(10.8, 4.4))
        q.end()
        if sweep:
            return pm
        if len(_STAT_ICONS) > 64:
            _STAT_ICONS.clear()
        _STAT_ICONS[key] = pm
    return pm


def time_runs(seconds: float, num: QFont, unit: QFont, num_colour: str, unit_colour: str) -> list:
    out: list = []
    for i, (n, u) in enumerate(i18n.playtime_parts(seconds)):
        if i:
            out.append(("g", 4 if i18n.language() == "en" else 1))
        out += [("t", n, num, num_colour), ("g", 1), ("t", u, unit, unit_colour)]
    return out


def runs_width(runs: list) -> float:
    w = 0.0
    for r in runs:
        if r[0] == "t":
            w += advance(r[2], r[1])
        elif r[0] == "i":
            w += r[3]
        else:
            w += r[1]
    return w


def draw_runs(p: QPainter, x: float, baseline: float, runs: list, mid: float) -> float:
    """Draw left to right on one baseline; icons centred on mid (the middle of the digits). Returns the end x."""
    dpr = p.device().devicePixelRatioF() if p.device() is not None else 1.0
    for r in runs:
        if r[0] == "t":
            p.setFont(r[2])
            p.setPen(QColor(r[3]))
            p.drawText(QPointF(x, baseline), r[1])
            x += advance(r[2], r[1])
        elif r[0] == "i":
            size = r[3]
            p.drawPixmap(QRectF(x, mid - size / 2, size, size),
                         stat_icon(r[1], r[2], size, dpr, r[4] if len(r) > 4 else 0.0), QRectF())
            x += size
        else:
            x += r[1]
    return x


def ink_left(text: str, font: QFont) -> float:
    """Blank space before the first visible pixel of text."""
    return QFontMetricsF(font).boundingRect(text).left() if text else 0.0


ICON_INK = 1.8 / 16 - 0.75 / 16                # stat_icon's blank left edge, as a share of its size (half the pen)


def run_ink(runs: list) -> float:
    """Blank space before the first visible pixel of a line of runs."""
    for r in runs:
        if r[0] == "t":
            return ink_left(r[1][:1], r[2])
        if r[0] == "i":
            return r[3] * ICON_INK
        return 0.0
    return 0.0


def digit_middle(font: QFont) -> float:
    """How far above the baseline the middle of the digits is (icons are centred there)."""
    return QFontMetricsF(font).tightBoundingRect("0123456789").height() / 2


class FadingStatusBar(QStatusBar):
    """The status line at the very bottom ("Now Reading...", "Library updated..."): messages fade in, fade out
    when they're cleared, and one replacing another fades out first, then the new one in (never both at once,
    nothing slides). showMessage / clearMessage / currentMessage work as usual; only the drawing is ours."""
    # fade in / fade out when cleared / fade out when replaced (in and replaced: the bottom bar's own timing)
    IN_S, OUT_S, SWAP_S = BAR_IN_S, 0.3, BAR_OUT_S

    def __init__(self, parent=None):
        super().__init__(parent)
        self._disp, self._alpha = "", 0.0       # what's on screen and how visible
        self._last_tick = time.monotonic()
        self._timer = VsyncClock(self)          # (one step per screen refresh: even on the owner's 143 Hz screen)
        self._timer.timeout.connect(self._tick)
        self.messageChanged.connect(self._changed)

    _hold_until = 0.0                           # a new message waits until then before fading in

    def show_with_bar(self, text: str, timeout: int = 0) -> None:
        """A message that belongs with a change of the bottom bar (a session's end: Synced, the stats): it fades in
        together with the bar's pieces - after their fade-out, over the same time (owner: it came back early)."""
        self._hold_until = time.monotonic() + BAR_OUT_S
        self.showMessage(text, timeout)

    def _changed(self, _text: str) -> None:
        self._last_tick = time.monotonic()
        self._timer.start()
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last_tick = now - self._last_tick, now
        target = self.currentMessage()
        if self._disp != target:
            if self._disp and self._alpha > 0:  # the old one goes first
                self._alpha = max(0.0, self._alpha - dt / (self.SWAP_S if target else self.OUT_S))
            elif now < self._hold_until and target:
                self._disp, self._alpha = "", 0.0   # (waiting for the bar)
            else:
                self._disp, self._alpha = target, 0.0
        elif self._disp and self._alpha < 1:
            self._alpha = min(1.0, self._alpha + dt / self.IN_S)
        if self._disp == target and (self._alpha >= 1 or not self._disp):
            self._timer.stop()
        self.update()

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        opt = QStyleOption()
        opt.initFrom(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt, p, self)      # the band (style sheet background)
        self._link_rect = QRectF()
        if self._disp:
            rect = self.rect().adjusted(6, 0, -6, 0)                     # (where Qt's own status bar puts it)
            p.setOpacity(ease(self._alpha))
            action = self._action if self._action and self._action[0] == self._disp else None
            link_w = QFontMetricsF(self._link_font()).horizontalAdvance(action[1]) + self.LINK_GAP if action else 0
            runs = self._runs(self._disp)
            fm = QFontMetricsF(self.font())
            base = rect.top() + (rect.height() - fm.height()) / 2 + fm.ascent()
            if runs and runs_width(runs) <= rect.width() - link_w:
                end = draw_runs(p, rect.left(), base, runs, base - digit_middle(self.font()))
            else:
                p.setPen(self.palette().color(self.foregroundRole()))
                shown = self.fontMetrics().elidedText(self._disp, Qt.ElideRight, int(rect.width() - link_w))
                p.drawText(QPointF(rect.left(), base), shown)
                end = rect.left() + fm.horizontalAdvance(shown)
            if action:                                   # e.g. Undo: bright like the stats' date, underlined
                lf = self._link_font()
                x = end + self.LINK_GAP
                p.setFont(lf)
                p.setPen(QColor(COLORS["text"]))
                p.drawText(QPointF(x, base), action[1])
                lfm = QFontMetricsF(lf)
                self._link_rect = QRectF(x - 3, rect.top(), lfm.horizontalAdvance(action[1]) + 6, rect.height())
        p.end()

    # a clickable word after a message (Undo): show_action
    LINK_GAP = 10
    _action: Optional[tuple] = None             # (the message it belongs to, its word, what a click does)
    _link_rect = QRectF()

    def _link_font(self) -> QFont:
        f = QFont(self.font())
        f.setUnderline(True)
        return f

    def show_action(self, text: str, word: str, on_click: Callable[[], None], timeout: int = 10000) -> None:
        """A message with a clickable word after it (Remove From Library -> "Removed X. Undo"). The word goes with
        the message (cleared, timed out or replaced: gone)."""
        self._action = (text, word, on_click)
        self.setMouseTracking(True)
        self.showMessage(text, timeout)

    def mouseMoveEvent(self, ev) -> None:
        over = self._link_rect.contains(ev.position()) and self._disp == self.currentMessage()
        self.setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)
        super().mouseMoveEvent(ev)

    def mousePressEvent(self, ev) -> None:
        a = self._action
        if (ev.button() == Qt.LeftButton and a and a[0] == self.currentMessage() == self._disp
                and self._link_rect.contains(ev.position())):
            self._action = None
            self.setCursor(Qt.ArrowCursor)
            self.clearMessage()
            a[2]()
            return
        super().mousePressEvent(ev)

    _TIME = re.compile(r"(\d+)(時間|分|秒|[hms])(?![A-Za-z])")

    def _runs(self, text: str) -> list:
        """A message with a playtime in it ("Read for 1h 05m."): the time's numbers bright and its units small,
        like the stats everywhere else; the words as usual. [] = no time in it (drawn as plain text)."""
        if not self._TIME.search(text):
            return []
        plain = self.font()
        unit = QFont(plain)
        unit.setPointSizeF((plain.pointSizeF() if plain.pointSizeF() > 0 else 10) * 0.8)
        muted = self.palette().color(self.foregroundRole()).name()
        out, at = [], 0
        for m in self._TIME.finditer(text):
            if m.start() > at:
                out.append(("t", text[at:m.start()], plain, muted))
            out += [("t", m.group(1), plain, COLORS["text"]), ("g", 1), ("t", m.group(2), unit, muted)]
            at = m.end()
        if at < len(text):
            out.append(("t", text[at:], plain, muted))
        return out


# =========================================================================== hover popups
# Our own, everywhere (instead of Windows' tooltips): shown sooner (TIP_WAKE_MS), fading in and out, following the
# mouse smoothly at 60 fps while it stays on the thing, only over the thing itself (a widget can narrow its own
# spot with tip_at), and kept inside the window: below the pointer, or above it when there's no room below.
TIP_WAKE_MS = 200                   # how long the mouse rests before a popup comes (Windows' own: ~700; 350 felt slow)
TIP_IN_S, TIP_OUT_S = 0.12, 0.1
TIP_FOLLOW = 18                     # how quickly it catches up with the mouse (per second)
TIP_OFFSET = QPoint(14, 20)         # from the pointer (below-right), or above it when there's no room below


class TipTiming(QProxyStyle):
    """Only the hover-popup delays change (Qt asks the style for them); everything else is the style as it was."""

    def styleHint(self, hint, option=None, widget=None, returnData=None) -> int:
        if hint == QStyle.SH_ToolTip_WakeUpDelay:
            return TIP_WAKE_MS
        if hint == QStyle.SH_ToolTip_FallAsleepDelay:
            return 1500                   # moving straight on to the next thing: its popup comes at once
        return super().styleHint(hint, option, widget, returnData)


class _DwmTiming(ctypes.Structure):
    _pack_ = 1                                       # (dwmapi.h packs it to 1 byte)
    _fields_ = [("cbSize", ctypes.c_uint32), ("rateRefresh", ctypes.c_uint32 * 2), ("qpcRefreshPeriod", ctypes.c_uint64),
                ("rateCompose", ctypes.c_uint32 * 2), ("qpcVBlank", ctypes.c_uint64), ("cRefresh", ctypes.c_uint64),
                ("cDXRefresh", ctypes.c_uint32), ("qpcCompose", ctypes.c_uint64), ("cFrame", ctypes.c_uint64),
                ("cDXPresent", ctypes.c_uint32), ("cRefreshFrame", ctypes.c_uint64), ("cFrameSubmitted", ctypes.c_uint64),
                ("cDXPresentSubmitted", ctypes.c_uint32), ("cFrameConfirmed", ctypes.c_uint64),
                ("cDXPresentConfirmed", ctypes.c_uint32), ("cRefreshConfirmed", ctypes.c_uint64),
                ("cDXRefreshConfirmed", ctypes.c_uint32), ("cFramesLate", ctypes.c_uint64),
                ("cFramesOutstanding", ctypes.c_uint32), ("cFrameDisplayed", ctypes.c_uint64),
                ("qpcFrameDisplayed", ctypes.c_uint64), ("cRefreshFrameDisplayed", ctypes.c_uint64),
                ("cFrameComplete", ctypes.c_uint64), ("qpcFrameComplete", ctypes.c_uint64), ("cFramePending", ctypes.c_uint64),
                ("qpcFramePending", ctypes.c_uint64)] + [(n, ctypes.c_uint64) for n in (
                "cFramesDisplayed", "cFramesComplete", "cFramesPending", "cFramesAvailable", "cFramesDropped",
                "cFramesMissed", "cRefreshNextDisplayed", "cRefreshNextPresented", "cRefreshesDisplayed",
                "cRefreshesPresented", "cRefreshStarted", "cPixelsReceived", "cPixelsDrawn", "cBuffersEmpty")]


_FRAME_TIMING: list = []          # [DwmGetCompositionTimingInfo, QueryPerformanceCounter, counts a second, info] or [None]


def frame_time() -> float:
    """When the frame being drawn now reaches the screen (perf_counter seconds): the next refresh after the last one.
    Scrolling is placed by THIS, not by the moment its step happens to run: steps run 0.5-4 ms after the refresh,
    unevenly, and at speed that showed - 5,000 px/s moved 22-55 px a frame instead of an even 34-36 (wheel-press
    scrolling, 2026-10-20). Falls back to perf_counter where Windows can't say."""
    now = time.perf_counter()
    if not _FRAME_TIMING:
        try:
            freq = ctypes.c_int64()
            ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
            info = _DwmTiming(cbSize=ctypes.sizeof(_DwmTiming))
            get = ctypes.windll.dwmapi.DwmGetCompositionTimingInfo
            get.argtypes = [ctypes.c_void_p, ctypes.POINTER(_DwmTiming)]
            _FRAME_TIMING[:] = [get, ctypes.windll.kernel32.QueryPerformanceCounter, freq.value, info]
        except (AttributeError, OSError):
            _FRAME_TIMING[:] = [None]
    get = _FRAME_TIMING[0]
    if get is None:
        return now
    _, qpc, freq, info = _FRAME_TIMING
    count = ctypes.c_int64()
    if get(None, ctypes.byref(info)) != 0 or not qpc(ctypes.byref(count)) or not info.qpcRefreshPeriod:
        return now
    period = info.qpcRefreshPeriod
    passed = count.value - info.qpcVBlank            # (the last refresh may be a few behind if DWM was idle)
    nxt = info.qpcVBlank + (passed // period + 1) * period
    return now + (nxt - count.value) / freq


def frame_period() -> float:
    """Seconds between the screen's refreshes (as of the last frame_time(); 1/60 where Windows can't say)."""
    if not _FRAME_TIMING:
        frame_time()                     # (asked before any frame: read Windows' timing first, not a 60 Hz guess)
    if _FRAME_TIMING and _FRAME_TIMING[0] is not None and _FRAME_TIMING[3].qpcRefreshPeriod:
        return _FRAME_TIMING[3].qpcRefreshPeriod / _FRAME_TIMING[2]
    return 1 / 60


class _VsyncHub(QObject):
    """The one background thread behind every VsyncClock: it waits for Windows' next screen composition (DwmFlush:
    once per refresh, whatever the screen's rate) and then ticks every running clock, all in the same frame. It
    sleeps while no clock runs. (No composition to wait for: every 16 ms.)"""
    _relay = Signal()
    _one: Optional["_VsyncHub"] = None

    @classmethod
    def get(cls) -> "_VsyncHub":
        if cls._one is None:
            cls._one = _VsyncHub()
        return cls._one

    def __init__(self):
        super().__init__()
        self.clocks: list = []
        self._want = threading.Event()
        self._pending = threading.Event()        # one tick at a time: a busy window never gets a backlog
        self._relay.connect(self._deliver, Qt.QueuedConnection)
        threading.Thread(target=self._loop, daemon=True, name="vsync").start()

    def add(self, clock: "VsyncClock") -> None:
        if clock not in self.clocks:
            self.clocks.append(clock)
        self._want.set()

    def remove(self, clock: "VsyncClock") -> None:
        if clock in self.clocks:
            self.clocks.remove(clock)
        if not self.clocks:
            self._want.clear()

    def _loop(self) -> None:
        try:
            flush = ctypes.windll.dwmapi.DwmFlush
        except (AttributeError, OSError):
            flush = None
        while True:
            self._want.wait()
            if flush is None or flush() != 0:
                time.sleep(0.016)
            if self._want.is_set() and not self._pending.is_set():
                self._pending.set()
                try:
                    self._relay.emit()
                except RuntimeError:                 # (closing down)
                    return

    def _deliver(self) -> None:
        self._pending.clear()
        for clock in list(self.clocks):
            if not shiboken6.isValid(clock):         # (its owner was deleted while it ran)
                self.remove(clock)
            elif clock in self.clocks:               # (an earlier clock's tick may have stopped this one)
                clock.timeout.emit()


class VsyncClock(QObject):
    """A timer that ticks once per frame the screen really shows (see _VsyncHub). 16 ms timers make 62.5 steps a
    second, which on a 120 / 144 / 165 / 240 Hz screen land unevenly (the owner's 143 Hz: 2 or 3 refreshes apart,
    so movement juddered although every step was on time). Every animation is paced by the real time passed, so it
    moves at the same speed at any rate - just in more, smaller steps on a faster screen. Used like a QTimer."""
    timeout = Signal()

    def start(self) -> None:
        _VsyncHub.get().add(self)

    def stop(self) -> None:
        if _VsyncHub._one is not None:
            _VsyncHub._one.remove(self)

    def isActive(self) -> bool:
        return _VsyncHub._one is not None and self in _VsyncHub._one.clocks

    def setInterval(self, _ms: int) -> None:      # (as a QTimer: the screen sets the pace here)
        pass

    def setTimerType(self, _kind) -> None:
        pass


class SmoothTip(QWidget):
    _one: Optional["SmoothTip"] = None

    @classmethod
    def instance(cls) -> "SmoothTip":
        if cls._one is None:
            cls._one = SmoothTip()
        return cls._one

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("smooth_tip")
        self.setAttribute(Qt.WA_StyledBackground, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(objectName="smooth_tip_text")
        self.label.setTextFormat(Qt.AutoText)
        lay.addWidget(self.label)
        self._owner: Optional[QWidget] = None
        self._zone = QRect()                # where the mouse must stay (global)
        self._pos = QPointF()
        self._alpha, self._leaving = 0.0, False
        self._last = time.monotonic()
        self._timer = VsyncClock(self)           # (follows the mouse once per screen refresh, whatever the rate)
        self._timer.timeout.connect(self._tick)

    def show_for(self, owner: QWidget, text: str, zone: QRect) -> None:
        """zone: in owner's coordinates."""
        first = not self.isVisible() or self._leaving or self._owner is not owner
        self._owner = owner
        self._zone = QRect(owner.mapToGlobal(zone.topLeft()), zone.size())
        if self.label.text() != text:
            self.label.setText(text)
            self.adjustSize()
        if first:
            self._pos = QPointF(self._target())   # starts right there, then follows
            self.move(self._pos.toPoint())
        self._leaving = False
        if not self.isVisible():
            self._alpha = 0.0
            self.setWindowOpacity(0.0)
            self.show()
        self.raise_()
        self._last = time.monotonic()
        self._timer.start()

    def leave(self) -> None:
        if self.isVisible():
            self._leaving = True
            self._last = time.monotonic()
            self._timer.start()

    def _room(self) -> QRect:
        """Where it may go: the window the thing is in (on its screen)."""
        win = self._owner.window() if self._owner is not None else None
        screen = (win.screen() if win is not None else None) or QApplication.primaryScreen()
        room = screen.availableGeometry()
        if win is not None and win.isVisible():
            room = room.intersected(QRect(win.mapToGlobal(QPoint(0, 0)), win.size()))
        return room

    def _target(self) -> QPoint:
        c = QCursor.pos()
        room = self._room()
        w, h = self.width(), self.height()
        x = c.x() + TIP_OFFSET.x()
        y = c.y() + TIP_OFFSET.y()
        if y + h > room.bottom() - 4:                 # no room below: above the pointer instead
            y = c.y() - h - 8
        x = max(room.left() + 4, min(x, room.right() - 4 - w))
        y = max(room.top() + 4, min(y, room.bottom() - 4 - h))
        return QPoint(x, y)

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last = min(0.1, now - self._last), now
        owner = self._owner
        gone = owner is None or not shiboken6.isValid(owner) or not owner.isVisible() \
            or not owner.window().isActiveWindow() and not owner.window().underMouse()
        if not self._leaving and (gone or not self._zone.contains(QCursor.pos())):
            self._leaving = True                         # the mouse left the thing: fade away
        if self._leaving:
            self._alpha = max(0.0, self._alpha - dt / TIP_OUT_S)
            if self._alpha == 0.0:
                self._timer.stop()
                self.hide()
                self._owner = None
                return
        else:
            self._alpha = min(1.0, self._alpha + dt / TIP_IN_S)
            t = QPointF(self._target())
            k = min(1.0, dt * TIP_FOLLOW)
            self._pos += (t - self._pos) * k
            self.move(self._pos.toPoint())
        self.setWindowOpacity(ease(self._alpha))


class TipManager(QObject):
    """App-wide: every hover popup goes through SmoothTip. A widget may say exactly where its popup applies with
    tip_at(pos) -> (text, zone in its coordinates); otherwise its toolTip() over the whole widget."""

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if t == QEvent.ToolTip and isinstance(obj, QWidget) and not isinstance(obj, SmoothTip):
            text, zone = obj.tip_at(ev.pos()) if hasattr(obj, "tip_at") else (obj.toolTip(), obj.rect())
            if text:
                SmoothTip.instance().show_for(obj, text, zone)
                return True
            if hasattr(obj, "tip_at"):
                SmoothTip.instance().leave()
                return True                              # (not over its text: no popup, not even the parent's)
            return False                                 # (Qt passes it on to the parent, as usual)
        if t in (QEvent.MouseButtonPress, QEvent.Wheel, QEvent.KeyPress, QEvent.ApplicationDeactivate) \
                and SmoothTip._one is not None and SmoothTip._one.isVisible():
            SmoothTip._one.leave()
        return False


_TIP_MANAGER: Optional[TipManager] = None


def install_smooth_tips(app: QApplication) -> None:
    global _TIP_MANAGER
    if _TIP_MANAGER is None:
        app.setStyle(TipTiming(app.style().name()))
        _TIP_MANAGER = TipManager(app)
        app.installEventFilter(_TIP_MANAGER)


class SmoothWheel(QObject):
    """The library's wheel glide for EVERY other scrolling area (owner, 2026-10-20: "everything super smooth"):
    Settings, Edit, Custom Theme, the guide, drop-down lists, VNDB results, the restore list... Same curve as
    LibraryGrid (cubic Hermite from the current position + speed to the target, WHEEL_GLIDE_S, never overshooting;
    a flick the other way starts from rest), placed by frame_time() on the shared VsyncClock. Item views scroll per
    pixel (per row they could only jump). Touchpads, sideways and with a key held: Qt's own scrolling. Nothing to
    scroll that way: the wheel goes on to the area outside, as before. The library grid has its own (strips)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.glides: dict = {}                 # QScrollBar -> [from, speed, start, length, target]
        self._seen: set = set()                # scroll bars already connected to
        self._setting = False
        self.clock = VsyncClock(self)
        self.clock.timeout.connect(self._step)

    @staticmethod
    def _area(obj) -> Optional[QAbstractScrollArea]:
        area = obj.parent() if isinstance(obj, QWidget) else None
        if isinstance(area, QAbstractScrollArea) and not isinstance(area, LibraryGrid) \
                and obj in (area.viewport(), area.verticalScrollBar()):
            return area
        return None

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.MiddleButton:
            return self._press(obj)
        if ev.type() != QEvent.Wheel:
            return False
        d = ev.angleDelta().y()
        if not d or ev.angleDelta().x() or ev.modifiers() or not ev.pixelDelta().isNull() \
                or ev.phase() != Qt.NoScrollPhase:
            return False
        area = self._area(obj)
        if WHEEL_LOG:
            WHEEL_LOG.append(f"{time.perf_counter():.4f} sw wheel {d} on {type(obj).__name__} "
                             f"area={type(area).__name__ if area else '-'} accepted={ev.isAccepted()}")
        if area is None:
            area = self._holding_area(obj)
            if area is None:
                return False
            # a drop-down / number box / slider inside a scrolling window: the wheel scrolls the window, never the
            # value (owner, 2026-10-20: Settings "bounced" - the glide slid Language / This Is under the pointer
            # and the next notch switched them, re-laying the whole window out)
            if not self._glide(area, d):
                ev.accept()
            return True
        return self._glide(area, d)

    def _press(self, obj) -> bool:
        """The wheel pressed over a scrolling area (not the library: it has its own): wheel-press scrolling."""
        area = self._area(obj) or self._holding_area(obj)
        if area is None and isinstance(obj, QWidget):          # (over a label / box inside a window's scroll area)
            w = obj
            while w is not None and not w.isWindow() and area is None:
                p = w.parentWidget()
                if isinstance(p, QAbstractScrollArea) and not isinstance(p, LibraryGrid) and w is p.viewport():
                    area = p
                w = p
        if area is None or area.verticalScrollBar().maximum() <= 0:
            return False
        auto = area.findChild(_AreaAutoScroll, options=Qt.FindDirectChildrenOnly)
        if auto is None:
            auto = _AreaAutoScroll(area)
        if not auto.active:
            auto.start()
        return True

    @staticmethod
    def _holding_area(obj) -> Optional[QAbstractScrollArea]:
        from PySide6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QComboBox, QScrollBar
        if not isinstance(obj, (QComboBox, QAbstractSpinBox, QAbstractSlider)) or isinstance(obj, QScrollBar):
            return None
        w = obj.parentWidget()
        while w is not None and not w.isWindow():
            area = w.parentWidget()
            if isinstance(area, QAbstractScrollArea) and not isinstance(area, LibraryGrid) and w is area.viewport():
                return area
            w = area
        return None

    def _glide(self, area: QAbstractScrollArea, d: int) -> bool:
        if isinstance(area, QAbstractItemView) and area.verticalScrollMode() != QAbstractItemView.ScrollPerPixel:
            area.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        sb = area.verticalScrollBar()
        g = self.glides.get(sb)
        base = g[4] if g else float(sb.value())
        if (d > 0 and base <= sb.minimum()) or (d < 0 and base >= sb.maximum()):
            if WHEEL_LOG:
                WHEEL_LOG.append(f"{time.perf_counter():.4f} sw at end d={d} base={base:.1f} max={sb.maximum()}")
            # still gliding there: the notch is spent (handed on, Qt scrolled the bar itself and cut the glide off
            # halfway - Settings "bounced" with a quick wheel, owner 2026-10-20); really there: the area outside
            return g is not None or sb.value() != (sb.minimum() if d > 0 else sb.maximum())
        notch = QApplication.wheelScrollLines() * WHEEL_LINE_PX
        notch = min(notch, max(20.0, area.viewport().height() * 0.75))     # (a short list: not past most of it)
        self._aim(sb, base - d / 120 * notch)
        return True

    @staticmethod
    def _state(g, now: float) -> tuple[float, float, float]:
        p0, v0, t0, length, p1 = g
        s = min(1.0, max(0.0, (now - t0) / length))
        s2, s3, m = s * s, s * s * s, v0 * length
        pos = (2 * s3 - 3 * s2 + 1) * p0 + (s3 - 2 * s2 + s) * m + (3 * s2 - 2 * s3) * p1
        vel = ((6 * s2 - 6 * s) * p0 + (3 * s2 - 4 * s + 1) * m + (6 * s - 6 * s2) * p1) / length
        return pos, vel, s

    def _aim(self, sb, target: float) -> None:
        target = min(float(sb.maximum()), max(float(sb.minimum()), target))
        now = time.perf_counter()
        g = self.glides.get(sb)
        pos, vel = self._state(g, now)[:2] if g else (float(sb.value()), 0.0)
        dist, length = target - pos, WHEEL_GLIDE_S
        if abs(dist) < 0.5:
            return
        if vel * dist > 0:
            length = min(length, 3 * dist / vel)          # (already heading there: never overshoot)
        elif vel * dist < 0:
            vel = 0.0                                     # (turned round: straight back)
        if g is None:
            if sb not in self._seen:
                self._seen.add(sb)
                sb.valueChanged.connect(lambda _v, sb=sb: self._moved_elsewhere(sb))
                sb.destroyed.connect(lambda _o=None, sb=sb: (self.glides.pop(sb, None), self._seen.discard(sb)))
            self._solid(sb, True)
        self.glides[sb] = [pos, vel, now, max(length, 0.05), target]
        if WHEEL_LOG:
            WHEEL_LOG.append(f"{now:.4f} sw aim {type(sb.parent().parent()).__name__} from={pos:.1f} v={vel:.0f} "
                             f"to={target:.1f} max={sb.maximum()}")
        if not self.clock.isActive():
            self.clock.start()

    @staticmethod
    def _solid(sb, on: bool) -> None:
        """While gliding, a window's scrolling contents are given a solid background (the window's own colour, so
        nothing looks different): Qt then slides what's already drawn and paints only the strip coming into view.
        See-through (as styled), every frame repainted the whole visible part - 4-5 ms in Settings, a quarter of
        frames late on a quick wheel at 143 Hz (owner, 2026-10-20: "a tad stuttery"); solid: 0.9 ms, none late.
        Only while gliding: a theme change (or its live preview) never meets a stale colour."""
        area = sb.parentWidget().parentWidget() if sb.parentWidget() else None
        if not isinstance(area, QScrollArea) or not isinstance(area.window(), QDialog) or area.widget() is None:
            return                              # (item views / text views draw their own background)
        for w in (area.viewport(), area.widget()):
            if on:
                pal = w.palette()
                pal.setColor(QPalette.Window, QColor(COLORS["bg"]))
                w.setPalette(pal)
            w.setAutoFillBackground(on)

    def _moved_elsewhere(self, sb) -> None:
        if not self._setting:
            if WHEEL_LOG and sb in self.glides:
                WHEEL_LOG.append(f"{time.perf_counter():.4f} sw moved elsewhere to {sb.value()} max={sb.maximum()}")
            if self.glides.pop(sb, None) is not None:   # (scroll bar dragged, keys, a jump to a row: that wins)
                self._solid(sb, False)

    def _step(self) -> None:
        now = frame_time()
        for sb, g in list(self.glides.items()):
            if not shiboken6.isValid(sb):
                self.glides.pop(sb, None)
                continue
            pos, _, s = self._state(g, now)
            if WHEEL_LOG:
                WHEEL_LOG.append(f"{time.perf_counter():.4f} sw step {pos:.1f} s={s:.2f} value={sb.value()}")
            self._setting = True
            try:
                sb.setValue(round(g[4] if s >= 1.0 else pos))
            finally:
                self._setting = False
            if s >= 1.0:
                self.glides.pop(sb, None)
                self._solid(sb, False)
        if not self.glides:
            self.clock.stop()


_SMOOTH_WHEEL: Optional[SmoothWheel] = None


def install_smooth_wheel(app: QApplication) -> None:
    global _SMOOTH_WHEEL
    if _SMOOTH_WHEEL is None:
        _SMOOTH_WHEEL = SmoothWheel(app)
        app.installEventFilter(_SMOOTH_WHEEL)


def bring_to_front(w: QWidget) -> None:
    """Put this window on top of every other window on the screen once - over a game or another program - without
    keeping it there: lifted to "always on top" and straight back, so other windows can cover it again afterwards.
    Then asks for the focus (Windows may refuse that to a program in the background: it flashes in the taskbar)."""
    if not w.isVisible() or sys.platform != "win32":
        return
    try:
        from ctypes import wintypes
        set_pos = ctypes.windll.user32.SetWindowPos
        # (handles are 64-bit: without these types -1 / -2 arrive cut to 32 bits and Windows ignores them)
        set_pos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                            wintypes.UINT]
        hwnd = wintypes.HWND(int(w.winId()))
        flags = 0x0001 | 0x0002 | 0x0010 | 0x0040        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_SHOWWINDOW
        set_pos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, flags)   # HWND_TOPMOST: above everything...
        set_pos(hwnd, wintypes.HWND(-2), 0, 0, 0, 0, flags)   # HWND_NOTOPMOST: ...and back to normal, still in front
    except (OSError, AttributeError):
        pass
    w.raise_()
    w.activateWindow()


def _windows_10() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return sys.getwindowsversion().build < 22000          # (Windows 11 is 10.0.22000 and up)
    except AttributeError:
        return False


WINDOWS_10 = _windows_10()


def _dwm_flag(w: QWidget, attribute: int, on: bool) -> None:
    if sys.platform != "win32":
        return
    try:
        from ctypes import wintypes
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attr.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        value = ctypes.c_int(1 if on else 0)
        set_attr(wintypes.HWND(int(w.winId())), attribute, ctypes.byref(value), ctypes.sizeof(value))
    except (OSError, AttributeError, RuntimeError):
        pass


def set_border(w: QWidget) -> None:
    """Windows 11 (owner, 2026-10-18: Settings flush with the main window there too): every window's 1 px border in
    ONE solid colour - the theme's bar colour - whether focused or not. Windows' own border is see-through on an
    unfocused window and solid on a focused one, so two windows of the same size didn't look the same size
    (measured on Windows 10, where it can't be set: see SettingsDialog._height_cap). DWMWA_BORDER_COLOR (34) is
    Windows 11 only; Windows 10 ignores it."""
    if sys.platform != "win32" or WINDOWS_10:
        return
    try:
        from ctypes import wintypes
        c = QColor(COLORS.get("band", "#1C1C1C"))
        value = wintypes.DWORD(c.red() | (c.green() << 8) | (c.blue() << 16))      # COLORREF: 0x00BBGGRR
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        set_attr.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        set_attr(wintypes.HWND(int(w.winId())), 34, ctypes.byref(value), ctypes.sizeof(value))
    except (OSError, AttributeError, RuntimeError):
        pass


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("hwndInsertAfter", ctypes.c_void_p), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", ctypes.c_uint)]


def _open_log(text: str) -> None:
    """IKEMEN_DEBUG_OPEN=1: what the opening did, in %APPDATA%\\IkemenReader\\open.log."""
    if os.environ.get("IKEMEN_DEBUG_OPEN"):
        try:
            with open(default_app_dir() / "open.log", "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except OSError:
            pass


def paint_into_window(w: QWidget, img: Optional[QImage] = None) -> Optional[QImage]:
    """Draw the widget's whole picture straight into its native window (Windows only): used as a window is shown, so
    the first thing on screen is its real picture, not an empty white window (see MainWindow.nativeEvent). Returns
    the picture, so it can be put in again without drawing it anew."""
    from ctypes import wintypes
    if img is None:
        for lay in (w.layout(),
                    getattr(w.centralWidget(), "layout", lambda: None)() if hasattr(w, "centralWidget") else None):
            if lay is not None:
                lay.activate()                         # (its final size and places, not a stale layout)
        img = w.grab().toImage().convertToFormat(QImage.Format_RGB32)
    if img.isNull():
        return None

    class BIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                    ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.SetDIBitsToDevice.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD, wintypes.DWORD,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                        ctypes.c_void_p, wintypes.UINT]
    hwnd = wintypes.HWND(int(w.winId()))
    # Owner, 2026-10-19 (Windows 11 laptop at 125 %, only the first opening after a restart): the opening animation
    # showed a much larger top bar for a moment. The picture must be exactly the window's size as Windows sees it right
    # now; when it isn't (Windows still has the window at another size or zoom), it's left out - Qt's own first
    # paint follows a moment later.
    client = wintypes.RECT()
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect(hwnd, ctypes.byref(client))
    try:
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        dpi = user32.GetDpiForWindow(hwnd)
    except AttributeError:
        dpi = 0
    _open_log(f"window {client.right}x{client.bottom} at {dpi} dpi, picture {img.width()}x{img.height()}")
    if (client.right, client.bottom) != (img.width(), img.height()):
        _open_log("sizes differ: not drawn")
        return img
    data = bytes(img.constBits())[: img.sizeInBytes()]
    buf = ctypes.create_string_buffer(data, len(data))
    head = BIH(ctypes.sizeof(BIH), img.width(), -img.height(), 1, 32, 0, 0, 0, 0, 0, 0)   # (top-down rows)
    hdc = user32.GetDC(hwnd)
    if hdc:
        try:
            lines = gdi32.SetDIBitsToDevice(hdc, 0, 0, img.width(), img.height(), 0, 0, 0, img.height(), buf,
                                            ctypes.byref(head), 0)
            gdi32.GdiFlush()
            _open_log(f"drew {lines} of {img.height()} rows ({img.width()}x{img.height()} picture)")
        finally:
            user32.ReleaseDC(hwnd, hdc)
        if os.environ.get("IKEMEN_DEBUG_OPEN") and not getattr(w, "_picture_saved", False):
            # the picture itself, to see what the opening starts with - AFTER drawing it: saving takes ~0.1 s, and
            # the second time round the window is already on screen (it sat white meanwhile)
            w._picture_saved = True
            try:
                img.save(str(default_app_dir() / f"open_picture_{time.strftime('%H%M%S')}.png"))
            except OSError:
                pass
    else:
        _open_log("no window DC")
    return img


def warm_title_bar() -> None:
    """Owner, 2026-10-19 (Windows 11 laptop): on the first opening after a restart the opening animation showed a tall
    EMPTY dark band at the top - most likely Windows' own title bar, its text / icon / buttons not loaded yet the first
    time a window has one after logging in (Ikemen Reader's own picture was complete, its sizes right). So before the
    real window: a small window with a normal title bar, cloaked (never on screen, nor on the taskbar), shown and its
    frame drawn once, then closed - the real window's title bar is ready when it appears. ~0.1 s."""
    if sys.platform != "win32":
        return
    t = time.perf_counter()
    try:
        from ctypes import wintypes
        w = QWidget()
        w.setWindowTitle("Ikemen Reader")
        w.setAttribute(Qt.WA_ShowWithoutActivating)
        w.resize(200, 100)
        _dwm_flag(w, 13, True)                     # DWMWA_CLOAK: before it's shown - never on screen
        _dwm_flag(w, 3, True)                      # DWMWA_TRANSITIONS_FORCEDISABLED
        w.show()
        user32 = ctypes.windll.user32
        user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
        user32.RedrawWindow(wintypes.HWND(int(w.winId())), None, None, 0x0401 | 0x0100)  # INVALIDATE|FRAME, UPDATENOW
        QApplication.processEvents()
        ctypes.windll.dwmapi.DwmFlush()
        w.hide()
        w.deleteLater()
    except (OSError, AttributeError, RuntimeError) as e:
        _open_log(f"title bar warm-up failed: {e!r}")
        return
    _open_log(f"title bar warmed up in {(time.perf_counter() - t) * 1000:.0f} ms")


def no_open_animation(w: QWidget) -> None:
    """Windows' own open / close animation (a fade with a slight zoom, ~250 ms) off for this window: during it the
    window is see-through, and the covers behind Settings showed through it (owner, 2026-10-18; measured on the
    screen: the cover's red fading into the Settings colour). It then simply appears - and PopupsToFront keeps it
    hidden ("cloaked") until its first picture is drawn: without the fade, Windows showed it WHITE for a frame."""
    _dwm_flag(w, 3, True)                   # DWMWA_TRANSITIONS_FORCEDISABLED


_CLOAKED: set = set()          # pop-ups shown but still hidden until drawn (PopupsToFront)
_HELD_TITLE: set = set()       # windows whose title bar was kept focused-looking meanwhile (hwnds)


class KeepsTitleBar:
    """Mixin for windows pop-ups open over (main window, Settings). Opening a pop-up that blocks this window disables
    it, and Windows at once draws its title bar in the grey of an unfocused window - while the pop-up, lying exactly
    over it, is still hidden until drawn: a grey blink of 1-3 frames on every opening (owner, 2026-10-18,
    "flickers"). While a pop-up is hidden, the "draw unfocused" message (WM_NCACTIVATE off) is held back; it's sent
    again once the pop-up is on screen and covering it (PopupsToFront._reveal)."""

    _closing = False                    # a pop-up on its way out (done())

    # Pressing the title bar: Windows' own handling waits there - without running the program - until the button is
    # released or the mouse moves, to see whether it's a drag. Everything froze for as long as the button was held
    # (owner, 2026-10-20, "clicking back into the window": a click is held ~50-100 ms; measured: held 30 ms -> 32 ms
    # frozen, 100 ms -> 102 ms; a click inside the window: 0). Browsers / Steam keep drawing on another thread, so
    # it never shows there. So the press is kept from Windows; the window watches the button itself and only once
    # the mouse really moves with it held does it hand the drag to Windows (startSystemMove: snapping, dragging a
    # maximised window off the top - all as usual). A double-click (maximise) comes as its own message, untouched.
    _press_at = None

    def _caption_pressed(self) -> bool:
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        self._press_at = (pt.x, pt.y)
        if not hasattr(self, "_press_watch"):
            self._press_watch = QTimer(self, interval=8, timerType=Qt.PreciseTimer)
            self._press_watch.timeout.connect(self._watch_press)
        self._press_watch.start()
        return True

    def _watch_press(self) -> None:
        from ctypes import wintypes
        u = ctypes.windll.user32
        button = 0x02 if u.GetSystemMetrics(23) else 0x01            # (SM_SWAPBUTTON: the left-hand button)
        if not u.GetAsyncKeyState(button) & 0x8000 or self._press_at is None:
            self._press_watch.stop()                                  # let go: it was just a click
            self._press_at = None
            return
        pt = wintypes.POINT()
        u.GetCursorPos(ctypes.byref(pt))
        x0, y0 = self._press_at
        if abs(pt.x - x0) > u.GetSystemMetrics(68) or abs(pt.y - y0) > u.GetSystemMetrics(69):   # SM_CX/CYDRAG
            self._press_watch.stop()
            self._press_at = None
            if not self.isMaximized():
                self.move(self.pos() + QPoint(pt.x - x0, pt.y - y0))  # (catch up with the mouse: no lag behind it)
            if self.windowHandle() is not None:
                self.windowHandle().startSystemMove()

    def nativeEvent(self, event_type, message):
        if sys.platform == "win32":
            try:
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x00A1 and msg.wParam == 2:         # WM_NCLBUTTONDOWN on the caption (HTCAPTION)
                    return self._caption_pressed(), 0
            except (ValueError, TypeError, OSError):
                pass
        if (_CLOAKED or self._closing) and sys.platform == "win32":
            try:
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0086 and not msg.wParam:          # WM_NCACTIVATE, going unfocused
                    if not self._closing:
                        _HELD_TITLE.add(int(msg.hWnd))
                    return True, 1                                    # (the change goes ahead; the title bar waits)
            except (ValueError, TypeError, OSError):
                pass
        return super().nativeEvent(event_type, message)

    def done(self, r) -> None:
        """Closing: Windows tells the window it's unfocused just before it disappears - its title bar turned grey
        for a frame on the way out (owner, 2026-10-18). It's leaving anyway: that last change is skipped."""
        self._closing = True
        # and the window behind it looks focused again BEFORE it's uncovered (it was still grey for a frame or so:
        # its real focus arrives 3-8 ms after this one has gone)
        behind = self.parentWidget().window() if isinstance(self, QDialog) and self.parentWidget() is not None else None
        if behind is not None and behind.isVisible() and self.isActiveWindow() and sys.platform == "win32":
            try:
                from ctypes import wintypes
                send = ctypes.windll.user32.SendMessageW
                send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                send(wintypes.HWND(int(behind.winId())), 0x0086, 1, 0)
            except (OSError, AttributeError, RuntimeError):
                pass
        super().done(r)


class PopupsToFront(QObject):
    """App-wide (owner, 2026-10-17): every pop-up comes to the front of the screen when it opens - e.g. "Force Close"
    while the game's window is on top - and can be covered by other windows afterwards as usual."""

    def eventFilter(self, obj, ev) -> bool:
        t = ev.type()
        if isinstance(obj, QFileDialog):
            # Windows' own file picker: Qt's QFileDialog is only an invisible stand-in for it - touching its window
            # (winId / bring_to_front) put that stand-in on screen as an empty white box beside the picker
            return False
        if t == QEvent.Show and isinstance(obj, QDialog) and obj.isWindow():
            # every pop-up appears at once, already drawn (owner, 2026-10-18): no see-through fade-in, and hidden
            # ("cloaked", DWMWA_CLOAK) until its first picture is on its way - Windows otherwise shows a new window
            # white for a frame. (Qt sends Show before the window goes on screen.)
            no_open_animation(obj)
            set_border(obj)
            _dwm_flag(obj, 13, True)
            obj.setProperty("cloaked", True)
            obj.setProperty("painted", None)
            _CLOAKED.add(obj)
            # the focus comes AFTER it's shown (_reveal): taking it first turned the main window's title bar grey -
            # right under where the pop-up was about to appear - for 1-3 frames (owner: "flickers")
            obj.setAttribute(Qt.WA_ShowWithoutActivating, True)
            QTimer.singleShot(250, obj, lambda d=obj: self._reveal(d, True))            # (in case one never comes)
        elif t == QEvent.Paint and isinstance(obj, QDialog) and obj.property("cloaked"):
            obj.setProperty("painted", True)
            QTimer.singleShot(0, obj, lambda d=obj: self._reveal(d))   # (after this paint reaches its window)
        return False

    @staticmethod
    def _reveal(d: QWidget, anyway: bool = False) -> None:
        """Shown once it's drawn, with its title bar already in the focused look (WM_NCACTIVATE), and only THEN given
        the focus (bring_to_front): Settings lies exactly over the main window, whose title bar turns grey (#2B2B2B)
        when the pop-up takes the focus - seen for 1-3 frames in every opening while the pop-up was still hidden."""
        if not shiboken6.isValid(d) or not d.property("cloaked"):
            return
        if not anyway and not d.property("painted"):
            return
        d.setProperty("cloaked", None)
        _CLOAKED.discard(d)
        if sys.platform == "win32":
            try:
                from ctypes import wintypes
                hwnd = wintypes.HWND(int(d.winId()))
                send = ctypes.windll.user32.SendMessageW
                send.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                send(hwnd, 0x0086, 1, 0)            # WM_NCACTIVATE: the title bar drawn in its focused look
                # wait for Windows to compose the frame that holds the first picture: uncloaked just before it, the
                # window could show empty for a frame. At most one screen refresh.
                ctypes.windll.dwmapi.DwmFlush()
            except (OSError, AttributeError, RuntimeError):
                pass
        _dwm_flag(d, 13, False)
        d.setAttribute(Qt.WA_ShowWithoutActivating, False)
        if sys.platform == "win32":
            try:
                ctypes.windll.dwmapi.DwmFlush()     # (on screen first - then the windows behind it go unfocused)
                if not _CLOAKED:
                    from ctypes import wintypes
                    post = ctypes.windll.user32.PostMessageW
                    post.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
                    front = ctypes.windll.user32.GetForegroundWindow()
                    for hwnd in list(_HELD_TITLE):
                        if hwnd != front:
                            post(wintypes.HWND(hwnd), 0x0086, 0, 0)
                    _HELD_TITLE.clear()
            except (OSError, AttributeError):
                pass
        bring_to_front(d)


_POPUPS_TO_FRONT: Optional[PopupsToFront] = None


def install_popups_to_front(app: QApplication) -> None:
    global _POPUPS_TO_FRONT
    if _POPUPS_TO_FRONT is None:
        _POPUPS_TO_FRONT = PopupsToFront(app)
        app.installEventFilter(_POPUPS_TO_FRONT)


@dataclasses.dataclass
class _PieceFade:
    rect: QRect                 # where (bar coordinates)
    old: QPixmap                # the bar as it looked when this piece's fade began
    bare: QPixmap               # what shows between out and in (the bar without the piece)
    t0: float
    cross: bool                 # True: cross-fade (the Read button); False: out, then in
    out_s: float = BAR_OUT_S
    in_s: float = BAR_IN_S
    xfade_s: float = BAR_XFADE_S
    delay: float = 0.0          # (cross-fades only) the old look holds this long first

    def done(self, now: float) -> bool:
        return now - self.t0 >= (self.delay + self.xfade_s if self.cross else self.out_s + self.in_s)

    def keeps_going(self, now: float) -> bool:
        """Changed again while fading: keep this fade's clock if the new look can still arrive under it unseen
        (fading out, or not yet half way in) - restarting is what made pieces flicker as a session ended (the bar
        is updated several times in a row then: light, then stats, then the re-read...)."""
        return not self.cross and now - self.t0 < self.out_s + self.in_s / 2


class BarFader(QWidget):
    """The bottom bar's changes, drawn over the bar on the window's clock (MainWindow._tick -> step); clicks pass
    through. Owner, 2026-10-17: the Read button CROSS-FADES (its old picture fades away over the new one); every other
    piece FADES OUT, THEN IN (the old picture fades into the bar without it, then that fades away from over the new
    look). Each piece has its own fade, started only when what IT shows changes - the rest stay perfectly still."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.pieces: dict[str, _PieceFade] = {}
        self.hide()

    @property
    def active(self) -> bool:
        return bool(self.pieces)

    def fade(self, key: str, piece: _PieceFade) -> None:
        now = time.monotonic()
        was = self.pieces.get(key)
        if was is not None and not was.done(now) and was.keeps_going(now):
            was.rect = was.rect | piece.rect     # (the new look arrives under the fade already under way)
            was.bare = piece.bare
            return
        self.pieces[key] = piece
        self.setGeometry(self.parentWidget().rect())
        self.show()
        self.raise_()
        self.update()

    def step(self) -> None:
        now = time.monotonic()
        for key in [k for k, f in self.pieces.items() if f.done(now)]:
            del self.pieces[key]
        if not self.pieces:
            self.hide()
            return
        self.update()

    def paintEvent(self, ev) -> None:
        now = time.monotonic()
        p = QPainter(self)
        # pieces fading in step whose areas overlap (the sync words and the stats line beside them): their overlap
        # is covered ONCE - covered twice, the half-faded cover stacked and ate a letter for a moment
        covered: dict = {}
        for f in self.pieces.values():
            t = now - f.t0
            key = (f.t0, f.cross, f.out_s, f.in_s, f.xfade_s, f.delay, id(f.old), id(f.bare))
            area = QRegion(f.rect).subtracted(covered.get(key, QRegion()))
            covered[key] = covered.get(key, QRegion()) | QRegion(f.rect)
            if area.isEmpty():
                continue
            p.save()
            p.setClipRegion(area)
            if f.cross:
                p.setOpacity(1.0 - ease_cross((t - f.delay) / f.xfade_s))
                p.drawPixmap(0, 0, f.old)
            elif t < f.out_s:                    # out: the old piece fades into the bar without it
                p.drawPixmap(0, 0, f.bare)
                p.setOpacity(1.0 - ease(t / f.out_s))
                p.drawPixmap(0, 0, f.old)
            else:                                # in: the bar without it fades away from over the new piece
                p.setOpacity(1.0 - ease(min(1.0, (t - f.out_s) / f.in_s)))
                p.drawPixmap(0, 0, f.bare)
            p.restore()
        p.end()


class ClickAway(QObject):
    """While installed on the application: a mouse press anywhere but on `widget` calls `done` (renaming in
    the bottom bar: clicking elsewhere saves what was typed, even where the click wouldn't take the focus)."""

    def __init__(self, widget: QWidget, done: Callable[[], None]):
        super().__init__(widget)
        self.widget, self.done = widget, done

    def eventFilter(self, obj, ev) -> bool:
        # (widgets only: a press also passes through the window itself first, which isn't "elsewhere")
        if ev.type() == QEvent.MouseButtonPress and isinstance(obj, QWidget) \
                and obj is not self.widget and not self.widget.isAncestorOf(obj):
            self.done()
        return False


class ElidedLabel(QLabel):
    """One line of plain text that ends in "..." when it doesn't fit (instead of being cut off mid-letter);
    pointing at it then shows the whole text. clicked: a left click (the bottom bar renames from it)."""
    clicked = Signal()
    clickable = False                       # set by the bar: the name can be renamed (text cursor over it)

    def _over_text(self, x: float) -> bool:
        r = self.contentsRect()
        return bool(self.text()) and r.left() <= x <= r.left() + self.fontMetrics().horizontalAdvance(self._shown()) + 4

    def mouseMoveEvent(self, ev) -> None:
        over = self.clickable and self._over_text(ev.position().x())
        self.setCursor(Qt.IBeamCursor if over else Qt.ArrowCursor)
        super().mouseMoveEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.LeftButton and self.clickable and self._over_text(ev.position().x()):
            self.clicked.emit()
            ev.accept()
            return
        super().mousePressEvent(ev)

    def setText(self, text: str) -> None:
        super().setText(text)
        self._tip()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._tip()

    def _shown(self) -> str:
        return self.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(0, self.contentsRect().width()))

    def _tip(self) -> None:
        self.setToolTip(self.text() if self.text() and self._shown() != self.text() else "")

    def tip_at(self, pos: QPoint) -> tuple[str, QRect]:
        """The whole name, only over the shortened name itself (the label spans the whole bar)."""
        r = self.contentsRect()
        w = self.fontMetrics().horizontalAdvance(self._shown())
        zone = QRect(r.left(), r.top(), w + 2, r.height())
        return (self.toolTip(), zone) if self.toolTip() and zone.contains(pos) else ("", QRect())

    blank = False                           # (the rename box floats over it: draw nothing, keep the space)

    def paintEvent(self, ev) -> None:
        if self.blank:
            return
        p = QPainter(self)
        self.style().drawItemText(p, self.contentsRect(), int(self.alignment()), self.palette(), self.isEnabled(),
                                  self._shown(), self.foregroundRole())
        p.end()


class SyncStatus(QWidget):
    """Bottom bar: the sync light's dot + its status ("● Synced"), or a folder's game count. Changes land at once:
    the bar's one cross-fade (BarFader) blends the old look into the new."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # as wide as its words (never more), but it may be squeezed on a very narrow window
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.setMinimumWidth(1)
        self._shown = (None, "", False)         # (light colour key or None, words, muted)
        self._w = 0.0

    def text(self) -> str:
        return self._shown[1]

    def tip_at(self, pos: QPoint) -> tuple[str, QRect]:
        """Its explanation, only over the light and its words (TipManager)."""
        zone = QRect(0, 0, math.ceil(min(self._w, self.width())), self.height())
        return (self.toolTip(), zone) if self.toolTip() and zone.contains(pos) else ("", QRect())

    def _natural(self, state) -> float:
        key, words, _ = state
        fm = QFontMetricsF(self.font())
        return (fm.horizontalAdvance("● ") if key else 0) + fm.horizontalAdvance(words) + 2 if words else 0.0

    def set_status(self, key: Optional[str], words: str, muted: bool = False) -> None:
        want = (key, words, muted)
        if want == self._shown:
            return
        self._shown = want
        self._w = self._natural(want)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(math.ceil(self._w), math.ceil(QFontMetricsF(self.font()).height()))

    def minimumSizeHint(self) -> QSize:
        return QSize(1, self.sizeHint().height())

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() in (QEvent.FontChange, QEvent.StyleChange):   # (language / font: the size follows at once)
            self._w = self._natural(self._shown)
            self.updateGeometry()

    def paintEvent(self, ev) -> None:
        key, words, muted = self._shown
        if not words:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.Antialiasing)
        f = self.font()
        fm = QFontMetricsF(f)
        p.setFont(f)
        base = (self.height() - fm.height()) / 2 + fm.ascent()      # (as a label centres it: level with the stats)
        x = 0.0
        if key:
            p.setPen(QColor(LIGHTS[key]))
            p.drawText(QPointF(x, base), "●")
            x += fm.horizontalAdvance("● ")
        p.setPen(QColor(COLORS["muted" if muted else "text"]))
        room = self.width() - x
        p.drawText(QPointF(x, base), fm.elidedText(words, Qt.ElideRight, max(0, int(room))))
        p.end()


class StatsLine(QWidget):
    """Bottom bar: "[clock] 12h 05m   [calendar] 3 Days Ago" on the sync line, right after the sync status, in the
    same style as the cover hover (owner's pick of four layouts). Groups that don't fit are left off the end (the
    date first). Pointing at the time shows it per device; pointing at the day shows the date itself.
    New values fade in place (the icons stay); after a reading session the clock's hand sweeps once round."""
    LEAD = 16                               # after the sync status
    solo = False                            # a solo device: plain hover boxes (MainWindow._apply_mode)
    COUNT_S = 0.8                           # count-up after a session

    def __init__(self, parent=None):
        super().__init__(parent)
        # takes what's left of the line: the sync status always comes first
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.seconds, self.last = 0.0, 0.0
        self.per_device: list[tuple[str, float]] = []
        self.per_last: list[tuple[str, Optional[float]]] = []   # last read per device (the day's hover box)
        self._spans: list = []                  # (x from, x to, tooltip) per drawn group
        self._key = None                        # whose values these are (game / folder id)
        self._count: Optional[tuple] = None     # (from seconds, since) while counting up
        self._timer = VsyncClock(self)          # (the roll moves once per screen refresh: no stutter at 143 Hz)
        self._timer.timeout.connect(self._tick)

    _switch: Optional[tuple] = None             # (the old groups as they fitted, since): values changing on screen

    @property
    def switching(self) -> bool:
        return self._switch is not None and time.monotonic() - self._switch[1] < BAR_OUT_S + BAR_IN_S

    def set_values(self, seconds: float, last: float, per_device: list[tuple[str, float]], key=None,
                   per_last: Optional[list[tuple[str, Optional[float]]]] = None) -> None:
        """per_last: (device, when) - a time, 0 = never read there, None = not recorded (Game.last_played_on).
        On screen, new values fade in place: the numbers and words fade out, then in; the icons stay (gliding if
        they move) - owner, 2026-10-17: no need for the icons to fade when switching between games."""
        shown = self.isVisible() and self._key is not None
        if shown and (seconds, last, key) != (self.seconds, self.last, self._key) \
                and (i18n.playtime(seconds), day_text(last) if last else "") != \
                (i18n.playtime(self.seconds), day_text(self.last) if self.last else ""):
            self._switch = (self._fitting(self._groups(self.seconds, self.last)), time.monotonic())
            self._timer.start()
        if key != self._key:
            self._count = None                  # (a count-up belongs to the game it started on)
            self._key = key
        self.seconds, self.last, self.per_device = seconds, last, per_device
        self.per_last = per_last or []
        self.updateGeometry()
        self.update()

    def end_switch(self) -> None:
        """Show the new values plainly at once (the bar fades this whole line instead)."""
        self._switch = None
        self.update()

    def set_shown(self, on: bool) -> None:
        """The whole line goes while a game runs and comes back after."""
        self.setVisible(on)

    def count_up(self, from_seconds: float, delay: float = 0.0) -> None:
        """Time read rolls up from from_seconds to the current total (after a reading session). delay: the old total
        stays put that long first (the bar settles before it rolls)."""
        if 0 <= from_seconds < self.seconds:
            self._count = (from_seconds, time.monotonic() + delay)
            self._switch = None                 # (the roll shows the change instead)
            self._timer.start()
            self.update()

    def _tick(self) -> None:
        if self._count and time.monotonic() - self._count[1] >= self.COUNT_S:
            self._count = None
        if self._switch is not None and not self.switching:
            self._switch = None
        if not self._count and self._switch is None:
            self._timer.stop()
        self.update()

    def _placed(self, fitting: list, x: float) -> list:
        """(x, run, its group's icon or None) for every run of these groups, laid out from x."""
        out = []
        for runs, _tip in fitting:
            icon = runs[0][1] if runs and runs[0][0] == "i" else None
            for r in runs:
                out.append((x, r, icon))
                x += runs_width([r])
            x += STAT_GROUP_GAP
        return out

    def _paint_switch(self, p: QPainter, base: float, mid: float, new: list) -> None:
        """Values changing on screen: old words / numbers fade out, then the new ones in; an icon in both stays
        fully drawn, gliding from its old place to its new one (only an icon that comes or goes fades)."""
        t = time.monotonic() - self._switch[1]
        out_a = 1.0 - ease(min(1.0, t / BAR_OUT_S))
        in_a = ease(max(0.0, min(1.0, (t - BAR_OUT_S) / BAR_IN_S)))
        x0 = float(self.LEAD)
        was, now = self._placed(self._switch[0], x0), self._placed(new, x0)
        icons_was = {r[1]: x for x, r, _ in was if r[0] == "i"}
        icons_now = {r[1]: x for x, r, _ in now if r[0] == "i"}

        def at(icon: str) -> float:
            """Where this icon is drawn now. A group (icon + its words) moves as one, and only while the numbers
            before it can't be crossed (owner, 2026-10-17: the calendar slid over the time): moving RIGHT during
            the fade-out (away from the old numbers), moving LEFT during the fade-in (the old numbers are gone, and
            the new ones end before its new place)."""
            a, b = icons_was[icon], icons_now[icon]
            g = ease(min(1.0, t / BAR_OUT_S)) if b > a else ease(max(0.0, min(1.0, (t - BAR_OUT_S) / BAR_IN_S)))
            return a + (b - a) * g
        for x, r, icon in was:
            if r[0] == "t" or (r[0] == "i" and r[1] not in icons_now):
                shift = at(icon) - icons_was[icon] if icon in icons_now and icon in icons_was else 0.0
                p.setOpacity(out_a)
                draw_runs(p, x + shift, base, [r], mid)
        for x, r, icon in now:
            if r[0] == "i" and r[1] in icons_was:
                p.setOpacity(1.0)
                draw_runs(p, at(r[1]), base, [r], mid)
            elif r[0] != "g":
                shift = at(icon) - icons_now[icon] if icon in icons_was and icon in icons_now else 0.0
                p.setOpacity(in_a)
                draw_runs(p, x + shift, base, [r], mid)
        p.setOpacity(1.0)

    def _fonts(self) -> tuple[QFont, QFont]:
        num = ui_font()                         # the sync line's size (the style sheet's 10 pt)
        unit = ui_font()
        unit.setPointSizeF(8)
        return num, unit

    def _groups(self, seconds: float, last: float, sweep: float = 0.0) -> list[tuple[list, str]]:
        """(runs, tooltip) per group. sweep: the clock hand's extra turn, in degrees (count-up)."""
        num, unit = self._fonts()
        text, muted = COLORS["text"], COLORS["muted"]
        icon = round(digit_middle(num) * 2 * 1.4)
        # every device listed, like the day's box: one that never read it says so (owner); a solo device: the time
        # spelled out, "12 Hours 5 Minutes" (owner's plan)
        tip = "<br>".join(f'{html.escape(n)}&nbsp;&nbsp;&nbsp;'
                          f'<b>{html.escape(i18n.playtime(s) if s else tr("stats.never_played"))}</b>'
                          for n, s in self.per_device)
        if self.solo:
            tip = html.escape(i18n.playtime_words(seconds)) if seconds else tr("stats.never_played")
        clock = ("i", "clock", text, icon, sweep) if sweep else ("i", "clock", text, icon)
        groups = [([clock, ("g", 5)] + time_runs(seconds, num, unit, text, muted), tip) if seconds
                  else ([("t", tr("card.never"), num, text)], tip)]
        if last:
            def when(t: Optional[float]) -> str:
                return i18n.date(t) if t and t > 0 else tr("stats.never_played" if t == 0 else "stats.not_recorded")
            day_tip = "<br>".join(f'{html.escape(n)}&nbsp;&nbsp;&nbsp;<b>{html.escape(when(t))}</b>'
                                  for n, t in self.per_last) or i18n.date(last)
            if self.solo:
                day_tip = "????" if last < 0 else i18n.date(last)   # (solo: just the full date - or not known)
            groups.append(([("i", "calendar", text, icon), ("g", 5), ("t", day_text(last), num, text)], day_tip))
        return groups

    def _fitting(self, groups: list) -> list:
        """The groups that fit this width, from the start (the date goes first)."""
        out, x = [], float(self.LEAD)
        for runs, tip in groups:
            w = runs_width(runs)
            if x + w > self.width():
                break
            out.append((runs, tip))
            x += w + STAT_GROUP_GAP
        return out

    def sizeHint(self) -> QSize:
        groups = self._groups(self.seconds, self.last)
        w = self.LEAD + sum(runs_width(r) for r, _ in groups) + STAT_GROUP_GAP * (len(groups) - 1)
        return QSize(math.ceil(w), math.ceil(QFontMetricsF(self._fonts()[0]).height()))

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        num, _ = self._fonts()
        fm = QFontMetricsF(num)
        now = time.monotonic()
        sweep = 0.0
        if self._count:
            # after a session the clock's hand sweeps once round; the numbers simply show the new total (a roll-up
            # was tried and dropped by the owner, 2026-10-17: "more trouble than it's worth")
            t = max(0.0, min(1.0, (now - self._count[1]) / self.COUNT_S))   # (< 0: still waiting to start)
            sweep = 360 * (1 - (1 - t) ** 3)    # quick start, gentle stop
        # same baseline as the sync status beside it (a label of the same font, centred the same way)
        base = (self.height() - fm.height()) / 2 + fm.ascent()
        mid = base - digit_middle(num)
        x = float(self.LEAD)
        self._spans = []
        fitting = self._fitting(self._groups(self.seconds, self.last, sweep))
        if self.switching:
            self._paint_switch(p, base, mid, fitting)
            for runs, tip in fitting:                # (hover boxes: where the new values are)
                self._spans.append((x, x + runs_width(runs), tip))
                x += runs_width(runs) + STAT_GROUP_GAP
            p.end()
            return
        for runs, tip in fitting:
            end = draw_runs(p, x, base, runs, mid)
            self._spans.append((x, end, tip))
            x = end + STAT_GROUP_GAP
        p.end()

    def tip_at(self, pos: QPoint) -> tuple[str, QRect]:
        """Over the time: per device; over the day: the date - only right over them (TipManager)."""
        for a, b, tip in self._spans:
            if a <= pos.x() <= b and tip:
                return tip, QRect(math.floor(a), 0, math.ceil(b - a) + 1, self.height())
        return "", QRect()


STAT_GROUP_GAP = 12                        # between "[clock] 12h 05m" and "[calendar] 3 Days Ago" on a cover
STAT_TITLE_LIFT = 3                        # the hover title sits this much higher above a stats line


def day_text(last: float) -> str:
    """When it was last read, the way people say it - or "Unknown" (-1: My Device, this computer's date not known)."""
    return tr("stats.unknown") if last < 0 else i18n.ago(last)


def cover_stat_groups(font: QFont, seconds: float, last: float, count: str = "") -> list:
    """The hover line of a cover (or folder: count + total time): groups of runs, light on the dark fade."""
    num = QFont(font)
    num.setWeight(QFont.Medium)
    unit = QFont(font)
    unit.setPointSizeF((font.pointSizeF() if font.pointSizeF() > 0 else 10) * 0.8)
    plain = QFont(font)
    text, soft = DARK["text"], DARK["focus"]
    icon = round(digit_middle(num) * 2 * 1.4)
    groups = []
    if count:                                   # "4 Games": the number bright, the word softer
        m = re.match(r"(\d+)(.*)", count)
        groups.append([("t", m.group(1), num, text), ("t", m.group(2), plain, soft)] if m else [("t", count, plain, soft)])
    if seconds:
        groups.append([("i", "clock", text, icon), ("g", 5)] + time_runs(seconds, num, unit, text, soft))
    elif not count:
        groups.append([("t", tr("card.never"), num, text)])
    if last:
        groups.append([("i", "calendar", text, icon), ("g", 5), ("t", day_text(last), num, text)])
    return groups


class CardDelegate(QStyledItemDelegate):
    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.w = window
        self.shadow = make_shadow(COVER_W, COVER_H)
        self._tiles: dict = {}             # rumbling (easter egg): each tile drawn upright once, then only turned
        from collections import OrderedDict
        self._flat: "OrderedDict[tuple, QPixmap]" = OrderedDict()   # still covers, finished (see _clipped)
        self._skip_light = False           # (the lights stay live on top, so they keep breathing)
        self._light_fx: dict = {}          # game id -> (old look, new look, since): lights cross-fading

    def sizeHint(self, option, index) -> QSize:
        return QSize(round(GRID["cover_w"]) + 2 * CARD_PAD_X, round(GRID["cover_h"]) + CARD_PAD_TOP + CARD_PAD_BOTTOM)

    @staticmethod
    def cover_rect(rect: QRect, hover: float) -> QRectF:
        e = ease(hover)
        scale = 1 + HOVER_GROW * e
        cw, ch = round(GRID["cover_w"]), round(GRID["cover_h"])
        w, h = cw * scale, ch * scale
        cx = rect.x() + (cw + 2 * CARD_PAD_X) / 2
        top = rect.y() + CARD_PAD_TOP + ch / 2 - h / 2 - HOVER_LIFT * e
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

    def _overlay(self, p: QPainter, option, cover: QRectF, e: float, title: str, line) -> None:
        """Title + a small line fading in at the bottom on hover (always light: it sits on a dark fade).
        line: plain text, or groups of runs (cover_stat_groups); groups that don't fit are left off the end."""
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
        if line and not isinstance(line, str):
            groups = list(line)
            room = cover.width() - 12 - 24      # (it sits beside the light, not over it: a bit wider than the title)
            while len(groups) > 1 and sum(runs_width(g) for g in groups) + STAT_GROUP_GAP * (len(groups) - 1) > room:
                groups.pop()                     # a narrow cover: the date goes first
            base = y - fm_small.height() + fm_small.ascent()
            # start where the title's first letter visibly starts (icons and letters carry a little blank edge)
            x = cover.x() + 12 + self._title_ink(option, title, text_w) - run_ink(groups[0])
            for g in groups:
                x = draw_runs(p, x, base, g, base - digit_middle(f)) + STAT_GROUP_GAP
        elif line:
            p.setPen(QColor(DARK["muted"]))
            p.setFont(f)
            p.drawText(QRectF(cover.x() + 12, y - fm_small.height(), text_w, fm_small.height()),
                       Qt.AlignLeft | Qt.AlignVCenter | Qt.TextDontClip, line)   # (descenders: the g of Reading)
        else:
            y += fm_small.height() + 4          # title sits at the bottom on its own
        if title:
            ft = QFont(option.font)
            ft.setPointSize(11)
            ft, lines = fit_title(title, ft, text_w, 2)
            fm = QFontMetrics(ft)
            p.setFont(ft)
            p.setPen(QColor(DARK["text"]))
            top = y - fm_small.height() - 4 - fm.height() * len(lines)
            if line and not isinstance(line, str):
                top -= STAT_TITLE_LIFT           # (stats line: a touch more room; statistics hidden: as before)
            for i, l in enumerate(lines):
                p.drawText(QRectF(cover.x() + 12, top + i * fm.height(), text_w, fm.height()),
                           Qt.AlignLeft | Qt.AlignVCenter | Qt.TextDontClip, l)
        p.setOpacity(1.0)

    @staticmethod
    def _title_ink(option, title: str, width: float) -> float:
        """How far right of its box the title's first letter visibly starts (0 without a title)."""
        if not title:
            return 0.0
        ft = QFont(option.font)
        ft.setPointSize(11)
        ft, lines = fit_title(title, ft, width, 2)
        return ink_left(lines[0][:1], ft) if lines and lines[0] else 0.0

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
            box = rect.adjusted(16, 16, -16, -16)
            f, lines = fit_title(title, f, box.width(), 7)
            q.setFont(f)
            q.setPen(QColor(COLORS["text"]))
            q.drawText(box, Qt.AlignCenter, "\n".join(lines))
            q.end()
            if len(self._placeholders) > 400:
                self._placeholders.clear()
            self._placeholders[key] = pm
        return pm

    def _clipped(self, p: QPainter, cover: QRectF, e: float, draw, key: Optional[tuple] = None,
                 lifted: Optional[tuple] = None) -> None:
        """Draw a tile's contents (draw(painter, rect)) inside its rounded outline. While it's hovered,
        the contents (cover + dark fade + text) are first painted together on a separate layer and then
        cut out once: cutting out each piece separately leaves faint light pixels along the rounded
        corners, where every piece is softened on its own.
        key (a still tile: what its contents depend on): the finished, rounded tile is kept (_flat) and later
        repaints only copy it into place. Laptop, 2026-10-19 (owner: "check scroll smoothness"): a scroll repaint of
        the 240-game library took ~11 ms of the 16.7 ms frame - each cover resampled and cut to its rounded edge
        again, every frame - and ~1 frame in 15 missed the screen's refresh."""
        if key is not None and e <= 0.01:
            dev = p.device()
            dpr = dev.devicePixelRatioF() if dev is not None else 1.0
            key = key + (int(cover.width()), int(cover.height()), dpr)
            tile = self._flat.get(key)
            if tile is None:
                tile = QPixmap(max(1, math.ceil(cover.width() * dpr)), max(1, math.ceil(cover.height() * dpr)))
                tile.setDevicePixelRatio(dpr)
                tile.fill(Qt.transparent)
                tp = QPainter(tile)
                tp.setRenderHint(QPainter.Antialiasing)
                tp.setRenderHint(QPainter.SmoothPixmapTransform)
                own = QRectF(0, 0, cover.width(), cover.height())
                outline = QPainterPath()
                outline.addRoundedRect(own, 8, 8)
                tp.setClipPath(outline)
                draw(tp, own)
                tp.end()
                self._flat[key] = tile
                while len(self._flat) > FLAT_TILES:
                    self._flat.popitem(last=False)
            else:
                self._flat.move_to_end(key)
            p.drawPixmap(cover.topLeft(), tile)        # (whole pixels: cover_rect) - a plain copy
            return
        clip = QPainterPath()
        clip.addRoundedRect(cover, 8, 8)
        p.save()
        if e <= 0.01:
            p.setClipPath(clip)
            draw(p, cover)
        else:
            dev = p.device()
            dpr = dev.devicePixelRatioF() if dev is not None else 1.0
            # fully lifted: the finished layer is kept like a still tile's - made again every frame it cost ~1 ms
            # (the cover resampled, the fade, the text), the most of any frame while a dragged game's edge scroll
            # lifts the cover under it (2026-10-24)
            full = key is not None and lifted is not None and e >= 1.0
            lkey = key + lifted + (cover.width(), cover.height(), dpr, "lifted") if full else None
            layer = self._flat.get(lkey) if full else None
            if layer is None:
                layer = QPixmap(max(1, math.ceil(cover.width() * dpr)), max(1, math.ceil(cover.height() * dpr)))
                layer.setDevicePixelRatio(dpr)
                layer.fill(Qt.transparent)
                lp = QPainter(layer)
                lp.setRenderHint(QPainter.Antialiasing)
                lp.setRenderHint(QPainter.SmoothPixmapTransform)
                lp.setRenderHint(QPainter.TextAntialiasing)
                draw(lp, QRectF(0, 0, cover.width(), cover.height()))
                lp.end()
                if full:
                    self._flat[lkey] = layer
                    while len(self._flat) > FLAT_TILES:
                        self._flat.popitem(last=False)
            elif full:
                self._flat.move_to_end(lkey)
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
                pm = self.w.covers.get(self.w.art(g), (g.crop_zoom, g.crop_x, g.crop_y))
                if pm:
                    q.drawPixmap(r, pm, QRectF(pm.rect()))
                else:
                    grad = QLinearGradient(r.topLeft(), r.bottomLeft())
                    grad.setColorAt(0, QColor(COLORS["placeholder_top"]))
                    grad.setColorAt(1, QColor(COLORS["placeholder_bottom"]))
                    q.fillRect(r, grad)
                q.restore()
                if self.w.bookmark_alpha > 0:
                    q.setOpacity(ease(self.w.bookmark_alpha))
                    paint_bookmark(q, r, g.status, 5)
                    q.setOpacity(1.0)
            if e > 0.01:
                n = len(games)
                count = tr("folder.count_one") if n == 1 else tr("folder.count_many", n=n)
                total = sum(g.total_playtime for g in games)
                line = cover_stat_groups(option.font, total, 0, count) if self.w.settings.show_stats else count
                self._overlay(q, option, rect, e, self.w.folder_name(fid), line)

        ba = self.w.bookmark_alpha
        key = None if 0 < ba < 1 else (                 # (not while the bookmarks fade in / out)
            "f", ba > 0, COLORS["button"], COLORS["placeholder_top"], COLORS["placeholder_bottom"],
            tuple((pm.cacheKey() if (pm := self.w.covers.get(self.w.art(g), (g.crop_zoom, g.crop_x, g.crop_y)))
                   else 0, g.status) for g in games[:4]))
        self._clipped(p, cover, e, draw, key)
        if option.state & QStyle.State_Selected:
            self._ring(p, cover)
        # one slice per colour inside, all breathing calmly together
        if not self._skip_light:
            paint_light(p, self.light_center(cover), self.w.folder_colours(fid), "calm", self.w.settings.pulse)
        p.restore()

    under_pass = False                     # True while LibraryGrid draws the arriving covers beneath the rest

    RUMBLE_MARGIN = 32                     # room round a tile's cell for its shadow, outline and bookmark

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        amp = getattr(self.w, "rumble", 0.0)
        if not amp:
            if self._tiles:
                self._tiles.clear()
            iid = index.data(GAME_ID_ROLE)
            if iid in self.w.appearing and not self.under_pass:
                return                                   # (drawn beneath the others first: LibraryGrid.paintEvent)
            if iid in self.w.appearing and not self.w.cover_ready(iid):
                # arriving, but its cover would have to be read and decoded right now (a frame-long freeze):
                # it loads in the background meanwhile and starts arriving once it's ready
                self.w.want_cover(iid)
                self.w.appearing[iid] = max(self.w.appearing[iid], time.monotonic() - self.w._appear_t0)
                return
            fade = self.w.appear_fade(iid)
            if fade <= 0:
                self._paint_item(p, option, index)
                return
            # just came into view (search / filter): rises and grows into place while fading in
            if fade >= 1:
                return                                   # (still waiting for its turn)
            c = QRectF(option.rect).center()
            k = 1 - APPEAR_GROW * fade
            p.save()
            p.setOpacity(1 - fade)
            p.translate(c.x(), c.y() + APPEAR_RISE * fade)
            p.scale(k, k)
            p.translate(-c)
            self._paint_item(p, option, index)
            p.restore()
            return
        # the easter egg's rocking box is hovered: every tile rocks around its own centre, each in its own
        # rhythm (a phase and a slightly different speed of its own, from its id)
        iid = index.data(GAME_ID_ROLE)
        cover = self.cover_rect(option.rect, self.w.hover.get(iid, 0.0))
        if not cover.intersects(QRectF(self.w.grid.viewport().rect())):
            return          # out of sight: it stays still (rocking, its corners would swing into view)
        seed = zlib.crc32(str(iid).encode("utf-8"))
        phase = (seed & 0xFFFF) / 0x10000 * 2 * math.pi
        speed = 0.9 + ((seed >> 16) & 0xFF) / 255 * 0.2
        deg = amp * math.sin(2 * math.pi * WobbleBox.ROCK_HZ * speed * self.w.rumble_t + phase)
        rect = QRectF(option.rect)
        m = self.RUMBLE_MARGIN
        key = (iid, option.rect.size().toTuple(), bool(option.state & QStyle.State_Selected))
        tile = self._tiles.get(key)
        if tile is None:
            dev = p.device()
            dpr = dev.devicePixelRatioF() if dev is not None else 1.0
            tile = QPixmap(math.ceil((rect.width() + 2 * m) * dpr), math.ceil((rect.height() + 2 * m) * dpr))
            tile.setDevicePixelRatio(dpr)
            tile.fill(Qt.transparent)
            q = QPainter(tile)
            q.translate(m - rect.x(), m - rect.y())
            self._skip_light = True
            try:
                self._paint_item(q, option, index)
            finally:
                self._skip_light = False
                q.end()
            self._tiles[key] = tile
        c = rect.center()
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.translate(c)
        p.rotate(deg)
        p.translate(-c)
        p.drawPixmap(rect.adjusted(-m, -m, m, m), tile, QRectF(tile.rect()))
        hover = self.w.hover.get(iid, 0.0)
        centre = self.light_center(self.cover_rect(option.rect, hover))
        if is_folder(iid):
            paint_light(p, centre, self.w.folder_colours(iid[len(FOLDER_PREFIX):]), "calm", self.w.settings.pulse)
        elif iid in self.w.games:
            self._paint_light(p, centre, self.w.light_state(iid), iid)
        p.restore()

    def _paint_item(self, p: QPainter, option: QStyleOptionViewItem, index) -> None:
        gid = index.data(GAME_ID_ROLE)
        if gid == self.w._dim_gid and self.w._landing is not None:
            return                                       # the dropped cover is gliding onto this spot
        off = self.w.slide_offset(gid)
        if off is not None:                              # sliding into its new place after a drop
            option = QStyleOptionViewItem(option)
            option.rect = option.rect.translated(off)
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
        pm = self.w.covers.get(self.w.art(game), (game.crop_zoom, game.crop_x, game.crop_y))
        if not pm and game.artwork and self.w.covers.no_decode:
            src = self._placeholder("", option.font)    # (still loading - a wheel glide: no title flashing up first)
        else:
            src = pm if pm else self._placeholder(game.title, option.font)

        def draw(q: QPainter, rect: QRectF) -> None:
            q.drawPixmap(rect, src, QRectF(src.rect()))
            # hover overlay: title + playtime fade in at the bottom (Steam style)
            if e > 0.01:
                if self.w.quiet(gid):
                    play_line = ""
                elif self.w.settings.show_stats:            # time read + last read (the bookmark shows the status)
                    play_line = cover_stat_groups(option.font, game.total_playtime, game.last_played)
                else:                                       # statistics hidden: the reading status only
                    play_line = tr(f"status.{game.status}") if game.status else ""
                # placeholders already show the title in the middle
                self._overlay(q, option, rect, e, game.title if pm else "", play_line)

        # (fully lifted: what its text shows is part of the key - day_text changes with the date)
        lifted = (game.title if pm else "", game.total_playtime, game.last_played, game.status, self.w.quiet(gid),
                  self.w.settings.show_stats, i18n.language(), option.font.key(), time.strftime("%Y%m%d"))
        self._clipped(p, cover, e, draw, ("g", src.cacheKey()), lifted)
        if self.w.bookmark_alpha > 0:                    # (fades when the bookmark button is pressed)
            p.setOpacity(ease(self.w.bookmark_alpha))
            paint_bookmark(p, cover, game.status, width=GRID["cover_w"])
            p.setOpacity(1.0)
        if selected:
            self._ring(p, cover)
        if not self._skip_light:
            self._paint_light(p, self.light_center(cover), self.w.light_state(gid), gid)
        if gid == self.w._dim_gid and self.w.drag_dim > 0:
            # only over the cover itself (not its whole cell: a neighbour may be sliding past through the gap)
            self._fade_into_background(p, cover.adjusted(-3, -3, 3, 3).toAlignedRect(), 0.75 * self.w.drag_dim)
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

    def _paint_light(self, p: QPainter, c: QPointF, state: str, gid: str = "") -> None:
        """A game's light. When its look changes - colour (blue while syncing -> green when done) or pulse
        (quick while starting -> calm once reading: the halo would jump mid-breath) - it cross-fades instead
        of switching (while the lights pulse: then they're redrawn every frame anyway)."""
        colour_key, pulse, _ = STATE_LOOK.get(state, STATE_LOOK["checking"])
        on = self.w.settings.pulse
        if gid and on:
            now = time.monotonic()
            look = (colour_key, pulse)
            old, new, t0 = self._light_fx.get(gid, (look, look, 0.0))
            if new != look:
                # (the first check's answers come within ~25 ms of opening - during Windows' opening animation and
                # the uneven first frames: grey -> colour looked abrupt. They wait until the window has settled)
                old, new, t0 = new, look, max(now, getattr(self.w, "opened_at", 0.0) + OPEN_SETTLE_S)
            self._light_fx[gid] = (old, new, t0)
            a = (now - t0) / LIGHT_FADE_S
            if old != new and a < 1:
                a = ease(max(0.0, a))
                paint_light(p, c, old[0], old[1], on, halo=1 - a)  # the old look underneath...
                p.save()
                p.setOpacity(a)                                    # ...the new one coming up over it
                paint_light(p, c, new[0], new[1], on)
                p.restore()
                return
        paint_light(p, c, colour_key, pulse, on)


PULSE_OFF_AT: list = [None]       # when pulsing was switched off (monotonic), while the lights still settle
PULSE_EPOCH: list = [0.0]         # the breathing clock's start: every breath begins at the low point from here
PULSE_HOLD: list = [None]         # the breathing paused at this moment (LiveColour's picture can't breathe)


def hold_pulse(on: bool) -> None:
    """Pause the lights' breathing where it is / carry on from that same point (nothing jumps either way)."""
    now = time.monotonic()
    if on and PULSE_HOLD[0] is None:
        PULSE_HOLD[0] = now
    elif not on and PULSE_HOLD[0] is not None:
        PULSE_EPOCH[0] += now - PULSE_HOLD[0]
        if PULSE_OFF_AT[0] is not None:
            PULSE_OFF_AT[0] += now - PULSE_HOLD[0]
        PULSE_HOLD[0] = None


def pulse_phase(period: float, now: float) -> float:
    return ((now - PULSE_EPOCH[0]) % period) / period


def pulse_settled_at() -> float:
    """When every light has finished its last breath after pulsing was switched off (the calm breath is the longest)."""
    off = PULSE_OFF_AT[0]
    if off is None:
        return 0.0
    e = PULSE_EPOCH[0]
    return max(e + math.ceil((off - e) / p) * p for p in (PULSE_CALM_S, PULSE_BUSY_S))


def pulse_switched(on: bool) -> None:
    """Pulsing switched on / off. Off: the lights settle (finish their breath). On from rest: the breathing starts
    again from the low point (the still look), not mid-breath - the shared clock kept running meanwhile (owner,
    2026-10-17). On while still settling: they just carry on from where they are."""
    now = time.monotonic()
    if not on:
        PULSE_OFF_AT[0] = now
        return
    if PULSE_OFF_AT[0] is None or now >= pulse_settled_at():
        PULSE_EPOCH[0] = now
    PULSE_OFF_AT[0] = None


LIGHTS_ALPHA: list = [1.0]               # 0 on a solo device; in between while switching (MainWindow._apply_mode)


def paint_light(p: QPainter, c: QPointF, colour_key, pulse: str, pulsing_on: bool, r: float = LIGHT_R,
                halo: float = 1.0) -> None:
    a = LIGHTS_ALPHA[0]
    if a <= 0.002:
        return                                  # (a solo device: nothing syncs, so no sync lights)
    if a >= 0.998:
        _paint_light(p, c, colour_key, pulse, pulsing_on, r, halo)
        return
    p.save()
    p.setOpacity(p.opacity() * a)               # (switching Dual <-> My Device: fading)
    try:
        _paint_light(p, c, colour_key, pulse, pulsing_on, r, halo)
    finally:
        p.restore()


def _paint_light(p: QPainter, c: QPointF, colour_key, pulse: str, pulsing_on: bool, r: float = LIGHT_R,
                 halo: float = 1.0) -> None:
    """A sync light: a dot with a soft halo that breathes. All lights share one clock, so they pulse
    in step. pulse: "calm" / "busy" (quicker) / "steady" (no pulse). colour_key may also be a list of
    colours (a folder): the light is then split into equal slices, the first at 12 o'clock and the rest
    clockwise, each slice glowing in its own colour."""
    keys = [colour_key] if isinstance(colour_key, str) else list(colour_key) or ["grey"]
    s = 0.0
    if pulse != "steady" and (pulsing_on or PULSE_OFF_AT[0] is not None):
        period = PULSE_BUSY_S if pulse == "busy" else PULSE_CALM_S
        now = PULSE_HOLD[0] or time.monotonic()   # (held: LiveColour)
        # just switched off: finish this breath and rest at its low point - where a still light sits - rather
        # than stopping mid-breath (owner, 2026-10-17)
        e = PULSE_EPOCH[0]
        if pulsing_on or now < e + math.ceil((PULSE_OFF_AT[0] - e) / period) * period:
            s = (1 - math.cos(2 * math.pi * pulse_phase(period, now))) / 2   # 0..1, all in step
            s = round(s * 256) / 256      # (finer than a shade of the glow: lights drawn in one frame share a look)
    # The lights breathe in step, so in any one frame lights of the same colour look the same: each look is drawn
    # once into a small picture and stamped (~20 lights x 3 gradient shapes cost ~1 ms a frame while scrolling).
    # Only where the stamp lands on whole pixels exactly as drawing would (no rotation / scaling, 100 % zoom).
    dev = p.device()
    t = p.transform()
    if dev is not None and dev.devicePixelRatioF() == 1.0 and p.opacity() == 1.0 \
            and t.type() in (QTransform.TxNone, QTransform.TxTranslate):
        ox, oy = c.x() + t.dx(), c.y() + t.dy()
        fx, fy = ox - math.floor(ox), oy - math.floor(oy)
        R = math.ceil(r * 2.8) + 3                      # (the biggest halo + the ring)
        key = (tuple(LIGHTS[k] for k in keys), s, halo, r, fx, fy)
        sprite = _LIGHT_SPRITES.get(key)
        if sprite is None:
            if len(_LIGHT_SPRITES) > 64:
                _LIGHT_SPRITES.clear()                  # (s changes every frame: last frames' looks)
            sprite = QPixmap(2 * R, 2 * R)
            sprite.fill(Qt.transparent)
            q = QPainter(sprite)
            q.setRenderHint(QPainter.Antialiasing)
            _draw_light(q, QPointF(R + fx, R + fy), keys, s, r, halo)
            q.end()
            _LIGHT_SPRITES[key] = sprite
        p.drawPixmap(QPointF(c.x() - fx - R, c.y() - fy - R), sprite)
        return
    _draw_light(p, c, keys, s, r, halo)


_LIGHT_SPRITES: dict = {}


def _draw_light(p: QPainter, c: QPointF, keys: list, s: float, r: float, halo: float) -> None:
    halo_r = r * (1.6 + 1.2 * s)
    span = 360 * 16 / len(keys)                         # Qt angles: 1/16 degree, counter-clockwise from 3 o'clock
    p.setPen(Qt.NoPen)
    for i, key in enumerate(keys):
        g = QRadialGradient(c, halo_r)
        hc = QColor(LIGHTS[key])
        hc.setAlpha(int((40 + 90 * s) * halo))
        g.setColorAt(0, hc)
        hc.setAlpha(0)
        g.setColorAt(1, hc)
        p.setBrush(g)
        if len(keys) == 1:
            p.drawEllipse(c, halo_r, halo_r)
        else:
            p.drawPie(QRectF(c.x() - halo_r, c.y() - halo_r, 2 * halo_r, 2 * halo_r),
                      round(90 * 16 - i * span), -round(span))
    for i, key in enumerate(keys):
        p.setBrush(QColor(LIGHTS[key]).lighter(100 + int(18 * s)))
        if len(keys) == 1:
            p.drawEllipse(c, r, r)
        else:
            p.drawPie(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), round(90 * 16 - i * span), -round(span))
    p.setPen(QPen(QColor(0, 0, 0, 150), 1.5))   # dark ring so it reads on light covers too
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(c, r, r)


_bookmarks: dict = {}


def _bookmark_pixmap(colour: str, border: str, w: int, dpr: float) -> QPixmap:
    """One bookmark, drawn once and reused (stamping a picture is far cheaper than drawing the shape on
    every frame). Flat colour with a chunky, soft border like Bookmark.png's, down the sides and along the
    notched bottom (the top is the cover's edge, where it hangs from), with nicely rounded points."""
    key = (colour, border, w, dpr)
    pm = _bookmarks.get(key)
    if pm is not None:
        return pm
    h = round(w * 1.55)
    pm = QPixmap(math.ceil(w * dpr), math.ceil(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)
    q = QPainter(pm)
    q.setRenderHint(QPainter.Antialiasing)
    b = max(1.5, w * 0.14)                              # border width (Bookmark.png's outline: ~1/7 of its width)
    x0, x1, top, bottom, cx = b / 2, w - b / 2, -2.0, h - b / 2, w / 2
    notch = bottom - h * 0.24
    r = w * 0.2                                         # rounded outer tips, like the icon (the notch stays sharp)

    def toward(ax, ay, bx, by, d):                      # a point d px from (ax, ay) towards (bx, by)
        length = math.hypot(bx - ax, by - ay) or 1.0
        return ax + (bx - ax) * d / length, ay + (by - ay) * d / length

    outline = QPainterPath()
    outline.moveTo(x1, top)
    pts = [(x1, bottom), (cx, notch), (x0, bottom)]
    prev = (x1, top)
    for i, (px, py) in enumerate(pts):
        nxt = pts[i + 1] if i + 1 < len(pts) else (x0, top)
        if (px, py) == (cx, notch):
            outline.lineTo(px, py)                      # the inner point of the notch: crisp
        else:
            outline.lineTo(*toward(px, py, *prev, r))
            outline.quadTo(px, py, *toward(px, py, *nxt, r))
        prev = (px, py)
    outline.lineTo(x0, top)
    shape = QPainterPath(outline)
    shape.closeSubpath()
    q.setPen(Qt.NoPen)
    q.setBrush(QColor(colour))
    q.drawPath(shape)
    q.setPen(QPen(QColor(border), b, Qt.SolidLine, Qt.FlatCap, Qt.MiterJoin))
    q.setBrush(Qt.NoBrush)
    q.drawPath(outline)                                 # sides + notched bottom, not the top
    q.end()
    if len(_bookmarks) > 64:
        _bookmarks.clear()
    _bookmarks[key] = pm
    return pm


def paint_bookmark(p: QPainter, cover: QRectF, status: str, corner: float = 8, width: Optional[float] = None) -> None:
    """A little ribbon bookmark in the reading status's colour, hanging from the top edge of a cover in its
    top-right corner (the shape of Bookmark.png), just clear of the cover's rounded corner."""
    colour = MARKS.get(status or "") if status != "border" else None
    if not colour:
        return
    w = max(6, round((width if width is not None else cover.width()) * 0.125))
    dev = p.device()
    dpr = dev.devicePixelRatioF() if dev is not None else 1.0
    pm = _bookmark_pixmap(colour, MARKS["border"], w, dpr)
    x = round(cover.right() - corner - 1 - w)
    p.drawPixmap(QPointF(x, round(cover.top())), pm)


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


# =========================================================================== file pickers
# Every Browse opens where its box points (the nearest folder that exists, when the path doesn't yet), or - with
# nothing filled in - in This PC, never in whatever folder Windows remembers from the last Browse. Save-folder
# pickers with an empty box open the game's own folder instead (owner's choice: saves are often near the game).
THIS_PC = "clsid:0AC0837C-BBF8-452A-850D-79D08E667CA7"     # FOLDERID_ComputerFolder (Qt understands clsid: URLs)


def browse_start(path: str = "", fallback: str = "") -> QUrl:
    for p in ((path or "").strip().strip('"'), (fallback or "").strip().strip('"')):
        if not p:
            continue
        q = Path(p)
        if q.is_absolute():
            if q.is_file():
                return QUrl.fromLocalFile(str(q))           # opens its folder with the file selected
            for d in (q, *q.parents):
                if d.is_dir():
                    return QUrl.fromLocalFile(str(d))
    return QUrl(THIS_PC) if sys.platform == "win32" else QUrl.fromLocalFile(str(Path.home()))


PICKER_QUIET_S = 0.05          # the picker is shown once its windows have painted nothing for this long
PICKER_WAIT_MAX_S = 0.6        # ... or after this, whatever it looks like


class PickerAppearsDrawn:
    """Windows' own file picker faded in half-drawn: its Explorer view is white until it has painted (~150 ms here),
    and the fade showed that - a white / light-grey flash before the dark folder list (owner, 2026-10-19; filmed one
    screen read per refresh). While a picker is open (with ...: around the QFileDialog call; the picker runs on our
    thread), two thread hooks watch it: WM_SHOWWINDOW of its window (#32770) -> no fade + hidden (cloaked), and every
    WM_PAINT of its windows; once they have painted nothing for PICKER_QUIET_S it's uncloaked - it appears at once,
    fully drawn. (A fixed wait guessed wrong: 40 ms still showed white for ~110 ms.)"""

    def __enter__(self):
        self.hooks = []
        if sys.platform != "win32":
            return self
        try:
            from ctypes import wintypes
            u = ctypes.WinDLL("user32")          # (own function objects: argtypes set here touch nobody else's)
            proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
            u.SetWindowsHookExW.argtypes = [ctypes.c_int, proc_type, wintypes.HINSTANCE, wintypes.DWORD]
            u.SetWindowsHookExW.restype = wintypes.HHOOK
            u.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            u.CallNextHookEx.restype = ctypes.c_ssize_t
            u.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
            u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            u.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
            u.GetAncestor.restype = wintypes.HWND
            dwm = ctypes.WinDLL("dwmapi")
            dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

            class CWPSTRUCT(ctypes.Structure):
                _fields_ = [("lParam", wintypes.LPARAM), ("wParam", wintypes.WPARAM), ("message", wintypes.UINT),
                            ("hwnd", wintypes.HWND)]

            def flag(hwnd, attribute, on):
                value = ctypes.c_int(1 if on else 0)
                dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value))

            state = {"picker": None, "painted": None}

            def reveal(hwnd, shown_at):
                while True:
                    time.sleep(0.005)
                    now, painted = time.perf_counter(), state["painted"]
                    if now - shown_at > PICKER_WAIT_MAX_S or (painted is not None and now - painted > PICKER_QUIET_S):
                        break
                dwm.DwmFlush()                  # (the last picture composed first)
                flag(hwnd, 13, False)           # DWMWA_CLOAK off

            def on_sent(code, wp, lp):
                if code == 0 and state["picker"] is None:
                    m = CWPSTRUCT.from_address(lp)
                    if m.message == 0x0018 and m.wParam:                 # WM_SHOWWINDOW, appearing
                        name = ctypes.create_unicode_buffer(16)
                        u.GetClassNameW(m.hwnd, name, 16)
                        if name.value == "#32770":
                            state["picker"] = m.hwnd
                            flag(m.hwnd, 3, True)                       # DWMWA_TRANSITIONS_FORCEDISABLED
                            flag(m.hwnd, 13, True)                      # DWMWA_CLOAK
                            threading.Thread(target=reveal, args=(m.hwnd, time.perf_counter()), daemon=True).start()
                return u.CallNextHookEx(None, code, wp, lp)

            def on_posted(code, wp, lp):
                if code == 0 and state["picker"]:
                    m = wintypes.MSG.from_address(lp)
                    if m.message == 0x000F and m.hWnd and u.GetAncestor(m.hWnd, 2) == state["picker"]:   # WM_PAINT
                        state["painted"] = time.perf_counter()
                return u.CallNextHookEx(None, code, wp, lp)

            self.u = u
            self.procs = (proc_type(on_sent), proc_type(on_posted))     # (kept alive while hooked)
            thread = ctypes.windll.kernel32.GetCurrentThreadId()
            for kind, proc in ((4, self.procs[0]), (3, self.procs[1])):  # WH_CALLWNDPROC, WH_GETMESSAGE
                hook = u.SetWindowsHookExW(kind, proc, None, thread)
                if hook:
                    self.hooks.append(hook)
        except (OSError, AttributeError):
            pass
        return self

    def __exit__(self, *exc):
        for hook in self.hooks:
            self.u.UnhookWindowsHookEx(hook)
        return False


def pick_folder(parent, caption: str, path: str = "", fallback: str = "") -> str:
    with PickerAppearsDrawn():
        url = QFileDialog.getExistingDirectoryUrl(parent, caption, browse_start(path, fallback),
                                                  supportedSchemes=["file"])
    return str(Path(url.toLocalFile())) if url.isValid() and url.toLocalFile() else ""


def pick_file(parent, caption: str, path: str = "", filter: str = "") -> str:
    with PickerAppearsDrawn():
        url, _ = QFileDialog.getOpenFileUrl(parent, caption, browse_start(path), filter, supportedSchemes=["file"])
    return url.toLocalFile() if url.isValid() else ""


def pick_files(parent, caption: str, filter: str = "") -> list[str]:
    with PickerAppearsDrawn():
        urls, _ = QFileDialog.getOpenFileUrls(parent, caption, browse_start(), filter, supportedSchemes=["file"])
    return [u.toLocalFile() for u in urls if u.isValid() and u.toLocalFile()]


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


def crisp_line(p: QPainter, x: float, width: float, y: float, colour: QColor) -> None:
    """A divider line one pixel thick, centred on y, lined up with the screen's own pixels. With Windows
    scaling at 125 % / 150 % a plain 1 px line falls between screen pixels, and two dividers in the same menu
    came out one and two pixels thick."""
    dev = p.device()
    dpr = dev.devicePixelRatioF() if dev is not None else 1.0
    t = p.transform()                                   # (lined up on the screen, not in the painter's units)
    thick = max(1, round(dpr))
    top = round((y + t.dy()) * dpr - thick / 2) / dpr - t.dy()
    left = round((x + t.dx()) * dpr) / dpr - t.dx()
    right = round((x + width + t.dx()) * dpr) / dpr - t.dx()
    p.save()
    p.setRenderHint(QPainter.Antialiasing, False)
    p.fillRect(QRectF(left, top, right - left, thick / dpr), colour)
    p.restore()


class MenuLines(QObject):
    """Right-click menus draw their own divider lines (crisp_line), so they're all equally thick at any
    Windows scaling; the style sheet's separator only keeps the space (transparent). They're rounded like the
    drop-down lists (owner, 2026-10-18): a see-through window without Windows' (square) shadow, ours drawn in the
    style sheet's margin right of and below the menu."""

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Polish and isinstance(obj, QMenu) and not obj.testAttribute(Qt.WA_WState_Created):
            obj.setWindowFlags(obj.windowFlags() | Qt.NoDropShadowWindowHint | Qt.FramelessWindowHint)
            obj.setAttribute(Qt.WA_TranslucentBackground, True)
            obj.setContentsMargins(0, 0, MENU_SHADOW, MENU_SHADOW)   # (Qt frames a menu by its TOP padding on every side, so the shadow room ate the bottom / right padding)
        if ev.type() in (QEvent.Show, QEvent.Hide) and isinstance(obj, QMenu) \
                and not isinstance(obj.parentWidget(), QMenu) and sys.platform == "win32":
            hand = getattr(QApplication.instance(), "_submenu_hand_over", None)
            if hand is not None:                  # (the menu itself: SubmenuHandOver's stand-in opens / closes with it)
                hand.menu_opened(obj) if ev.type() == QEvent.Show else hand.menu_closed()
        if ev.type() in (QEvent.Show, QEvent.Move) and isinstance(obj, QMenu) \
                and isinstance(obj.parentWidget(), QMenu) and obj.isVisible():
            self._place_submenu(obj)        # (Qt moves it once more after showing it: put back, before it's drawn)
        if ev.type() == QEvent.Paint and isinstance(obj, QMenu):
            if isinstance(obj.parentWidget(), QMenu):
                hand = getattr(QApplication.instance(), "_submenu_hand_over", None)
                if hand is not None:
                    hand.submenu_drawn()
            if obj.testAttribute(Qt.WA_TranslucentBackground):
                p = QPainter(obj)                       # the shadow first (the menu's own background ends before it)
                p.drawImage(0, 0, drop_shadow(obj.width() - MENU_SHADOW, obj.height() - MENU_SHADOW))
                p.end()
            QMenu.paintEvent(obj, ev)                   # the menu as usual ...
            if obj.objectName() == "ticks":            # the reading status in use: the app's tick, right of its name
                p = QPainter(obj)
                tick = self._tick()
                for a in obj.actions():
                    if a.data() == "current":
                        r = obj.actionGeometry(a)            # (20 = the item's right padding beyond the tick's room)
                        p.drawPixmap(r.right() - 20 - MENU_TICK + 1, r.center().y() - MENU_TICK // 2 + 1, tick)
                p.end()
            seps = [a for a in obj.actions() if a.isSeparator() and a.isVisible()]
            if seps:
                p = QPainter(obj)                       # ... then its dividers on top
                for a in seps:
                    r = obj.actionGeometry(a)
                    crisp_line(p, r.left() + MENU_LINE_INSET, r.width() - 2 * MENU_LINE_INSET,
                               r.top() + r.height() / 2, QColor(COLORS["border"]))
                p.end()
            return True
        return False

    _ticks: dict = {}

    def _tick(self) -> QPixmap:
        path = ICONS["tick"] or ICONS["check"]          # (a file per theme colour: _write_icons)
        pm = self._ticks.get(path)
        if pm is None:
            pm = self._ticks[path] = QIcon(path).pixmap(MENU_TICK, MENU_TICK)
        return pm

    @staticmethod
    def _place_submenu(sub: QMenu) -> None:
        """A submenu floats SUBMENU_GAP px from its menu (like the drop-down lists from their box; owner, 2026-10-19 -
        Qt overlapped them), its first item level with the item that opened it; on the left when Qt put it there
        (no room on the right)."""
        parent = sub.parentWidget()
        act = next((a for a in parent.actions() if a.menu() is sub), None)
        if act is None or not sub.actions():
            return
        pg = parent.mapToGlobal(QPoint(0, 0))
        visible_w = sub.width() - (MENU_SHADOW if sub.testAttribute(Qt.WA_TranslucentBackground) else 0)
        parent_w = parent.width() - (MENU_SHADOW if parent.testAttribute(Qt.WA_TranslucentBackground) else 0)
        if sub.x() >= pg.x():                                  # (Qt's choice of side stands)
            x = pg.x() + parent_w + SUBMENU_GAP
        else:
            x = pg.x() - SUBMENU_GAP - visible_w
        first = next((a for a in sub.actions() if not a.isSeparator() and a.isVisible()), None)
        y = pg.y() + parent.actionGeometry(act).top() - (sub.actionGeometry(first).top() if first else 0)
        screen = (sub.screen() or QApplication.primaryScreen()).availableGeometry()
        y = max(screen.top(), min(y, screen.bottom() + 1 - sub.height()))
        if sub.pos() != QPoint(x, y):
            sub.move(x, y)


class _MenuStandIn(QWidget):
    """A click-through window that's on the screen (empty) while a right-click menu is open: it shows a submenu's
    last picture for the moment between Qt taking that submenu away and the next one appearing (SubmenuHandOver)."""

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.picture: Optional[QPixmap] = None

    def paintEvent(self, _ev) -> None:
        if self.picture is not None:
            p = QPainter(self)
            p.drawPixmap(0, 0, self.picture)
            p.end()


class SubmenuHandOver(QAbstractNativeEventFilter):
    """Moving from one submenu item to another (Reading Status -> Add to Folder), Qt takes the old submenu away (its
    window is destroyed) and opens the new one in a NEW window, which Windows only shows once its first picture has
    arrived: for a frame neither was on the screen - a flash (owner, 2026-10-19; filmed, and the same before the
    menus were restyled). Now, at the last moment before the old one goes (WM_WINDOWPOSCHANGING, "hide"), its picture
    is put into a stand-in window that's already on the screen (_MenuStandIn, opened with the menu), which shows it
    in the very frame the old one disappears, and clears once the new one has been drawn (a frame later, so they never
    leave a gap between them; at the latest HOLD_MS). Only for a switch between two submenus of the same menu."""
    HOLD_MS = 120
    AFTER_NEW_MS = 16                                  # the new submenu drawn -> on the screen by the next frame

    def __init__(self):
        super().__init__()
        self.stand_in: Optional[_MenuStandIn] = None
        self._pos = None                               # the WINDOWPOS layout (made on first use)

    def menu_opened(self, menu: QMenu) -> None:
        if self.stand_in is None:
            self.stand_in = _MenuStandIn()
        self.stand_in.picture = None
        self.stand_in.setGeometry(menu.x(), menu.y(), 1, 1)
        self.stand_in.show()

    def menu_closed(self) -> None:
        if self.stand_in is not None:
            self.stand_in.picture = None
            self.stand_in.hide()

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG" or sys.platform != "win32" or self.stand_in is None \
                or not self.stand_in.isVisible():
            return False, 0
        try:
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message != 0x0046:                  # WM_WINDOWPOSCHANGING
                return False, 0
            if self._pos is None:
                class WINDOWPOS(ctypes.Structure):
                    _fields_ = [("hwnd", wintypes.HWND), ("after", wintypes.HWND), ("x", ctypes.c_int),
                                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]
                self._pos = WINDOWPOS
            if not self._pos.from_address(int(msg.lParam)).flags & 0x0080:     # SWP_HIDEWINDOW
                return False, 0
            menu = QWidget.find(int(msg.hWnd))
            parent = menu.parentWidget() if isinstance(menu, QMenu) else None
            nxt = parent.activeAction() if isinstance(parent, QMenu) else None
            if not (isinstance(parent, QMenu) and parent.isVisible() and nxt is not None and nxt.menu() is not None
                    and nxt.menu() is not menu):
                return False, 0
            stand = self.stand_in
            stand.picture = menu.grab()
            stand.setGeometry(menu.geometry())
            stand.repaint()                            # (on the screen now, before the old one goes)
            QTimer.singleShot(self.HOLD_MS, self._clear)
        except (ValueError, TypeError, OSError, RuntimeError):
            pass
        return False, 0

    def submenu_drawn(self) -> None:
        """The new submenu has drawn itself: the stand-in clears a frame later."""
        if self.stand_in is not None and self.stand_in.picture is not None:
            QTimer.singleShot(self.AFTER_NEW_MS, self._clear)

    def _clear(self) -> None:
        stand = self.stand_in
        if stand is not None and stand.picture is not None:
            stand.picture = None
            stand.repaint()


def install_menu_lines(app: QApplication) -> None:
    app._menu_lines = MenuLines(app)
    app.installEventFilter(app._menu_lines)
    # menus appear at once, fully drawn (like the drop-downs): Qt follows Windows' "fade menus into view", and moving
    # between Reading Status and Add to Folder restarted a submenu's fade-in from nothing each time - it flashed
    # (owner, 2026-10-19; filmed: ~100 ms from see-through)
    QApplication.setEffectEnabled(Qt.UI_FadeMenu, False)
    QApplication.setEffectEnabled(Qt.UI_AnimateMenu, False)
    app._submenu_hand_over = SubmenuHandOver()
    app.installNativeEventFilter(app._submenu_hand_over)


def _shadow_pixels(w: int, h: int) -> QImage:
    """The shadow right of and below a w x h rounded box (radius _ListShadow.RADIUS), in a (w + SIZE) x (h + SIZE)
    picture, worked out pixel by pixel: Windows' own shadow, measured on white, following the rounded corners."""
    S, r, hw, hh = _ListShadow.SIZE, _ListShadow.RADIUS, w / 2, h / 2
    across, start = _ListShadow.ACROSS, _ListShadow.START
    img = QImage(w + S, h + S, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    ease = lambda i: start[i] if i < len(start) else 1.0

    def dark(e: float) -> float:                      # by distance out from the box's edge, between the measures
        k = max(0.0, e - 0.5)
        i = int(k)
        if i >= len(across) - 1:
            return 0.0
        return across[i] + (across[i + 1] - across[i]) * (k - i)
    for y in range(h + S):
        # (only the strips right of and below the box's straight inside part can lie outside it)
        xs = range(w + S) if y >= h - r - 1 else range(w - r - 1, w + S)
        for x in xs:
            qx = abs(x + 0.5 - hw) - (hw - r)         # distance out from the rounded rectangle (its edge = 0)
            qy = abs(y + 0.5 - hh) - (hh - r)
            e = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
            if e <= 0:
                continue
            a = round(dark(e) * ease(x) * ease(y))
            if a > 0:
                img.setPixel(x, y, a << 24)
    return img


def drop_shadow(w: int, h: int) -> QImage:
    """The same shadow for any size, put together from pieces of one small model (made once): every pixel of it
    depends only on how far it is from the nearest corner or edge, so the corners are copied and the straight parts
    stretched - pixel-identical, and ~0.1 ms for any size instead of 3-8 ms (10-25 ms on the laptop) the first time
    each list / menu size opened."""
    key = (w, h)
    img = _SHADOWS.get(key)
    if img is not None:
        return img
    S, r, T = _ListShadow.SIZE, _ListShadow.RADIUS, len(_ListShadow.START)
    edge = r + 1                                        # the corner's reach in from the right / bottom edge
    if w < T + edge + 1 or h < T + edge + 1:
        img = _shadow_pixels(w, h)                      # (too small for the pieces: the slow way)
    else:
        m = _SHADOWS.get("model")
        if m is None:
            m = _SHADOWS["model"] = _shadow_pixels(T + edge + 1, T + edge + 1)
        mw = mh = T + edge + 1                           # the model's own box size
        img = QImage(w + S, h + S, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        right = w - edge                                # where the right strip starts (x)
        bottom = h - edge                               # where the bottom strip starts (y)
        cw, ch = edge + S, edge + S                     # a corner piece's size
        p.drawImage(right, 0, m, mw - edge, 0, cw, T)                       # top of the right side (eases in)
        p.drawImage(QRect(right, T, cw, bottom - T), m, QRect(mw - edge, T, cw, 1))       # its straight part
        p.drawImage(right, bottom, m, mw - edge, mh - edge, cw, ch)         # the bottom-right corner
        p.drawImage(0, bottom, m, 0, mh - edge, T, ch)                      # left end of the bottom (eases in)
        p.drawImage(QRect(T, bottom, right - T, ch), m, QRect(T, mh - edge, 1, ch))       # its straight part
        p.end()
    if len(_SHADOWS) > 40:
        _SHADOWS.clear()
    _SHADOWS[key] = img
    return img


_SHADOWS: dict = {}


class _ListShadow(QWidget):
    """The drop shadow right of and below an open drop-down list, drawn by us inside the list's own window (Windows'
    shadow is switched off). Windows redrew its shadow around however much of the list the slide-open showed, a
    frame late: a dark line and band jumped down the growing edge (owner, 2026-10-18: "flashing a little"). Ours is
    part of the window, so it unrolls with the list in the same frame. Windows' look (measured on white), following
    the list's rounded corners (RADIUS, as the style sheet's)."""
    SIZE = 5
    RADIUS = 6
    ACROSS = (113, 84, 43, 14, 3, 0)                     # darkness 0.5, 1.5 ... 5.5 px out from the list's edge (of 255)
    START = (0, 0, 0, 0, 0, 0.115, 0.336, 0.664, 0.885, 0.982)   # eases in, from the top / left corner

    def __init__(self, box: QWidget, combo: QComboBox):
        super().__init__(box)
        self.combo = combo
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def paintEvent(self, _ev) -> None:
        v = self.combo.view().geometry()
        p = QPainter(self)
        p.drawImage(v.topLeft(), drop_shadow(v.width(), v.height()))
        p.end()


class _SeparatorDelegate(QStyledItemDelegate):
    """Draws a drop-down's dividers as a thin line (Qt draws them with a style our style sheet doesn't support,
    so they came out invisible); every other item is drawn by Qt's own delegate, unchanged."""

    def __init__(self, inner, parent):
        super().__init__(parent)
        self.inner = inner

    @staticmethod
    def _is_separator(index) -> bool:
        return index.data(Qt.AccessibleDescriptionRole) == "separator"

    def paint(self, p: QPainter, option, index) -> None:
        if not self._is_separator(index):
            self.inner.paint(p, option, index)
            return
        r = option.rect
        line = QColor(COLORS["muted"])
        line.setAlphaF(0.45)
        p.save()
        p.fillRect(r, QColor(COLORS["input"]))
        crisp_line(p, r.left(), r.width(), r.top() + r.height() / 2, line)    # edge to edge
        p.restore()

    def sizeHint(self, option, index) -> QSize:
        if self._is_separator(index):
            return QSize(0, 9)
        return self.inner.sizeHint(option, index)


class ThemedCombo(QComboBox):
    """A drop-down whose list opens without a white flash. Windows puts a newly shown ordinary window on
    screen before the program has drawn it, white, for a frame - and Qt's slide-open effect even makes an
    extra window just for the slide. Here the list's window is a layered one (WA_TranslucentBackground),
    which Windows only shows once it has been drawn, and the slide-open is done on that same window by
    revealing it top-down (or bottom-up, when the list opens above) with a growing mask."""
    SLIDE_S = 0.16
    LIST_GAP = 3                                   # px between the box and its open list

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        QApplication.setEffectEnabled(Qt.UI_AnimateCombo, False)     # (Qt's slide: its extra window flashes)
        self._box().setAttribute(Qt.WA_TranslucentBackground, True)    # before its window first exists
        self._shade = _ListShadow(self._box(), self)
        self._shade.lower()
        self.view().setItemDelegate(_SeparatorDelegate(self.view().itemDelegate(), self))   # visible dividers
        self.view().setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)   # (glides: whole rows could only jump)
        self._t0 = 0.0
        self._down = True
        self._last = 0.0
        self._slide_timer = VsyncClock(self)   # (once per screen refresh, at any rate)
        self._slide_timer.setTimerType(Qt.PreciseTimer)
        self._slide_timer.setInterval(0)                                # paced by time, like the other animations
        self._slide_timer.timeout.connect(self._slide)
        self._watched: Optional[QWidget] = None
        self.activated.connect(lambda _i: self.fade_on_pick and self.fade_words(
            self.painted, 0, bar=True, at_once=self.fade_on_pick == "at_once"))

    # a pick fades the words at the bottom bar's pace (Settings). "at_once": the old words go in the same instant
    # as every other word in the window and the new ones fade in from there (Language: owner, 2026-10-18)
    fade_on_pick = False

    def _box(self) -> QWidget:
        return self.view().parentWidget()          # Qt's frame round the list: the pop-up window itself

    blank = False                                  # the box without its words (the status drop-down while reading)
    painted = ""                                   # the words last drawn (what a pick fades away from)
    _words: Optional[tuple] = None                 # (old words, since, wait, in_s, bar): words changing on a pick

    def fade_words(self, old: str, wait: float, bar: bool = False, at_once: bool = False, span: float = 0.0) -> None:
        """Words changing on a pick (owner, 2026-10-18). Top bar's drop-downs: the old words fade out as the covers
        leave, the new ones fade in from `wait` on, on the arriving covers' own curve (ease out) - starting with the
        first cover and stretched by `span` (the covers' stagger) so they settle with the LAST one (owner: past three
        covers it stopped looking uniform). bar=True (Settings' drop-downs): the bottom status drop-down's pace and curve (BAR_OUT_S out,
        BAR_IN_S in)."""
        if old == self.currentText() or not self.isVisible():
            return
        now = time.monotonic()
        w = self._words
        if w is not None and w[0] == old and now - w[1] < 0.05:
            now = w[1]                             # (the same pick, its timing now known: keep the clock)
        wait = 0.0 if at_once else BAR_OUT_S if bar else max(wait, 0.05)
        self._words = (old, now, wait, BAR_IN_S if bar else APPEAR_S + span, bar)
        if not hasattr(self, "_word_clock"):
            self._word_clock = VsyncClock(self)
            self._word_clock.setInterval(0)
            self._word_clock.timeout.connect(self.update)
        self._word_clock.start()
        self.update()

    def paintEvent(self, ev) -> None:
        words = self._words
        if words is not None and time.monotonic() - words[1] >= words[2] + words[3]:
            self._words = words = None
            self._word_clock.stop()
        if not self.blank and words is None:
            self.painted = self.currentText()
            super().paintEvent(ev)
            return
        from PySide6.QtWidgets import QStyleOptionComboBox, QStylePainter
        p = QStylePainter(self)                    # (as QComboBox draws itself, with no text)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        text = opt.currentText
        opt.currentText, opt.currentIcon = "", QIcon()
        p.drawComplexControl(QStyle.CC_ComboBox, opt)
        if words is None:
            p.drawControl(QStyle.CE_ComboBoxLabel, opt)
        else:
            old, t0, wait, in_s, bar = words
            t = time.monotonic() - t0
            k = min(1.0, max(0.0, (t - wait) / in_s))
            if not wait:                                                # (at once: nothing fades out)
                out_a, in_a = 0.0, ease(k)
            elif bar:                                                   # (BarFader's curves)
                out_a, in_a = 1 - ease(min(1.0, t / wait)), ease(k)
            else:                                                       # (the leaving / arriving covers' ease)
                out_a, in_a = max(0.0, 1 - t / wait) ** 3, 1 - (1 - k) ** 3
            for words_now, a in ((old, out_a), (text, in_a)):
                if a > 0.003:
                    opt.currentText = words_now
                    p.setOpacity(a)
                    p.drawControl(QStyle.CE_ComboBoxLabel, opt)
            p.setOpacity(1.0)
            self.painted = text
        p.end()

    # -- the arrow points the way the list will open (up when there isn't room for it below on the screen)
    def _goes_up(self, need: int) -> bool:
        """Which way a list `need` px tall (with its gap and shadow) opens: down while the screen has room for it
        (past the program's edge too: owner, 2026-10-19), up only when it doesn't. Decided with the list's real
        height - Qt's own choice left out the gap and shadow, and the list ran 2 px off the screen instead of
        opening upwards."""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None or not self.isVisible():
            return False
        room = screen.availableGeometry()
        top = self.mapToGlobal(QPoint(0, 0)).y()
        bottom = top + self.height()
        return bottom + need > room.bottom() + 1 and top - need >= room.top()

    def _opens_up(self) -> bool:
        """The arrow's guess before opening (the list's height from its rows, padding, frame, gap and shadow)."""
        rows = min(self.count(), self.maxVisibleItems())
        need = sum(self.view().sizeHintForRow(i) for i in range(rows)) + 10 + self.LIST_GAP + _ListShadow.SIZE
        return self._goes_up(need)

    def _update_arrow(self) -> None:
        if not shiboken6.isValid(self):
            return                                 # (its window closed and deleted before this came round)
        up = self._opens_up()
        if bool(self.property("opens_up")) != up:
            self.setProperty("opens_up", up)
            self.style().unpolish(self)
            self.style().polish(self)
            self.update()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        win = self.window()
        if win is not self._watched:               # (moving / resizing the window changes the room below)
            if self._watched is not None:
                self._watched.removeEventFilter(self)
            win.installEventFilter(self)
            self._watched = win
        QTimer.singleShot(0, self, self._update_arrow)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._update_arrow()

    def eventFilter(self, obj, ev) -> bool:
        # (Qt may call this while the drop-down is still being built: nothing to watch yet then)
        if obj is getattr(self, "_watched", None) and ev.type() in (QEvent.Move, QEvent.Resize, QEvent.Show,
                                                                     QEvent.ScreenChangeInternal):
            QTimer.singleShot(0, self, self._update_arrow)
        return False

    def showPopup(self) -> None:
        box = self._box()
        box.setMask(QRegion(0, 0, 1, 1))             # hidden until the slide reveals it
        # opened before: its see-through window still holds the last picture (the whole list), which Windows showed
        # for a frame before the slide's first one - kept hidden ("cloaked") until that first picture has gone out
        cloak = box.testAttribute(Qt.WA_WState_Created) and sys.platform == "win32"
        if cloak:
            _dwm_flag(box, 13, True)
        if not box.windowFlags() & Qt.NoDropShadowWindowHint:
            # Windows' own shadow off (ours: _ListShadow). Set here, once: Qt sets the list window's flags itself
            # after the drop-down is made, which dropped it when set any earlier
            box.setWindowFlags(box.windowFlags() | Qt.NoDropShadowWindowHint | Qt.FramelessWindowHint)
            box.setAttribute(Qt.WA_TranslucentBackground, True)
        lay, s = box.layout(), _ListShadow.SIZE
        lay.setContentsMargins(0, 0, 0, 0)           # Qt sizes and places the list as always...
        super().showPopup()
        lay.setContentsMargins(0, 0, s, s)           # ...then its window grows by the shadow, right and down
        box.resize(box.width() + s, box.height() + s)
        self._shade.setGeometry(box.rect())
        # it floats a little away from the box (rounded, like the box: their corners no longer meet), opening inside
        # this window when it can (see _goes_up)
        top = self.mapToGlobal(self.rect().topLeft()).y()
        if self._goes_up(box.height() + self.LIST_GAP):
            y = top - self.LIST_GAP - (box.height() - s)
        else:
            y = top + self.height() + self.LIST_GAP
        box.move(box.x(), y - (box.mapToGlobal(QPoint(0, 0)).y() - box.y()))
        # nothing highlighted when it opens (the choice in use already shows in the box itself); the highlight
        # follows the mouse from there
        self.view().selectionModel().clearSelection()
        self.view().setCurrentIndex(self.view().model().index(-1, 0))
        # a layered window shows the last picture it was given: draw the list afresh now, or a list that changed
        # while it was closed (e.g. the tick moved to another choice) is shown as it was until something redraws
        self.view().viewport().repaint()
        box.repaint()
        self._down = box.mapToGlobal(QPoint(0, 0)).y() >= self.mapToGlobal(QPoint(0, 0)).y()
        if bool(self.property("opens_up")) == self._down:     # (the guess was off: the arrow follows the real way)
            self.setProperty("opens_up", not self._down)
            self.style().unpolish(self)
            self.style().polish(self)
        self._t0 = self._last = time.monotonic()
        self._slide()
        self._slide_timer.start()
        if cloak:                                    # (after that first slide picture reaches its window)
            QTimer.singleShot(0, lambda b=box: shiboken6.isValid(b) and _dwm_flag(b, 13, False))

    def hidePopup(self) -> None:
        self._slide_timer.stop()
        self._box().clearMask()
        super().hidePopup()

    def _slide(self) -> None:
        box = self._box()
        t = (time.monotonic() - self._t0) / self.SLIDE_S
        if not box.isVisible() or t >= 1:
            self._slide_timer.stop()
            box.clearMask()
            box.repaint()
            return
        e = 1 - (1 - t) ** 2.2                       # ease out
        h = max(1, round(box.height() * e))
        box.setMask(QRegion(0, 0 if self._down else box.height() - h, box.width(), h))
        # drawn afresh every frame: the first redraw, right as the window appears, doesn't reach the screen
        # (a layered window keeps showing its last picture - an old tick - until it's drawn again)
        box.repaint()


class DragGhost(QWidget):
    """The cover that follows the mouse while a game (or folder) is dragged. Our own see-through window (not
    Windows' drag picture, which can't change mid-drag), so it can fade almost away over the trash can.
    Picked up: it lifts out of its place at full size and shrinks to dragging size under the mouse.
    Dropped (land): it glides to where it ended up - its new place, into a folder or the trash, or home."""
    NORMAL, OVER_TRASH = 0.85, 0.1
    SMALL = 0.5                      # dragging size, of the full cover
    LIFT_S, LAND_S = 0.16, 0.22

    def __init__(self, pm: QPixmap, start: QRectF):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                         Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.pm = pm
        self.full = QSizeF(pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio())
        self.opacity, self.target = 1.0, self.NORMAL       # fades to see-through as it lifts
        self.cursor = QPointF(QCursor.pos())
        self._from, self._to = QRectF(start), QRectF(start)
        self._mode = "lift"
        self._t0 = self._last_frame = self._t = time.monotonic()
        self._land_opacity = (1.0, 1.0)
        self._done: Optional[Callable] = None
        self._timer = VsyncClock(self)   # (once per screen refresh, at any rate)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(0)                           # frames paced by time (Windows timers tick in 15.6 ms steps)
        self._timer.timeout.connect(self._step)
        self._place()
        self._timer.start()

    @staticmethod
    def _ease(t: float) -> float:
        return 1 - (1 - max(0.0, min(1.0, t))) ** 2.2

    @staticmethod
    def _lerp(a: QRectF, b: QRectF, e: float) -> QRectF:
        return QRectF(a.x() + (b.x() - a.x()) * e, a.y() + (b.y() - a.y()) * e,
                      a.width() + (b.width() - a.width()) * e, a.height() + (b.height() - a.height()) * e)

    def _drag_rect(self) -> QRectF:
        w, h = self.full.width() * self.SMALL, self.full.height() * self.SMALL
        return QRectF(self.cursor.x() - w / 2, self.cursor.y() - h / 2, w, h)

    def current(self) -> QRectF:
        """Where the cover is on screen right now (screen coordinates)."""
        t = (time.monotonic() - self._t0)
        if self._mode == "lift":
            return self._lerp(self._from, self._drag_rect(), self._ease(t / self.LIFT_S))
        if self._mode == "land":
            return self._lerp(self._from, self._to, self._ease(t / self.LAND_S))
        return self._drag_rect()

    def _place(self) -> None:
        """A window round the mouse: while lifting, big enough for the full-size cover on its way; once it's
        dragging size, just that (+ a margin) - Windows recomposites the whole see-through window on every
        mouse move, and most of the lifting-size one would be empty."""
        k = 2.2 if self._mode == "lift" else self.SMALL + 0.1
        w, h = int(self.full.width() * k), int(self.full.height() * k)
        if self.width() == w and self.height() == h:
            self.move(int(self.cursor.x()) - w // 2, int(self.cursor.y()) - h // 2)   # (no new picture needed)
        else:
            self.setGeometry(int(self.cursor.x()) - w // 2, int(self.cursor.y()) - h // 2, w, h)

    def follow(self, global_pos: QPoint) -> None:
        if QPointF(global_pos) == self.cursor and self._mode == "drag":
            return                   # (the edge scroll asks every frame: moving the window again cost ~0.4 ms)
        self.cursor = QPointF(global_pos)
        if self._mode != "land":
            self._place()
            if self._mode != "drag":
                self.update()        # (dragging size: the cover sits still inside its window - moving it is enough)

    def set_faded(self, on: bool) -> None:
        target = self.OVER_TRASH if on else self.NORMAL
        if target != self.target:
            self.target = target
            self._t = time.monotonic()
            self._timer.start()

    def land(self, to: QRectF, end_opacity: float, done: Callable) -> None:
        """Glide from where it is to `to`, fading to end_opacity, then disappear and call done()."""
        self._from = self.current()
        self._to = QRectF(to)
        self._land_opacity = (self.opacity, end_opacity)
        self._mode, self._t0, self._done = "land", time.monotonic(), done
        self.setGeometry(self._from.united(self._to).adjusted(-4, -4, 4, 4).toAlignedRect())
        self._timer.start()

    def finish(self) -> None:
        """Done (or stopped early: another drag starting). The real cover is drawn first (done()), and only
        then does this one go - the other way round, for a frame neither is there and the spot flashes."""
        if self._mode == "gone":
            return
        self._mode = "gone"
        self._timer.stop()
        done, self._done = self._done, None
        if done is not None:
            done()
        QTimer.singleShot(40, self, self._vanish)

    def _vanish(self) -> None:
        self.hide()
        self.deleteLater()

    def _step(self) -> None:
        now = time.monotonic()
        if self._mode == "land":
            if now - self._t0 >= self.LAND_S:
                self.finish()
                return
        else:
            dt, self._t = now - self._t, now
            self.opacity += (self.target - self.opacity) * min(1.0, dt * 16)     # a quick, smooth fade
            if abs(self.target - self.opacity) < 0.01:
                self.opacity = self.target
            if self._mode == "lift" and now - self._t0 >= self.LIFT_S:
                self._mode = "drag"
                self._place()                                # (the smaller window)
            if self._mode == "drag" and self.opacity == self.target:
                self._timer.stop()                           # still: redrawn when the mouse moves
        if now - self._last_frame >= 0.007:
            self._last_frame = now
            self.repaint()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        if self._mode == "land":
            a, b = self._land_opacity
            p.setOpacity(a + (b - a) * self._ease((time.monotonic() - self._t0) / self.LAND_S))
        else:
            p.setOpacity(self.opacity)
        r = self.current().translated(-self.x(), -self.y())
        p.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
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


class PulseButton(QPushButton):
    """The Read / Stop button: while the mouse is over it (and it can be pressed) it swells and settles
    slowly, like breathing. A 3 px margin round the button leaves room to grow without being cut off.
    Drawn here (not by the style): its colour eases (greyed <-> ready <-> hover). A new label lands at once -
    the bar's one cross-fade (BarFader) blends the old label into the new."""
    PULSE, PERIOD = 0.03, 1.6           # grows up to 3 %, one beat every 1.6 s
    MARGINS = (3, 4, 3, 2)              # left, top, right, bottom: as the style sheet's margin (centred on the band)
    RADIUS = 10
    COLOUR_RATE = 14                    # colours ease (per second; ~0.2 s)

    def __init__(self, **kw):
        super().__init__(**kw)
        self._hover = False
        self._scale = 1.0
        self._since = 0.0
        self._t = time.monotonic()
        self._bg: Optional[QColor] = None
        self._fg: Optional[QColor] = None
        self._timer = VsyncClock(self)   # (once per screen refresh, at any rate)
        self._timer.setTimerType(Qt.PreciseTimer)                   # even frames (see EasterEgg._rumble)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self.on_width: Optional[Callable[[], None]] = None   # its label / font changed (the bar keeps it in place)

    def _width_changed(self) -> None:
        if self.on_width is not None:
            self.on_width()
        self.updateGeometry()

    slot_labels: tuple = ()             # every label it can show (Read / Stop / Open): its space fits the longest
    blank = False                       # the button without its label (the bar's fade: the word goes, the box stays)

    def sizeHint(self) -> QSize:
        natural = super().sizeHint()
        w = max([natural.width()] + [self.width_for(t) for t in self.slot_labels])
        return QSize(w, natural.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def width_for(self, text: str) -> int:
        """The button's natural width with this label (as sizeHint would be)."""
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        opt.text = text
        fm = self.fontMetrics()
        return self.style().sizeFromContents(QStyle.CT_PushButton, opt,
                                             QSize(fm.horizontalAdvance(text), fm.height()), self).width()

    # -- what it should look like right now
    def _targets(self) -> tuple[QColor, QColor]:
        if not self.isEnabled():
            return QColor(COLORS["play_disabled"]), QColor(COLORS["play_disabled_text"])
        return QColor(COLORS["play_hover" if self._hover else "play"]), QColor(COLORS["play_text"])

    def setText(self, text: str) -> None:
        if text == self.text():
            return
        super().setText(text)
        self._width_changed()
        self.update()

    def _pulsing(self) -> bool:
        return self._hover and self.isEnabled()

    def _kick(self) -> None:
        self._t = time.monotonic()
        self._timer.start()

    def enterEvent(self, ev) -> None:
        super().enterEvent(ev)
        self._hover = True
        self._since = time.monotonic()
        self._kick()

    def leaveEvent(self, ev) -> None:
        super().leaveEvent(ev)
        self._hover = False
        self._kick()

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() in (QEvent.EnabledChange, QEvent.StyleChange, QEvent.FontChange):
            self._since = time.monotonic()
            if ev.type() != QEvent.EnabledChange:   # (theme / language font: the size follows at once)
                self._width_changed()
            self._kick()

    def _step(self) -> None:
        now = time.monotonic()
        dt, self._t = min(0.1, now - self._t), now
        busy = False
        # breathing while hovered
        if self._pulsing():
            t = now - self._since
            self._scale = 1 + self.PULSE * (1 - math.cos(2 * math.pi * t / self.PERIOD)) / 2
            busy = True
        else:
            self._scale += (1.0 - self._scale) * min(1.0, dt * 16)    # settle back (~0.2 s, at any frame rate)
            if abs(self._scale - 1.0) < 0.001:
                self._scale = 1.0
            else:
                busy = True
        # colours ease towards greyed / ready / hover
        bg, fg = self._targets()
        k = min(1.0, dt * self.COLOUR_RATE)
        for name, want in (("_bg", bg), ("_fg", fg)):
            cur = getattr(self, name) or want
            mixed = QColor.fromRgbF(*(a + (b - a) * k for a, b in zip(cur.getRgbF(), want.getRgbF())))
            close = all(abs(a - b) < 0.004 for a, b in zip(mixed.getRgbF(), want.getRgbF()))
            setattr(self, name, want if close else mixed)
            busy = busy or not close
        if not busy:
            self._timer.stop()
        self.update()

    def paintEvent(self, ev) -> None:
        if self._bg is None:
            self._bg, self._fg = self._targets()
        elif (self._bg, self._fg) != self._targets() and not self._timer.isActive():
            self._kick()                               # (e.g. the theme changed: ease to the new colours)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        l, t, r, b = self.MARGINS
        # the visible button fills the space kept for its longest label (owner, 2026-10-17: Read / Stop differed by a
        # few px - it keeps one size and only grows if a label needs more room)
        box = QRectF(l, t, self.width() - l - r, self.height() - t - b)
        # only the button itself grows; its label stays exactly where it is (scaled text shimmers)
        c = box.center()
        grown = QRectF(c.x() - box.width() * self._scale / 2, c.y() - box.height() * self._scale / 2,
                       box.width() * self._scale, box.height() * self._scale)
        p.setPen(Qt.NoPen)
        p.setBrush(self._bg)
        p.drawRoundedRect(grown, self.RADIUS * self._scale, self.RADIUS * self._scale)
        if self.text() and not self.blank:
            p.setPen(self._fg)
            p.setFont(self.font())
            p.drawText(box, Qt.AlignCenter, self.text())
        p.end()


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
        self._timer = VsyncClock(self)   # (once per screen refresh, at any rate)
        self._timer.setTimerType(Qt.PreciseTimer)                   # even frames (see EasterEgg._rumble)
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
        if abs(target - self._scale) < 0.0002:     # (the last step invisible: 0.002 jumped a box's edges ~0.2 px)
            self._scale = target
        if self._scale == target and not self._hot and self._wiggle_from < 0:
            self._timer.stop()                                              # still: stop redrawing
        self.update()

    def _rest_opacity(self) -> float:
        return 0.85

    def paintEvent(self, _ev) -> None:                          # only the icon: no button background
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        if not self._live():
            p.setOpacity(ICON_DIM)                              # greyed: as dim as the bookmark button when off
        else:                                                   # brightens as it grows (no sudden jump)
            rest = self._rest_opacity()
            p.setOpacity(rest + (1 - rest) * max(0.0, min(1.0, (self._scale - 1.0) / (self.HOVER_SCALE - 1.0))))
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


class BookmarkButton(TrashButton):
    """Bookmarks on / off (Bookmark.png, recoloured with the theme, no button box). Moves like the trash
    can: while bookmarks are off, hovering gives one nudge; while they're on, it rocks steadily."""

    def __init__(self):
        super().__init__()
        self.setObjectName("bookmarks")
        self.setAcceptDrops(False)
        self.on = True

    def refresh_icon(self) -> None:
        self._icon = QPixmap.fromImage(themed_image("Bookmark.png", self.ICON * 3))
        self.update()

    def _rest_opacity(self) -> float:
        return 0.85 if self.on else ICON_DIM            # dimmer while bookmarks are hidden

    def set_on(self, on: bool) -> None:
        self.on = on
        if self._hover:
            self.set_hot(on)
        self.update()

    def enterEvent(self, ev) -> None:
        if self.on:
            QWidget.enterEvent(self, ev)
            self._hover = True
            self.set_hot(True)
        else:
            super().enterEvent(ev)

    def leaveEvent(self, ev) -> None:
        super().leaveEvent(ev)
        self.set_hot(False)


class BoxZone:
    """For the easter egg's boxes: their widgets are bigger than the boxes drawn in them (room to grow and
    tilt), so the mouse counts as "over" only inside the box itself (as big as it is drawn right now).
    Subclasses provide _zone() (the box's rectangle) and _arrive() / _depart(); a click outside the box
    goes on to the dark cover behind (= closes the easter egg)."""

    def _zone_init(self) -> None:
        self.setMouseTracking(True)
        self._inside = False

    def _zone_check(self, pos) -> None:
        zone = self._zone()
        if self._inside:
            zone = zone.adjusted(-4, -4, 4, 4)   # a little leeway once inside, so the edge doesn't flicker
        inside = zone.contains(QPointF(pos))
        if inside != self._inside:
            self._inside = inside
            self._arrive() if inside else self._depart()

    def enterEvent(self, ev) -> None:
        QWidget.enterEvent(self, ev)
        self._zone_check(ev.position())

    def mouseMoveEvent(self, ev) -> None:
        self._zone_check(ev.position())

    def leaveEvent(self, ev) -> None:
        QWidget.leaveEvent(self, ev)
        if self._inside:
            self._inside = False
            self._depart()

    def mousePressEvent(self, ev) -> None:
        if self._zone().contains(ev.position()):
            QPushButton.mousePressEvent(self, ev)
        else:
            ev.ignore()


class WobbleBox(BoxZone, TrashButton):
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
        self._zone_init()

    def refresh_icon(self) -> None:          # no icon; just repaint (theme changes)
        self.update()

    def _zone(self) -> QRectF:
        w, h = self.box.width() * self._scale, self.box.height() * self._scale
        lift = (self._scale - 1.0) * 10
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2 - lift, w, h)

    def _arrive(self) -> None:
        if self.rock:
            self.set_hot(True)
        else:                                # (the trash can's own hover: rise + one wiggle)
            self._hover = True
            self._wiggle_from = time.monotonic()
            self._animate()

    def _depart(self) -> None:
        if self.rock:
            self.set_hot(False)
        else:
            self._hover = False
            self._animate()

    def _settled_picture(self, scale: float, fill: str) -> QPixmap:
        """The box exactly as it was always drawn standing still at this size (upright, at rest or grown):
        made once per size / colour / text / screen."""
        dpr = self.devicePixelRatioF()
        key = (scale, fill, COLORS["border"], COLORS["text"], self.text_, self.box_font.key(), self.size(), dpr)
        cache = self.__dict__.setdefault("_pictures", {})
        pm = cache.get(key)
        if pm is None:
            if len(cache) > 8:
                cache.clear()
            pm = QPixmap(round(self.width() * dpr), round(self.height() * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.TextAntialiasing)
            p.translate(self.width() / 2, self.height() / 2 - (scale - 1.0) * 10)
            p.scale(scale, scale)
            r = QRectF(-self.box.width() / 2, -self.box.height() / 2, self.box.width(), self.box.height())
            p.setPen(QPen(QColor(COLORS["border"]), 1))
            p.setBrush(QColor(fill))
            p.drawRoundedRect(r, 8, 8)
            p.setPen(QColor(COLORS["text"]))
            p.setFont(self.box_font)
            p.drawText(r, Qt.AlignCenter, self.text_)
            p.end()
            cache[key] = pm
        return pm

    def paintEvent(self, _ev) -> None:
        # The box is shown from a picture of how it looks standing still at the size it's heading for, moved /
        # turned / grown from there: drawn live, Windows drew the words and edges one way while the box was turned or
        # grown and snapped them to whole pixels once it stood still - a small shift each time a wiggle settled
        # (owner, 2026-10-20). Standing still it IS that picture, 1:1 (the old look exactly). When it heads for a new
        # size, the picture it left blends into the new one over the move.
        target = self._target_scale()
        fill = COLORS["button_hover"] if (self._hot or self._hover) else COLORS["button"]
        dest = (target, fill)
        if dest != getattr(self, "_dest", None):
            self._leaving = getattr(self, "_dest", None)
            self._dest, self._dest_from = dest, self._scale
        s = self._scale
        span = target - self._dest_from
        k = 1.0 if self._leaving is None or abs(span) < 1e-9 else max(0.0, min(1.0, (s - self._dest_from) / span))
        if k >= 1.0:
            self._leaving = None
        angle = self._angle(time.monotonic())
        cx, cy = self.width() / 2, self.height() / 2
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        layers = ([(self._leaving, 1.0)] if self._leaving is not None else []) + [(dest, k)]
        for (pic_scale, pic_fill), opacity in layers:
            if opacity <= 0:
                continue
            p.save()
            if s != pic_scale or angle:              # (standing still at its size: the picture as it is)
                p.translate(cx, cy - (s - 1.0) * 10)
                p.rotate(angle)
                p.scale(s / pic_scale, s / pic_scale)
                p.translate(-cx, -(cy - (pic_scale - 1.0) * 10))
            p.setOpacity(opacity)
            p.drawPixmap(0, 0, self._settled_picture(pic_scale, pic_fill))
            p.restore()
        p.end()


class AnkiBox(BoxZone, QPushButton):
    """The easter egg's reward: a box like the WobbleBoxes that opens Anki. While the mouse is over it, the box
    beats like the Read button, only stronger; the text stays still."""
    PULSE, PERIOD = 0.10, 1.2           # grows up to 10 %, one beat every 1.2 s

    def __init__(self, text: str):
        super().__init__()
        self.text_ = text
        self.box_font = ui_font(13)
        fm = QFontMetrics(self.box_font)
        self.box = QSizeF(fm.horizontalAdvance(text) + 56, fm.height() + 26)
        # the same footprint as a WobbleBox, so the three sit evenly spaced
        self.setFixedSize(int(self.box.width() * 1.2) + 24, int(self.box.height() * 1.2) + 44)
        self._hover = False
        self._scale = 1.0
        self._since = 0.0
        self._timer = VsyncClock(self)   # (once per screen refresh, at any rate)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._step)
        self._zone_init()

    def hitButton(self, pos) -> bool:         # only the box itself, not the room left around it
        return self._rect().contains(QPointF(pos))

    def _zone(self) -> QRectF:
        return self._rect()

    def _rect(self, scale: float = 1.0) -> QRectF:
        w, h = self.box.width() * scale, self.box.height() * scale
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _arrive(self) -> None:
        self.setCursor(Qt.PointingHandCursor)
        self._hover = True
        self._since = time.monotonic()
        self._timer.start()

    def _depart(self) -> None:
        self.unsetCursor()
        self._hover = False
        self._timer.start()

    def _step(self) -> None:
        now = time.monotonic()
        dt, self._last_step = min(0.1, now - getattr(self, "_last_step", now)), now
        if self._hover:
            t = now - self._since
            self._scale = 1 + self.PULSE * (1 - math.cos(2 * math.pi * t / self.PERIOD)) / 2
        else:
            # settle back: a quarter of the way per 1/60 s, at any screen rate
            self._scale += (1.0 - self._scale) * (1 - 0.75 ** (dt * 60))
            if abs(self._scale - 1.0) < 0.001:
                self._scale = 1.0
                self._timer.stop()
        self.update()

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setPen(QPen(QColor(COLORS["border"]), 1))
        p.setBrush(QColor(COLORS["button_hover"] if self._hover else COLORS["button"]))
        p.drawRoundedRect(self._rect(self._scale), 8 * self._scale, 8 * self._scale)
        p.setPen(QColor(COLORS["text"]))
        p.setFont(self.box_font)
        p.drawText(self._rect(), Qt.AlignCenter, self.text_)     # (unscaled: the text never moves)
        p.end()


class UpdateDownload(QThread):
    """Downloads and unpacks a release in the background (progress in bytes)."""
    progress = Signal(int, int)
    done = Signal(object)                # the unpacked folder (Path)
    failed = Signal(object)

    def __init__(self, release: "updater.Release", folder: Path):
        super().__init__()
        self.release, self.folder = release, folder
        self.stop = False

    def run(self) -> None:
        try:
            z = updater.download(self.release, self.folder, lambda d, t: self.progress.emit(d, t), lambda: self.stop)
            self.done.emit(updater.stage(z, self.folder))
        except Exception as e:  # noqa: BLE001 - reported to the UI
            self.failed.emit(e)


class UpdateDialog(QDialog):
    """Update Now: downloads the new version with a progress bar (Cancel stops it)."""

    def __init__(self, release: "updater.Release", folder: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("update.available_title"))
        self.release, self.new = release, None
        self.error: Optional[Exception] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(12)
        self.label = QLabel(tr("update.downloading", version=release.version, done=0,
                               total=max(1, round(release.size / 1048576))))
        self.label.setMinimumWidth(380)
        lay.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.setSpacing(BUTTON_GAP)
        cancel = QPushButton(tr("game.cancel"))
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        row.addStretch()
        lay.addLayout(row)
        self.task = UpdateDownload(release, folder)
        self.task.progress.connect(self._progress)
        self.task.done.connect(self._done)
        self.task.failed.connect(self._failed)
        _LIVE_TASKS.add(self.task)
        self.task.finished.connect(lambda: _LIVE_TASKS.discard(self.task))
        self.task.start()

    def _progress(self, done: int, total: int) -> None:
        if total > 0:
            self.bar.setValue(round(1000 * done / total))
        self.label.setText(tr("update.downloading", version=self.release.version, done=round(done / 1048576),
                              total=max(1, round((total or self.release.size) / 1048576))))

    def _done(self, new) -> None:
        self.new = new
        self.accept()

    def _failed(self, e) -> None:
        if not isinstance(e, InterruptedError):
            self.error = e
        self.reject()

    def reject(self) -> None:
        self.task.stop = True                    # (Cancel: the half-download is deleted)
        super().reject()


class DimLayer(QWidget):
    """The window goes dark with boxes in the middle (the easter egg, a game's notes). Clicking anywhere else
    (or Esc) brings the library back."""
    DARK = 175            # how dark the cover gets (0-255)
    FADE_S = 0.3          # darkening / brightening, with the boxes fading alongside

    def __init__(self, window: QWidget, boxes: list):
        super().__init__(window)
        self.setGeometry(window.rect())
        self.setFocusPolicy(Qt.StrongFocus)
        self._alpha, self._target = 0.0, float(self.DARK)
        self.boxes = boxes
        for b in self.boxes:
            b.setParent(self)
        self._place()
        window.installEventFilter(self)          # follow the window's size
        self._fade = VsyncClock(self)   # (once per screen refresh, at any rate)
        self._fade.setInterval(16)
        self._fade.timeout.connect(self._step)
        self._snap, self._box_snaps, self._grabbing = None, [], False
        self._freeze(window.grab())               # (taken before this layer shows: the window as it was)
        self.show()
        self.raise_()
        self.setFocus()
        self._begin_fade()

    # The fades draw a picture of the window taken as they start, darkened, with pictures of the boxes on top: a see-
    # through layer over the live window made Qt redraw the WHOLE window (background picture included) on every
    # frame - ~40 % of frames late and the darkness stepping unevenly (owner, 2026-10-20: "something seems off").
    # The boxes used to pop in / out at once; they fade with the darkness now. Settled: the live window again.
    def _freeze(self, snap: QPixmap) -> None:
        self._snap = snap
        self._box_snaps = [(b.geometry().topLeft(), self._box_picture(b)) for b in self.boxes]
        for b in self.boxes:
            b.hide()
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)   # (nothing underneath is redrawn meanwhile)

    @staticmethod
    def _box_picture(b: QWidget) -> QPixmap:
        """The box as drawn, on a see-through picture (grab() filled its spare margin with a solid colour: a grey
        square round each box while fading)."""
        dpr = b.devicePixelRatioF()
        pm = QPixmap(round(b.width() * dpr), round(b.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        b.render(pm, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        return pm

    def _thaw(self) -> None:
        self._snap, self._box_snaps = None, []
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def _begin_fade(self) -> None:
        self._from, self._t0 = self._alpha, frame_time()
        self._fade.start()

    def _place(self) -> None:
        gap = -18                                # the boxes' own margins already leave space between them
        total = sum(b.height() for b in self.boxes) + gap * (len(self.boxes) - 1)
        y = (self.height() - total) // 2
        for b in self.boxes:
            b.move((self.width() - b.width()) // 2, y)
            y += b.height() + gap

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.parent() and ev.type() == QEvent.Resize:
            self.setGeometry(self.parent().rect())
            self._place()
            if self._snap is not None:              # (the picture no longer fits: the live window, darkened)
                self._thaw()
                if self._target:
                    for b in self.boxes:
                        b.show()
        return False

    def _step(self) -> None:
        # placed by when the frame reaches the screen (see frame_time), a fixed length, eased out: even steps
        s = min(1.0, max(0.0, (frame_time() - self._t0) / self.FADE_S))
        self._alpha = self._from + (self._target - self._from) * (1 - (1 - s) ** 3)
        if s >= 1.0:
            self._alpha = self._target
            self._fade.stop()
            if self._target == 0:                # faded away: gone
                self.parent().removeEventFilter(self)
                self.deleteLater()
                return
            self._thaw()                         # settled dark: the live window (lights, rocking covers) again
            for b in self.boxes:
                b.show()
            self._settled()
        self.update()

    def _settled(self) -> None:
        """Faded in: the boxes are live now."""

    def dismiss(self) -> None:
        if self._target != 0:
            self._target = 0.0
            if self._snap is None:               # (still opening: its picture is the window as it is anyway)
                boxes = [(b.geometry().topLeft(), self._box_picture(b)) for b in self.boxes]
                for b in self.boxes:
                    b.hide()                     # (not in the picture: they fade on top of it)
                self._grabbing = True            # (this layer draws nothing into the picture)
                try:
                    snap = self.parent().grab()
                finally:
                    self._grabbing = False
                self._freeze(snap)
                self._box_snaps = boxes
            self._begin_fade()

    def paintEvent(self, _ev) -> None:
        if self._grabbing:
            return
        p = QPainter(self)
        if self._snap is not None:
            p.drawPixmap(self.rect(), self._snap)
        p.fillRect(self.rect(), QColor(0, 0, 0, int(self._alpha)))
        if self._box_snaps:
            p.setOpacity(min(1.0, self._alpha / self.DARK))
            for at, pm in self._box_snaps:
                p.drawPixmap(at, pm)
        p.end()

    def mousePressEvent(self, ev) -> None:      # (the boxes take their own clicks, so this is "elsewhere")
        self.dismiss()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape:
            self.dismiss()
        else:
            super().keyPressEvent(ev)


class EasterEgg(DimLayer):
    """Click the logo: the window goes dark with two boxes in the middle."""

    def __init__(self, window: QWidget):
        boxes = [WobbleBox(f"Ver. {APP_VERSION}", rock=False), WobbleBox(tr("egg.title"), rock=True)]   # top, bottom
        settings = getattr(window, "settings", None)
        anki_path = settings.anki_path if settings else ""
        if anki_path:                            # the reward for hunting (Settings > Anki)
            anki = AnkiBox(tr("egg.anki"))
            boxes.insert(0, anki)                # above the version
        super().__init__(window, boxes)
        self.anki_path = anki_path
        if anki_path:
            boxes[0].clicked.connect(self._open_anki)
        # hovering the rocking "Easter Egg" box makes every cover in the library rock with it
        self._rocker = next(b for b in self.boxes if isinstance(b, WobbleBox) and b.rock)
        self._rumble = VsyncClock(self)   # (once per screen refresh, at any rate)
        self._rumble.setTimerType(Qt.PreciseTimer)   # (a normal timer ticks unevenly on Windows: 16 / 31 ms)
        self._rumble.setInterval(16)
        self._rumble.timeout.connect(self._rumble_step)
        self._rumble.start()

    def _rumble_step(self) -> None:
        b, w = self._rocker, self.parent()
        grid = getattr(w, "grid", None)
        if grid is None:
            return
        # as strong as the box has grown (eases in and out with it); each cover rocks in its own rhythm
        k = max(0.0, min(1.0, (b._scale - 1.0) / (b.HOT_SCALE - 1.0))) if b.isVisible() else 0.0
        amp = b.ROCK_DEG * k
        if bool(amp) != bool(w.rumble):
            launcher.hold_warm_up(bool(amp))     # (its first look at all programs stalls the frames)
            if not amp:
                grid.viewport().update()         # (settled: drawn upright once more)
        # the window's own animation clock draws the rocking (MainWindow._tick): one redraw per frame, evenly
        # spaced - this timer redrawing as well gave two unevenly spaced paints per frame
        w.rumble = amp
        if amp:
            w._update_anim()

    def _open_anki(self) -> None:
        w = self.parent()
        if w is not None:
            w.anki_from_here = True                  # (Discord presence: Anki in front counts as Browsing)
        try:
            if outside.show_anki(self.anki_path):    # already open: just bring it forward (starting another
                self.dismiss()                       # copy takes seconds before it hands over to the open one)
                return
        except Exception:                            # (finding it failed: start it as usual)
            pass
        try:
            outside.popen([self.anki_path], cwd=str(Path(self.anki_path).parent))
        except OSError as e:
            message(self.parent(), tr("egg.anki_failed", error=e))
            return
        self.dismiss()

    def dismiss(self) -> None:
        if self._target != 0:
            super().dismiss()
            self._rumble_step()                  # (boxes hidden: the covers settle straight back)
            self._rumble.stop()


class NotesLayer(DimLayer):
    """A game's notes (right-click > Notes): a big text box over the darkened window. Saved when it closes
    (a click outside or Esc; owner's choice: no saving while typing)."""
    SIZE = 0.725          # of the window, each way

    def __init__(self, window: QWidget, title: str, text: str, save):
        box = QFrame(objectName="notes_box")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = ElidedLabel(title, objectName="notes_title")      # (the library's font, like everything else)
        head.setTextFormat(Qt.PlainText)
        head.setMinimumWidth(1)                           # (a long title ends in "..." instead of widening the box)
        lay.addWidget(head)
        self.head = head
        # (a QTextEdit, not QPlainTextEdit: that one scrolls by whole lines - the wheel glide (SmoothWheel) and
        # wheel-press scrolling need a bar that moves by the pixel)
        self.editor = QTextEdit(objectName="notes")
        self.editor.setAcceptRichText(False)              # (pasted text arrives plain)
        self.editor.setPlainText(text)
        self.editor.setPlaceholderText(tr("notes.placeholder"))
        self.editor.moveCursor(QTextCursor.End)
        lay.addWidget(self.editor, 1)
        self._text, self._save = text, save
        super().__init__(window, [box])
        self.editor.installEventFilter(self)              # (the window's keys stay out while typing: see eventFilter)

    def eventFilter(self, obj, ev) -> bool:
        # every key but Esc belongs to the notes: the window's own (F5 refresh, Alt+Left back) did their thing behind
        # the open notes
        if obj is self.editor and ev.type() == QEvent.ShortcutOverride and ev.key() != Qt.Key_Escape:
            ev.accept()
            return False
        return super().eventFilter(obj, ev)

    def _place(self) -> None:
        # the text box is 72.5 % of the window; it and the title above it are centred together
        w, h = round(self.width() * self.SIZE), round(self.height() * self.SIZE)
        h += self.head.sizeHint().height() + self.boxes[0].layout().spacing()
        self.boxes[0].setGeometry((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def _settled(self) -> None:
        self.editor.setFocus()

    def keyPressEvent(self, ev) -> None:
        if ev.key() != Qt.Key_Escape and ev.text().isprintable() and ev.text() and self._target:
            self.editor.insertPlainText(ev.text())        # typed while it was still fading in: not lost
        else:
            super().keyPressEvent(ev)

    def refresh(self, text: str) -> None:
        """Newer notes from the cloud folder (the other device wrote them): shown, unless typing has begun here."""
        if text != self._text and self.editor.toPlainText() == self._text and self._target:
            self._text = text
            self.editor.setPlainText(text)
            self.editor.moveCursor(QTextCursor.End)

    def save_now(self, wait: bool = False) -> None:
        text = self.editor.toPlainText()
        if text != self._text:
            self._text = text
            self._save(text, wait)

    def dismiss(self) -> None:
        if self._target != 0:
            self.save_now()
            super().dismiss()


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
class _ColourLayer(QWidget):
    """LiveColour's picture of a window, lying over all of it (clicks pass through)."""

    def __init__(self, win: QWidget):
        super().__init__(win)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_OpaquePaintEvent)          # (nothing beneath it is drawn meanwhile)
        self.image: Optional[QImage] = None

    def paintEvent(self, ev) -> None:
        if self.image is not None:
            p = QPainter(self)
            p.drawImage(0, 0, self.image)
            p.end()


class LiveColour:
    """One colour changing continuously (the colour picker's drag), shown at the screen's own pace (owner, 2026-10-18:
    "liquid smooth", like the live background). A restyle can't do that (Qt rebuilds every widget's styling: 52+ ms
    a step). Every pixel on screen depends LINEARLY on any one colour role (flat fills, and anti-aliased edges are
    alpha blends): drawn once with the role black (= everything else) and once white, their difference is how much of
    the role each pixel holds. A step is then: the black picture + that difference tinted with the new colour (Qt's
    Multiply + Plus: ~1 ms for the main window), on a layer over each window. Measured against the real restyle:
    99.95 % of pixels identical, the rest within 8/255 (text gamma, derived shades). Set-up ~115 ms (two restyles
    + grabs), done when the picker opens; finish() once the real look is back underneath. The windows themselves are
    left in the real look the whole time (a third restyle), so nothing depends on the layer going away."""

    def __init__(self, role: str, colors: dict, preview: Callable[[dict], None], exclude: tuple = ()):
        from PySide6.QtWidgets import QColorDialog
        self.role = role
        app_ = QApplication.instance()
        self.windows = [w for w in app_.topLevelWidgets()
                        if w.isVisible() and w.windowType() in (Qt.Window, Qt.Dialog)
                        and not isinstance(w, (ThemeDialog, QColorDialog)) and w not in exclude]
        hold_pulse(True)        # (the lights pause where they are: the pictures and the window underneath agree)
        shots = []
        for probe in ("#000000", "#FFFFFF"):
            c = dict(colors)
            c[role] = probe
            preview(c)
            shots.append([w.grab().toImage().convertToFormat(QImage.Format_RGB32) for w in self.windows])
        preview(colors)                                   # (the real look underneath again)
        self.parts = []
        for w, black, white in zip(self.windows, *shots):
            diff = QImage(white)                          # how much of the role each pixel holds (x 255)
            p = QPainter(diff)
            p.setCompositionMode(QPainter.CompositionMode_Difference)
            p.drawImage(0, 0, black)
            p.end()
            layer, canvas = QImage(black), QImage(black)
            cover = _ColourLayer(w)
            cover.setGeometry(w.rect())
            cover.image = canvas
            self.parts.append((cover, black, diff, layer, canvas))
        self.show(colors[role])
        for cover, *_ in self.parts:
            cover.raise_()
            cover.show()

    def show(self, colour: str) -> None:
        col = QColor(colour)
        for cover, black, diff, layer, canvas in self.parts:
            p = QPainter(layer)
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.drawImage(0, 0, diff)
            p.setCompositionMode(QPainter.CompositionMode_Multiply)
            p.fillRect(QRect(QPoint(0, 0), layer.size()), col)
            p.end()
            p = QPainter(canvas)
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.drawImage(0, 0, black)
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            p.drawImage(0, 0, layer)
            p.end()
            try:
                cover.update()
            except RuntimeError:                          # (its window has closed)
                pass

    def finish(self) -> None:
        """The real look is back underneath: take the layers away (same frame: the two match)."""
        hold_pulse(False)
        for cover, *_ in self.parts:
            try:
                cover.hide()
                cover.deleteLater()
            except RuntimeError:
                pass
        self.parts = []


class ThemeGrid(QWidget):
    """Custom Theme's Saved Themes, two to a row: each a small drawing of the library in its colours, background and
    font, its name underneath, and the big + (save the current look) last. One widget draws them all: as separate
    widgets, every live preview step re-styled each of them (40 themes: 245 ms a step instead of 65)."""
    CARD_H, NAME, GAP, COLUMNS = 150, 26, 12, 2

    def __init__(self, dialog: "ThemeDialog"):
        super().__init__()
        self.dialog = dialog
        self.items: list = []
        self.chosen: Optional[str] = None          # the id of the theme being tried
        self.hovered = -1                          # the card under the mouse (index; len(items) = the +)
        self._pics: dict = {}                      # theme id -> its background cropped to the drawing's size
        self._look: dict = {}                      # theme id -> (colours, crop, font family)
        self.setMouseTracking(True)

    def set_items(self, items: list) -> None:
        self.items = items
        self._look = {it["theme"]["id"]: (theme_colors("custom", _colour_map(it["theme"].get("colors"))),
                                          theme_crop(it["theme"]), self.dialog._theme_font(it, i18n.language()))
                      for it in items}
        self._pics = {k: v for k, v in self._pics.items() if k in self._look}
        rows = (len(items) + 1 + self.COLUMNS - 1) // self.COLUMNS
        self.setFixedHeight(rows * self.CARD_H + (rows - 1) * self.GAP)
        self._set_hovered(self._index_at(self.mapFromGlobal(QCursor.pos())) if self.underMouse() else -1)
        self.update()

    def card_rect(self, i: int) -> QRectF:
        w = (self.width() - self.GAP * (self.COLUMNS - 1)) / self.COLUMNS
        row, col = divmod(i, self.COLUMNS)
        return QRectF(col * (w + self.GAP), row * (self.CARD_H + self.GAP), w, self.CARD_H)

    def _index_at(self, pos) -> int:
        for i in range(len(self.items) + 1):
            if self.card_rect(i).contains(QPointF(pos)):
                return i
        return -1

    def _set_hovered(self, i: int) -> None:
        if i != self.hovered:
            for k in (self.hovered, i):
                if k >= 0:
                    self.update(self.card_rect(k).toAlignedRect().adjusted(-2, -2, 2, 2))
            self.hovered = i
            self.setCursor(Qt.PointingHandCursor if i >= 0 else Qt.ArrowCursor)
            self.setToolTip(tr("themes.add_tip") if i == len(self.items) else "")

    def mouseMoveEvent(self, e) -> None:
        self._set_hovered(self._index_at(e.position()))

    def leaveEvent(self, e) -> None:
        self._set_hovered(-1)

    def mouseReleaseEvent(self, e) -> None:
        i = self._index_at(e.position())
        if e.button() == Qt.LeftButton and i >= 0:
            self.dialog._card_clicked(i)

    def contextMenuEvent(self, e) -> None:
        i = self._index_at(e.pos())
        if 0 <= i < len(self.items):
            self.dialog._card_menu(self.items[i], e.globalPos())

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        for i in range(len(self.items) + 1):
            card = self.card_rect(i)
            if card.intersects(QRectF(e.rect()).adjusted(-2, -2, 2, 2)):
                self._paint_card(p, i, card)
        p.end()

    def _paint_card(self, p: QPainter, i: int, card: QRectF) -> None:
        r = QRectF(card.left() + 1, card.top() + 1, card.width() - 2, card.height() - self.NAME - 2)
        hot = i == self.hovered
        if i == len(self.items):
            self._paint_plus(p, r, hot)
            name, colour = tr("themes.add"), COLORS["text"] if hot else COLORS["muted"]
        else:
            t = self.items[i]["theme"]
            shape = QPainterPath()
            shape.addRoundedRect(r, 8, 8)
            p.save()
            p.setClipPath(shape)
            self._paint_library(p, r, self.items[i])
            p.restore()
            chosen = t["id"] == self.chosen
            p.setPen(QPen(QColor(COLORS["select"] if chosen else COLORS["focus"] if hot else COLORS["border"]),
                          2 if chosen else 1))
            p.setBrush(Qt.NoBrush)
            p.drawPath(shape)
            name, colour = str(t.get("name", "")), COLORS["text"]
        p.setFont(ui_font(10))
        p.setPen(QColor(colour))
        text = p.fontMetrics().elidedText(name, Qt.ElideRight, int(card.width()) - 8)
        p.drawText(QRectF(card.left(), card.bottom() - self.NAME, card.width(), self.NAME), Qt.AlignCenter, text)

    @staticmethod
    def _paint_plus(p: QPainter, r: QRectF, hot: bool) -> None:
        p.setPen(QPen(QColor(COLORS["focus"] if hot else COLORS["border"]), 1.5, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QPen(QColor(COLORS["text"] if hot else COLORS["muted"]), 3, Qt.SolidLine, Qt.RoundCap))
        c, arm = r.center(), 16
        p.drawLine(QPointF(c.x() - arm, c.y()), QPointF(c.x() + arm, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - arm), QPointF(c.x(), c.y() + arm))

    def _paint_library(self, p: QPainter, r: QRectF, item: dict) -> None:
        tid = item["theme"]["id"]
        c, crop, family = self._look[tid]
        p.fillRect(r, QColor(c["bg"]))
        image = item["image"]
        if not image.isNull():
            size = r.size().toSize()
            pic = self._pics.get(tid)
            if pic is None or pic.size() != size:              # (made once per size; never the cloud's file)
                _, zoom, x, y = crop
                part, _, _ = crop_rect(image.width(), image.height(), zoom, x, y,
                                       aspect=size.width() / max(1, size.height()))
                pic = self._pics[tid] = QPixmap.fromImage(image.copy(part.toAlignedRect()).scaled(
                    size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
            p.setOpacity(crop[0])
            p.drawPixmap(r.topLeft(), pic)
            p.setOpacity(1.0)
        p.setPen(Qt.NoPen)
        top, bottom = r.height() * 0.14, r.height() * 0.24
        p.fillRect(QRectF(r.left(), r.top(), r.width(), top), QColor(c["band"]))
        p.fillRect(QRectF(r.left(), r.bottom() - bottom, r.width(), bottom), QColor(c["band"]))
        p.setBrush(QColor(c["input"]))                                  # the search box
        p.drawRoundedRect(QRectF(r.left() + r.width() * 0.3, r.top() + top * 0.25, r.width() * 0.4, top * 0.5), 3, 3)
        gap = r.width() * 0.05                                          # a row of covers
        w = (r.width() - gap * 5) / 4
        h = min(w * 1.4, r.height() - top - bottom - gap * 2)
        y = r.top() + top + (r.height() - top - bottom - h) / 2
        for k in range(4):
            box = QRectF(r.left() + gap + k * (w + gap), y, w, h)
            grad = QLinearGradient(box.topLeft(), box.bottomLeft())
            grad.setColorAt(0, QColor(c["placeholder_top"]))
            grad.setColorAt(1, QColor(c["placeholder_bottom"]))
            p.setBrush(grad)
            p.drawRoundedRect(box, 3, 3)
        band = QRectF(r.left(), r.bottom() - bottom, r.width(), bottom)  # the Read button and some text in the font
        play = QRectF(band.left() + gap, band.top() + band.height() * 0.22, r.width() * 0.22, band.height() * 0.56)
        p.setBrush(QColor(c["play"]))
        p.drawRoundedRect(play, 4, 4)
        f = QFont()
        f.setFamilies([family, *ui_families()] if family else ui_families())
        f.setPointSizeF(9)
        p.setFont(f)
        p.setPen(QColor(c["text"]))
        p.drawText(QRectF(play.right() + gap, band.top(), band.right() - play.right() - gap * 2, band.height()),
                   Qt.AlignVCenter | Qt.AlignLeft, "Aa あア漢")


def _number(v, default: float) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else default


def _colour_map(colors) -> dict:
    """A saved theme's colours, only the valid ones (a damaged entry falls back to Dark's)."""
    if not isinstance(colors, dict):
        return {}
    return {k: v for k, v in colors.items() if isinstance(k, str) and isinstance(v, str) and QColor.isValidColorName(v)
            and v.startswith("#") and len(v) == 7}


def theme_crop(theme: dict) -> tuple:
    """A saved theme's background (opacity, zoom, x, y), kept within what the Library Background window allows."""
    bg = theme.get("bg") if isinstance(theme.get("bg"), dict) else {}
    return (max(0.0, min(1.0, _number(bg.get("opacity"), 0.35))), max(1.0, min(4.0, _number(bg.get("zoom"), 1.0))),
            max(0.0, min(1.0, _number(bg.get("x"), 0.5))), max(0.0, min(1.0, _number(bg.get("y"), 0.5))))


def load_saved_themes(root: str, protect: frozenset = frozenset()) -> list[dict]:
    """(In the background.) The saved themes, each with this computer's copies of its picture and fonts and the picture
    decoded small enough for its preview: {"theme", "picture", "fonts", "image"}. protect: copies being used now."""
    from PySide6.QtGui import QImageReader
    items = themes.local_copies(root, default_app_dir() / "theme_cache", themes.load(root), protect)
    for item in items:
        img = QImage()
        if item["picture"]:
            reader = QImageReader(item["picture"])
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and max(size.width(), size.height()) > 800:      # (decoded small: 4x quicker for big JPEGs)
                reader.setScaledSize(size.scaled(800, 800, Qt.KeepAspectRatio))
            img = reader.read()
        item["image"] = img
    return items


_PREVIEW_FONTS: dict[str, list] = {}       # font file -> its families, loaded once for showing saved themes


class ThemeDialog(TaskOwner, QDialog):
    """Custom theme: every colour role with a swatch and a hex box; changes preview live. With a library
    root, a Saved Themes tab too (themes.py): saved looks to pick from, and a + to save the current one. The cloud
    folder is only ever touched in the background (it can stall for seconds): the list starts loading as the window
    opens, into copies on this computer, so the tab and picking a theme never wait for it."""

    def __init__(self, colors: dict, preview: Callable[[dict], None], parent=None, solo: bool = False,
                 root: str = ""):
        super().__init__(parent)
        from PySide6.QtWidgets import QScrollArea
        self.root = root
        self.chosen: Optional[dict] = None            # a saved theme picked here: {"theme", "bg", "fonts", "files"}
        self._own_fonts = dict(FONT_CHOICE)           # (a saved theme's font this device can't get: keep these)
        self.grid: Optional[ThemeGrid] = None
        self.saved: Optional[list] = None             # the loaded saved themes (None: still loading)
        self._saved_busy = False                      # saving / renaming / deleting one right now
        self.saved_scroll = None
        self.solo = solo                              # (a solo device has no sync lights: their colours aren't listed)
        self.setWindowTitle(tr("theme.title"))
        self.setMinimumSize(430, 560)
        self.resize(450, 620)
        self.colors = dict(colors)
        self.preview = preview
        self._live: Optional[LiveColour] = None       # the colour picker's live drag (see _pick)
        self.rows: dict[str, tuple] = {}
        # redraw the whole window only once changes pause (dragging in the colour picker stays smooth)
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(80)
        self._preview_timer.timeout.connect(lambda: self.preview(self.colors))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)

        if root:
            tabs = QHBoxLayout()
            tabs.setSpacing(6)
            group = QButtonGroup(self)
            for i, key in enumerate(("theme.tab_colours", "theme.tab_saved")):
                b = QPushButton(tr(key), objectName="seg")
                b.setCheckable(True)
                group.addButton(b, i)
                tabs.addWidget(b)
            tabs.addStretch()
            group.button(0).setChecked(True)
            group.idClicked.connect(self._tab)
            self.tabs = group
            lay.addLayout(tabs)
            lay.addSpacing(18 - lay.spacing())    # (as much room under the tabs as above them: the window's top margin)
        self.pages = QStackedWidget()
        page = QWidget()
        outer, lay = lay, QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.pages.addWidget(page)
        outer.addWidget(self.pages, 1)
        if root:
            self.saved_scroll = QScrollArea()
            self.saved_scroll.setWidgetResizable(True)
            self.saved_scroll.setFrameShape(QFrame.NoFrame)
            body = QWidget(objectName="scrollbody")
            col = QVBoxLayout(body)
            col.setContentsMargins(0, 2, 0, 0)
            # room beside the scroll bar only while there is one: the cards' right edge lines up with Save otherwise
            self.saved_scroll.verticalScrollBar().rangeChanged.connect(
                lambda _lo, hi, col=col: col.setContentsMargins(0, 2, 8 if hi > 0 else 0, 0))
            self.grid = ThemeGrid(self)
            col.addWidget(self.grid)
            col.addStretch()
            self.saved_scroll.setWidget(body)
            self.pages.addWidget(self.saved_scroll)
            self._run(lambda: load_saved_themes(root), self._saved_loaded, lambda _e: self._saved_loaded([]))

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
        lay = outer

        self.warning = QLabel(objectName="hint")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color: {COLORS['danger']};")
        lay.addWidget(self.warning)
        btns = QHBoxLayout()
        btns.setSpacing(BUTTON_GAP)
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
        QTimer.singleShot(0, self, self._fit)

    def _fit(self) -> None:
        """Make the window exactly tall enough for the main colours (no scrolling slack); the extra
        colours scroll."""
        if not shiboken6.isValid(self):
            return                                    # (closed and deleted before this came round)
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
            if group == "lights" and self.solo:
                continue
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
            QTimer.singleShot(0, self, lambda: self.scroll.verticalScrollBar().setValue(
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
        # update continuously while dragging (at most every 80 ms), not only once it stops - unless the colour
        # picker's live layer shows it (every step, at once)
        if self._live is None and not self._preview_timer.isActive():
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
        if self._live is not None and k == self._live.role:
            self._live.show(self.colors[k])
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
        self._preview_timer.stop()
        try:
            self._live = LiveColour(k, self.colors, self.preview)         # live while choosing, every step
        except Exception:                                               # (then the restyles every 80 ms as before)
            self._live = None
        d = QColorDialog(QColor(before), self)
        d.setWindowTitle(tr("theme.pick"))
        d.currentColorChanged.connect(lambda c: self._set(k, c.name()))
        try:
            if d.exec():
                self._set(k, d.currentColor().name())
            else:
                self._set(k, before)
        finally:
            discard(d)                             # (gone before the real look is restyled underneath)
            live, self._live = self._live, None
            if live is not None:
                self.preview(self.colors)          # the real look underneath, then the layer goes (same frame)
                live.finish()

    # ------------------------------------------------------------------ saved themes
    def _tab(self, i: int) -> None:
        self.pages.setCurrentIndex(i)
        self.tabs.button(i).setChecked(True)

    def _saved_loaded(self, items: list) -> None:
        self.saved = items
        self._saved_busy = False
        self._fill_saved()

    def _fill_saved(self) -> None:
        self.grid.chosen = self.chosen["theme"]["id"] if self.chosen else None
        self.grid.set_items(self.saved or [])

    def _theme_font(self, item: dict, lang: str) -> Optional[str]:
        """A saved theme's font for this language, if this device has it or its copy (loaded just for showing it -
        Save adds it for good). None: the device keeps its own."""
        fam = item["theme"].get(f"font_{lang}")
        if not isinstance(fam, str) or not fam:
            return None
        if fam in FONTS:
            return fam
        f = item["fonts"].get(fam)
        if f and f not in _PREVIEW_FONTS:
            fid = QFontDatabase.addApplicationFont(f)
            _PREVIEW_FONTS[f] = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        return fam if f and fam in _PREVIEW_FONTS[f] else None

    def _card_clicked(self, i: int) -> None:
        items = self.grid.items
        if i == len(items):
            self._save_current()
            return
        item = items[i]
        self.grid.chosen = item["theme"]["id"]
        self.grid.update()
        opacity, zoom, x, y = theme_crop(item["theme"])
        fonts = {lang: self._theme_font(item, lang) or self._own_fonts[lang] for lang in ("en", "ja")}
        self.chosen = {"theme": item["theme"], "bg": (item["picture"], opacity, zoom, x, y), "fonts": fonts,
                       "files": dict(item["fonts"])}
        apply_fonts(fonts["en"], fonts["ja"])
        self._load(theme_colors("custom", _colour_map(item["theme"].get("colors"))))   # (restyles everything shortly)
        bg, pic = self.chosen["bg"], item["picture"]
        if pic and pic not in _bg_source:
            # the full picture decoded in the background first: the library decoding it while drawing froze the
            # window for 50-100 ms (a 4K photo)
            self._run(lambda: QImage(pic), lambda img: self._picture_ready(img, bg))
        else:
            apply_background(*bg)

    def _picture_ready(self, img: QImage, bg: tuple) -> None:
        if self.chosen and self.chosen["bg"] == bg:                 # (still the one being tried)
            if not img.isNull():
                _bg_source.clear()
                _bg_source[bg[0]] = img
            apply_background(*bg)

    def _ask_name(self, title: str, name: str = "") -> str:
        d = NameDialog(title, name, self, label=tr("themes.name"))
        try:
            return d.name() if d.exec() else ""
        finally:
            discard(d)

    def _change_saved(self, change: Callable[[], None], after: Callable[[], None] = lambda: None) -> None:
        """Save / rename / delete in the background, then load the list again (the other device may have changed it
        too). One at a time."""
        self._saved_busy = True
        root = self.root
        c = self.chosen
        protect = frozenset([c["bg"][0], *c["files"].values()] if c else [])

        def work():
            change()
            return load_saved_themes(root, protect)

        def done(items):
            self._saved_loaded(items)
            after()

        def failed(e):
            self._saved_busy = False
            message(self, tr("themes.failed", error=e))
        self._run(work, done, failed)

    def _save_current(self) -> None:
        if self._saved_busy or self.saved is None:
            return
        name = self._ask_name(tr("themes.save_title"))
        if not name:
            return
        b = BACKGROUND
        bg = (b["path"], b["opacity"], b["zoom"], b["x"], b["y"]) if b["path"] else None
        root, colors, fonts, files = self.root, dict(self.colors), dict(FONT_CHOICE), dict(FONT_FILES)
        bar = self.saved_scroll.verticalScrollBar()
        self._change_saved(lambda: themes.add(root, name, colors, fonts["en"], fonts["ja"], bg, files),
                           lambda: QTimer.singleShot(0, bar, lambda: bar.setValue(bar.maximum())))   # (the new one: last)

    def _card_menu(self, item: dict, pos: QPoint) -> None:
        if self._saved_busy:
            return
        m = QMenu(self)
        m.addAction(tr("themes.rename"), lambda: self._rename(item["theme"]))
        m.addAction(tr("themes.delete"), lambda: self._delete(item["theme"]))
        m.exec(pos)

    def _rename(self, t: dict) -> None:
        name = self._ask_name(tr("themes.rename_title"), t.get("name", ""))
        if name and name != t.get("name") and not self._saved_busy:
            root, tid = self.root, t["id"]
            self._change_saved(lambda: themes.rename(root, tid, name))

    def _delete(self, t: dict) -> None:
        if heading_message(self, tr("game.remove_sure"), tr("themes.delete_body", name=t.get("name", "")),
                           [("no", tr("game.cancel")), ("yes", tr("game.remove_yes"))], dismiss="no") != "yes":
            return
        if not self._saved_busy:
            root, tid = self.root, t["id"]
            self._change_saved(lambda: themes.delete(root, tid))


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
        self.combo = ThemedCombo(objectName="fontpick")
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
        btns.setSpacing(BUTTON_GAP)
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
        path = pick_file(self, tr("font.pick"), "", tr("font.files"))
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
        main = parent.window() if parent is not None else None
        solo = bool(getattr(getattr(main, "settings", None), "solo", False) or getattr(parent, "_solo", False))
        text.setHtml(guide.guide_html(i18n.language(), COLORS, LIGHTS, solo=solo))
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
        btns.setSpacing(BUTTON_GAP)
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
        f = pick_file(self, tr("bg.choose"), "", tr("file.images"))
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


class FitsScreen:
    """For dialogs laid out as [scroll area holding self._content, button row]: as tall as the contents
    need, but never taller than the screen; then the contents scroll and the buttons stay in view."""

    @staticmethod
    def _screen_room(widget: QWidget) -> QRect:
        screen = widget.screen() or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _fit_height(self, width: int = 0) -> None:
        """As tall as the contents need (with the hint texts wrapped at the real width), but never
        taller than the screen: then the contents scroll and Cancel / Save stay in view."""
        width = max(width, self.width(), self.minimumWidth())
        scroll = self.findChild(QScrollArea)
        if scroll is not None:
            scroll.setViewportMargins(0, 0, 0, 0)            # (re-worked out once fitted: _no_half_rows)
        content = self._content.layout().totalHeightForWidth(width)
        buttons = self.layout().itemAt(1).sizeHint().height()
        room = self._screen_room(self)
        frame = self.frameGeometry().height() - self.geometry().height() if self.isVisible() else 32
        self._most = room.height() - max(frame, 32) - 8
        cap = self._height_cap()
        if cap:
            self._most = min(self._most, cap - frame)     # (the real title bar: the heights match exactly)
        self._set_fit_height(width, min(content + buttons, self._most))
        # the estimate above can be a few pixels short (wrapped hints): then a scroll bar with nothing to
        # scroll appeared. Once laid out, grow by whatever is left over, as far as the screen allows.
        self._top_ups = 3
        QTimer.singleShot(0, self, self._top_up)

    def _height_cap(self) -> int:
        """Tallest the whole window (frame included) may open; 0 = only the screen limits it."""
        return 0

    def _align_with(self) -> Optional[QWidget]:
        """A window to open centred over (top and bottom line up when the heights match); None = Qt's placing."""
        return None

    def _set_fit_height(self, width: int, h: int) -> None:
        # kept centred over its window while it's where we put it (owner, 2026-10-18): a later change of height - the
        # rows coming back with Dual Device, the last pixels of fitting - used to grow only the bottom edge
        unmoved = self.isVisible() and getattr(self, "_aligned_pos", None) == self.pos()
        self.setMinimumHeight(min(320, h))
        target = self._align_with() if getattr(self, "_aligning", False) or unmoved else None
        if self.isVisible() and target is not None and target.isVisible():
            # size and place in ONE step: resize, then move, showed the grown bottom edge for a frame before the
            # window moved up (owner, 2026-10-20: Settings flashed at the bottom going back to Dual Device)
            frame, inner = self.frameGeometry(), self.geometry()
            deco_w, deco_h = frame.width() - inner.width(), frame.height() - inner.height()
            t = target.frameGeometry()
            x = t.left() + (t.width() - (width + deco_w)) // 2
            y = t.top() + (t.height() - (h + deco_h)) // 2
            if getattr(self, "_aligning", False):        # opening: as it always was (one step flashed on open)
                self.resize(width, h)
                self.move(x, y)
            else:
                self.setGeometry(x + inner.x() - frame.x(), y + inner.y() - frame.y(), width, h)
            self._aligned_pos = self.pos()
        else:
            self.resize(width, h)
        if self.isVisible():                    # keep the whole window on the screen
            room = self._screen_room(self)
            frame = max(self.frameGeometry().height() - self.geometry().height(), 32)
            top = self.frameGeometry().top()
            if top + h + frame > room.bottom() or top < room.top():
                self.move(self.x(), max(room.top(), room.bottom() - h - frame))

    def _top_up(self) -> None:
        scroll = self.findChild(QScrollArea)
        if scroll is None or not self.isVisible() or self._top_ups <= 0:
            return
        self._top_ups -= 1
        extra = scroll.verticalScrollBar().maximum()
        if extra > 0 and self.height() < self._most:
            self._set_fit_height(self.width(), min(self.height() + extra, self._most))
            QTimer.singleShot(0, self, self._top_up)
        else:
            self._no_half_rows(scroll)

    HALF_ROW_MAX = 60                   # a row taller than this cut at the edge is left as it is (a long hint text)

    def _no_half_rows(self, scroll: QScrollArea) -> None:
        """When the contents don't fit (Settings opens as tall as the main window), the visible part ended halfway
        through a row: "Install Updates Automatically" showed only its top half (owner, 2026-10-19). The visible
        part now stops just above whatever row the edge would slice, so it opens on whole rows; the rest is a scroll
        away as before. Worked out at each fitting: language / My Device / Dual Device move the cut."""
        scroll.setViewportMargins(0, 0, 0, 0)
        if scroll.verticalScrollBar().maximum() <= 0:
            return                                          # everything fits: nothing is cut
        vp = scroll.viewport()
        bottom = vp.height() + scroll.verticalScrollBar().value()        # (the contents' y at the visible edge)
        content = scroll.widget()
        cut_top = None
        for w in content.findChildren(QWidget):
            if not w.isVisibleTo(content) or w.height() > self.HALF_ROW_MAX:
                continue
            if any(c.isVisibleTo(content) for c in w.findChildren(QWidget)):
                continue                                    # (rows themselves, not the boxes holding them)
            top = w.mapTo(content, QPoint(0, 0)).y()
            if top < bottom < top + w.height():
                cut_top = top if cut_top is None else min(cut_top, top)
        if cut_top is not None and bottom - cut_top <= self.HALF_ROW_MAX:
            scroll.setViewportMargins(0, 0, 0, bottom - cut_top)

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        self._aligning = True                   # only while opening: a window moved by hand stays put
        self._fit_height()
        QTimer.singleShot(400, self, lambda: setattr(self, "_aligning", False))


class SettingsDialog(KeepsTitleBar, FitsScreen, QDialog):
    def _height_cap(self) -> int:
        """Owner's choice: Settings opens no taller than the main window (the rest scrolls). On Windows 10 one pixel
        less: the main window, unfocused behind it, has an almost see-through bottom border while Settings' is solid,
        so at the same size Settings LOOKED one pixel taller (measured on the owner's screen, 2026-10-18; the top edges
        both show a line and match). Windows 11 draws borders differently: not adjusted there (unmeasured)."""
        main = self._align_with()
        if main is None or not main.isVisible():
            return 0
        return main.frameGeometry().height() - (1 if WINDOWS_10 else 0)

    def _align_with(self) -> Optional[QWidget]:
        """...and centred over it, so at the same height top and bottom line up (owner's request)."""
        return self.parentWidget().window() if self.parentWidget() else None

    def __init__(self, current: Optional[LocalSettings], parent=None, welcome: bool = False, solo: bool = False):
        super().__init__(parent)
        self._i18n: list[Callable] = []          # re-applies every text when the language is switched
        self._rules: list = []                       # the faint lines (_rule)
        self._i18n.append(self._place_rules)
        self._tx(self, "settings.welcome" if welcome else "settings.title", "setWindowTitle")
        self.setObjectName("settings_dialog")
        self.setMinimumWidth(580)
        no_open_animation(self)
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
        self.lang = ThemedCombo()
        self.lang.fade_on_pick = "at_once"
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
                    self._isync_head, self._isync_btn = head, b
                    # a solo device: Dual Mode takes Ikemen Sync's place (same size and spot)
                    self._dual_btn = self._tx(QPushButton(), "settings.dual")
                    self._tx(self._dual_btn, "settings.dual_tip", "setToolTip")
                    self._dual_btn.clicked.connect(lambda _=False: self._set_solo(False))
                    self._dual_btn.hide()
                    row.addWidget(self._dual_btn)
            row.addStretch()
            col.addLayout(row)
            pair.addLayout(col, 1)
        lay.addLayout(pair)
        self._rule(lay, "top")

        self._device_head = self._tx(section(""), "settings.device")
        lay.addWidget(self._device_head)
        self.device = ThemedCombo()
        self.device.fade_on_pick = True
        for slot in SLOTS:
            self.device.addItem(tr("settings.device_n", n=SLOT_NUMBER[slot]), slot)
            self._i18n.append(lambda i=self.device.count() - 1, n=SLOT_NUMBER[slot]:
                              self.device.setItemText(i, tr("settings.device_n", n=n)))
        self.device.addItem(tr("settings.device_solo"), "solo")
        self._i18n.append(lambda i=self.device.count() - 1: self.device.setItemText(i, tr("settings.device_solo")))
        # the device number used in Dual Mode: kept while solo, picked again when going back (owner's plan)
        self._dual_slot = current.machine_name if current else SLOTS[0]
        if current:
            self.device.setCurrentIndex(SLOTS.index(current.machine_name))
        lay.addWidget(self.device)
        self._device_hint = self._tx(hint(""), "settings.device_hint")
        lay.addWidget(self._device_hint)

        # room for This Is's open list: it ended 10 px into the cloud folder box, across its top edge (owner,
        # 2026-10-18: "not really clean") - now it ends just above it; Settings' usual faint line sits in the middle
        # of that room (the owner's idea). A widget of its own, so My Device hides it with the cloud folder rows
        self._cloud_gap = self._rule(lay, "cloud")
        self._cloud_head = self._tx(section(""), "settings.cloud")
        lay.addWidget(self._cloud_head)
        self.cloud = QLineEdit(current.cloud_root if current else "")
        self._tx(self.cloud, "settings.cloud_placeholder", "setPlaceholderText")
        self._cloud_row = self._browse_row(self.cloud, self._pick_cloud)
        lay.addLayout(self._cloud_row)
        self._cloud_hint = self._tx(hint(""), "settings.cloud_hint")
        lay.addWidget(self._cloud_hint)
        self.device.currentIndexChanged.connect(self._device_changed)
        self._i18n.append(lambda: self._isync_head.setText(tr("settings.mode" if self._solo else "settings.isync")))
        if (current and current.solo) or solo:      # (solo: "One Computer" at the first start)
            self.device.blockSignals(True)
            self.device.setCurrentIndex(self.device.findData("solo"))
            self.device.blockSignals(False)
            self._set_solo(True, quiet=True)

        lay.addWidget(self._tx(section(""), "settings.le"))
        self.le = QLineEdit(current.le_path if current else "")
        self._tx(self.le, "settings.le_placeholder", "setPlaceholderText")
        lay.addLayout(self._browse_row(self.le, self._pick_le))

        lay.addWidget(self._tx(section(""), "settings.luna"))
        self.luna = QLineEdit(current.luna_path if current else "")
        self._tx(self.luna, "settings.luna_placeholder", "setPlaceholderText")
        lay.addLayout(self._browse_row(self.luna, self._pick_luna))
        lay.addWidget(self._tx(hint(""), "settings.luna_hint"))

        lay.addWidget(self._tx(section(""), "settings.anki"))
        self.anki = QLineEdit(current.anki_path if current else "")
        self._tx(self.anki, "settings.anki_placeholder", "setPlaceholderText")
        lay.addLayout(self._browse_row(self.anki, self._pick_anki))
        lay.addWidget(self._tx(hint(""), "settings.anki_hint"))

        # a faint line: what follows is personalisation
        self._rule(lay, "anki")

        lay.addWidget(self._tx(section(""), "settings.accessibility"))
        lay.addSpacing(4)
        self.pulse = self._tx(QCheckBox(), "settings.pulse")
        if self._solo:                         # (only hide: shown before it has a parent, it'd open as its own
            self.pulse.hide()                  # window for a moment - the main window's title bar blinked)
        self.pulse.setChecked(current.pulse if current else True)
        # shown at once, like the theme (owner, 2026-10-17): Save keeps it, Cancel puts it back
        self._orig_pulse = self.pulse.isChecked()
        self.pulse.toggled.connect(lambda on: getattr(self.parent(), "set_pulse", lambda _on: None)(on))
        self.auto_update = self._tx(QCheckBox(), "settings.auto_update")
        self.auto_update.setChecked(current.auto_update if current else False)
        self.startup = self._tx(QCheckBox(), "settings.startup")
        self.startup.setChecked(startup.is_enabled())        # the real state of Windows' startup list
        self.vndb = self._tx(QCheckBox(), "settings.vndb")
        self.vndb.setChecked(current.vndb if current else True)
        self.vndb_safe = self._tx(QCheckBox(), "settings.vndb_safe")
        self.vndb_safe.setChecked(current.vndb_safe_only if current else False)
        self.discord = self._tx(QCheckBox(), "settings.discord")
        self.discord.setChecked(current.discord if current else False)      # (off until turned on)
        lay.addWidget(self.pulse)
        lay.addWidget(self.discord)                          # (right under Pulsing Sync Lights - owner)
        lay.addWidget(self.startup)
        lay.addWidget(self.auto_update)
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
        row.setSpacing(BUTTON_GAP)
        row.addStretch()
        cancel = self._tx(QPushButton(), "settings.cancel")
        cancel.clicked.connect(self.reject)
        save = self._tx(QPushButton(), "settings.save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        row.setContentsMargins(22, 10, 22, 18)
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

    _solo = False

    def _device_changed(self, _i: int) -> None:
        slot = self.device.currentData()
        if slot == "solo":
            self._set_solo(True)
        else:
            self._dual_slot = slot

    def _set_solo(self, on: bool, quiet: bool = False) -> None:
        """A Solo Device (owner's plan): the device number and cloud folder rows go, and Dual Mode takes Ikemen Sync's
        place; Dual Mode brings them back with the device number and cloud folder used before. Applied on Save."""
        self._solo = on
        frozen = [] if quiet else [x for x in (self,) if x.updatesEnabled()]
        for x in frozen:
            x.setUpdatesEnabled(False)
        try:
            row = [self._cloud_row.itemAt(i).widget() for i in range(self._cloud_row.count())]
            for w in (self._device_head, self.device, self._device_hint, self._cloud_gap, self._cloud_head,
                      self._cloud_hint, *row):
                if w is not None:
                    w.setVisible(not on)
            self._isync_btn.setVisible(not on)
            self._dual_btn.setVisible(on)
            self._place_rules()                  # (what's beside the lines changed with the mode)
            if hasattr(self, "pulse"):
                self.pulse.setVisible(not on)      # (no sync lights on My Device)
            self._isync_head.setText(tr("settings.mode" if on else "settings.isync"))
            if not on:
                self.device.blockSignals(True)
                self.device.setCurrentIndex(SLOTS.index(self._dual_slot))
                self.device.blockSignals(False)
                self.device.painted = self.device.currentText()   # (no fade from "A Solo Device")
            if not quiet:
                self.layout().activate()
                self._fit_height(self.sizeHint().width())
        finally:
            for x in frozen:
                x.setUpdatesEnabled(True)

    def _switch_language(self, *_):
        """Preview the chosen language everywhere at once (Save keeps it, Cancel switches back)."""
        # nothing is drawn until it's all done, and the window gets its new size in ONE step (adjustSize first
        # shrank it, then _fit_height grew it back: the library's covers showed through for a frame - owner,
        # 2026-10-18)
        main = self.parent()
        frozen = [x for x in (self, main) if isinstance(x, QWidget) and x.updatesEnabled()]
        for x in frozen:
            x.setUpdatesEnabled(False)
        try:
            set_interface_language(self.lang.currentData())
            for f in self._i18n:
                f()
            if hasattr(main, "preview_language"):
                main.preview_language()
            self.layout().activate()
            self._fit_height(self.sizeHint().width())
        finally:
            for x in frozen:
                x.setUpdatesEnabled(True)

    def _preview(self, theme: str, custom: dict) -> None:
        self._previewed = True
        apply_theme(theme, custom)
        restyle_all()

    def _choose_theme(self, name: str) -> None:
        if name == "custom":
            start = theme_colors("custom", self.custom) if self.custom else theme_colors(self.theme)
            d = ThemeDialog(start, lambda c: self._preview("custom", c), self, solo=self._solo,
                            root=self.current.library_root if self.current is not None else "")
            root = self
            while root.parentWidget() is not None:
                root = root.parentWidget()
            sheet_on(root)                       # (quick live steps while it's open)
            try:
                if d.exec():
                    self.theme, self.custom = "custom", d.colors
                    self._persist(theme="custom", custom_colors=dict(self.custom))
                    self._orig_theme, self._orig_custom = self.theme, dict(self.custom)
                    if d.chosen:
                        self._take_saved_theme(d.chosen)
                # cancelled: keep whatever was chosen before (and its look, background and fonts)
                self.theme_btns[self.theme].setChecked(True)
                apply_fonts(self.fonts["en"], self.fonts["ja"])
                apply_background(self.bg_path, self.bg_opacity, *self.bg_crop)
                self._preview(self.theme, self.custom)
            finally:
                discard(d)                       # (before the sheet moves back: no restyling a closed window)
                sheet_on(None)
            return
        self.theme = name
        self._preview(name, self.custom)

    def _take_saved_theme(self, chosen: dict) -> None:
        """Keep a saved theme's background (none = no background) and fonts, picked in Custom Theme. A font
        this device doesn't have comes from the cloud's copy; if that's missing too, this device keeps its own."""
        t = chosen["theme"]
        for lang in ("en", "ja"):
            fam = t.get(f"font_{lang}")
            if isinstance(fam, str) and fam and fam not in FONTS and chosen["files"].get(fam):
                try:
                    add_user_font(chosen["files"][fam])  # (this computer's copy of the cloud's)
                except OSError:
                    pass
            self.fonts[lang] = fam if fam in FONTS else self.fonts[lang]
        path, opacity, zoom, x, y = chosen["bg"]
        self.bg_path, self.bg_opacity, self.bg_crop = path, opacity, (zoom, x, y)
        if self.current is not None:
            self.bg_path = self._store_background()      # (our own copy of the picture)
        self._persist(font_en=self.fonts["en"], font_ja=self.fonts["ja"], bg_image=self.bg_path,
                      bg_opacity=round(opacity, 2), bg_zoom=round(zoom, 4), bg_x=round(x, 4), bg_y=round(y, 4))
        self._orig_bg = (self.bg_path, self.bg_opacity, *self.bg_crop)

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
                       bg_y=new.bg_y, show_stats=new.show_stats, show_bookmarks=new.show_bookmarks)
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

    def _rule(self, lay: QVBoxLayout, key: str) -> QWidget:
        """A faint line between groups, in a block of its own whose room above and below it comes from
        SETTINGS_RULES (re-placed when the language or the mode changes: _place_rules)."""
        block = QWidget()
        inner = QVBoxLayout(block)
        inner.setSpacing(0)
        inner.addLayout(self._divider())
        lay.addWidget(block)
        self._rules.append((block, inner, key))
        self._place_rules()
        return block

    def _place_rules(self) -> None:
        mode = bool(getattr(self, "_solo", False))
        for block, inner, key in getattr(self, "_rules", []):
            table = SETTINGS_RULES[key]
            above, below = table.get((i18n.language(), mode)) or table[(i18n.language(), False)]
            inner.setContentsMargins(0, above, 0, below)

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
            discard(d)
            apply_fonts(self.fonts["en"], self.fonts["ja"])
            restyle_all()
        else:
            discard(d)

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
        self._bg_previewed = True
        d = BackgroundDialog(self.bg_path, self.bg_opacity, apply_background, self, crop=self.bg_crop, area=area)
        if d.exec():
            self.bg_path, self.bg_opacity, self.bg_crop = d.path, d.opacity, d.crop()
            if self.current is not None:
                self.bg_path = self._store_background()
                z, x, y = self.bg_crop
                self._persist(bg_image=self.bg_path, bg_opacity=round(self.bg_opacity, 2),
                              bg_zoom=round(z, 4), bg_x=round(x, 4), bg_y=round(y, 4))
                self._orig_bg = (self.bg_path, self.bg_opacity, *self.bg_crop)
        discard(d)
        apply_background(self.bg_path, self.bg_opacity, *self.bg_crop)   # cancelled: back to the earlier choice

    _previewed = False                          # a theme was previewed (Cancel puts the original back)
    _bg_previewed = False                       # a background picture was tried (the same)

    def reject(self) -> None:
        # undo any preview - and only then: re-styling the whole app for nothing took ~70 ms on every Cancel
        if self._previewed:
            self._preview(self._orig_theme, self._orig_custom)
        if self._bg_previewed:
            apply_background(*self._orig_bg)
        if hasattr(self.parent(), "set_pulse"):
            self.parent().set_pulse(self._orig_pulse)               # (the pulse preview too)
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
        d = pick_folder(self, tr("settings.pick_cloud"), self.cloud.text())
        if d:
            self.cloud.setText(d)

    def _pick_le(self):
        f = pick_file(self, tr("settings.pick_le"), self.le.text(), "LEProc.exe (LEProc.exe);;*.exe")
        if f:
            self.le.setText(str(Path(f)))

    def _pick_luna(self):
        f = pick_file(self, tr("settings.pick_luna"), self.luna.text(), "*.exe")
        if f:
            self.luna.setText(str(Path(f)))

    def _pick_anki(self):
        f = pick_file(self, tr("settings.pick_anki"), self.anki.text(), "*.exe")
        if f:
            self.anki.setText(str(Path(f)))

    @guarded
    def _save(self):
        cloud, le, lp = self.cloud.text().strip(), self.le.text().strip(), self.luna.text().strip()
        anki = self.anki.text().strip()
        solo = self._solo
        if not cloud and not solo:
            message(self, tr("settings.need_cloud"))
            return
        for path in (le, lp, anki):
            if path and not Path(path).is_file():
                message(self, tr("settings.bad_file", path=path))
                return
        if lp and luna.is_admin_build(lp):
            message(self, tr("settings.luna_admin"))
            return
        if not solo:
            try:
                Path(cloud).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                message(self, tr("settings.bad_cloud", error=e))
                return
        slot = self._dual_slot if solo else self.device.currentData()
        # safeguard: is that device number already used by another computer? Only asked when the device number or
        # the cloud folder changed: it reads (and writes) the cloud folder, and Google Drive can keep the window
        # waiting - Settings closed with a hard lag (owner, 2026-10-18). Unchanged, it's already this computer's.
        same = solo or (self.current is not None and not self.current.solo and slot == self.current.machine_name
                        and os.path.normcase(cloud) == os.path.normcase(self.current.cloud_root))
        lib = None if same else Library(cloud)
        owner = None if same else lib.slot_owner(slot)
        if owner and owner.lower() != this_computer().lower():
            n, other = SLOT_NUMBER[slot], SLOT_NUMBER[other_slot(slot)]
            choice = message(self, tr("settings.device_taken", n=n, computer=owner, other=other),
                             tr("settings.device_taken_title"),
                             buttons=[("other", tr("settings.device_use", n=other)),
                                      ("keep", tr("settings.device_taken_keep", n=n)),
                                      ("cancel", tr("settings.cancel"))])
            if choice == "cancel":
                return
            if choice == "other":
                slot = other_slot(slot)
        if not same:
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
            show_bookmarks=self.current.show_bookmarks if self.current else True,
            auto_update=self.auto_update.isChecked(),
            folder_place=self.current.folder_place if self.current else "mixed",
            last_sort=self.current.last_sort if self.current else "hours",
            anki_path=anki,
            bg_opacity=round(self.bg_opacity, 2), bg_zoom=round(self.bg_crop[0], 4),
            bg_x=round(self.bg_crop[1], 4), bg_y=round(self.bg_crop[2], 4),
            discord=self.discord.isChecked(), solo=solo,
            device_filter=self.current.device_filter if self.current else "here",
            window_pos=list(self.current.window_pos) if self.current else [])
        save_local_settings(self.result_settings)
        self.accept()


def _fetch_bytes(url: str) -> bytes:
    return vndb.fetch(url, timeout=15)          # kept-open connections + remembered for the session


def _download_cover(url: str) -> str:
    d = default_app_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    ext = os.path.splitext(url.split("?")[0])[1] or ".jpg"
    return vndb.download(url, str(d / f"vndb_{int(time.time() * 1000)}{ext}"))


class CoverPickerDialog(TaskOwner, QDialog):
    """All of a VN's covers on VNDB as thumbnails; pick one."""

    def __init__(self, title: str, options: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("vndb.pick_cover"))
        self.setMinimumSize(640, 560)
        self.choice = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
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
        row.setSpacing(BUTTON_GAP)
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
        lay.setContentsMargins(22, 18, 22, 18)        # (as every other window)
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
        row.setSpacing(BUTTON_GAP)
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
            QTimer.singleShot(0, self, self.search)

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


class GameDialog(KeepsTitleBar, FitsScreen, TaskOwner, QDialog):
    """Add or edit a game: title, cover, launch options, reading status, and paths for This Device
    (with Browse) and the Other Device (greyed, text only)."""

    def _align_with(self) -> Optional[QWidget]:
        """Opens centred over the program's window, like Settings (owner, 2026-10-18)."""
        return self.parentWidget().window() if self.parentWidget() else None

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
        self.removed = False                    # (removed: what Undo needs)
        self.removed_how = "both"               # "here" / "both"
        self.saved_id: Optional[str] = None
        self.title_locked = game.title_locked if game else False
        self.manual_title = game.manual_title if game else ""      # restored when Automatic is turned off
        self._suggestion: Optional[tuple] = None

        # the contents scroll when the screen is too short for them; the buttons stay fixed below
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._content = QWidget(objectName="settings_content")
        outer = QHBoxLayout(self._content)
        outer.setContentsMargins(22, 18, 22, 4)
        outer.setSpacing(20)
        scroll = QScrollArea(objectName="settings_scroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self._content)
        root.addWidget(scroll, 1)
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
        self.status = ThemedCombo()
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
        solo = settings.solo                  # (a solo device: the other device's fields + registry hidden, kept)
        for slot in (self.machine, other_slot(self.machine)):
            here = slot == self.machine
            head = section(tr("game.this_device") if here else tr("game.other_device"))
            lay.addWidget(head)
            if not here and solo:
                head.hide()                   # (after it's placed: shown loose, it'd open as its own window)
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
                self.save_hint = hint("")               # "Suggested: ..." under the save folder
                self.save_hint.hide()
                g.addWidget(self.save_hint, 2, 1)
                save_e.textEdited.connect(lambda _t: self.save_hint.hide())
                exe_e.editingFinished.connect(self._suggest_save)
            else:
                for e in (exe_e, save_e):
                    e.setProperty("secondary", True)
                    e.setToolTip(tr("game.other_tip"))
                if solo:
                    for i in range(g.count()):
                        g.itemAt(i).widget().hide()
            lay.addLayout(g)
            self.fields[slot] = (exe_e, save_e)

        reg_head = section(tr("game.registry"))
        lay.addWidget(reg_head)
        self.registry = QLineEdit(game.registry_key if game else "")
        self.registry.setPlaceholderText(r"HKEY_CURRENT_USER\Software\...")
        lay.addWidget(self.registry)
        reg_hint = hint(tr("game.registry_hint"))
        lay.addWidget(reg_hint)
        for w in (reg_head, self.registry, reg_hint):
            w.setVisible(not solo)            # (registry saves only matter for syncing)
        lay.addStretch()

        row = QHBoxLayout()
        row.setSpacing(BUTTON_GAP)
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
        # under the right-hand column, as before (the cover column's width + the gaps on its left)
        row.setContentsMargins(22 + left.sizeHint().width() + 20, 8, 22, 18)
        root.addLayout(row)

        # adding with "fetch covers from VNDB" on: look it up straight away
        if not game and settings.vndb and self.title.text().strip():
            self._auto_vndb(self.title.text().strip())
        self._suggest_save()

    # -- save folder
    def _suggest_save(self):
        """Save folder still empty: look for where this game saves (the other device's location, or an
        existing save folder) and fill it in as a suggestion; the owner checks it and saves."""
        exe_e, save_e = self.fields[self.machine]
        exe = exe_e.text().strip()
        if save_e.text().strip() or not exe or self.settings.solo:
            return
        o = self.game.paths_for(other_slot(self.machine)) if self.game else None
        other = (o.exe, o.save_dir) if o and o.save_dir else None
        title = self.title.text().strip()

        def done(s):
            if s is None or save_e.text().strip() or exe_e.text().strip() != exe:
                return                              # nothing found, or the owner changed things meanwhile
            save_e.setText(s.path)
            self.save_hint.setText(tr("game.saves_from_other" if s.reason == "other_device" else "game.saves_found"))
            self.save_hint.show()
        self._run(lambda: suggest_save(self.settings, exe, title, other), done)

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
        f = pick_file(self, tr("file.pick_cover"), "", tr("file.images"))
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
        f = pick_file(self, tr("file.pick_exe"), edit.text(), tr("file.programs"))
        if f:
            exe = lnk.resolve_game_exe(f) or f
            edit.setText(str(Path(exe)))
            if not self.title.text().strip() or not self.title_locked:
                self.title.setText(guess_title(exe))
            if edit is self.fields[self.machine][0]:
                self._suggest_save()

    def _pick_save(self, edit: QLineEdit):
        exe = self.fields[self.machine][0].text().strip()
        d = pick_folder(self, tr("file.pick_saves"), edit.text(), str(Path(exe).parent) if exe else "")
        if d:
            edit.setText(d)

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
        how = confirm_remove(self, self.game, self.machine, solo=self.settings.solo)
        if how:
            self.removed = remove_game_from(self.library, self.game.game_id, self.machine, how)   # (Undo needs it)
            self.removed_how = how
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
                                        tr("error.use_anyway"), tr("game.cancel"),
                                        title=tr("error.save_shared_title")):
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

    def __init__(self, title: str, name: str, parent=None, label: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.addWidget(section(label or tr("folder.name")))
        self.edit = QLineEdit(name)
        self.edit.selectAll()
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        row.setSpacing(BUTTON_GAP)
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
    def __init__(self, title: str, info: ConflictInfo, parent=None, other_set_up: bool = True, first_sync: bool = False,
                 setup: Optional[dict] = None, machine: str = ""):
        """other_set_up: the game is set up on the other device. If it isn't, the cloud copy was uploaded from this
        device earlier (e.g. after this device's sync record was lost), and the window says so.
        first_sync: this device has never synced the game: it already had files of its own (the game may have made
        them when installed or first opened); the cloud has the other device's saves.
        setup: Game.setup_saves - a side still holding exactly the saves the game made in the session that found its
        save folder says so instead of "(Newer)" (the game wrote them by starting: newest, maybe no progress - owner)."""
        super().__init__(parent)
        self.setWindowTitle(tr("conflict.title"))
        self.setMinimumWidth(480)
        self.choice: Optional[str] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        body = QLabel(tr("conflict.body_solo" if not other_set_up else "conflict.body_first" if first_sync
                         else "conflict.body", title=html.escape(title)))
        body.setWordWrap(True)
        lay.addWidget(body)
        notes = {key: next((("conflict.setup_here" if m == machine else "conflict.setup_other")
                            for m, s in (setup or {}).items() if fp and s == fp), None)
                 for key, fp in (("local", info.local_fp), ("cloud", info.cloud_fp))}
        newer = "local" if (info.local_mtime or 0) >= (info.cloud_mtime or 0) else "cloud"
        if notes[newer]:
            newer = None                     # (its date is when the game was opened: not worth calling newer)
        default = newer or next((k for k in ("local", "cloud") if not notes[k]), None)
        for key, label, t in (("local", tr("conflict.local"), info.local_mtime),
                              ("cloud", tr("conflict.cloud" if other_set_up else "conflict.cloud_solo"), info.cloud_mtime)):
            when = i18n.datetime_str(t) if t else tr("conflict.none")
            b = QPushButton(f"{label}\n{tr('conflict.last_saved', date=when)}" +
                            (f"   {tr('conflict.newer')}" if key == newer else "") +
                            (f"\n{tr(notes[key])}" if notes[key] else ""))
            b.setStyleSheet("text-align: left; padding: 10px 14px;")
            if key == default:
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


def _newest(folder: Path) -> Optional[float]:
    """When anything in this folder last changed (None: nothing in it)."""
    best = None
    try:
        for f in folder.rglob("*"):
            if f.is_file():
                t = f.stat().st_mtime
                best = t if best is None or t > best else best
    except OSError:
        pass
    return best


class SaveChoicesDialog(QDialog):
    """Back to Dual (owner's plan): every game whose saves changed both here and in the cloud while this computer was
    on its own, in ONE window - pick a side for each (the newer one picked at first), or Keep Newest for All. The
    copy not kept is backed up as usual. Later: nothing changes now (the games wait as "saves changed on both")."""

    def __init__(self, rows: list[tuple[str, str, Optional[float], Optional[float]]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dual.saves_title"))
        self.setMinimumWidth(560)
        self.choices: dict[str, str] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        body = QLabel(tr("dual.saves_body"))
        body.setWordWrap(True)
        lay.addWidget(body)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self._groups: dict[str, tuple] = {}
        for r, (gid, title, local_t, cloud_t) in enumerate(rows):
            name = QLabel(title)
            name.setTextFormat(Qt.PlainText)
            grid.addWidget(name, r, 0)
            group = QButtonGroup(self)
            newer = "local" if (local_t or 0) >= (cloud_t or 0) else "cloud"
            for c, (key, label, t) in enumerate((("local", tr("dual.this_computer"), local_t),
                                                 ("cloud", tr("dual.cloud"), cloud_t))):
                when = i18n.datetime_str(t) if t else tr("conflict.none")
                b = QPushButton(f"{label}\n{when}", objectName="seg")
                # never squeezed below its text + the seg padding (22 px each side): the dates were cut off (JA)
                fm = QFontMetrics(ui_font())
                b.setMinimumWidth(max(fm.horizontalAdvance(t) for t in (label, when)) + 2 * 22 + 4)
                b.setCheckable(True)
                b.setChecked(key == newer)
                group.addButton(b)
                b.setProperty("side", key)
                grid.addWidget(b, r, c + 1)
            self._groups[gid] = (group, newer)
        grid.setColumnStretch(0, 1)
        lay.addLayout(grid)
        lay.addWidget(hint(tr("conflict.backup_note")))
        row = QHBoxLayout()
        row.setSpacing(BUTTON_GAP)
        later = QPushButton(tr("dual.later"))
        later.clicked.connect(self.reject)
        newest = QPushButton(tr("dual.newest_all"))
        newest.clicked.connect(lambda: self._done(True))
        keep = QPushButton(tr("dual.keep_these"))
        keep.clicked.connect(lambda: self._done(False))
        row.addWidget(later)
        row.addStretch()
        row.addWidget(newest)
        row.addWidget(keep)
        lay.addLayout(row)

    def _done(self, newest: bool) -> None:
        for gid, (group, newer) in self._groups.items():
            b = group.checkedButton()
            self.choices[gid] = newer if newest or b is None else b.property("side")
        self.accept()


class DetectDialog(QDialog):
    """After a first play without a save folder: confirm where the game saves."""

    def __init__(self, game: Game, candidates: list, app_dir: Path, parent=None, cloud_root: str = "",
                 game_dir: str = ""):
        super().__init__(parent)
        self.cloud_root, self.game_dir = cloud_root, game_dir
        self.setWindowTitle(tr("detect.title"))
        self.setMinimumWidth(620)
        self.game = game
        self.save_dir: Optional[str] = None
        self.registry_key = ""
        from sync_engine import safe_game_id
        self._registry_only_dir = str(app_dir / "registry-only" / safe_game_id(game.game_id))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        folders = [c for c in candidates if c.kind == "folder"]
        regs = [c for c in candidates if c.kind == "registry"]
        sure = [c for c in regs if c.sure]
        maybe = [c for c in regs if not c.sure][:3]
        self._sure_key = sure[0].path if sure else ""      # plainly this game's: stored without asking
        n_options = len(folders) if folders else (1 if regs else 0)
        key = "detect.none_body" if not candidates else ("detect.body_one" if n_options == 1 else "detect.body_many")
        body = QLabel(tr(key, title=html.escape(game.title)))
        body.setWordWrap(True)
        lay.addWidget(body)
        if regs:                                           # two sections: never a choice between the two
            lay.addSpacing(4)
            lay.addWidget(section(tr("detect.section_saves")))
        self.group = QButtonGroup(self)
        sure_folders = [c for c in folders if c.sure]
        for c in folders:
            rb = QRadioButton(f"{c.path}\n{tr('detect.files', n=c.files)}")
            rb.setProperty("path", c.path)
            rb.setChecked(len(sure_folders) == 1 and c is sure_folders[0])   # a guess is never ticked for you
            self.group.addButton(rb)
            lay.addWidget(rb)
        if regs and not folders:
            rb = QRadioButton(tr("detect.registry_only"))
            rb.setProperty("path", self._registry_only_dir)
            rb.setChecked(True)
            self.group.addButton(rb)
            lay.addWidget(rb)
        self.reg_boxes: list[QCheckBox] = []
        if regs:
            lay.addSpacing(6)
            lay.addWidget(section(tr("detect.section_registry")))
            if self._sure_key:
                lay.addWidget(hint(tr("detect.registry_auto", key=self._sure_key)))
            for c in maybe:                                 # not sure: offered, unticked
                b = QCheckBox(c.path)                           # (under the Registry heading)
                b.setToolTip(tr("detect.registry_tip"))
                b.setProperty("key", c.path)
                if self._sure_key:
                    b.hide()                                # one registry key per game: the sure one is used
                self.reg_boxes.append(b)
                lay.addWidget(b)
            if not self._sure_key and not folders and self.reg_boxes:
                self.reg_boxes[0].setChecked(True)          # nothing else changed: it's the only lead
            if not self._sure_key and len(maybe) > 1:       # one key per game: ticking one unticks the others
                for b in self.reg_boxes:
                    b.toggled.connect(lambda on, me=b: on and [o.setChecked(False) for o in self.reg_boxes if o is not me])
        row = QHBoxLayout()                                 # "cancel" far left, the actions on the right
        row.setSpacing(BUTTON_GAP)
        later = QPushButton(tr("detect.later"))
        later.clicked.connect(self.reject)
        row.addWidget(later)
        row.addStretch()
        other = QPushButton(tr("detect.browse"))
        other.clicked.connect(self._browse)
        row.addWidget(other)
        if candidates:
            use = QPushButton(tr("detect.use"))
            use.setDefault(True)
            use.clicked.connect(self._use)
            use.setEnabled(self.group.checkedButton() is not None)     # nothing ticked: pick one first
            self.group.buttonToggled.connect(lambda *_: use.setEnabled(self.group.checkedButton() is not None))
            row.addWidget(use)
        lay.addSpacing(8)
        lay.addLayout(row)

    def _registry_choice(self) -> str:
        if self._sure_key:
            return self._sure_key
        return next((b.property("key") for b in self.reg_boxes if b.isChecked()), "")

    def _ok(self, path: str) -> bool:
        problem = save_folder_problem(path, self.cloud_root)
        if problem:
            message(self, tr(problem))
            return False
        return True

    def _browse(self):
        d = pick_folder(self, tr("file.pick_saves"), "", self.game_dir)
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
        lay.setContentsMargins(22, 18, 22, 18)
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
        row.setSpacing(BUTTON_GAP)
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
                          tr("restore.restore"), tr("game.cancel"), title=tr("restore.title")):
            return
        try:
            self.engine.restore(self.game.game_id, b.name, self.save_dir, self.game.registry_key)
        except (OSError, ValueError) as e:
            report_failure(self, e if isinstance(e, ValueError) and str(e) in i18n.S else OSError())
            return
        self.restored = True
        self.accept()


# =========================================================================== main window
class MainWindow(KeepsTitleBar, TaskOwner, QMainWindow):
    START_ROWS = 2                          # the window opens just tall enough for this many rows, evenly spaced

    def __init__(self, settings: LocalSettings):
        super().__init__()
        self.setWindowIcon(app_icon())
        self.resize(1200, 800)                  # (the height is fitted to the rows when it first opens)
        self._start_fitted = False
        self.setMinimumHeight(520)          # room for the bars and at least one row of covers
        self.setAcceptDrops(True)
        self.covers = CoverCache()
        # covers are prepared in the background once things are quiet (not while the window opens, or between
        # keystrokes of a search): a burst of decoding then would slow what you're waiting for
        self._prefetch_wait = QTimer(self)
        self._prefetch_wait.setSingleShot(True)
        self._prefetch_wait.setInterval(PREFETCH_QUIET_MS)
        self._prefetch_wait.timeout.connect(lambda: self._prefetch_covers(self._prefetch_entries))
        self._prefetch_entries: list = []
        self._art: dict = {}                          # (game id, cover file name) -> its path, see art()
        self.games: dict[str, Game] = {}
        self.sync_states: dict[str, str] = {}
        self.hover: dict[str, float] = {}       # game_id -> 0..1 hover progress
        self.hovered: Optional[str] = None
        self.worker: Optional[PlayWorker] = None
        self.checker: Optional[StatusChecker] = None
        self.syncer: Optional[SyncWorker] = None      # Sync Now / Choose Now running
        self._conflicts_snoozed: set = set()  # "both changed" games not to ask about until the other device changes something
        self._ask_now: set = set()            # save folder just set here: a save mismatch goes straight to "which to keep"
        self._fill_busy = False               # _fill_save_folders running
        self._count_up: Optional[tuple] = None   # (game id, total before the session): the bar counts up once
        self._waiting: Optional[tuple] = None    # (game id, since, after a session?): a sync not shown yet
        self._phase_t0 = 0.0                     # when play_phase last changed
        self._recheck = False
        self.playing_id: Optional[str] = None
        self._quiet_polls = 0                # live polls since the last full save check
        self._quiet_check = False            # the running check is a routine background one
        self.stop_pending = False            # Stop was pressed; waiting for the game to close
        self.looking_for_saves = False       # Read pressed: checking for a likely save folder first
        self.play_phase = "done"
        self.current_folder: Optional[str] = None     # folder id being viewed, None = the library
        self.folder_names: dict[str, str] = {}
        self.folder_meta: dict[str, dict] = {}         # folder id -> {"sort", "order"} (each folder's own sort)
        self.library_order: list[str] = []            # the owner's own order (sort: Custom)
        self.folder_games: dict[str, list[Game]] = {}
        self._sort_mode: Optional[str] = "?"          # which choices the sort drop-down shows (a folder or the library)
        self._press: Optional[tuple] = None           # (position, game id) for starting a drag
        self._ghost: Optional[DragGhost] = None       # the cover following the mouse while a game is dragged
        self._dragging: Optional[str] = None
        self._landing: Optional[DragGhost] = None     # a dropped cover gliding into place
        self.slides: dict[str, tuple] = {}            # item id -> (dx, dy): sliding from its old place
        self.appearing: dict[str, float] = {}         # item id -> delay: fading in (search / filter let them in)
        self._appear_t0 = 0.0
        self.arrive_wait = ARRIVE_WAIT                # (when the latest arrivals start: _fade_top_words)
        self.arrive_span = 0.0                        # (and how far apart the first and last start)
        self._cover_wanted: set = set()               # tiles waiting for their covers to load (search arrivals)
        self._cover_loading = False
        self._cover_given_up: set = set()
        self._names_busy = False                      # fetching other names from VNDB (see _fetch_names)
        self._names_tried: set = set()                # vndb ids that failed this session (tried again next start)
        self._view_save = QTimer(self, singleShot=True, interval=VIEW_SAVE_DELAY_MS)   # see _save_view_choice
        self._view_save.timeout.connect(self._save_view_choice_now)
        self.no_results = 0.0                         # "No Results Found" shown 0..1 (it fades)
        self.no_results_key = "grid.no_results"       # ...or "grid.empty" (no games yet): kept while it fades out
        self.bookmark_alpha = 1.0 if settings.show_bookmarks else 0.0   # bookmarks shown 0..1 (they fade)
        self._leaving: list = []                       # [(snapshot, rects, start)]: covers fading out (each change
                                                       # adds one; earlier ones finish their fade, never cut short)
        self._slide_t0 = 0.0
        self._dim_gid: Optional[str] = None           # the picked-up game, faded in its place
        self.drag_dim = 0.0                           # 0..1 how far it has faded
        self.rumble = 0.0                             # how far the covers rock, in degrees (easter egg)
        self.rumble_t = 0.0                           # the rocking's clock (one per frame for all covers)

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
        # the grid rearranges once typing pauses (holding Backspace / fast typing: one calm change at the end)
        self._search_pause = QTimer(self, singleShot=True, interval=SEARCH_PAUSE_MS)
        self._search_pause.timeout.connect(self._apply_filter)
        self.search.textChanged.connect(self._search_pause.start)
        tl.addWidget(self.search)
        self.sort = ThemedCombo()
        self.sort.activated.connect(self._sort_picked)
        tl.addWidget(self.sort)
        self.filter = ThemedCombo()
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
        # as wide as its longest word (Synced / Syncing / Error), so a change of word never moves the bar
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
        fb.setContentsMargins(14, 10, 16, 12)                    # name centred (17 px each side) between the top bar and the covers' edge
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
        self._fit_grid()
        self.grid.setItemDelegate(CardDelegate(self))
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._context_menu)
        self.grid.itemDoubleClicked.connect(lambda _: self.activate_selected())
        self.grid.currentItemChanged.connect(lambda *_: self._refilling or self._update_details())
        self.grid.itemSelectionChanged.connect(lambda: self._refilling or self._update_details())
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
        # right 12: the trash can's box has room round the icon for its wiggle; the icon itself then sits as
        # far from the right edge as the Read button from the left (25 px)
        dl.setContentsMargins(22, 23, 12, 3)
        dl.setSpacing(12)
        self.play_btn = PulseButton(objectName="play")
        self.play_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.play_btn.clicked.connect(self._play_button)
        # its label (Read / Stop / Open) grows or shrinks the visible button on both sides around a fixed centre
        def keep_centre(b=self.play_btn, lay=dl) -> None:
            # one size, fitting the longest label; its visible left edge stays where it always was (25 px)
            b.slot_labels = (tr("bar.play"), tr("bar.stop"), tr("bar.open"))
            left = 25 - b.MARGINS[0]
            m = lay.contentsMargins()
            if m.left() != left:
                lay.setContentsMargins(left, m.top(), m.right(), m.bottom())
            b.updateGeometry()
        self.play_btn.on_width = keep_centre
        dl.addWidget(self.play_btn)
        dl.addSpacing(10)
        info = QVBoxLayout()
        info.setSpacing(9)                      # (room for the rename box between the name and the line below)
        # the name + stats block sits 2 px higher than the rest (top margin 23, not 25) and keeps 3 px below it, so
        # everything else stays put (measured, to the pixel): the block is then centred on the dark band (it sat
        # 1.5-3 px low)
        info.setContentsMargins(0, 0, 0, 3)
        self.d_title = ElidedLabel(objectName="title")
        self.d_title.setTextFormat(Qt.PlainText)
        self.d_title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.d_title.clicked.connect(self._start_rename)
        self.d_title.setMouseTracking(True)        # (text cursor only over the name itself)
        info.addWidget(self.d_title)
        # click the name to rename the game / folder in place: Enter or clicking elsewhere saves, Esc cancels
        # (it floats over the name, not in the layout: the bar keeps its height and the text doesn't move)
        self.d_title_edit = QLineEdit(self.details, objectName="title_edit")
        self.d_title_edit.hide()
        self.d_title_edit.editingFinished.connect(lambda: self._finish_rename(True))
        self.d_title_edit.textChanged.connect(lambda _t: self._place_title_edit())
        self.d_title_edit.installEventFilter(self)
        self._renaming: Optional[tuple] = None      # ("game" | "folder", id) while the name is being edited
        self._click_away = ClickAway(self.d_title_edit, lambda: self._finish_rename(True))
        line = QHBoxLayout()                   # sync status, then time read + last read
        line.setSpacing(0)
        self.d_sync = SyncStatus()             # (a long message may still be cut on a very narrow window)
        line.addWidget(self.d_sync)
        self.d_stats = StatsLine()
        sp = self.d_stats.sizePolicy()
        sp.setRetainSizeWhenHidden(True)       # (hidden, it still holds the line's height: the bar never shrinks -
        self.d_stats.setSizePolicy(sp)         # on My Device nothing else is on that line)
        self.d_stats.hide()
        line.addWidget(self.d_stats, 1)
        line.addStretch()                      # (stats hidden: the line still takes the spare width, not the buttons)
        info.addLayout(line)
        dl.addLayout(info, 1)
        self.d_status = ThemedCombo()
        self.d_status.currentIndexChanged.connect(self._status_changed)
        dl.addWidget(self.d_status)
        self.saves_btn = QPushButton()
        self.saves_btn.clicked.connect(self._open_saves)
        self.edit_btn = QPushButton()
        self.edit_btn.clicked.connect(lambda: self.edit_game(self.bar_game()) if self._bar_shows_running()
                                      else self.edit_selected())
        self.bookmark_btn = BookmarkButton()
        self.bookmark_btn.clicked.connect(self._toggle_bookmarks)
        self.trash_btn = TrashButton()
        self.trash_btn.clicked.connect(lambda: self.remove_game(self.selected_id() or ""))
        self.trash_btn.installEventFilter(self)       # games can be dropped on it
        dl.addWidget(self.saves_btn)
        # Edit | bookmark | trash: the icons' boxes carry invisible room round them (for their motion), so the
        # spacing is set by what you see: the bookmark icon sits exactly midway, 36 px of clear space each side
        # (measured: 18 + the bookmark's own 18 / 2 + the boxes' 18 + 16), the trash can unmoved from the edge
        tail = QHBoxLayout()
        tail.setSpacing(0)
        tail.addWidget(self.edit_btn)
        tail.addSpacing(18)
        tail.addWidget(self.bookmark_btn)
        tail.addSpacing(2)
        tail.addWidget(self.trash_btn)
        dl.addLayout(tail)
        root.addWidget(self.details)
        self._fader = BarFader(self.details)       # the bar's one cross-fade (on top of everything in it)
        # create the status line now (Qt makes it on the first message otherwise): the band then
        # always has the same height, so the bar's contents stay centred and nothing jumps
        self.setStatusBar(FadingStatusBar(self))   # (messages fade in and out)
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
        self.anim = VsyncClock(self)   # (once per screen refresh, at any rate)
        self.anim.setInterval(16)
        self.anim.setTimerType(Qt.PreciseTimer)        # (a normal timer ticks unevenly on Windows: 16 / 31 ms)
        self.anim.timeout.connect(self._tick)
        self._last_tick = time.monotonic()

        # live refresh: notice what the other device changes in the cloud folder (checked in the background)
        self._live_sig = None
        self._live_checking = False
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(4000)
        self._live_timer.timeout.connect(self._live_poll)

        # Discord presence (Settings checkbox, per device): what this window is doing, checked every 2 s
        self.presence = presence.Presence()
        self.anki_from_here = False                   # (Anki started / brought forward by the easter egg)
        self._presence_since = time.time()            # the elapsed timer (restarts when reading starts / ends)
        self._presence_reading = False
        self._last_active = time.monotonic()          # last time the owner was using Ikemen Reader (or its Anki)
        self._presence_timer = QTimer(self)
        self._presence_timer.setInterval(PRESENCE_CHECK_MS)
        self._presence_timer.timeout.connect(self._update_presence)
        self._presence_timer.start()
        self.apply_settings(settings)
        self._run(warm_up, lambda _: None)          # makes the first Play react instantly
        self._live_timer.start()

        # new versions: looked for a few seconds after opening (only the packaged program updates itself;
        # IKEMEN_UPDATE_CHECK=1 shows the question when running from source, for testing)
        self._release: Optional[updater.Release] = None
        self._update_ready: Optional[Path] = None    # unpacked and waiting: installed when Ikemen Reader closes
        self._update_asked = False
        before = updater.note_version(self.app_dir, APP_VERSION)
        if before:
            QTimer.singleShot(1500, lambda: self.statusBar().showMessage(tr("update.done", version=APP_VERSION), 10000))
        if updater.FROZEN or os.environ.get("IKEMEN_UPDATE_CHECK") == "1":
            QTimer.singleShot(4000, self._check_for_update)

    def restyle(self) -> None:
        """Redraw everything that isn't covered by the style sheet after a theme change."""
        if not hasattr(self, "anim"):
            return
        self.logo.refresh()
        self.add_btn.refresh_icon()
        self.settings_btn.refresh_icon()
        self.trash_btn.refresh_icon()
        self.bookmark_btn.refresh_icon()
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
        if not Path(self.settings.library_root).is_dir():
            self.empty.setText(tr("grid.no_cloud", path=self.settings.cloud_root))
        self.grid.viewport().update()
        self._styled_fonts = ui_families()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("app.name"))
        self.search.setPlaceholderText(tr("top.search"))
        self.add_btn.setText(tr("top.add"))
        self.settings_btn.setText(tr("top.settings"))
        self.play_btn.setText(tr("bar.play"))
        self.saves_btn.setText(tr(self._folder_key()))
        self.edit_btn.setText(tr("bar.edit"))
        self.back_btn.setText(tr("folder.back"))
        self.back_btn.setToolTip(tr("folder.back_tip"))
        self.trash_btn.setToolTip(tr("bar.trash_tip"))
        self.bookmark_btn.set_on(self.settings.show_bookmarks)
        self.bookmark_btn.setToolTip(tr("bar.bookmarks_hide" if self.settings.show_bookmarks else "bar.bookmarks_show"))
        self.stats_btn.setText(tr("top.hide_stats" if self.settings.show_stats else "top.show_stats"))
        self.stats_btn.setToolTip(tr("top.stats_tip"))
        fm = self.sync_label.fontMetrics()
        self.sync_label.setFixedWidth(max(fm.horizontalAdvance(tr(k)) for k in ("top.synced", "top.syncing", "top.attention_one")) + 2)
        self._fill_sort()
        self._fill_filter()
        for combo, items, current in (
                (self.sort, None, None),
                (self.filter, None, None),
                (self.d_status, [(tr(f"status.{s or 'none'}"), s) for s in STATUSES], None)):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)   # resize when the language changes
            if items is not None:
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
                texts = [combo.itemText(i) for i in range(combo.count())]
                if combo is self.sort:                          # wide enough in and out of folders
                    texts = [c[0] for c in self._sort_choices(True) + self._sort_choices(False) if c]
                else:                                           # (and in both modes)
                    texts = [c[0] for c in self._filter_choices(False) if c]
                longest = max(fm.horizontalAdvance(t) for t in texts)
                combo.setFixedWidth(longest + chrome + 6)       # 6 px to spare
            else:
                # Qt keeps an old minimum otherwise; +12 because its estimate is a few px short with our arrow
                combo.setFixedWidth(combo.sizeHint().width() + 12)
        for b in (self.add_btn, self.settings_btn, self.saves_btn, self.edit_btn, self.play_btn, self.stats_btn):
            b.updateGeometry()

    # ------------------------------------------------------------------ data
    def apply_settings(self, settings: LocalSettings) -> None:
        before = getattr(self, "settings", None)
        if before is not None and before.pulse != settings.pulse:
            pulse_switched(settings.pulse)               # (e.g. Ikemen Sync: settle / start from rest, as by hand)
        self.settings = settings
        self.bookmark_alpha = 1.0 if settings.show_bookmarks else 0.0   # (a new setting shows at once)
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
        if before is not None and before.machine_name == settings.machine_name and before.solo == settings.solo \
                and os.path.normcase(before.cloud_root) == os.path.normcase(settings.cloud_root) \
                and getattr(self, "library", None) is not None:
            # the same cloud folder and device: nothing to set up again, and the library is re-read in the background
            # (on the window's own thread, Google Drive kept Settings' Save waiting - owner, 2026-10-18)
            self.retranslate()
            self.refresh_soon(keep_files=True)
            self._update_details()
            self.grid.viewport().update()
            self._update_anim()
            self.share_settings()
            return
        self.app_dir = default_app_dir()
        if settings.solo:
            # A Solo Device: its own copy of the library, and no syncing at all - the sync records stay exactly as
            # they were, so going back to Dual compares saves correctly (solo progress counts as this device's)
            if (before is not None and not before.solo) or not solo_dir(self.app_dir).is_dir():
                try:
                    start_solo_library(settings.cloud_root, self.app_dir)
                except OSError as e:
                    report_failure(self, e)
            self.library = Library(settings.library_root)
            self.engine = None
        else:
            back_from_solo = before is not None and before.solo
            if back_from_solo:
                self._merge_from_solo(settings)
            self.library = Library(settings.cloud_root)
            self.engine = SyncEngine(settings.cloud_root, self.app_dir, computers=self.library.known_computers)
            try:
                owner = self.library.slot_owner(settings.machine_name)
                if owner is None or owner.lower() == this_computer().lower():
                    self.library.claim_slot(settings.machine_name)
            except OSError:
                pass
            if back_from_solo:
                self._solo_saves_asked = False      # (asked with the first background check's results)
        self.retranslate()
        self._apply_mode()
        self.refresh()
        self._update_anim()
        self.share_settings()

    _solo_saves_asked = True                     # (False just after going back to Dual: _ask_solo_saves)

    def _merge_from_solo(self, settings: LocalSettings) -> None:
        """Back to Dual: this computer's solo library goes into the shared one (library.merge_solo). Games added on
        both devices meanwhile: Ask for Each | Join All (owner's plan)."""
        machine = settings.machine_name
        try:
            backup_solo_library(self.app_dir)
            pairs = solo_joins(settings.cloud_root, machine, self.app_dir)
        except OSError as e:
            report_failure(self, e)
            return
        joins = {}
        if pairs:
            listing = "\n".join(f"• {mine.title}" for mine, _ in pairs)
            how = message(self, tr("dual.joins_body", titles=listing), tr("dual.joins_title"),
                          [("each", tr("dual.ask_each")), ("all", tr("dual.join_all"))], dismiss="each")
            for mine, theirs in pairs:
                if how == "all" or message(self, tr("dual.join_one", mine=mine.title, theirs=theirs.title),
                                           tr("dual.joins_title"),
                                           [("no", tr("dual.keep_separate")), ("join", tr("dual.join"))],
                                           dismiss="no") == "join":
                    joins[mine.game_id] = theirs.game_id
        try:
            merge_solo(settings.cloud_root, machine, joins, self.app_dir)
        except OSError as e:
            report_failure(self, e)
            return
        self.statusBar().showMessage(i18n.line("status.dual_merged"), 8000)

    def _ask_solo_saves(self) -> None:
        """Right after going back to Dual: saves changed here AND in the cloud while solo - asked at once, in one
        window (owner's plan). The games come from the first background check (the window never scans saves itself:
        that froze the fade for seconds - owner, 2026-10-18); only their dates are read, in the background too."""
        if self._solo_saves_asked or self.engine is None:
            return
        self._solo_saves_asked = True
        machine, engine = self.settings.machine_name, self.engine
        games = [g for gid, st in self.sync_states.items() if st == "conflict" and (g := self.games.get(gid)) is not None]
        if not games:
            return
        self._conflicts_snoozed |= {g.game_id for g in games}   # (this window asks: not Choose Now / Later too)

        def dates():
            return [(g.game_id, g.title, _newest(Path(g.paths_for(machine).save_dir)), _newest(engine.cloud_dir(g.game_id)))
                    for g in games]

        def ask(rows: list) -> None:
            if engine is not self.engine or self.syncer is not None or self.worker is not None:
                return
            d = SaveChoicesDialog(rows, self)
            if not d.exec():
                return
            answers = dict(d.choices)
            s = SyncWorker(self.engine, games, machine)
            s.conflict.connect(lambda info: setattr(s, "answer", answers.get(info.game_id)), Qt.BlockingQueuedConnection)
            s.finished_all.connect(self._synced_now)
            self.syncer = s
            self.statusBar().showMessage(tr("status.checking"))
            s.start()
        self._run(dates, ask)

    _mode_anim: Optional[dict] = None          # switching Dual <-> My Device on screen (see _apply_mode)
    _check_after_mode = False                  # a save check waiting for that to finish
    _mode_shown: Optional[bool] = None         # the mode the window shows (solo True / False)

    def _apply_mode(self) -> None:
        """Dual / solo: on a solo device everything about syncing is hidden (owner's plan) - the top bar's
        Synced / Syncing / Error, every sync light, the bottom bar's light + words (the time and date start where the
        light was). Switched on screen, it's animated (owner, 2026-10-18), at the bar's pace: to My Device everything
        about syncing fades out with the time + date, then the time + date fade in where the light was; back to Dual
        the time + date fade out, then everything fades in together."""
        solo = self.settings.solo
        self.d_stats.solo = solo
        self._fill_filter()                              # (the device group only with two devices)
        if self._mode_shown is None or not self.isVisible() or self._mode_shown == solo:
            self._mode_anim = None
            self._mode_final(solo)
            return
        if self._mode_anim is not None:
            self._mode_final(self._mode_anim["solo"])        # (switched again mid-way: start from the end of it)
        self._mode_anim = {"solo": solo, "t0": time.monotonic(), "phase": 0}
        self._mode_effects = {}
        fading = [self.d_stats] + ([self.sync_label, self.d_sync] if solo else [])
        for w in fading:
            fx = QGraphicsOpacityEffect(w)
            fx.setOpacity(1.0)
            w.setGraphicsEffect(fx)
            self._mode_effects[w] = fx
        if not hasattr(self, "_mode_clock"):
            self._mode_clock = VsyncClock(self)
            self._mode_clock.setInterval(0)
            self._mode_clock.timeout.connect(self._mode_step)
        self._mode_clock.start()

    def _mode_final(self, solo: bool) -> None:
        """The mode's look, with nothing fading."""
        LIGHTS_ALPHA[0] = 0.0 if solo else 1.0
        for w in (self.sync_label, self.d_sync, self.d_stats):
            w.setGraphicsEffect(None)
        self.sync_light.setVisible(not solo)
        self.sync_label.setVisible(not solo)
        self.d_sync.setVisible(not solo)
        self.d_stats.LEAD = 0 if solo else StatsLine.LEAD
        self.d_stats.setMinimumHeight(self.d_sync.sizeHint().height())   # (the bar keeps its height: no 3rd row)
        self.d_stats.updateGeometry()
        self.d_stats.update()
        self._mode_shown = solo
        self._update_details()
        self.grid.viewport().update()
        self.sync_light.update()

    def _mode_step(self) -> None:
        a = self._mode_anim
        if a is None:
            self._mode_clock.stop()
            return
        t = time.monotonic() - a["t0"]
        solo = a["solo"]
        if a["phase"] == 0:
            k = 1.0 - ease(min(1.0, t / BAR_OUT_S))           # fading out
            for fx in self._mode_effects.values():
                fx.setOpacity(k)
            if solo:
                LIGHTS_ALPHA[0] = k
            if t < BAR_OUT_S:
                self._mode_repaint()
                return
            # the swap: the bar takes its new shape while nothing shows, then everything due fades in
            a["phase"], a["t0"] = 1, time.monotonic()
            for w in (self.sync_label, self.d_sync, self.d_stats):
                w.setGraphicsEffect(None)
            self._mode_effects = {}
            self.sync_light.setVisible(not solo)
            self.sync_label.setVisible(not solo)
            self.d_sync.setVisible(not solo)
            self.d_stats.LEAD = 0 if solo else StatsLine.LEAD
            self.d_stats.setMinimumHeight(self.d_sync.sizeHint().height())
            self.d_stats.updateGeometry()
            self._update_details_now()           # (the new mode's bar, still invisible: no fade of its own)
            for w in [self.d_stats] + ([] if solo else [self.sync_label, self.d_sync]):
                fx = QGraphicsOpacityEffect(w)
                fx.setOpacity(0.0)
                w.setGraphicsEffect(fx)
                self._mode_effects[w] = fx
            if not solo:
                LIGHTS_ALPHA[0] = 0.0
            self._mode_repaint()
            return
        k = ease(min(1.0, t / BAR_IN_S))                       # fading in
        for fx in self._mode_effects.values():
            fx.setOpacity(k)
        if not solo:
            LIGHTS_ALPHA[0] = k
        if t < BAR_IN_S:
            self._mode_repaint()
            return
        self._mode_anim = None
        self._mode_clock.stop()
        self._mode_final(solo)
        if self._check_after_mode:
            self._check_after_mode = False
            self._check_sync()

    def _mode_repaint(self) -> None:
        # (the cover lights redraw in _tick's lights-only frames - _lights_moving is true meanwhile - over the stored
        # picture of the covers: redrawing the whole grid every frame made the fade stutter)
        self.sync_light.update()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        set_border(self)                           # (Windows 11: one solid border colour, like every pop-up)
        if not self._start_fitted:
            self._start_fitted = True
            self._fit_start_height()
            # nothing active at launch (owner, 2026-10-19): Qt hands the keyboard to the first box in the window,
            # the search; the window itself holds it instead until something is clicked
            self.setFocus(Qt.OtherFocusReason)
            if os.environ.get("IKEMEN_DEBUG_FOCUS"):          # (launch checks: what's active a moment after opening)
                QTimer.singleShot(3000, self._log_launch_focus)

    def changeEvent(self, ev) -> None:
        super().changeEvent(ev)
        if ev.type() == QEvent.WindowStateChange and hasattr(self, "anim"):
            self._update_anim()                    # (minimized: the animation clock stops; restored: it starts again)

    _prepaint_left = 2                  # 2: paint at WM_SHOWWINDOW; 1: again once visible; 0: done
    _prepainted: Optional[QImage] = None

    def nativeEvent(self, event_type, message):
        """Opening (owner, 2026-10-18): Windows' open animation fades and zooms in whatever the window shows - and a new
        window shows WHITE until the program has drawn into it. The first paint came ~150-250 ms after the window went
        on screen (covers decoded, the background picture prepared), so the whole animation was a white window fading
        in, the real picture popping in at the end. Now, the moment before Windows shows it (WM_SHOWWINDOW), the
        window's finished picture is drawn straight into it: the animation starts with the real window, like any
        native program's. (Settings and pop-ups still open at once: see no_open_animation / PopupsToFront.)"""
        if self._prepaint_left and sys.platform == "win32":
            try:
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0018 and msg.wParam and self._prepaint_left == 2:   # WM_SHOWWINDOW: about to show
                    self._prepaint_left = 1
                    self.opened_at = time.monotonic()
                    t = time.perf_counter()
                    self._prepainted = paint_into_window(self)
                    _open_log(f"pre-painted {self.width()}x{self.height()} in {(time.perf_counter() - t) * 1000:.0f} ms")
                elif msg.message == 0x0047 and self._prepaint_left == 1:                # WM_WINDOWPOSCHANGED
                    pos = ctypes.cast(msg.lParam, ctypes.POINTER(_WINDOWPOS)).contents
                    if pos.flags & 0x0040:                                              # SWP_SHOWWINDOW: now visible
                        # Drawn into the still-hidden window, the picture sometimes didn't survive its becoming
                        # visible (Windows sets up what's shown at that moment): the opening fade began with a white
                        # window in 1 of 2 - 6 of 7 openings of the built program (owner, 2026-10-19; filmed). So the
                        # same picture goes in again now - visible, but not yet composed on screen.
                        self._prepaint_left = 0
                        if self._prepainted is not None:
                            paint_into_window(self, self._prepainted)
                            _open_log("painted again as it became visible")
                        self._prepainted = None
            except Exception as e:                                # (then it opens as any Qt window would)
                self._prepaint_left = 0
                _open_log(f"pre-paint failed: {e!r}")
        return super().nativeEvent(event_type, message)

    def _fit_start_height(self) -> None:
        """Opening size: START_ROWS rows of covers with the same gap above, between and below them (the
        covers' height depends on the window's width, the bars' on the font). Never taller than the screen."""
        if self.isMaximized() or self.isFullScreen():
            return
        self._fit_grid()
        # (1 px short of the full gap below: at exactly the gap, the next row's top edge could show as a line)
        need = ROW_GAP + self.START_ROWS * (round(GRID["cover_h"]) + ROW_GAP) - 1
        h = self.height() + need - self.grid.viewport().height()
        room = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        frame = max(self.frameGeometry().height() - self.height(), 32)
        h = max(self.minimumHeight(), min(h, room.height() - frame))
        if h != self.height():
            self.resize(self.width(), h)
            top = self.frameGeometry().top()
            if top + h + frame > room.bottom() or top < room.top():
                self.move(self.x(), max(room.top(), room.bottom() - h - frame))

    def easter_egg(self) -> None:
        if self.findChild(DimLayer) is None:
            EasterEgg(self)

    def share_settings(self) -> None:
        """Copy this device's personal settings into the cloud folder for Ikemen Sync (in the background:
        a big background picture or font file can take a moment the first time)."""
        if self.settings.solo:
            return                              # (no other device to share with)
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

    def art(self, game: Game) -> Optional[Path]:
        """A game's cover file (None: no cover). Remembered until the library is reloaded: looking on the
        cloud drive on every redraw is far too slow (see CoverCache)."""
        key = (game.game_id, game.artwork)
        if key not in self._art:
            if self.covers.no_decode:
                return None                    # (a wheel glide's strip: looked up in the background - want_cover)
            self._art[key] = self.library.artwork_path(game)
        return self._art[key]

    def cover_ready(self, iid: str) -> bool:
        """Can this tile be drawn without reading or decoding a cover file (a folder: its mosaic)?"""
        if iid in self._cover_given_up:
            return True
        for g in self._tile_games(iid):
            if not g.artwork:
                continue
            key = (g.game_id, g.artwork)
            if key not in self._art or not self.covers.ready(self._art[key], (g.crop_zoom, g.crop_x, g.crop_y)):
                return False
        return True

    def _tile_games(self, iid: str) -> list:
        """The games whose covers a tile shows (a folder: the first four, its mosaic)."""
        if is_folder(iid):
            return self.folder_games.get(iid[len(FOLDER_PREFIX):], [])[:4]
        return [self.games[iid]] if iid in self.games else []

    def want_cover(self, iid: str) -> None:
        """Load this tile's cover(s) in the background (it arrives once they're ready; see CardDelegate.paint)."""
        if iid not in self._cover_wanted:
            self._cover_wanted.add(iid)
            if not self._cover_loading:
                QTimer.singleShot(0, self._load_wanted_covers)

    def _load_wanted_covers(self) -> None:
        ids, self._cover_wanted = set(self._cover_wanted), set()
        if not ids or self._cover_loading:
            self._cover_wanted |= ids
            return
        games = [g for iid in ids for g in self._tile_games(iid)]
        todo = [(g.game_id, g.artwork, (g.crop_zoom, g.crop_x, g.crop_y)) for g in games if g.artwork]
        art_dir, known, skip = self.library.art_dir, dict(self._art), self.covers.cached()
        self._cover_loading = True

        def work():
            arts, wanted = {}, []
            for gid, artwork, crop in todo:
                key = (gid, artwork)
                path = known[key] if key in known else arts.setdefault(key, (art_dir / artwork) if (art_dir / artwork).is_file() else None)
                if path:
                    wanted.append((path, crop))
            mtimes = {}
            for path, _ in wanted:
                try:
                    mtimes[str(path)] = path.stat().st_mtime
                except OSError:
                    mtimes[str(path)] = None
            return arts, mtimes, CoverCache.prepare(wanted, skip)

        def done(result):
            self._cover_loading = False
            arts, mtimes, prepared = result
            for key, path in arts.items():
                self._art.setdefault(key, path)
            for sp, m in mtimes.items():
                self.covers._mtimes.setdefault(sp, m)
            self.covers.accept(prepared, urgent=True)
            for iid in ids:
                if self.cover_ready(iid):
                    continue
                if self._pushed_out(iid, skip):
                    self._cover_wanted.add(iid)           # (asked for again - see _pushed_out)
                else:
                    self._cover_given_up.add(iid)         # (damaged / unreadable: drawn the usual way)
            self.grid.covers_arrived()
            self.grid.viewport().update()
            if self._cover_wanted:
                self._load_wanted_covers()

        def failed(_):
            self._cover_loading = False
            self._cover_given_up |= ids
            self.grid.viewport().update()
        self._run(work, done, failed, low=True)

    def _pushed_out(self, iid: str, skip: frozenset) -> bool:
        """A cover in memory when it was asked for (so not loaded again) and pushed out of the cover memory since:
        scrolling fast through a big library does that. It used to count as unreadable - a placeholder in the wheel
        strips and a decode on the window's thread whenever drawn normally, until the library was read again."""
        for g in self._tile_games(iid):
            path = self._art.get((g.game_id, g.artwork)) if g.artwork else None
            m = self.covers._mtimes.get(str(path)) if path else None
            crop = (g.crop_zoom, g.crop_x, g.crop_y)
            if m is not None and CoverCache._key(str(path), m, crop) in skip and not self.covers.ready(path, crop):
                return True
        return False

    def _forget_files(self) -> None:
        self._art = {}
        self._cover_given_up = set()
        self.covers.forget_files()

    def refresh(self) -> None:
        """Re-read the library from the cloud folder, then rebuild the grid. The library's fingerprint (every save
        file's size and time, for the live check) is read in the background: on Google Drive walking the saves took
        the window a moment after every rename, edit, removal..."""
        self._apply_library(self._load_library(signature=False))
        self._own_change()

    def refresh_soon(self, keep_files: bool = False) -> None:
        """refresh(), with the reading done in the background: right after a session Google Drive is busy with the
        save just uploaded and can take a while to answer - the window mustn't stop while it does. keep_files: the
        covers didn't change (a session ended): keep remembering their files, or the next paint asks Google Drive
        about every cover again (the grid's draw took 25 ms on the owner's PC right after closing a game)."""
        library = self.library

        def done(data: dict) -> None:
            if library is self.library:
                self._apply_library(data, keep_files)

        self._run(lambda: self._load_library(library), done, lambda _e: self.refresh())

    def _load_library(self, library: Optional[Library] = None, signature: bool = True) -> dict:
        """Everything refresh needs from the cloud folder (only reading: safe in the background). signature=False:
        the live check's fingerprint is left out (refresh() reads it in the background instead)."""
        library = library or self.library
        out = {"cloud": Path(library.root).is_dir()}
        here = self.settings.machine_name                  # (games removed from this device only: not shown here)
        out["games"] = [g for g in library.list_games() if not g.removed_here(here)] if out["cloud"] else []
        if self.settings.solo:
            # solo: only the games set up on this computer, with only this computer's time read and dates (display
            # copies: every change re-reads the game's file, so the other device's numbers are never lost)
            out["games"] = [dataclasses.replace(
                g, playtime={here: g.playtime[here]} if here in g.playtime else {},
                # (None: this computer's date isn't known - read here before dates were kept per device -> -1,
                # shown as "Unknown", owner 2026-10-18; never read here -> 0)
                last_played=-1.0 if g.last_played_on(here) is None else g.last_played_on(here),
                last_played_by={here: g.last_played_by[here]} if here in g.last_played_by else {})
                for g in out["games"] if g.paths_for(here).exe]
        out["folders"] = library.list_folders() if out["games"] else {}
        try:
            out["meta"] = {fid: {"sort": v["sort"], "order": v["order"]} for fid, v in library._folders_raw().items()}
            out["order"] = library.library_order()
        except OSError:
            out["meta"] = out["order"] = None
        try:
            out["sig"] = library.change_signature() if signature else None
        except OSError:
            out["sig"] = None
        return out

    _quiet_rebuild = False                   # (the re-read after a session: the grid may stay as it is)
    _refilling = False                       # (the grid is being emptied and refilled: the bar waits - _rebuild)
    _play_status_key = ""                    # the session's last status message (see _play_status)

    CLOUD_RETRY_MS = 5000                    # the cloud folder didn't answer: read again this much later
    _shown_root: Optional[str] = None        # the library folder the shown games were read from

    def _apply_library(self, data: dict, keep_files: bool = False) -> None:
        root = self.settings.library_root
        if not data["cloud"] and self.games and root == self._shown_root:
            # the cloud folder was there a moment ago: Google Drive stalls while busy and then answers "no such
            # folder" for a while (owner, 2026-10-20: the library vanished for ~30 s). Keep what's shown, try again.
            self.statusBar().showMessage(tr("status.cloud_waiting"), self.CLOUD_RETRY_MS + 1000)
            library = self.library
            QTimer.singleShot(self.CLOUD_RETRY_MS,
                              lambda: self.refresh_soon(keep_files=True) if library is self.library else None)
            return
        if not keep_files:
            self._forget_files()
        self._quiet_rebuild = keep_files
        games = data["games"]
        was_empty = not self.games
        self.games = {g.game_id: g for g in games}
        self.folder_names = data["folders"]
        if data["meta"] is not None:
            self.folder_meta, self.library_order = data["meta"], data["order"]
        try:
            self._rebuild()
        finally:
            self._quiet_rebuild = False
        if was_empty and games and self.no_results > 0 and self.isVisible():
            self._slide_from({}, appear=True)            # the first games fade in once "No Games Yet" has gone
        cloud = data["cloud"]                        # (asked once, in the read: Google Drive can stall on each ask)
        self._shown_root = root if cloud else None
        self.empty.setText(tr("grid.no_cloud", path=self.settings.cloud_root))
        self.empty.setVisible(not cloud)             # (no games yet: drawn on the grid like No Results Found)
        self.grid.setVisible(cloud)
        self.details.setVisible(bool(games))
        self._update_anim()
        self._check_sync()
        if data["sig"] is not None:
            self._live_sig = data["sig"]
        self._fetch_names()
        self._fill_save_folders()

    def _fill_save_folders(self) -> None:
        """The other device set a game's save folder and this device's is still empty: fill it in by itself when
        its place here is certain (certain_saves), within one live poll of the change arriving. Then the usual
        check syncs it: nothing here -> silent download; files of its own here -> straight to "which to keep"."""
        if self._fill_busy or not self.games or self.settings.solo:
            return
        self._fill_busy = True
        library, settings, games = self.library, self.settings, list(self.games.values())

        def done(found: dict) -> None:
            self._fill_busy = False
            if library is not self.library or not found:
                return
            me, filled = self.settings.machine_name, []
            for gid, folder in found.items():
                g = self.library.get(gid)
                if g is None or g.paths_for(me).save_dir or not g.paths_for(me).exe or gid == self.playing_id:
                    continue                            # changed meanwhile / being read: left alone
                try:
                    g = self.library.set_paths(gid, me, save_dir=folder)
                except (OSError, KeyError, ValueError):
                    continue                            # tried again at the next refresh
                if gid in self.games:
                    self.games[gid].paths = g.paths
                self._ask_now.add(gid)
                filled.append(g.title)
            if filled:
                self.statusBar().showMessage(tr("status.save_filled", games=i18n.games_list(filled)), 10000)
                self._own_change()
                self._update_details()
                self._check_sync()

        def failed(_e) -> None:
            self._fill_busy = False
        self._run(lambda: certain_saves(settings, games), done, failed)

    def _fetch_names(self) -> None:
        """Search also finds games by the other names VNDB knows them by (English title, romaji, aliases).
        Fetched once per game in the background and kept in its file in the cloud folder, so both devices have
        them and searching never waits for the internet. Only with VNDB switched on in Settings."""
        if not self.settings.vndb or self._names_busy:
            return
        tried = self._names_tried
        todo = {g.game_id: g.vndb_id for g in self.games.values()
                if g.vndb_id and g.names_for != g.vndb_id and g.vndb_id not in tried}
        if not todo:
            return
        self._names_busy = True
        tried |= set(todo.values())

        def done(found: dict) -> None:
            self._names_busy = False
            changed = False
            for gid, vid in todo.items():
                g = self.library.get(gid)
                if g is None or g.vndb_id != vid:
                    continue                             # changed meanwhile
                try:
                    g = self.library.update(gid, names=found.get(vid, []), names_for=vid)
                except (OSError, KeyError, ValueError):
                    continue                             # a bonus: tried again next start
                if gid in self.games:
                    self.games[gid].names, self.games[gid].names_for = g.names, g.names_for
                changed = True
            if changed:
                self._own_change()
                if self.search.text().strip():
                    self._apply_filter()

        def failed(_e) -> None:
            self._names_busy = False

        self._run(lambda: vndb.names(list(set(todo.values()))), done, failed)

    def _live_poll(self) -> None:
        if self._live_checking or self.settings.solo:
            return                              # (solo: nothing else changes this library)
        self._live_checking = True
        library = self.library

        def done(sig) -> None:
            self._live_checking = False
            if library is not self.library:
                return                              # (settings changed meanwhile)
            if sig == self._live_sig:               # nothing new from the other device
                self._quiet_polls += 1
                # still re-check now and then: a held-back download whose game was closed (every poll),
                # saves made on this device outside Ikemen Reader (about once a minute)
                waiting = any(s == "pull" for s in self.sync_states.values())
                if (waiting or self._quiet_polls >= RECHECK_POLLS) and not (self.checker and self.checker.isRunning()):
                    self._quiet_polls = 0
                    self._check_sync(quiet=True)
                return
            if self._ghost is not None or self.worker is not None or QApplication.activePopupWidget() is not None \
                    or QApplication.activeModalWidget() is not None:
                return                              # mid-drag / a game running / a menu or window open: next time
            self._conflicts_snoozed = set()         # something new from the other device: ask again
            self.refresh_soon()                     # (read in the background: nothing here is waiting for it)
            self.statusBar().showMessage(tr("status.live_update"), 6000)

        def failed(_e) -> None:
            self._live_checking = False
        self._run(library.change_signature, done, failed)

    def _check_sync(self, quiet: bool = False) -> None:
        """quiet: a routine background re-check - the top bar doesn't flash "Syncing" for it."""
        if self.settings.solo:
            self.sync_states = {gid: "synced" for gid in self.games}   # (nothing syncs on a solo device)
            return
        if self._mode_anim is not None:
            self._check_after_mode = True          # (after the switch's fade: checking saves alongside made it stutter)
            return
        if self.checker and self.checker.isRunning():
            self._recheck = True  # one at a time; check again when the running one finishes
            return
        self._recheck = False
        self._quiet_check = quiet
        for gid in self.games:
            self.sync_states.setdefault(gid, "checking")
        skip = {self.playing_id} if self.worker is not None and self.playing_id else set()
        self.checker = StatusChecker(self.engine, list(self.games.values()), self.settings.machine_name, skip)
        checker = self.checker
        # (results of a check made for another engine - the settings changed, or Dual -> My Device, while it ran - are
        # dropped: they described a library / mode the window no longer shows)
        checker.done.connect(lambda *res: self._sync_checked(*res) if checker.engine is self.engine else None)
        self.checker.finished.connect(self._update_sync_label)  # isRunning() is reliably False by then
        self.checker.start()
        self._update_details()
        self._update_sync_label()

    def _sync_checked(self, states: dict, uploaded: list, downloaded: list) -> None:
        self.sync_states = dict(states)
        if uploaded:                                # our own upload: not a change from the other device
            try:
                self._live_sig = self.library.change_signature()
            except OSError:
                pass
        notes = ([tr("status.startup_uploaded", games=i18n.games_list(uploaded))] if uploaded else []) + \
                ([tr("status.auto_downloaded", games=i18n.games_list(downloaded))] if downloaded else [])
        if notes:
            self.statusBar().showMessage("  ".join(notes), 10000)
        self._lights_changed()
        self._update_details()
        self._update_sync_label()
        if self._recheck:
            self._check_sync()
        elif not self._solo_saves_asked:
            self._ask_solo_saves()                  # (just back to Dual: one window for all of them)
        else:
            self._offer_conflicts()

    def _offer_conflicts(self) -> None:
        """Saves changed on both devices can't sync silently: ask once (Choose Now / Later). Later isn't
        asked again for the same games until the other device changes something new. A game whose settings
        were just saved here skips that step: straight to "which saves to keep" (even if Later was said before)."""
        if self.settings.solo:
            return
        conflicts = [gid for gid, s in self.sync_states.items() if s == "conflict" and gid in self.games
                     and gid != self.playing_id]
        self._ask_now &= set(conflicts)             # (the others synced by themselves, or need nothing)
        new = [gid for gid in conflicts if gid not in self._conflicts_snoozed or gid in self._ask_now]
        if not new or self.worker is not None or self.syncer is not None or self._ghost is not None \
                or QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return                                  # nothing new, or busy: asked again at the next check
        if self._ask_now:
            direct = [self.games[gid] for gid in conflicts if gid in self._ask_now]
            self._ask_now = set()
            self._conflicts_snoozed |= {g.game_id for g in direct}
            self.sync_now(direct)
            return
        games = [self.games[gid] for gid in conflicts]
        listing = "\n".join("• " + g.title for g in games)
        mixed = "_mixed" if any(self.only_here(g) or self.first_sync(g) for g in games) else ""
        choice = message(self, tr("conflict.waiting_body" + mixed, games=listing), tr("conflict.waiting_title" + mixed),
                         [("now", tr("conflict.choose_now")), ("later", tr("conflict.ask_later"))], dismiss="later")
        self._conflicts_snoozed |= set(conflicts)   # asked: not again (Choose Now's own answers included)
        if choice == "now":
            self.sync_now(games)

    def sync_now(self, games: list) -> None:
        """Sync these games right away (right-click Sync Now, or Choose Now for saves changed on both)."""
        if self.syncer is not None or self.worker is not None or not games or self.engine is None:
            return
        s = SyncWorker(self.engine, games, self.settings.machine_name)
        s.conflict.connect(lambda info: setattr(s, "answer", self._conflict_choice(info)), Qt.BlockingQueuedConnection)
        s.finished_all.connect(self._synced_now)
        self.syncer = s
        self.statusBar().showMessage(tr("status.checking"))
        s.start()

    def _synced_now(self, results: list) -> None:
        self.syncer.wait()
        self._conflicts_snoozed |= {g.game_id for g in self.syncer.games}   # closed without choosing: not again
        self.syncer = None
        by_title = {g.title: g for g in self.games.values()}
        colon = "：" if i18n.language() == "ja" else ": "
        self.statusBar().showMessage("  ".join(f"{t}{colon}{i18n.line(self.solo_key(k, by_title.get(t)), title=t)}"
                                               for t, k in results), 10000)
        self.refresh()

    def folder_name(self, fid: str) -> str:
        return self.folder_names.get(fid) or tr("folder.new_name")

    def _rebuild(self, select: Optional[str] = None) -> None:
        """Fill the grid: the library (games + folder tiles), or the open folder's games. While
        searching or filtering, games inside folders are shown directly so nothing is hidden."""
        current = select or self.selected_id()
        games = [g for g in self.games.values() if self._on_device(g)]      # this library view's games
        q = self.search.text().strip()
        status = self.settings.status_filter
        sort = self.settings.sort

        self.folder_games = {}
        for g in sort_games(games, "recent"):
            if g.folder:
                self.folder_games.setdefault(g.folder, []).append(g)
        for fid, gs in self.folder_games.items():       # each folder in its own sort (or the library's)
            fs = self.folder_sort(fid)
            self.folder_games[fid] = in_order(gs, lambda g: g.game_id, self.folder_meta.get(fid, {}).get("order", [])) \
                if fs == "custom" else sort_games(gs, fs)
        if self.current_folder and self.current_folder not in self.folder_games:
            self.current_folder = None                    # it was emptied or removed
        flat = bool(q) or self._filtering()

        def keep(g: Game) -> bool:
            return matches_search(g, q) and (status == "all" or g.status == status)

        def keep_folder(fid: str) -> bool:              # searching: a folder whose name matches shows as itself
            return bool(q) and matches_names((self.folder_name(fid),), q) and \
                    (status == "all" or any(g.status == status for g in self.folder_games[fid]))

        top = [("game", g) for g in sort_games(games, "recent") if not g.folder]
        top += [("folder", fid) for fid in self.folder_games]
        if sort == "custom":                             # My Arrangement: exactly your order
            top = in_order(top, self._entry_id, self.library_order)
        else:                                            # folders mixed in / first / last, by the same sort
            top = self._auto_order(top, sort, self.settings.folder_place)
        self._top_entries = top
        if self.current_folder:
            entries = [("game", g) for g in self.folder_games[self.current_folder] if keep(g)]
        elif flat:
            if sort == "custom":                         # the library's order, each folder's games in its place
                entries = []
                for kind, obj in top:
                    if kind == "folder":
                        entries += [("folder", obj)] if keep_folder(obj) else []
                        entries += [("game", g) for g in self.folder_games[obj] if keep(g)]
                    elif keep(obj):
                        entries.append(("game", obj))
            else:
                entries = [("game", g) for g in sort_games(games, sort) if keep(g)]
                folders = [("folder", fid) for fid in self.folder_games if keep_folder(fid)]
                if folders:                              # placed like on the main page (first / last / mixed)
                    entries = self._auto_order(entries + folders, sort, self.settings.folder_place)
        else:
            entries = top

        # nothing visible changed (e.g. the re-read after a session: only playtime / dates, which show on hover):
        # keep the grid as it is - rebuilding redrew every cover (20-30 ms on the owner's PC, right as a game closed)
        look = [self._entry_look(kind, obj) for kind, obj in entries]
        if self._quiet_rebuild and look == getattr(self, "_grid_look", None) and self.grid.count() == len(entries):
            sel = self.selected_id()
            if sel != current:
                self._select(current) if current else self.grid.clearSelection()
            self._after_rebuild(entries)
            return
        self._grid_look = look
        scroll = self.grid.verticalScrollBar().value()       # keep your place (a refresh mustn't jump to the top)
        # while the grid is emptied and refilled the selection passes through other games and nothing: the bar
        # must not follow it (it faded the name / light / stats out and back in for the same game - the flicker
        # a few seconds after a session, when the upload made the library reload); it's updated once, after
        self._refilling = True
        try:
            self.grid.clear()
            for kind, obj in entries:
                it = QListWidgetItem()
                iid = obj.game_id if kind == "game" else FOLDER_PREFIX + obj
                it.setData(GAME_ID_ROLE, iid)
                it.setData(Qt.DisplayRole, obj.title if kind == "game" else self.folder_name(obj))
                self.grid.addItem(it)
                if iid == current:
                    self.grid.setCurrentItem(it)
                    it.setSelected(True)
            if current is None:
                self.grid.clearSelection()          # nothing was selected: keep it that way
        finally:
            self._refilling = False
        if select is None:
            self.grid.doItemsLayout()
            self.grid.verticalScrollBar().setValue(scroll)
        self._after_rebuild(entries)

    def _entry_look(self, kind: str, obj) -> tuple:
        """Everything about a tile that shows without hovering (to know when a rebuild would change nothing)."""
        if kind == "game":
            return ("g", obj.game_id, obj.title, obj.artwork, obj.crop_zoom, obj.crop_x, obj.crop_y, obj.status)
        inner = tuple((g.game_id, g.artwork, g.crop_zoom, g.crop_x, g.crop_y, g.status)
                      for g in self.folder_games.get(obj, [])[:4])
        return ("f", obj, self.folder_name(obj), len(self.folder_games.get(obj, [])), inner)

    def _after_rebuild(self, entries) -> None:
        inside = self.current_folder is not None
        self.folderbar.setVisible(inside)
        if inside:
            self.folder_title.setText(self.folder_name(self.current_folder))
        if self._sort_mode != self.current_folder:
            self._fill_sort()                           # the library's choices, or this folder's
        else:
            self._mark_sort()                           # (folders may have come or gone: greyed or not)
        self._update_details()
        self._prefetch_entries = entries
        self._prefetch_wait.start()                    # (restarted by every change: see _prefetch_covers)

    def prepare_first_screen(self, n: int = 40) -> None:
        """Before the window first appears: decode the covers it opens with, so its first picture is ready in
        milliseconds. Windows' open animation fades in whatever the window shows at that moment, and decoding ~20
        covers inside the first paint took ~165 ms - longer than the animation, which then faded in a blank white window
        (owner, 2026-10-18)."""
        games = [g for i in range(min(n, self.grid.count())) for g in self._tile_games(self.grid.item(i).data(GAME_ID_ROLE))]
        wanted = [(path, (g.crop_zoom, g.crop_x, g.crop_y)) for g in games if g.artwork and (path := self.art(g))]
        try:
            self.covers.accept(CoverCache.prepare(wanted, self.covers.cached()))
            self.grid.covers_arrived()
        except Exception:                          # noqa: BLE001 - (they're then decoded when drawn, as before)
            pass

    def _prefetch_covers(self, entries, ahead: bool = False) -> None:
        """Prepare the covers of what's shown (in order; a folder's first four for its mosaic) in the background,
        so scrolling to them later never waits for a decode - and the cover files are looked up there too, not
        on the cloud drive while drawing. One task at a time; a newer request replaces a waiting one."""
        games = []
        for kind, obj in entries:
            games += [obj] if kind == "game" else self.folder_games.get(obj, [])[:4]
        for kind, obj in [] if ahead else entries:     # then the rest of each folder, so opening one is instant
            games += self.folder_games.get(obj, [])[4:] if kind == "folder" else []
        todo = [(g.game_id, g.artwork, (g.crop_zoom, g.crop_x, g.crop_y))
                for g in games if g.artwork][:CoverCache.MAX_COVERS]
        if getattr(self, "_prefetching", False):
            self._prefetch_next = (entries, ahead)
            return
        if not todo:
            return
        self._prefetching, self._prefetch_next = True, None
        art_dir, known, skip = self.library.art_dir, dict(self._art), self.covers.cached()

        def work():
            arts, wanted = {}, []
            for gid, artwork, crop in todo:
                time.sleep(0.002)                      # (a breather each time: the window stays smooth)
                key = (gid, artwork)
                if key in known:
                    path = known[key]
                else:
                    f = art_dir / artwork
                    path = arts[key] = f if f.is_file() else None
                if path:
                    wanted.append((path, crop))
            return arts, CoverCache.prepare(wanted, skip)

        def done(result):
            self._prefetching = False
            if result:
                arts, prepared = result
                for key, path in arts.items():
                    self._art.setdefault(key, path)
                self.covers.accept(prepared, ahead=ahead)
                self.grid.covers_arrived()
            if self._prefetch_next is not None:
                self._prefetch_covers(*self._prefetch_next)

        def failed(_):
            self._prefetching = False
        self._run(work, done, failed, low=True)

    _ahead_pending = False

    def scrolled(self) -> None:
        """The grid scrolled: get the covers around the view ready (once per moment, not per scroll step), and the
        cover now under the pointer is the hovered one (it only followed mouse moves: with the wheel's glide, the
        lifted cover slid away under a still pointer - browsers and Steam follow the scroll too)."""
        if self._ghost is not None:
            pass                                        # (a game being dragged sets its own target)
        elif self.grid.scroll_settling():
            self._set_hovered(None)                     # (lifts once scrolling has stopped - LibraryGrid._end_glide)
        else:
            vp = self.grid.viewport()
            pos = vp.mapFromGlobal(QCursor.pos())
            it = self.grid.itemAt(pos) if vp.rect().contains(pos) and vp.underMouse() else None
            self._set_hovered(it.data(GAME_ID_ROLE) if it else None)
        if not self._ahead_pending:
            self._ahead_pending = True
            QTimer.singleShot(30, self._prefetch_ahead)

    def _prefetch_ahead(self) -> None:
        """The covers on screen and the next screenful above and below, nearest first, decoded in the background.
        A library bigger than the cover memory can't have every cover ready, and a cover decoded while drawing
        held a scrolling frame up (~6 ms each on the laptop, a row at a time)."""
        self._ahead_pending = False
        vis = self.grid.visible_range()
        if not vis or not hasattr(self, "games"):
            return
        n = len(vis)
        rows = list(vis) + [i for k in range(1, n + 1) for i in (vis.stop - 1 + k, vis.start - k)]
        entries = []
        for i in rows:
            if 0 <= i < self.grid.count():
                iid = self.grid.item(i).data(GAME_ID_ROLE)
                if is_folder(iid):
                    entries.append(("folder", iid[len(FOLDER_PREFIX):]))
                elif iid in self.games:
                    entries.append(("game", self.games[iid]))
        self._prefetch_covers(entries, ahead=True)

    @staticmethod
    def _entry_id(entry) -> str:
        kind, obj = entry
        return obj.game_id if kind == "game" else FOLDER_PREFIX + obj

    def _read_orders(self) -> None:
        try:
            self.folder_meta = {fid: {"sort": v["sort"], "order": v["order"]}
                                for fid, v in self.library._folders_raw().items()}
            self.library_order = self.library.library_order()
        except OSError:
            pass

    def folder_sort(self, fid: str) -> str:
        """The sort a folder's games are shown in: its own, or the library's when it has none."""
        return self.folder_meta.get(fid, {}).get("sort") or self.settings.sort

    # ------------------------------------------------------------------ the sort drop-down
    def _sort_choices(self, inside: bool) -> list:
        """(label, data) for the sort drop-down: the three automatic sorts, then under a divider Folders First,
        Folders Last (actions: they move the folders) and My Arrangement. Inside a folder: the three sorts, then
        Same as Library and My Arrangement."""
        sorts = [(tr(f"sort.{s}"), s) for s in ("title", "hours", "recent")]
        mine = (tr("sort.custom"), "custom")
        if inside:
            return sorts + [None, (tr("sort.follow"), "follow"), mine]
        # three groups, each under a thin line: the sorts | where folders go | your own arrangement
        return sorts + [None] + [(tr(f"place.{p}"), "place:" + p) for p in ("first", "last", "mixed")] + [None, mine]

    def _fill_sort(self) -> None:
        c = self.sort
        inside = self.current_folder is not None
        self._sort_mode = self.current_folder
        c.blockSignals(True)
        c.clear()
        for choice in self._sort_choices(inside):
            if choice is None:
                c.insertSeparator(c.count())
                continue
            label, data = choice
            c.addItem(label, data)
        c.blockSignals(False)
        self._mark_sort()

    def _mark_sort(self) -> None:
        """Show the sort in use (without rebuilding the list: rebuilding it on every pick made it slow to reopen)."""
        c = self.sort
        inside = self.current_folder is not None
        current = (self.folder_meta.get(self.current_folder, {}).get("sort") or "follow") if inside else self.settings.sort
        c.blockSignals(True)
        c.setCurrentIndex(max(0, c.findData(current)))
        has_folders = bool(self.folder_games)              # no folders: nothing for the folder options to do
        for i in range(c.count()):
            d = c.itemData(i)
            if d and d.startswith("place:"):
                c.model().item(i).setEnabled(has_folders)
        c.blockSignals(False)

    def _filter_choices(self, solo: bool) -> list:
        """(label, data) for the filter drop-down: with two devices, the library views This Device / Other Device /
        Both Devices, a divider, then the reading statuses (owner, 2026-10-18). The box shows the view; a status picked
        filters within it, folder-option style (remembered, not shown in the box)."""
        statuses = [(tr("filter.all"), "all")] + [(tr(f"status.{s}"), s) for s in STATUSES[1:]]
        if solo:
            return statuses
        return [(tr(f"filter.{d}"), "dev:" + d) for d in ("here", "other", "both")] + [None] + statuses

    def _fill_filter(self) -> None:
        c = self.filter
        c.blockSignals(True)
        c.clear()
        for choice in self._filter_choices(self.settings.solo):
            if choice is None:
                c.insertSeparator(c.count())
            else:
                c.addItem(*choice)
        c.blockSignals(False)
        self._mark_filter()

    def _mark_filter(self) -> None:
        """The box shows the library view (My Device: the reading status, as before). No ticks, like the sort's."""
        c = self.filter
        c.blockSignals(True)
        c.setCurrentIndex(max(0, c.findData(self.settings.status_filter if self.settings.solo
                                            else "dev:" + self.settings.device_filter)))
        c.blockSignals(False)

    def _device_filter(self) -> str:
        return "both" if self.settings.solo else self.settings.device_filter

    def _filtering(self) -> bool:
        """A status filter is on (games inside folders then show directly, nothing can be rearranged). The library
        view isn't one: it's a library of its own, folders and all."""
        return self.settings.status_filter != "all"

    def _on_device(self, g: Game) -> bool:
        """Is this game in the library view? here = set up on this device, other = set up only on the other one."""
        dev = self._device_filter()
        if dev == "both":
            return True
        me = self.settings.machine_name
        mine = bool(g.paths_for(me).exe)
        return mine if dev == "here" else not mine and bool(g.paths_for(other_slot(me)).exe)

    def _all_top_ids(self) -> list[str]:
        """Every main-page item of the whole library (all views): games not in a folder, and the folders."""
        return [g.game_id for g in self.games.values() if not g.folder] + \
            list(dict.fromkeys(FOLDER_PREFIX + g.folder for g in self.games.values() if g.folder))

    def _place_folders(self, place: str) -> list[str]:
        """My Arrangement + a folder option: the folders re-placed, sorted by the automatic sort used last (games
        keep your order). First / Last: all folders at the front / back. Mixed: the folders take the very spots
        that sort would give them, your games filling the others in your order."""
        sort = self.settings.last_sort
        entries = list(getattr(self, "_top_entries", []))
        games = [e for e in entries if e[0] == "game"]
        folders = sorted((e for e in entries if e[0] == "folder"), key=lambda e: self._sort_key(e, sort))
        if place == "first":
            arranged = folders + games
        elif place == "last":
            arranged = games + folders
        else:
            slots = {i for i, e in enumerate(self._auto_order(entries, sort, "mixed")) if e[0] == "folder"}
            fi, gi, arranged = iter(folders), iter(games), []
            for i in range(len(entries)):
                arranged.append(next(fi) if i in slots else next(gi))
        return [self._entry_id(e) for e in arranged]

    def _sort_key(self, entry, sort: Optional[str] = None) -> tuple:
        from library import title_sort_key
        sort = sort or self.settings.sort
        kind, obj = entry
        if kind == "game":
            last, hours, title = obj.last_played, obj.total_playtime, obj.title
        else:
            gs = self.folder_games[obj]
            last = max(g.last_played for g in gs)
            hours = sum(g.total_playtime for g in gs)
            title = self.folder_name(obj)
        if sort == "hours":
            return (-hours, title_sort_key(title))
        if sort == "title":
            return (title_sort_key(title),)
        return (-last, title_sort_key(title))

    def _auto_order(self, entries: list, sort: str, place: str) -> list:
        """entries in an automatic sort, folders mixed in / first / last (each group in that sort)."""
        out = sorted(entries, key=lambda e: self._sort_key(e, sort))
        if place != "mixed":
            folders = [e for e in out if e[0] == "folder"]
            loose = [e for e in out if e[0] == "game"]
            out = folders + loose if place == "first" else loose + folders
        return out

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

    def bar_game(self) -> Optional[Game]:
        """The game the bottom bar shows and acts on: the selected one; but while a game runs and nothing
        (or that game) is selected, the running game - so Now Reading and Stop come back by clicking off."""
        if self._bar_shows_running():
            return self.games[self.playing_id]
        return self.selected()

    def _bar_shows_running(self) -> bool:
        return (self.worker is not None and self.playing_id in self.games
                and self.selected_id() in (None, self.playing_id))

    def open_folder(self, fid: str) -> None:
        """Into a folder, the same way search changes the grid: the library fades out, then the folder's games
        fade in one after another (the "‹ Library" bar above them shows where you are)."""
        self.current_folder = fid
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._apply_filter(after_rebuild=self.grid.scrollToTop)

    def close_folder(self) -> None:
        egg = self.findChild(DimLayer)
        if egg is not None:                         # Esc on the dark screen (easter egg, notes): back to the library
            egg.dismiss()
            return
        if self._ghost is not None:                 # Esc while dragging a game: put it back
            self._end_game_drag(QCursor.pos(), drop=False)
            return
        if self.current_folder:
            # back out: the folder's games fade out, then the library fades in - the folder you came from first
            # (and selected), so your eye lands where you left off
            fid, self.current_folder = self.current_folder, None
            tile = FOLDER_PREFIX + fid

            def find_tile():
                self.grid.doItemsLayout()
                self.grid.scrollToItem(self.grid.currentItem()) if self.grid.currentItem() else None
            self._apply_filter(select=tile, after_rebuild=find_tile)
            if tile in self.appearing and len(self.appearing) > 1:
                first = min(d for k, d in self.appearing.items() if k != tile)
                self.appearing[tile] = max(0.0, first - 2 * APPEAR_STAGGER)
        else:
            self.clear_selection()

    def _running(self, gid: Optional[str]) -> bool:
        """Is this game being started or running (the big button says Stop)?"""
        return (self.worker is not None and gid is not None and gid == self.playing_id
                and self.play_phase in ("starting", "playing"))

    def _play_button(self) -> None:
        g = self.bar_game()
        if g is not None and self._running(g.game_id):
            if self._session_look(g.game_id)[0] == "bar.stop":
                self._stop_game()                   # (only once it shows Stop: before, a press does nothing)
            return
        self.activate_selected()

    def _stop_game(self) -> None:
        """Stop: before the game appears, stop waiting; once it runs, ask it to close like its own X button
        (it may still ask "Quit?"). If it's still open a few seconds later, offer to force it closed."""
        worker = self.worker
        self.stop_pending = True
        worker.stop_pressed.set()
        self._update_details()
        if self.play_phase == "playing":
            QTimer.singleShot(STOP_PATIENCE_MS, lambda: self._still_running(worker))

    def _still_running(self, worker) -> None:
        if worker is not self.worker or self.play_phase != "playing":
            return                                  # it closed (or a new game was started since)
        g = self.games.get(self.playing_id)
        force = ask_yes_no(self, tr("stop.body", title=g.title if g else ""), tr("stop.force"), tr("stop.keep"),
                           title=tr("stop.title"))
        if worker is not self.worker or self.play_phase != "playing":
            return                                  # it closed while the question was open
        if force:
            worker.force_close.set()
        else:
            self.stop_pending = False               # Stop can be pressed again
            self._update_details()

    def activate_selected(self) -> None:
        """Double-click / Enter / the big button: open a folder, or play a game."""
        fid = self.selected_folder()
        if fid:
            self.open_folder(fid)
        else:
            self.play_selected()

    def quiet(self, gid: str) -> bool:
        """No playtime / reading status while the game is running, or before it has any save data
        (the "Save at least once!" state): only the message matters then. Follows the LIGHT, not the button (owner,
        2026-10-17): hidden while it says Starting / Now Reading / Syncing, back with Synced - the button turns back
        to Read the moment the game closes, while the light still says Now Reading until the saves have uploaded."""
        if gid == self.playing_id:                  # (the session runs until its saves have synced)
            return self._session_look(gid)[1] in ("starting", "playing", "syncing")
        if self.settings.solo:
            return False                            # (My Device: no "save at least once" - nothing syncs)
        return self.sync_states.get(gid) == "detect"

    def folder_colours(self, fid: str) -> list[str]:
        """The light colours of the games in a folder, each once, most important first."""
        present = {STATE_LOOK.get(self.light_state(g.game_id), STATE_LOOK["checking"])[0]
                   for g in self.folder_games.get(fid, [])}
        return [c for c in LIGHT_ORDER if c in present] or ["grey"]

    def light_state(self, gid: str) -> str:
        if is_folder(gid):
            states = {self.light_state(g.game_id) for g in self.folder_games.get(gid[len(FOLDER_PREFIX):], [])}
            return next((s for s in STATE_PRIORITY if s in states), "synced")
        if gid == self.playing_id:
            return self._session_look(gid)[1]
        if self.settings.solo and self._mode_shown is not False:   # (still fading out: their real colours)
            return "synced"
        return self.sync_states.get(gid, "checking")

    # ------------------------------------------------------------------ sort / filter
    def _apply_filter(self, select: Optional[str] = None, after_rebuild: Optional[Callable] = None) -> None:
        """Search / filter / sort / opening or closing a folder: covers taken away (or whose place changes) fade
        out where they were (drawn from a picture of the grid taken just before), then the ones let in fade in;
        nothing slides. after_rebuild: run once the new grid is in place (e.g. scroll), before the fade starts."""
        animate = self.isVisible()
        before = self._positions() if animate else {}
        vp_before = self.grid.viewport().mapTo(self, QPoint(0, 0))
        snap = self._grid_snapshot() if before else None
        rects = {}
        if snap is not None:
            view = self.grid.viewport().rect()
            for i in self.grid.visible_range():
                it = self.grid.item(i)
                iid = it.data(GAME_ID_ROLE)
                rects[iid] = QRect(before[iid], self.grid.visualItemRect(it).size()).adjusted(-2, -2, 2, 2) & view
        # a folder can keep its place while its mosaic changes (a sort reorders what's inside): its four covers then
        # had to be decoded mid-frame (a 64 ms frame on the PC) - it fades out and in like a cover that moved instead,
        # its new covers loaded in the background first
        mosaics = {iid: self._mosaic_ids(iid) for iid in before if is_folder(iid)}
        self._search_pause.stop()                        # (called directly: no second run when the pause ends)
        self._rebuild(select)
        restyled = {iid for iid, ids in mosaics.items() if self._mosaic_ids(iid) != ids}
        if after_rebuild is not None or select is not None:
            self.backdrop.layout() and self.backdrop.layout().activate()   # (the folder bar shows / hides)
        if after_rebuild is not None:
            after_rebuild()
        # the grid may have moved on screen (the folder bar above it): the leaving pictures stay where they were
        shift = vp_before - self.grid.viewport().mapTo(self, QPoint(0, 0))
        if not animate and not self.showing_no_results():
            self.no_results = 0.0                        # (not on screen: nothing to fade)
        if animate:                                      # (also from an empty grid: "No Results Found" -> results)
            moved = self._slide_from(before, appear=True, restyled=restyled)
            kept = {self.grid.item(i).data(GAME_ID_ROLE) for i in range(self.grid.count())}
            gone = []
            for iid, r in rects.items():
                if (iid not in kept or iid in moved) and not r.isEmpty():
                    if not any((r & g).width() * (r & g).height() > 0.3 * r.width() * r.height() for g in gone):
                        gone.append(r)                   # (two covers caught on top of each other: one picture)
            if gone:
                self._leaving = (self._leaving + [(snap, gone, time.monotonic(), shift)])[-MAX_LEAVING_GROUPS:]
        self._update_anim()                              # (also fades "No Results Found" in)

    def _mosaic_ids(self, iid: str) -> tuple:
        """The games whose covers a folder tile shows (its first four)."""
        return tuple(g.game_id for g in self.folder_games.get(iid[len(FOLDER_PREFIX):], [])[:4])

    def _grid_snapshot(self) -> Optional[QPixmap]:
        """The grid's covers as they are on screen now, without the background."""
        vp = self.grid.viewport()
        if vp.width() <= 0 or vp.height() <= 0:
            return None
        dpr = self.devicePixelRatioF()
        pm = QPixmap(round(vp.width() * dpr), round(vp.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        self.grid.bare = True
        try:
            vp.render(p, QPoint(0, 0), QRegion(), QWidget.DrawChildren)
        finally:
            self.grid.bare = False
            p.end()
        return pm

    def leaving_frame(self) -> list:
        """[(snapshot, rects, opacity, scale, shift)] for covers fading out after changes (oldest first); rects are
        where they were in the snapshot, shift = how far to move them so they stay put on screen."""
        now = time.monotonic()
        self._leaving = [g for g in self._leaving if now - g[2] < LEAVE_S]
        out = []
        for pm, rects, t0, shift in self._leaving:
            k = 1 - (1 - (now - t0) / LEAVE_S) ** 3      # ease out
            out.append((pm, rects, 1 - k, 1 - LEAVE_SHRINK * k, shift))
        return out

    def showing_no_results(self) -> bool:
        """Searching / filtering and nothing matches."""
        flat = bool(self.search.text().strip()) or self._filtering() or self._device_filter() != "both"
        return flat and self.grid.count() == 0 and bool(self.games)

    def _toggle_stats(self) -> None:
        """Hide Statistics / Show Statistics: playtime and last played, on the covers and the bottom bar."""
        self.settings.show_stats = not self.settings.show_stats
        self.stats_btn.setText(tr("top.hide_stats" if self.settings.show_stats else "top.show_stats"))
        self._save_view_choice()                 # remembered for next time (and shared by Ikemen Sync)
        self.grid.viewport().update()
        self._update_details()

    def _toggle_bookmarks(self) -> None:
        """The bookmark button: show / hide the reading-status ribbons on the covers."""
        self.settings.show_bookmarks = not self.settings.show_bookmarks
        self.bookmark_btn.set_on(self.settings.show_bookmarks)
        self.bookmark_btn.setToolTip(tr("bar.bookmarks_hide" if self.settings.show_bookmarks else "bar.bookmarks_show"))
        self._save_view_choice()                 # remembered for next time (and shared by Ikemen Sync)
        self._update_anim()                      # the bookmarks fade in / out (see _tick)

    def _save_view_choice(self) -> None:
        """Remember a view choice (sort, filter, statistics, bookmarks...) and share it for Ikemen Sync - once
        things have settled: writing the files right away competed with the fade it starts (late frames), and
        quick changes in a row are saved once. Closing saves at once (_flush_view_choice)."""
        self._view_save.start()

    def _flush_view_choice(self) -> None:
        if self._view_save.isActive():
            self._view_save.stop()
            self._save_view_choice_now()

    def _save_view_choice_now(self) -> None:
        try:
            save_local_settings(self.settings)
        except OSError:
            return              # locked for a moment: it's saved with the next change anyway
        self.share_settings()

    def _sort_picked(self, index: int) -> None:
        """A choice in the sort drop-down. Acted on just after the click: rebuilding the drop-down's list
        while Qt is still handling the click left it unsure what was selected (the next pick was ignored)."""
        d = self.sort.itemData(index)
        if d:
            was = self.sort.painted                  # (the box may be redrawn before choose_sort runs)
            self.sort.fade_words(was, ARRIVE_WAIT)   # (from the click: no frame of the new words first)
            QTimer.singleShot(0, lambda: self.choose_sort(d, was))

    @guarded
    def choose_sort(self, d: str, was: Optional[str] = None) -> None:
        if not d:
            return
        if was is None:
            was = self.sort.painted
        if d.startswith("place:"):
            # Folders Mixed / First / Last: remembered for the automatic sorts (until another is pressed). In My
            # Arrangement they also rearrange the folders, by the automatic sort used last (see _place_folders)
            place = d[len("place:"):]
            self.settings.folder_place = place
            if self.settings.sort == "custom" and not self.current_folder:
                order = merge_order(self.library_order + self._all_top_ids(), self._place_folders(place))
                self.library.set_library_order(order)
                self.library_order = order
                self._own_change()
        elif d in ("recent", "hours", "title") and not self.current_folder:
            self.settings.sort = self.settings.last_sort = d
        elif self.current_folder:                    # this folder's own sort ("follow" = the library's)
            self.library.set_folder_sort(self.current_folder, "" if d == "follow" else d)
            self._read_orders()
            self._own_change()
        else:                                        # My Arrangement (remembering the sort you came from)
            if self.settings.sort in ("recent", "hours", "title"):
                self.settings.last_sort = self.settings.sort
            self.settings.sort = d
        self._save_view_choice()
        self._mark_sort()
        # like search: covers whose place changes fade out there and back in at their new place (no sliding)
        self._fade_top_words(self.sort, was, self._apply_filter)   # (no reload or save check: only the order changed)

    def _filter_changed(self, _i: int) -> None:
        d = self.filter.currentData() or "all"
        was = self.filter.painted
        if d.startswith("dev:"):
            self.settings.device_filter = d[len("dev:"):]
        else:
            self.settings.status_filter = d
        self._mark_filter()                              # (a status picked under a device: the box keeps the device)
        self._save_view_choice()
        self._fade_top_words(self.filter, was, self._apply_filter)

    def _fade_top_words(self, box: "ThemedCombo", was: str, change: Callable[[], None]) -> None:
        """A pick in the top bar's sort / status filter: the grid changes, and the box's words fade with the covers
        (out as they leave, in with the first arriving one)."""
        self.arrive_wait, self.arrive_span = ARRIVE_WAIT, 0.0   # (nothing arrives: the usual pause)
        change()
        box.fade_words(was, self.arrive_wait, span=self.arrive_span)

    @guarded
    def _status_changed(self, _i: int) -> None:
        g = self.bar_game()
        if g is None:
            return
        s = self.d_status.currentData() or ""
        if s != g.status:
            self._fade_status_words(max(0, self.d_status.findData(g.status)))
            self._set_status(g.game_id, s)

    def _fade_status_words(self, old_index: int) -> None:
        """A status picked from the drop-down: its words fade like a game switch's (out, then the new ones in). The
        box already shows the new words, so the old look is drawn for a moment with the old ones put back."""
        d = self.d_status
        if not self.details.isVisible() or old_index == d.currentIndex():
            return
        new_index = d.currentIndex()
        fading = self._fader.isVisible()
        self._fader.hide()
        d.blockSignals(True)
        try:
            d.setCurrentIndex(old_index)
            old = self.details.grab()
            d.setCurrentIndex(new_index)
        finally:
            d.blockSignals(False)
            if fading:
                self._fader.show()
        self._fader.fade("status", _PieceFade(self._status_text_rect(), old, self._bar_without_status_words(),
                                              time.monotonic(), cross=False))
        self._update_anim()

    # ------------------------------------------------------------------ bottom bar / top status
    def _bar_pieces(self) -> dict:
        """The bar's pieces -> what each shows (a piece fades when that changes; the button cross-fades)."""
        def btn(b):
            return b.isVisible(), b.isEnabled(), b.text()
        st, ds = self.d_stats, self.d_status
        return {
            "button": (self.play_btn, (self.play_btn.text(), self.play_btn.isEnabled())),
            "title": (self.d_title, self.d_title.text()),
            "sync": (self.d_sync, self.d_sync._shown if self.d_sync.isVisibleTo(self) else None),   # (hidden: solo)
            "stats": (st, (st.isVisible(), st.seconds, st.last, st._key)),
            "status": (ds, (self._status_box_shown(), ds.currentIndex(), ds.blank)),
            "saves": (self.saves_btn, btn(self.saves_btn)),
            "edit": (self.edit_btn, btn(self.edit_btn)),
            "bookmark": (self.bookmark_btn, btn(self.bookmark_btn)),
            "trash": (self.trash_btn, btn(self.trash_btn)),
        }

    def _status_box_shown(self) -> bool:
        return self.d_status.isVisible()

    def _in_bar(self, w: QWidget) -> QRect:
        return QRect(w.mapTo(self.details, QPoint(0, 0)), w.size()) if w.isVisible() else QRect()

    def _status_text_rect(self) -> QRect:
        """Inside the status drop-down: just its words (the box itself stays put while they change)."""
        from PySide6.QtWidgets import QStyleOptionComboBox
        d = self.d_status
        opt = QStyleOptionComboBox()
        d.initStyleOption(opt)
        r = d.style().subControlRect(QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxEditField, d)
        return r.translated(d.mapTo(self.details, QPoint(0, 0)))

    def _update_details(self) -> None:
        """Bring the bar up to date; each piece whose content changed is blended by BarFader: the Read button
        cross-fades, every other piece fades out, then in (the status drop-down: only its words)."""
        fade = self.details.isVisible() and not self._resizing and not getattr(self, "_renaming", None) \
            and hasattr(self, "_fader")
        # a language switch: every word in the window changes at once, so the bar does too (owner, 2026-10-18: Edit
        # faded out and back in on its own)
        lang, self._bar_lang = getattr(self, "_bar_lang", None), i18n.language()
        if fade and lang is not None and lang != self._bar_lang:
            fade = False
            self._fader.pieces.clear()
            self._fader.update()
        if not fade:
            self._update_details_now()
            return
        before = {k: (self._in_bar(w), sig) for k, (w, sig) in self._bar_pieces().items()}
        box_before = self._status_box_shown()
        old = self.details.grab()                     # (~1 ms; as it looks now, fades under way included)
        self._update_details_now()
        after = self._bar_pieces()
        changed = [k for k, (w, sig) in after.items() if sig != before[k][1]]
        if not changed:
            return
        self.details.layout().activate()              # (new sizes / places now, not at the next event)
        dpr = old.devicePixelRatio()
        bare = None
        now = time.monotonic()
        if (before["title"][1] == "") != (after["title"][1] == ""):
            # nothing selected <-> a game / folder: the whole bar comes to life (or goes quiet) as ONE - every
            # piece blended at the same moment over the same time (owner: the pieces' own timings made it arrive
            # in steps). Switching between games keeps each piece's own fade.
            self._fader.pieces.clear()               # (the picture already shows any fade under way)
            for k, (w, _sig) in after.items():
                rect = before[k][0] | self._in_bar(w)
                if rect.isValid():
                    self._fader.fade(k, _PieceFade(rect, old, old, now, cross=True, xfade_s=BAR_WAKE_S))
            self._update_anim()
            return
        greys, swapped = [], False                   # swapped: some piece fades out and in
        for k in changed:
            w = after[k][0]
            if k == "button":                         # (cross-fades; timed with the greyed ones below)
                greys.append((k, before[k][0] | self._in_bar(w)))
                continue
            if k == "stats" and before[k][1][0] and after[k][1][0]:
                if before[k][0].x() == self._in_bar(w).x():
                    continue                          # (in place, before and after: StatsLine fades its own values,
                                                      # icons staying put)
                # the line MOVED (the sync words before it changed length: game -> folder...): it fades out and in
                # as a whole like those words - its icons gliding under their fade got cut in half (owner, 2026-10-17)
                w.end_switch()
            if bare is None:                          # the bar with nothing on it (its own background)
                bare = QPixmap(old.size())
                bare.setDevicePixelRatio(dpr)
                bare.fill(Qt.transparent)
                self.details.render(bare, QPoint(), QRegion(), QWidget.RenderFlag.DrawWindowBackground)
            rect = before[k][0] | self._in_bar(w)
            if k == "status" and box_before and self._status_box_shown():
                continue                              # only its words changed (another game): plain, no fade (owner)
            if k in ("saves", "edit", "bookmark", "trash") and before[k][1][::2] == after[k][1][::2]:
                # only greyed / ungreyed: blended smoothly (fading out and back in read as a flicker) - timed below
                greys.append((k, rect))
            elif rect.isValid():
                # (one pace for the whole bar - a quicker one for the name felt flashy, owner 2026-10-17)
                self._fader.fade(k, _PieceFade(rect, old, bare, now, cross=False))
                swapped = True
        for k, rect in greys:
            # the Read button's cross-fade and greyed / ungreyed buttons: with other pieces fading out and in (a game
            # starting / closing: light, stats), the old look holds through their fade-out and blends into the new
            # during their fade-in - everything leaves and arrives together (owner: the trash, then Read / Stop,
            # came early). Alone (just the button changed), at once: a press never feels late.
            if swapped and k == "button":
                # Read <-> Stop with the rest of the bar: its WORD fades out with the others and the new one in with
                # them; the button itself stays. Two words never show at once (a cross-fade's muddy middle), and
                # the whole bar leaves and arrives together (owner, 2026-10-17)
                self._fader.fade(k, _PieceFade(rect, old, self._bar_without_button_label(), now, cross=False))
                continue
            sync = dict(delay=BAR_OUT_S, xfade_s=BAR_IN_S) if swapped else {}
            self._fader.fade(k, _PieceFade(rect, old, old, now, cross=True, **sync))
        self._update_anim()

    def _bar_without_button_label(self) -> QPixmap:
        """The bar as it is now, with the Read button drawn without its word."""
        b = self.play_btn
        fading = self._fader.isVisible()
        self._fader.hide()
        b.blank = True
        try:
            return self.details.grab()
        finally:
            b.blank = False
            if fading:
                self._fader.show()

    def _bar_without_status_words(self) -> QPixmap:
        """The bar as it is now, with the status drop-down drawn as an empty box."""
        d = self.d_status
        fading, was = self._fader.isVisible(), d.blank
        self._fader.hide()
        d.blank = True
        try:
            return self.details.grab()
        finally:
            d.blank = was
            if fading:
                self._fader.show()

    def _update_details_now(self) -> None:
        self.d_title.clickable = self._rename_target() is not None
        if not self.d_title.clickable:
            self.d_title.setCursor(Qt.ArrowCursor)
        fid = None if self._bar_shows_running() else self.selected_folder()
        if fid:
            n = len(self.folder_games.get(fid, []))
            self.d_title.setText(self.folder_name(fid))
            count = tr("folder.count_one") if n == 1 else tr("folder.count_many", n=n)
            self.d_sync.set_status(None, count, muted=True)
            self.d_sync.setToolTip("")
            gs = self.folder_games.get(fid, [])
            self._show_stats(self.settings.show_stats and n > 0, sum(g.total_playtime for g in gs),
                             max((g.last_played for g in gs), default=0), gs)
            self.play_btn.setText(tr("bar.open"))
            self.play_btn.setEnabled(True)
            self.play_btn.setToolTip("")
            self.edit_btn.setText(tr("bar.rename"))
            self.edit_btn.setEnabled(True)
            self.trash_btn.setEnabled(False)          # folders aren't removed this way
            self.saves_btn.hide()
            self.d_status.setGraphicsEffect(None)
            self.d_status.hide()
            return
        self.saves_btn.show()
        self.edit_btn.setText(tr("bar.edit"))
        g = self.bar_game()
        busy = self.worker is not None
        for w in (self.play_btn, self.edit_btn, self.saves_btn, self.d_status):
            w.setEnabled(g is not None)
        self.trash_btn.setEnabled(g is not None and not busy)
        if g is None:                               # nothing selected: an empty bar, buttons greyed out
            self.d_stats.set_shown(False)
            self.d_title.setText("")
            self.d_sync.set_status(None, "")
            self.d_sync.setToolTip("")
            self.play_btn.setText(tr("bar.play"))
            self.play_btn.setToolTip("")
            self.d_status.setGraphicsEffect(None)
            self.d_status.setVisible(True)
            if self.d_status.currentIndex() != 0 and not self.d_status.view().isVisible():
                self.d_status.blockSignals(True)       # (greyed, showing no game's status - not the last one's)
                self.d_status.setCurrentIndex(0)
                self.d_status.blockSignals(False)
            return
        self.d_title.setText(g.title)
        colour_key, _, text_key = STATE_LOOK[self.light_state(g.game_id)]
        text_key = self.solo_key(text_key, g)
        quiet = self.quiet(g.game_id)
        self.d_sync.set_status(colour_key, tr(text_key))
        self._show_stats(self.settings.show_stats and not quiet, g.total_playtime, g.last_played, [g])
        tip = self.solo_key(STATE_LOOK[self.light_state(g.game_id)][2].replace("sync.state.", "sync.tip."), g)
        self.d_sync.setToolTip(tr(tip) if tip in i18n.S else "")
        # leave the drop-down alone while its list is open (a background refresh mustn't redraw or close it)
        if not self.d_status.view().isVisible():
            self._show_status(True)                 # (owner: it keeps showing the status while reading too)
            want = max(0, self.d_status.findData(g.status))
            if self.d_status.currentIndex() != want:
                self.d_status.blockSignals(True)
                self.d_status.setCurrentIndex(want)
                self.d_status.blockSignals(False)

        mine = g.paths_for(self.settings.machine_name)
        ready = bool(mine.exe)
        running = self._running(g.game_id)
        if g.game_id == self.playing_id:            # in a session: mirrors it (see _session_look)
            self.play_btn.setText(tr(self._session_look(g.game_id)[0]))
            # never greyed for the moments around a session (it flickered): pressing Read then just does nothing
            self.play_btn.setEnabled(not self.stop_pending)
        else:
            self.play_btn.setText(tr("bar.play"))
            self.play_btn.setEnabled(ready and not busy)
        self.saves_btn.setText(tr(self._folder_key()))
        folder = self._bar_folder(g)
        self.saves_btn.setEnabled(bool(folder) and Path(folder).is_dir())
        self.play_btn.setToolTip(tr("bar.tip_stop") if running else
                                 tr("bar.tip_busy") if busy else ("" if ready else tr("bar.tip_no_exe")))

    def _rename_target(self) -> Optional[tuple]:
        fid = None if self._bar_shows_running() else self.selected_folder()
        if fid:
            return ("folder", fid)
        g = self.bar_game()
        return ("game", g.game_id) if g else None

    def _start_rename(self) -> None:
        target = self._rename_target()
        if target is None or self._renaming:
            return
        self._renaming = target
        e = self.d_title_edit
        e.setText(self.folder_name(target[1]) if target[0] == "folder" else self.games[target[1]].title)
        self.d_title.blank = True
        self.d_title.update()
        self._place_title_edit()
        e.show()
        e.raise_()
        e.setFocus()
        e.selectAll()
        QApplication.instance().installEventFilter(self._click_away)   # a click anywhere else saves

    def _place_title_edit(self) -> None:
        """The box sits round the name with the usual text-box gaps, the text exactly where the name was;
        as wide as what's typed (at least a little), never past the drop-down."""
        e = self.d_title_edit
        if not self._renaming:
            return
        pad_x = 3                                    # border 1 + Qt's own 2: text lands where the name was, and the
                                                     # space beside it matches the space above / below (owner)
        lab = self.d_title.geometry()                # (in the bar's coordinates: the bar is its parent)
        top_left = lab.topLeft()
        h = e.sizeHint().height()                    # (padding 2 above / 1 below: clear of the line beneath)
        text_w = e.fontMetrics().horizontalAdvance(e.text() or " ") + 2      # (+ room for the cursor)
        right_edge = (self.d_status.geometry().left() if self.d_status.isVisible()
                      else self.edit_btn.geometry().left()) - 12
        x = top_left.x() - pad_x
        w = max(40, min(text_w + 2 * pad_x, right_edge - x))
        e.setGeometry(x, top_left.y() + lab.height() + 3 - h, w, h)   # text level with the name (measured, to the pixel)

    def _finish_rename(self, save: bool) -> None:
        """Enter / clicking elsewhere: save; Esc: back as it was. A game's typed title is kept as its own
        (locked, like a title typed in Edit); an empty name or the same name changes nothing."""
        if not self._renaming:
            return
        (kind, iid), self._renaming = self._renaming, None
        QApplication.instance().removeEventFilter(self._click_away)
        text = self.d_title_edit.text().strip()
        if self.d_title_edit.hasFocus():
            self.grid.setFocus()                    # (not the Read button: a later Enter / Space would start the game)
        self.d_title_edit.hide()
        self.d_title.blank = False
        self.d_title.update()
        if not save or not text:
            return
        try:
            if kind == "folder":
                if text != self.folder_name(iid):
                    self.library.rename_folder(iid, text)
                    self.refresh()
            else:
                g = self.library.get(iid)
                if g and text != g.title:
                    self.library.update(iid, title=text, title_locked=True, manual_title=text)
                    self.refresh()
        except (ValueError, KeyError, OSError) as e:
            report_failure(self, e)

    def _show_status(self, on: bool) -> None:
        """The reading-status drop-down is hidden while a game runs and back after, keeping its place so nothing in
        the bar moves (the bar's one cross-fade, BarFader, blends it out / in). Hidden = an opacity effect at 0,
        attached only then: Qt draws text under one a little softer."""
        # owner, 2026-10-17: the box itself stays (looking as always); only its words go, and it ignores the mouse
        d = self.d_status
        d.show()
        if d.blank == (not on):
            return
        d.blank = not on
        d.setAttribute(Qt.WA_TransparentForMouseEvents, not on)
        d.setFocusPolicy(Qt.StrongFocus if on else Qt.NoFocus)
        d.update()

    def _show_stats(self, want: bool, seconds: float, last: float, games: list) -> None:
        """Time read + last read after the sync status (what doesn't fit is left off the end: StatsLine).
        Pointing at the time shows it per device."""
        if not want:
            self.d_stats.set_shown(False)           # (e.g. a game starting)
            return
        me, other = self.settings.machine_name, other_slot(self.settings.machine_name)
        per = [(tr("game.this_device"), sum(g.playtime.get(me, 0) for g in games)),
               (tr("game.other_device"), sum(g.playtime.get(other, 0) for g in games))]

        def last_on(machine: str) -> Optional[float]:      # (a folder: its latest; see Game.last_played_on)
            when = [g.last_played_on(machine) for g in games]
            known = [t for t in when if t]
            return max(known) if known else (None if None in when else 0.0)

        per_last = [(tr("game.this_device"), last_on(me)), (tr("game.other_device"), last_on(other))]
        self.d_stats.set_values(seconds, last, per, key=tuple(g.game_id for g in games), per_last=per_last)
        if self._count_up and time.monotonic() - self._count_up[2] > COUNT_UP_WAIT_S:
            self._count_up = None                           # (that game wasn't shown after its session)
        # (only once the new total has arrived: the bar is updated the moment a game closes, but the new playtime
        # comes with the background re-read a little later - starting then found nothing to count and dropped it)
        if self._count_up and len(games) == 1 and games[0].game_id == self._count_up[0] \
                and seconds > self._count_up[1]:
            # a session just ended: the time rolls up - once the bar's cross-fade has finished and it has sat still
            # a moment (owner: starting with the fade, a short session's roll was nearly over before it could be seen)
            self.d_stats.count_up(self._count_up[1], delay=BAR_OUT_S + BAR_IN_S + COUNT_UP_SETTLE_S)
            self._count_up = None
        self.d_stats.set_shown(True)                        # (e.g. a game just closed)

    _resizing = False

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._resizing = True           # (no cross-fade while the window is being resized)
        try:
            self._update_details()      # re-check whether the playtime still fits
        finally:
            self._resizing = False
        self._place_title_edit()        # (renaming: the box follows the name)

    def _update_sync_label(self) -> None:
        """The one-line status in the top bar. Details (device, cloud folder) live in Settings."""
        checking = self.checker is not None and self.checker.isRunning() and not self._quiet_check
        conflicts = sum(1 for s in self.sync_states.values() if s == "conflict")
        tip = ""
        if self.play_phase == "syncing" or checking:
            text = tr("top.syncing")
            self.sync_light.set_state("blue", "busy")
        elif conflicts:
            label = tr("top.attention_one") if conflicts == 1 else tr("top.attention_many", n=conflicts)
            text = (f'<a href="conflict" style="color:{COLORS["text"]}; text-decoration:none">'
                    f"{label}</a>")
            mixed = any(self.only_here(self.games.get(gid)) or self.first_sync(self.games.get(gid))
                        for gid, s in self.sync_states.items() if s == "conflict")
            tip = tr("top.attention_tip_mixed" if mixed else "top.attention_tip")
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
        if self._filtering() or self._device_filter() == "other":   # (they're all set up here)
            was = self.filter.painted
            self.settings.status_filter = "all"
            if self.settings.device_filter == "other":
                self.settings.device_filter = "here"
            self._mark_filter()
            self._save_view_choice()
            self._fade_top_words(self.filter, was, self._apply_filter)
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
        if obj is self.d_title_edit and t in (QEvent.ShortcutOverride, QEvent.KeyPress):
            # typing a name: Enter / Backspace / Esc belong to the box, not to the window's shortcuts
            # (open the game, leave the folder); Esc here cancels the rename
            if t == QEvent.ShortcutOverride and ev.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter,
                                                             Qt.Key_Backspace):
                ev.accept()
                return False
            if t == QEvent.KeyPress and ev.key() == Qt.Key_Escape:
                self._finish_rename(False)
                return True
            if t == QEvent.KeyPress and ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._finish_rename(True)           # saves, and the key stops here (it used to start the game)
                return True
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
                self._press = (ev.position().toPoint(), iid) if iid else None   # (folders: to rearrange)
                if it is None:                      # clicked the background, not a cover
                    self.clear_selection()
                    return True
            elif t == QEvent.MouseButtonDblClick and self.grid.itemAt(ev.position().toPoint()) is None:
                return True                         # (Qt treats it as a fresh click, and selected the cell's game)
            elif t == QEvent.MouseButtonRelease:
                self._press = None
                if self._ghost is not None:                     # let go of a dragged game
                    self._end_game_drag(ev.globalPosition().toPoint(), drop=True)
                    return True
            elif t == QEvent.MouseMove:
                pos = ev.position().toPoint()
                if self._ghost is not None:                     # dragging a game (the grid keeps the mouse)
                    self._drag_moved = True                     # (handled once a frame: _drag_autoscroll)
                    return True
                if self._press and ev.buttons() & Qt.LeftButton and self.worker is None and \
                        (pos - self._press[0]).manhattanLength() >= QApplication.startDragDistance():
                    gid = self._press[1]
                    self._press = None
                    self._start_game_drag(gid)
                    return True
                # (a held button keeps the mouse past the grid's edge: over the bottom bar, the row hidden under it lifted)
                it = self.grid.itemAt(pos) if self.grid.viewport().rect().contains(pos) else None
                if self.grid.auto_scrolling():
                    it = None                       # (the mouse steers the scrolling: no cover lifts meanwhile)
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

    def _fit_grid(self) -> None:
        """Covers grow with the window (Steam style): as many columns as fit at the smallest cover size,
        then the covers stretch so the row is filled exactly, with the same gap at the left and right
        edges as between covers."""
        g, sb = self.grid, self.grid.verticalScrollBar()
        # Qt's grid always keeps room for a scroll bar (shown or not) plus a pixel before it starts a new
        # column; that room is taken out of the right-hand edge, where the scroll bar appears anyway
        reserve = g.style().pixelMetric(QStyle.PM_ScrollBarExtent, None, sb) + 1
        edge = max(GRID_GAP // 2, reserve)                  # (each cover's own inset is the other half)
        inner = g.width() - 2 * g.frameWidth()
        avail = inner - 2 * edge
        if avail <= 0:
            return
        cols = max(1, int(avail // (COVER_W + GRID_GAP)))
        cell = int(avail // cols)
        cover_w = min(2.0 * COVER_W, cell - GRID_GAP)
        cover_h = cover_w * COVER_H / COVER_W
        side = inner - cols * cell                          # both edges together
        left = side // 2
        right = side - left - reserve
        size = QSize(cell, round(cover_h) + CARD_PAD_TOP + CARD_PAD_BOTTOM + GRID_SPACING)
        if (GRID["cover_w"], GRID["cover_h"]) != (cover_w, cover_h) or g.gridSize() != size:
            GRID["cover_w"], GRID["cover_h"] = cover_w, cover_h
            g.setGridSize(size)
        m = g.viewportMargins()
        if (m.left(), m.right()) != (left, right):
            g.setViewportMargins(left, 0, right, 0)     # covers scroll right up to the bars
            g.doItemsLayout()                   # (new margins alone don't make the view lay out again)

    def _start_game_drag(self, gid: str) -> None:
        if is_folder(gid):
            self._start_folder_drag(gid)
            return
        g = self.games.get(gid)
        if g is None:
            return
        # a copy of the cover follows the mouse (see-through: DragGhost sets the opacity); full size, so it's
        # sharp when it lifts out of and lands back in the grid
        dpr = self.devicePixelRatioF()
        w, h = round(GRID["cover_w"]), round(GRID["cover_h"])
        img = QPixmap(round(w * dpr), round(h * dpr))
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), 8, 8)
        p.setClipPath(clip)
        pm = self.covers.get(self.art(g), (g.crop_zoom, g.crop_x, g.crop_y))
        if pm:
            p.drawPixmap(QRectF(0, 0, w, h), pm, QRectF(pm.rect()))
        else:
            grad = QLinearGradient(0, 0, 0, h)
            grad.setColorAt(0, QColor(COLORS["placeholder_top"]))
            grad.setColorAt(1, QColor(COLORS["placeholder_bottom"]))
            p.fillRect(QRectF(0, 0, w, h), grad)
        p.end()
        self._begin_drag(gid, img)

    def _cover_global(self, iid: str) -> Optional[QRectF]:
        """Where an item's cover is on screen (None when it isn't in view)."""
        for i in range(self.grid.count()):
            it = self.grid.item(i)
            if it.data(GAME_ID_ROLE) == iid:
                r = self.grid.visualItemRect(it)
                if not r.intersects(self.grid.viewport().rect()):
                    return None
                c = CardDelegate.cover_rect(r, self.hover.get(iid, 0.0))
                o = self.grid.viewport().mapToGlobal(QPoint(0, 0))
                return c.translated(o.x(), o.y())
        return None

    def _begin_drag(self, gid: str, img: QPixmap) -> None:
        if self._landing is not None:
            self._landing.finish()                      # the last drop is still gliding: put it down now
        start = self._cover_global(gid)
        if start is None:
            size = QSizeF(img.width() / img.devicePixelRatio(), img.height() / img.devicePixelRatio())
            start = QRectF(QPointF(QCursor.pos()) - QPointF(size.width() / 2, size.height() / 2), size)
        self._dragging = gid
        self._dim_gid = gid
        self._drag_racing = False
        self._drag_scroll_v, self._drag_scroll_a, self._drag_scroll_pos, self._drag_scroll_n = 0.0, 0.0, None, 0
        self._drag_scroll_t = frame_time()
        self._drag_moved = False
        self._ghost = DragGhost(img, start)
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
        self._drag_scroll = VsyncClock(self)   # (once per screen refresh, at any rate)
        self._drag_scroll.setInterval(16)
        self._drag_scroll.timeout.connect(self._drag_autoscroll)
        self._drag_scroll.start()

    def _drag_autoscroll(self) -> None:
        """While a game is dragged near the top or bottom of the grid (or past it), the library scrolls:
        slowly at the start of the zone, faster the deeper you go, fastest well beyond the edge."""
        # placed by when the frame reaches the screen (frame_time), not by when this step happens to run, from a
        # float position, with the speed eased in and out: whole pixels from uneven step times jerked along
        # (owner, 2026-10-21: make it as smooth as the wheel). It counts as a glide, so the strips draw it.
        now = frame_time()
        dt = min(0.05, max(0.0, now - getattr(self, "_drag_scroll_t", now - 1 / 60)))
        self._drag_scroll_t = now
        if self._ghost is None:
            return
        gp = QCursor.pos()
        vp = self.grid.viewport()
        top_left = vp.mapToGlobal(QPoint(0, 0))
        y = gp.y() - top_left.y()
        target = 0.0                                 # px per 1/60 s, + down
        kind = self._drag_hit(gp, aim=False)[0]
        if kind in ("trash", "back"):
            pass                                     # aiming at a button, not scrolling
        elif not (top_left.x() - 40 <= gp.x() <= top_left.x() + vp.width() + 40):
            pass
        elif y < DRAG_SCROLL_ZONE or y > vp.height() - DRAG_SCROLL_ZONE:
            up = y < DRAG_SCROLL_ZONE
            depth = ((DRAG_SCROLL_ZONE - y) if up else (y - vp.height() + DRAG_SCROLL_ZONE)) / DRAG_SCROLL_ZONE
            # inside the zone: depth 0..1 (gentle start); past the edge it keeps growing up to 3
            depth = min(depth, 3.0)
            speed = DRAG_SCROLL_MIN + (DRAG_SCROLL_MAX - DRAG_SCROLL_MIN) * (depth / 3.0) ** 1.6
            # (never under a whole pixel a refresh: below it, some refreshes move and some don't - a stutter)
            target = (-1 if up else 1) * max(speed, 1 / (60 * frame_period()))
        g, sb = self.grid, self.grid.verticalScrollBar()
        pos = starting = getattr(self, "_drag_scroll_pos", None)
        if pos is None or abs(pos - sb.value()) >= 1:
            pos = float(sb.value())                  # (just starting, or something else moved the grid)
        # the speed follows the pointer's like a critically damped spring: its acceleration builds up and dies away
        # smoothly too (a plain ease jumped straight to full acceleration - owner, 2026-10-24: it lurched into motion)
        v, acc = getattr(self, "_drag_scroll_v", 0.0), getattr(self, "_drag_scroll_a", 0.0)
        k = 2.0 / DRAG_SCROLL_EASE_S
        for _ in range(max(1, math.ceil(dt / 0.004))):           # (small steps: stable at any frame time)
            h = dt / max(1, math.ceil(dt / 0.004))
            acc += (k * k * (target - v) - 2 * k * acc) * h
            v += acc * h
        # and it brakes into the end of the library like the wheel does, instead of slamming into it (a short
        # library is mostly ends: the owner's scrolls ~650 px)
        room = (sb.maximum() - pos) if (v or target) > 0 else (pos - sb.minimum())
        cap = math.sqrt(2 * DRAG_SCROLL_BRAKE * max(0.0, room)) / 60          # (px per 1/60 s)
        if abs(v) > cap:
            v, acc = math.copysign(cap, v), 0.0
        self._drag_scroll_a = acc
        # a whole number of pixels every refresh, kept until the speed clearly leaves it: a speed like 1.4 px a
        # refresh, rounded each time, went 1, 2, 1, 1, 2 - the judder of a slow drag (owner, 2026-10-24). The wheel
        # rarely sits at such speeds; this holds them at an even 1 or 2.
        frames = max(1, round(dt / frame_period())) if dt > 0 else 0
        per = v * frame_period() * 60                                  # (px a refresh)
        n = getattr(self, "_drag_scroll_n", 0)
        if abs(per - n) > 0.7:
            n = round(per)
        self._drag_scroll_n = n
        new = min(float(sb.maximum()), max(float(sb.minimum()), round(pos) + n * frames))
        # the mouse's moves are handled here, once a frame (the cover's window placed, what's under it worked out): a
        # mouse reports hundreds a second, ~1 ms each, and handled as they came they piled up into late frames
        # whenever the hand moved (dragbench 2026-10-24: a still pointer 3 late frames in 3 s, a moving one 8+)
        moved, self._drag_moved = self._drag_moved, False
        if (target == 0 and abs(v) < 0.1) or (n * frames and new == round(pos)):
            self._drag_scroll_v, self._drag_scroll_a, self._drag_scroll_pos = 0.0, 0.0, None   # stopped / at the end:
            self._drag_scroll_n = 0                                                           # back to normal drawing
            if starting is not None and g._glide_to is not None:     # (ours: a wheel glide is left alone)
                g._heading = 0
                g._end_glide()
            if self._drag_racing:
                self._drag_racing, moved = False, True
            if moved:
                self._move_game_drag(gp)
            return
        self._drag_scroll_v, self._drag_scroll_pos = v, new
        if starting is None:                         # (like wheel-press scrolling: _AutoScroll._begin)
            g._end_glide()                           # (a wheel glide running: this takes over)
            g._glide_clock_ready()
            g._rehover_timer().stop()
        g._heading = 1 if v > 0 else -1
        g._glide_to = new
        before = sb.value()
        g._gliding = True
        try:
            sb.setValue(round(new))
        finally:
            g._gliding = False
        if sb.value() != before and g.layer_active():
            # drawn NOW, before the mouse's work below (that only matters from the next frame on): drawn after it, a
            # frame often finished just past the refresh and showed one late - filmed 2026-10-24, the screen showed
            # steps of 4, 13, 8, 9, 4, 13 px while the app drew every frame on time (the wheel: an even 11-12)
            g.viewport().repaint()
        # no aiming while it scrolls, at any speed (owner, 2026-10-24): the cover under the pointer lifting and the drop
        # line moving every frame were the drag's only difference from the wheel - measured: without them 0-1 uneven
        # steps in 3 s, as the wheel; with them 21-31. They come back once the scroll has eased to a stop.
        racing = True
        if moved or racing != self._drag_racing or (sb.value() != before and not racing):
            self._drag_racing = racing               # what's under the mouse changed (racing: nothing to aim at)
            self._move_game_drag(gp, (kind, None) if racing or kind != "grid" else None)

    def _start_folder_drag(self, iid: str) -> None:
        """A folder picked up (only to rearrange it): its tile, small, follows the mouse."""
        if self.current_folder or iid[len(FOLDER_PREFIX):] not in self.folder_games:
            return
        r = None
        for i in range(self.grid.count()):
            if self.grid.item(i).data(GAME_ID_ROLE) == iid:
                r = CardDelegate.cover_rect(self.grid.visualItemRect(self.grid.item(i)), 0)
        if r is None:
            return
        self.grid.bare = True
        try:
            shot = self.grid.viewport().grab(r.toAlignedRect())
        finally:
            self.grid.bare = False
        self._begin_drag(iid, shot)

    def _check_drag_button(self) -> None:
        if self._ghost is not None and not (QApplication.mouseButtons() & Qt.LeftButton):
            self._end_game_drag(QCursor.pos(), drop=False)

    def _drag_hit(self, gp: QPoint, aim: bool = True) -> tuple:
        """What a dragged game is over: ("trash", None), ("back", None), ("grid", target or None) or (None, None).
        aim=False: the grid's spot isn't worked out (rows racing past: nothing is shown to aim at)."""
        def over(w) -> bool:
            return w.isVisible() and w.rect().contains(w.mapFromGlobal(gp))
        folder = is_folder(self._dragging)
        if over(self.trash_btn) and self.worker is None and not folder:
            return "trash", None
        if self.current_folder and over(self.back_btn) and not folder:
            return "back", None
        vp = self.grid.viewport()
        if over(vp):
            return "grid", self._drop_spot(vp.mapFromGlobal(gp)) if aim else None
        return None, None

    def _drop_spot(self, pos) -> Optional[tuple]:
        """Where a dragged cover would land at pos: ("onto", item) - the middle of a cover: make a folder /
        add to it; or ("between", index, marker) - the gap or edge between two covers: move it there.
        None: nowhere (e.g. right next to where it already is)."""
        dragged = self._dragging
        visible = []
        view = self.grid.viewport().rect()
        for i in self.grid.visible_range():
            r = self.grid.visualItemRect(self.grid.item(i))
            if r.intersects(view):
                visible.append((i, self.grid.item(i).data(GAME_ID_ROLE), CardDelegate.cover_rect(r, 0)))
        if not visible:
            return None
        # the row under the mouse (or the nearest one), then the cover nearest to it in that row
        def row_gap(c: QRectF) -> float:
            return 0 if c.top() - CARD_PAD_TOP <= pos.y() <= c.bottom() + CARD_PAD_BOTTOM else \
                min(abs(pos.y() - c.top()), abs(pos.y() - c.bottom()))
        best = min(row_gap(c) for _, _, c in visible)
        row = [v for v in visible if row_gap(v[2]) == best]
        i, iid, c = min(row, key=lambda v: abs(pos.x() - v[2].center().x()))
        inside_cover = c.contains(QPointF(pos)) and c.left() + c.width() * 0.2 <= pos.x() <= c.right() - c.width() * 0.2
        if inside_cover and iid != dragged and not is_folder(dragged) and not self.current_folder:
            return ("onto", iid)
        flat = bool(self.search.text().strip()) or self._filtering()
        if flat or (inside_cover and iid == dragged):
            return None                                  # (searching / filtering: nothing to rearrange)
        before = pos.x() < c.center().x()
        index = i if before else i + 1
        mine = next((k for k in range(self.grid.count()) if self.grid.item(k).data(GAME_ID_ROLE) == dragged), -1)
        if index in (mine, mine + 1):
            return None                                  # it would stay where it is
        x = c.left() - GRID_GAP / 2 if before else c.right() + GRID_GAP / 2
        return ("between", index, QRectF(x - 2, c.top() + 6, 4, c.height() - 12))

    _drag_racing = False      # the edge scroll is going fast: rows race past, nothing to aim at (no line / lift)
    _drag_moved = False       # the mouse moved since the last frame (dragging a game: handled once a frame)

    def _move_game_drag(self, gp: QPoint, hit: Optional[tuple] = None) -> None:
        self._ghost.follow(gp)
        kind, spot = hit or self._drag_hit(gp, aim=not self._drag_racing)
        if self._drag_racing and kind == "grid":
            # (owner, 2026-10-19: the drop line "flickers like crazy" - with the rows racing past a still pointer it
            # switched on over every gap and off over every cover, and the covers under it lifted and dropped)
            spot = None
        self.trash_btn.set_hot(kind == "trash")
        self._ghost.set_faded(kind == "trash")          # almost see-through over the trash can
        onto = spot[1] if kind == "grid" and spot and spot[0] == "onto" else None
        self._set_hovered(onto)                          # the target lifts, so you can see where it'll go
        self.grid.set_marker(spot[2] if kind == "grid" and spot and spot[0] == "between" else None)

    def _end_game_drag(self, gp: QPoint, drop: bool) -> None:
        gid = self._dragging
        # (let go while the rows race past: nothing was shown to aim at - it goes back where it was; scrolling slowly,
        # it lands under the pointer as usual)
        fast = self._drag_racing and abs(getattr(self, "_drag_scroll_v", 0.0)) > DRAG_AIM_SPEED
        kind, spot = self._drag_hit(gp) if drop and not fast else (None, None)
        self._drag_racing = False
        self.grid.set_marker(None)
        if self._ghost is None:
            return
        self._drag_watch.stop()
        self._drag_scroll.stop()
        if getattr(self, "_drag_scroll_pos", None) is not None:   # (dropped mid edge-scroll: it stops there)
            self._drag_scroll_v, self._drag_scroll_a, self._drag_scroll_pos, self._drag_scroll_n = 0.0, 0.0, None, 0
            self.grid._heading = 0
            self.grid._end_glide()
        QApplication.instance().removeEventFilter(self._esc)
        ghost, self._ghost = self._ghost, None
        self._landing = ghost
        self._dragging = None
        QApplication.restoreOverrideCursor()
        self.trash_btn.set_hot(False)
        self._set_hovered(None)
        before = self._positions()

        def shrink_into(rect: Optional[QRectF], scale: float = 0.3) -> Optional[QRectF]:
            if rect is None:
                return None
            c = rect.center()
            w, h = ghost.full.width() * ghost.SMALL * scale, ghost.full.height() * ghost.SMALL * scale
            return QRectF(c.x() - w / 2, c.y() - h / 2, w, h)

        def widget_rect(w: QWidget) -> QRectF:
            return QRectF(QPointF(w.mapToGlobal(QPoint(0, 0))), QSizeF(w.size()))

        to, end_opacity = None, 1.0
        if gid and kind == "trash":
            to, end_opacity = shrink_into(widget_rect(self.trash_btn)), 0.0
            QTimer.singleShot(0, lambda: self.remove_game(gid))
        elif gid and kind == "back":
            self._drop_game(gid, None, True)
            to, end_opacity = shrink_into(widget_rect(self.back_btn)), 0.0
        elif gid and spot and spot[0] == "onto":
            self._drop_game(gid, spot[1], False)
            g = self.games.get(gid)
            home = FOLDER_PREFIX + g.folder if g is not None and g.folder else spot[1]
            to, end_opacity = shrink_into(self._cover_global(home)), 0.0
        elif gid and spot and spot[0] == "between":
            self._move_to(gid, spot[1])
            to = self._cover_global(gid)
        else:
            to = self._cover_global(gid) if gid else None      # nothing happened: back home
        self._slide_from(before, skip=gid)
        if to is None:                                       # (off screen) just fade where it is
            to, end_opacity = shrink_into(ghost.current(), 2.0), 0.0
        ghost.land(to, end_opacity, self._landed)

    def _landed(self) -> None:
        """The dropped cover has arrived: the real one takes over (no fade: they're in the same place). Drawn
        right now, before the landing cover goes."""
        self._landing = None
        self.drag_dim = 0.0
        self._dim_gid = None
        self.grid.viewport().repaint()
        self._update_anim()

    # ------------------------------------------------------------------ covers sliding into new places
    def _positions(self) -> dict:
        """Where each cover is drawn now (mid-glide too, so a new change carries on from there, no jump)."""
        out = {}
        for i in range(self.grid.count()):
            iid = self.grid.item(i).data(GAME_ID_ROLE)
            pos = self.grid.visualItemRect(self.grid.item(i)).topLeft()
            off = self.slide_offset(iid)
            out[iid] = pos + off if off is not None else pos
        return out

    def _slide_from(self, before: dict, skip: Optional[str] = None, appear: bool = False,
                    restyled: frozenset = frozenset()) -> set:
        """After the grid changed: every cover that moved slides from where it was to its new place.
        appear (search / filter): nothing slides. Covers that weren't there before, and covers whose place
        changed (or whose picture did: restyled, a folder's mosaic), arrive (fade in) at their place one after
        another, after the covers leaving have faded out; returns the ones whose place or picture changed (the
        caller fades them out where they were)."""
        self.grid.doItemsLayout()
        now = {self.grid.item(i).data(GAME_ID_ROLE): self.grid.visualItemRect(self.grid.item(i)).topLeft()
               for i in range(self.grid.count())}             # (the new places themselves)
        t = time.monotonic()
        moved: set = set()
        if appear:
            self.slides = {}
            arriving = []
            for iid, p in now.items():
                old = before.get(iid)
                if old is None:
                    arriving.append(iid)
                elif old != p or iid in restyled:
                    moved.add(iid)
                    arriving.append(iid)
            # still arriving in the same place from an earlier change: carry on; the rest queue up in grid order
            keep = {iid: d - (t - self._appear_t0) for iid, d in self.appearing.items()
                    if iid in now and iid not in moved and t - self._appear_t0 - d < APPEAR_S}
            fresh = [iid for iid in arriving if iid not in keep]
            leaving = bool(moved) or any(iid not in now for iid in before)
            base = ARRIVE_WAIT if leaving else 0.0
            if self.no_results > 0:                      # "No Results Found" fades out first
                base = max(base, self.no_results * NO_RESULTS_OUT_S + 0.02)
            step = min(APPEAR_STAGGER, APPEAR_STAGGER_MAX / max(1, len(fresh) - 1))
            self.appearing = {**keep, **{iid: base + i * step for i, iid in enumerate(fresh)}}
            self._appear_t0 = t
            self.arrive_wait = base                      # (the top drop-downs' words come in with the first cover...
            self.arrive_span = step * max(0, len(fresh) - 1)   # ...and settle with the last)
        else:
            self.slides = {}
            for iid, p in now.items():
                old = before.get(iid)
                if old is not None and iid != skip and old != p:
                    self.slides[iid] = (old.x() - p.x(), old.y() - p.y())
            if self.slides:
                self._slide_t0 = t
        self.grid.viewport().update()
        self._update_anim()
        return moved

    def appear_fade(self, iid: str) -> float:
        """How much of the background still covers a cover that's arriving (1 = hidden, 0 = fully there)."""
        d = self.appearing.get(iid)
        if d is None:
            return 0.0
        t = (time.monotonic() - self._appear_t0 - d) / APPEAR_S
        if t <= 0:
            return 1.0
        return 0.0 if t >= 1 else (1 - t) ** 3          # ease out: there at once, settling gently

    def _hold_blocked_arrivals(self, now: float) -> None:
        """A cover that hasn't started arriving waits while a leaving cover still shows in its place (no
        cross-fade of two pictures in one spot), and all of them wait while "No Results Found" fades out;
        their turn moves on until the way is clear."""
        if self.no_results > 0 and self.appearing:
            t = now - self._appear_t0
            for iid, d in self.appearing.items():
                if t - 0.005 <= d < t + 0.02:            # (not started yet; ones already showing carry on)
                    self.appearing[iid] = t + 0.02       # (a little ahead: not even a frame of it shows)
            return
        ghosts = []
        for _, rects, opacity, scale, shift in self.leaving_frame():
            if opacity < ARRIVE_CLEAR:
                continue
            for r in rects:
                g = QRectF(r.translated(shift))
                c = g.center()
                g.setSize(g.size() * scale)
                g.moveCenter(c)
                ghosts.append(g)
        if not ghosts or not self.appearing:
            return
        t = now - self._appear_t0
        for i in self.grid.visible_range():
            it = self.grid.item(i)
            iid = it.data(GAME_ID_ROLE)
            d = self.appearing.get(iid)
            if d is None or t - d > 0.02:
                continue                                 # (not arriving, or already showing)
            spot = CardDelegate.cover_rect(self.grid.visualItemRect(it), 0.0)
            if any(spot.intersects(g) for g in ghosts):
                self.appearing[iid] = t

    def slide_offset(self, iid: str) -> Optional[QPoint]:
        d = self.slides.get(iid)
        if d is None:
            return None
        t = (time.monotonic() - self._slide_t0) / SLIDE_S
        if t >= 1:
            return None
        k = (1 - t) ** 2.2                               # ease out: quick start, gentle landing
        return QPoint(round(d[0] * k), round(d[1] * k))

    @guarded
    def _move_to(self, iid: str, index: int) -> None:
        """A cover dropped between two others: that's its place now, in this view's own order. Not sorted by
        Custom yet? Then it switches to Custom, starting from the order you were looking at."""
        ids = [self.grid.item(i).data(GAME_ID_ROLE) for i in range(self.grid.count())]
        if iid not in ids:
            return
        old = ids.index(iid)
        ids.pop(old)
        ids.insert(index - 1 if index > old else index, iid)
        switched = False
        if self.current_folder:
            fid = self.current_folder
            ids = merge_order(self.folder_meta.get(fid, {}).get("order", []) +
                              [g.game_id for g in self.games.values() if g.folder == fid], ids)
            self.library.set_folder_order(fid, ids)
            meta = self.folder_meta.setdefault(fid, {"sort": "", "order": []})
            meta["order"] = list(ids)
            if meta.get("sort") != "custom":             # its own My Arrangement (even if "Same as Library"
                self.library.set_folder_sort(fid, "custom")   # was already showing the library's)
                meta["sort"] = "custom"
                switched = True
        else:
            ids = merge_order(self.library_order + self._all_top_ids(), ids)
            self.library.set_library_order(ids)
            self.library_order = list(ids)
            if self.settings.sort != "custom":           # My Arrangement, starting from what was on screen
                self.settings.last_sort = self.settings.sort   # (its folder options use the sort you came from)
                self.settings.sort = "custom"
                switched = True
                self._save_view_choice()
        self._mark_sort()
        self._rebuild()
        self._own_change()
        self._select_item(iid)
        if switched:
            self.statusBar().showMessage(i18n.line("status.now_custom"), 8000)

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
            spot = self._drop_spot(ev.position().toPoint())
            target = spot[1] if spot and spot[0] == "onto" else None
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
            self._reshow((gid,))
            return
        if is_folder(target):
            fid = target[len(FOLDER_PREFIX):]
            self.library.set_folder(gid, fid)
            self._reshow((gid,))
            self._select_item(target)
            return
        other = self.games.get(target)
        if other is None or self.library.get(target) is None:
            return
        if other.folder:                        # (while searching) joining that game's folder
            self.library.set_folder(gid, other.folder)
            self._reshow((gid,))
            return
        fid = self.library.create_folder(tr("folder.new_name"))
        self.library.set_folder(target, fid)
        self.library.set_folder(gid, fid)
        order = list(self.library_order)
        if target in order:                     # (My Arrangement: the folder takes the target's place - it went
            order[order.index(target)] = FOLDER_PREFIX + fid        # to the end, and the view jumped there)
            self.library.set_library_order([x for x in order if x != gid])
        self._reshow((gid, target))
        self._select_item(FOLDER_PREFIX + fid, glide=True)

    def _reshow(self, changed: tuple = ()) -> None:
        """After a change made here (folders, order): only the games that changed are read again, then the grid
        is redrawn - no full reload, no save check, no "Syncing" in the top bar (the saves haven't changed)."""
        for gid in changed:
            g = self.library.get(gid)
            if g is None:
                self.games.pop(gid, None)
            else:
                self.games[gid] = g
        self.folder_names = self.library.list_folders()
        self._read_orders()
        self._rebuild()
        self._own_change()

    def _select_item(self, iid: str, glide: bool = False) -> None:
        """glide: brought into view by gliding there (after a drop), not a jump."""
        for i in range(self.grid.count()):
            if self.grid.item(i).data(GAME_ID_ROLE) == iid:
                if glide:
                    self.grid.setAutoScroll(False)          # (Qt would scroll there at once)
                    try:
                        self.grid.setCurrentRow(i)
                    finally:
                        self.grid.setAutoScroll(True)
                    self.grid.item(i).setSelected(True)
                    self.grid.glide_to_show(self.grid.model().index(i, 0))
                else:
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

    def _lights_moving(self) -> bool:
        """The lights breathe: pulsing is on, or was just switched off and they're finishing their last breath."""
        if self._mode_anim is not None:
            return True                        # (fading in / out: Dual <-> My Device)
        if self.settings.solo:
            return False                       # (no lights on a solo device)
        if self.settings.pulse:
            return True
        if PULSE_OFF_AT[0] is None:
            return False
        if time.monotonic() < pulse_settled_at():
            return True
        PULSE_OFF_AT[0] = None                            # all settled: still from here on
        self.grid.viewport().update()
        self.sync_light.update()
        return False

    def set_pulse(self, on: bool) -> None:
        """Pulsing Sync Lights, at once (Settings previews it as it's ticked; Cancel puts it back). Switched off,
        each light finishes its breath and rests where a still light sits."""
        if on == self.settings.pulse:
            return
        self.settings.pulse = on
        pulse_switched(on)
        self._update_anim()
        self.grid.viewport().update()
        self.sync_light.update()

    def _update_anim(self) -> None:
        needed = (self._lights_moving() or self.hovered is not None or bool(self.hover)
                  or self.sb_alpha > 0 or self._sb_target() > 0   # keep ticking until it has faded out
                  or self._ghost is not None or self.drag_dim > 0
                  or self._landing is not None or bool(self.slides) or self._search_moving()
                  or (hasattr(self, "_fader") and self._fader.active) or bool(self.rumble)
                  or self.bookmark_alpha != (1.0 if self.settings.show_bookmarks else 0.0))
        # minimized: nothing to see, so nothing animates (it cost 6-8 % of a core - battery on the laptop). Every
        # animation is timed by the real clock, so on restore they carry on in step, the lights mid-breath as they
        # would have been
        needed = needed and not self.isMinimized()
        if needed and not self.anim.isActive():
            self._last_tick = time.monotonic()
            self.anim.start()
        elif not needed and self.anim.isActive():
            self.anim.stop()
            self.grid.viewport().update()

    def showing_empty_library(self) -> bool:
        """No games at all yet (the cloud folder is there): said the same way as No Results Found."""
        return not self.games and Path(self.settings.library_root).is_dir()

    def _no_results_target(self) -> float:
        """1 once nothing matches (or there are no games) AND the last cover has finished fading out (never both
        on screen at once). The message is remembered, so it doesn't change words while fading out."""
        if self._leaving:
            return 0.0
        if self.showing_empty_library():
            self.no_results_key = "grid.empty"
            return 1.0
        if self.showing_no_results():
            # the library view itself is empty (no search, no status): say so - "No Results Found" on a new second
            # computer hid that the whole library is one drop-down away (owner, 2026-10-19)
            view = self._device_filter()
            plain = not self.search.text().strip() and not self._filtering() and not self.current_folder
            self.no_results_key = f"grid.empty_{view}" if plain and view != "both" else "grid.no_results"
            return 1.0
        return 0.0

    def _search_moving(self) -> bool:
        return bool(self.appearing) or bool(self._leaving) \
            or self.no_results != self._no_results_target()

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
        dim_target = 1.0 if (self._ghost is not None or self._landing is not None) else 0.0
        if self.drag_dim != dim_target:
            d = (step * HOVER_MS) / 120                  # a quick fade, ~0.12 s
            self.drag_dim = min(dim_target, self.drag_dim + d) if dim_target > self.drag_dim \
                else max(dim_target, self.drag_dim - d)
            if self._dim_gid:
                moving.add(self._dim_gid)
            changed = True
        elif self.drag_dim == 0 and self._ghost is None:
            self._dim_gid = None
        mark = 1.0 if self.settings.show_bookmarks else 0.0
        if self.bookmark_alpha != mark:
            d = step * HOVER_MS / 180                     # ~0.18 s
            self.bookmark_alpha = min(mark, self.bookmark_alpha + d) if mark > self.bookmark_alpha \
                    else max(mark, self.bookmark_alpha - d)
            self.grid.viewport().update()
            changed = True
        target = self._sb_target()
        if self.sb_alpha != target:
            d = step * HOVER_MS / SCROLLBAR_FADE_MS
            self.sb_alpha = min(target, self.sb_alpha + d) if target > self.sb_alpha else max(target, self.sb_alpha - d)
            self._style_scrollbar()
            changed = True
        # redraw only what actually moves: a lifting tile (with its shadow) and the small squares
        # around the breathing lights - never the whole grid, which would cost far more with many games
        region = QRegion()
        view = self.grid.viewport().rect()
        lights = []                                       # (centre, id) of the lights redrawn on their own
        live = QRegion()                                  # what moves (hovers): drawn the usual way (see LibraryGrid.paintEvent)
        breathing = self._lights_moving()                 # (pulsing, or settling after being switched off)
        quiet = self.bookmark_alpha == mark and self.drag_dim in (0.0, 1.0) \
            and self._ghost is None and self._landing is None and not self.rumble
        lights_only = quiet and not moving
        layered = self.grid.layer_active()                # (gliding: every frame is drawn whole anyway)
        for i in (() if layered else self.grid.visible_range()):
            it = self.grid.item(i)
            r = self.grid.visualItemRect(it)
            iid = it.data(GAME_ID_ROLE)
            if iid in moving:
                region += r.adjusted(-30, -30, 30, 36)
                live += r.adjusted(-30, -30, 30, 36)
            elif breathing:
                c = CardDelegate.light_center(CardDelegate.cover_rect(r, self.hover.get(iid, 0.0)))
                rad = int(LIGHT_R * 2.9) + 2
                square = QRect(int(c.x()) - rad, int(c.y()) - rad, 2 * rad + 1, 2 * rad + 1)
                region += square
                lights.append((c, iid, square))
        if not live.isEmpty():
            # a light whose square reaches into the moving part is drawn with it, the usual way - drawn on its own
            # too, its glow went on twice along the edge (a few pixels a few shades off: measured)
            joined = True
            while joined:                                 # (a square joining can reach the next one)
                joined = False
                for c, iid, square in lights:
                    if live.intersects(square) and not live.contains(square):
                        live += square
                        joined = True
            lights = [(c, iid, square) for c, iid, square in lights if not live.intersects(square)]
        lights = [(c, iid) for c, iid, _square in lights]
        if self.slides:                                   # covers sliding: the whole grid moves
            if now - self._slide_t0 >= SLIDE_S:
                self.slides = {}
            region = QRegion(view)
            changed = True
        if self._search_moving():                         # search / filter: gliding, arriving, leaving
            self._hold_blocked_arrivals(now)
            if self.appearing and now - self._appear_t0 >= max(self.appearing.values()) + APPEAR_S:
                self.appearing = {}
            target = self._no_results_target()
            if self.no_results != target:
                # (at most two frames' worth: a slow first frame - the window just opened - mustn't jump the fade)
                frame_ms = min(step * HOVER_MS, 2000 / 60)
                d = frame_ms / ((NO_RESULTS_S if target > self.no_results else NO_RESULTS_OUT_S) * 1000)
                self.no_results = min(target, self.no_results + d) if target > self.no_results \
                    else max(target, self.no_results - d)
            region = QRegion(view)
            changed = True
        if self.rumble:                                   # the easter egg's rocking covers (its strength: EasterEgg)
            self.rumble_t = now
            region = QRegion(view)
            changed = True
        # the check of all running programs (launcher.warm_up) waits while covers move: it keeps Python busy
        launcher.hold_warm_up(bool(self.slides) or self._search_moving() or self._ghost is not None
                              or self._landing is not None, "grid")
        if layered:
            if breathing or changed:
                self.grid.viewport().update()            # gliding: each frame slides the covers' strips anyway
        elif not region.isEmpty():
            # (not while a folder opens / closes: the animation covers the grid, and each of these little
            # updates would make it redraw out of step)
            if lights_only and self.grid.painted_all:
                pass    # the whole grid was just drawn (scrolling...), lights and all: making the still picture now
                #         would draw it all a second time each frame (scrolling a big library stuttered on the laptop)
            else:
                if lights_only and not self.slides and not self._search_moving() and lights \
                        and self.grid.prepare_still():
                    self.grid.light_frame = {"region": region, "lights": lights}
                elif quiet and moving and not self.slides and not self._search_moving() and lights \
                        and self.grid.prepare_still(skip=live):
                    # a hover moving while the lights breathe: the still picture + the lights, and only the
                    # moving part drawn the usual way (see LibraryGrid.paintEvent)
                    self.grid.light_frame = {"region": region, "lights": lights, "live": live}
                self.grid.viewport().update(region)
        self.grid.painted_all = False
        if breathing:
            self.sync_light.update()
        if self._fader.active:                            # the bottom bar's cross-fade, on this same clock
            self._fader.step()
            changed = True
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
        files = pick_files(self, tr("file.pick_exe"), tr("file.programs"))
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
                    if GameDialog(self.library, self.settings, game, parent=self).exec():   # e.g. its save folder
                        self._ask_now.add(match.game_id)
                    last = match.game_id
                    continue
            d = GameDialog(self.library, self.settings, exe=exe, parent=self)
            if d.exec() and d.saved_id:
                last = d.saved_id
                self._ask_now.add(d.saved_id)
        self.refresh()
        if last:
            self._select(last)

    def _added_on_other_device(self, exe: str) -> Optional[Game]:
        """A game the other device added that isn't set up here yet and looks like this .exe (same file
        name there, or the same title). Names shared by many games don't count: Start.exe / BGI.exe /
        SiglusEngine.exe..., or a guessed title that is really an engine's name (two different BGI games were
        taken for the same game: both "BGI.exe", both guessed "Ethornell - BURIKO General Interpreter")."""
        from titles import is_engine_name, is_generic_name
        stem = Path(exe).stem
        name = "" if is_generic_name(stem) or is_engine_name(stem) else Path(exe).name.casefold()
        guess = guess_title(exe).strip()
        guess = "" if is_engine_name(guess) or is_generic_name(guess) else guess.casefold()
        machine = self.settings.machine_name
        for g in self.library.list_games():
            if g.paths_for(machine).exe:
                continue
            names = {Path(p.exe.replace("\\", "/")).name.casefold() for p in g.paths.values() if p.exe}
            if (name and name in names) or (guess and guess in (g.title.casefold(), g.auto_title.casefold())):
                return g
        return None

    def _select(self, gid: str) -> None:
        """Select a game (opening its folder if it's in one)."""
        g = self.games.get(gid)
        if g is not None:
            want = g.folder or None
            if self.search.text().strip() == "" and not self._filtering() and want != self.current_folder:
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
        self.edit_game(self.selected())

    def edit_game(self, g: Optional[Game]) -> None:
        if g is None:
            return
        g = self.library.get(g.game_id) or g
        mine = lambda x: (x.paths_for(self.settings.machine_name).save_dir, x.registry_key) if x else None
        before = mine(g)
        d = GameDialog(self.library, self.settings, g, parent=self)
        ok = d.exec()
        removed, removed_how = d.removed, d.removed_how
        discard(d)                                          # (else restyled on every theme change from now on)
        if ok:
            if removed:                                     # the Edit window's Remove From Library
                self.clear_selection()
                self.refresh()
                self._offer_undo(g.title, removed, removed_how)
                return
            if mine(self.library.get(g.game_id)) != before:     # save folder set up / changed here: ask right away
                self._ask_now.add(g.game_id)
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
                      tr("menu.remove_folder"), tr("game.cancel"), title=tr("menu.remove_folder")):
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

    def _folder_key(self) -> str:
        """The bar's folder button: on My Device nothing syncs, so the game's own folder is the useful one (owner,
        2026-10-19); with two devices, the save folder. (The right-click menu always has both.)"""
        return "bar.open_game" if self.settings.solo else "bar.open_saves"

    def _bar_folder(self, g: Game) -> str:
        mine = g.paths_for(self.settings.machine_name)
        if self.settings.solo:
            return str(Path(mine.exe).parent) if mine.exe else ""
        return mine.save_dir

    def _open_saves(self) -> None:
        g = self.bar_game()
        if g:
            open_folder(self._bar_folder(g))

    def open_settings(self) -> None:
        if self.worker:
            message(self, tr("settings.close_game"))
            return
        d = SettingsDialog(self.settings, self)
        # a closed window stays as a hidden child of its parent until deleted: each Settings / Edit / theme window
        # opened since start was restyled on every theme step after that (owner, 2026-10-20: "comically slow")
        ok = d.exec() and d.result_settings
        result = d.result_settings
        discard(d)
        if ok:
            self.apply_settings(result)

    def restore_selected(self) -> None:
        g = self.selected()
        if g is None or self.engine is None:          # (My Device: no backups are made, nothing to restore)
            return
        mine = g.paths_for(self.settings.machine_name)
        if not mine.save_dir:
            return
        d = RestoreDialog(g, self.engine, mine.save_dir, self)
        if d.exec() and d.restored:
            self.statusBar().showMessage(tr("restore.done"), 8000)
            self.refresh()

    def play_selected(self, without: tuple[str, ...] = (), watch: bool = False,
                      gid: Optional[str] = None, asked: bool = False) -> None:
        g = self.games.get(gid) if gid else self.selected()
        if g is None or self.worker is not None or self.looking_for_saves or self.syncer is not None:
            return
        mine = g.paths_for(self.settings.machine_name)
        if not mine.exe:
            self.edit_selected()
            return
        if not mine.save_dir and not watch and not asked and not self.settings.solo:
            self._offer_save_folder(g, without)     # comes back here (asked=True) when done
            return
        self.playing_id = g.game_id
        self.stop_pending = False
        self._waiting = None                        # (a new session: nothing from the last one carries over)
        self.worker = PlayWorker(self.library.get(g.game_id) or g, self.settings, self.library, self.engine,
                                 without, watch)
        self.worker.status.connect(self._play_status)
        self.worker.conflict.connect(self._ask_conflict, Qt.BlockingQueuedConnection)
        self.worker.result.connect(self._play_finished)
        self.worker.phase.connect(self._on_phase)
        self.worker.start()
        self._update_details()

    def _offer_save_folder(self, g: Game, without: tuple[str, ...]) -> None:
        """No save folder on This Device yet: if we can tell where it saves (the other device's location,
        or an existing save folder), ask to use it before starting - then the other device's saves come
        across now instead of after a first session without them."""
        o = g.paths_for(other_slot(self.settings.machine_name))
        other = (o.exe, o.save_dir) if o.save_dir else None
        exe = g.paths_for(self.settings.machine_name).exe
        self.looking_for_saves = True

        def go_on(s):
            self.looking_for_saves = False
            fresh = self.library.get(g.game_id)
            if fresh is None or self.worker is not None:
                return
            if s is not None and not fresh.paths_for(self.settings.machine_name).save_dir:
                why = tr("play.suggest_other" if s.reason == "other_device" else "play.suggest_found")
                choice = message(self, tr("play.suggest_body", title=fresh.title, path=s.path, why=why),
                                 tr("play.suggest_title"),
                                 [("use", tr("play.suggest_use")), ("skip", tr("play.suggest_skip")),
                                  ("cancel", tr("game.cancel"))], dismiss="cancel")
                if choice == "cancel":
                    return
                if choice == "use":
                    try:
                        self.library.set_paths(g.game_id, self.settings.machine_name, save_dir=s.path)
                    except (OSError, KeyError) as e:
                        report_failure(self, e)
                        return
                    self.refresh()
            self.play_selected(without, gid=g.game_id, asked=True)

        def failed(_e):
            go_on(None)                             # the search itself failed: just play as before
        self._run(lambda: suggest_save(self.settings, exe, g.title, other), go_on, failed)

    def _play_status(self, key: str) -> None:
        self._play_status_key = key
        if key == "status.launching":
            # the bottom bar already says "Starting the Game..."; keep the status line clear while playing
            self.statusBar().clearMessage()
        elif key in ("status.checking", "status.closed_syncing"):
            # the quick checks / sync around a session: only shown if still going after SYNC_SHOW_AFTER_S, like
            # the light (a quick one flashed on the status line for a moment)
            worker = self.worker

            def still() -> None:
                if self.worker is worker and worker is not None and self._play_status_key == key:
                    self.statusBar().showMessage(i18n.line(key))
            QTimer.singleShot(round(SYNC_SHOW_AFTER_S * 1000), still)
        else:
            self.statusBar().showMessage(i18n.line(key))

    def _on_phase(self, phase: str) -> None:
        if phase == "syncing" and self.playing_id:  # (timed from when the sync itself begins)
            self._sync_wait(self.playing_id, after_session=self.play_phase in ("starting", "playing"))
        if phase == "starting":                     # shown only if the game takes a moment to appear
            QTimer.singleShot(round(SYNC_SHOW_AFTER_S * 1000) + 10, self._sync_wait_over)
        self._phase_t0 = time.monotonic()
        self.play_phase = phase
        self._update_sync_label()
        self._update_details()

    # Syncing (button, light, words) mirrors the real sync, but only once it has taken SYNC_SHOW_AFTER_S: a quick
    # one is never shown (Stop -> Read, Read -> Stop, no flash in between). Around it - checks before starting,
    # recording the session after - the button and light keep the look from before (Read / Stop).
    def _sync_wait(self, gid: str, after_session: bool) -> None:
        self._waiting = (gid, time.monotonic(), after_session)
        QTimer.singleShot(round(SYNC_SHOW_AFTER_S * 1000) + 10, self._sync_wait_over)

    def _session_look(self, gid: str) -> tuple[str, str]:
        """(button label key, light state) for the game in a session right now. The button only ever says Read or
        Stop (owner: a Syncing label was a blink of noise): Stop while the game starts / runs, Read otherwise."""
        phase = self.play_phase
        w = self._waiting if self._waiting and self._waiting[0] == gid else None
        if phase == "starting":
            if time.monotonic() - self._phase_t0 >= SYNC_SHOW_AFTER_S:
                return "bar.stop", "starting"
            # a game that appears straight away: no separate "Starting" step (the look before stays until it
            # shows, then button, light and words change once, together)
            return "bar.play", self.sync_states.get(gid, "checking")
        if phase == "playing":
            return "bar.stop", "playing"
        if phase == "syncing" and w and time.monotonic() - w[1] >= SYNC_SHOW_AFTER_S:
            return "bar.play", "syncing"
        if w and w[2]:
            # the game has closed, its quick sync not shown (yet): the light still says Now Reading, so the button
            # still says Stop - they change together, in one update (Stop -> Read came ~0.1 s before the light did,
            # so it couldn't fade with it). A press meanwhile does nothing (the session is finishing).
            return "bar.stop", "playing"
        return "bar.play", self.sync_states.get(gid, "checking")

    def _sync_wait_over(self) -> None:
        if self.worker is not None or (self._waiting and time.monotonic() - self._waiting[1] >= SYNC_SHOW_AFTER_S):
            self._update_details()                  # still syncing: now it says so (or it already ended)
            self._update_sync_label()
            self._lights_changed()

    def _lights_changed(self) -> None:
        """Only the sync lights' colours changed. While they pulse they're redrawn every frame anyway (on their own,
        from the grid's still picture): asking for a whole-grid redraw here cost 20-30 ms on the owner's PC right at
        a game's start / close - the hitch. Without pulsing, the grid is redrawn as before."""
        if not self.settings.pulse:
            self.grid.viewport().update()

    def only_here(self, g: Optional[Game]) -> bool:
        """Set up on this device but not the other: then a save mismatch can't be "both devices" (the cloud copy
        was uploaded from this one) and the messages say so."""
        return g is not None and not g.paths_for(other_slot(self.settings.machine_name)).exe

    def first_sync(self, g: Optional[Game]) -> bool:
        """This device has never synced the game (but the other device has it): a mismatch then isn't "both changed",
        this device already had files of its own (often made by the game when installed or first opened)."""
        return g is not None and self.engine is not None and not self.only_here(g) \
                and not self.engine.has_synced(g.game_id)

    def solo_key(self, key: str, g: Optional[Game]) -> str:
        """The true wording for a save mismatch: "<key>_solo" for a game set up only on this device (the cloud copy
        came from here), "<key>_first" for this device's first sync of it; else the usual "both devices" one."""
        if self.only_here(g) and key + "_solo" in i18n.S:
            return key + "_solo"
        if key + "_first" in i18n.S and self.first_sync(g):
            return key + "_first"
        return key

    def _conflict_choice(self, info: ConflictInfo, fresh: bool = False) -> Optional[str]:
        """fresh: re-read the game first - right after a save folder was found, finish_first_play has just written
        its setup_saves (only then: a cloud read on the window's thread can stall while Google Drive is busy)."""
        g = (self.library.get(info.game_id) if fresh else None) or self.games.get(info.game_id)
        other = bool(g and g.paths_for(other_slot(self.settings.machine_name)).exe)
        d = ConflictDialog(g.title if g else info.game_id, info, self, other_set_up=other,
                           first_sync=other and not self.engine.has_synced(info.game_id),
                           setup=g.setup_saves if g else {}, machine=self.settings.machine_name)
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
        self.stop_pending = False
        self.play_phase = "done"
        game = self.library.get(gid) if gid else None
        if r.message:
            r = dataclasses.replace(r, message=self.solo_key(r.message, game))
        if not r.launched:
            self.statusBar().showMessage(i18n.line(r.message))   # (short: the pop-up explains)
            self.refresh()
            if r.message != "play.cancelled":          # the owner chose that: no "Can't Start" box
                self._launch_problem(r, worker.without, gid)
            return
        msg = i18n.line(r.message) if r.message else ""
        bar = self.statusBar()                      # (fades in together with the bottom bar's new look)
        if r.seconds < INSTANT_CLOSE_S:
            # closed again straight away (something missing, a crash...): say so, rather than report a 3-second
            # "session" as if all was well (owner, 2026-10-17)
            bar.show_with_bar(tr("status.closed_at_once"), 15000)
        else:
            # (strip: with no sync message - My Device - the line began with the gap meant to follow it)
            bar.show_with_bar(tr("status.played_for", msg=msg, time=i18n.playtime(r.seconds)).strip(), 15000)
        if game and r.seconds > 0:
            self._count_up = (game.game_id, max(0.0, game.total_playtime - r.seconds), time.monotonic())
        if r.new_title:
            bar.show_with_bar(i18n.line("status.title_updated", title=r.new_title), 15000)
        self._update_details()                      # the bar at once (Stop -> Read); the new playtime etc. follow
        self._update_sync_label()                   # when the library has been re-read:
        self._lights_changed()
        self.refresh_soon(keep_files=True)          # (in the background: Google Drive is busy with the new save)
        self.raise_()
        self.activateWindow()
        if game and self.engine is not None and not game.paths_for(self.settings.machine_name).save_dir:
            self._confirm_detection(game, r)        # (My Device: nothing syncs, so no save folder is needed)
            self.refresh()
        if game and self.settings.vndb and not game.artwork and game.artwork_from != "removed" and r.window_title:
            self._auto_cover(game.game_id, r.new_title or game.title)

    def _launch_problem(self, r: PlayResult, already_without: tuple[str, ...], gid: Optional[str] = None) -> None:
        buttons = []
        done_when, game_dir = None, None
        if r.retry_without == "close_luna":           # Luna open, certainly idle: it can be closed for you
            buttons.append(("close_luna", tr("play.close_luna")))
        if r.message == "play.luna_running":
            buttons.append(("retry", tr("play.retry")))
        if r.retry_without == "watch":
            buttons.append(("watch", tr("play.watch")))
            g = self.games.get(gid) if gid else None
            exe = g.paths_for(self.settings.machine_name).exe if g else ""
            if exe:
                # (owner, 2026-10-17) Force Close too; and if the game is closed while this is open, it goes away
                # by itself and the game starts through Ikemen Reader - Read was pressed for that
                game_dir = Path(exe).resolve().parent
                buttons.append(("force", tr("stop.force")))
                done_when = lambda d=game_dir: not launcher.already_running(d)
        if r.retry_without == "luna":
            buttons.append(("without_luna", tr("play.retry_without_luna")))
        elif r.retry_without == "le":
            buttons.append(("without_le", tr("play.retry_without_le")))
        if r.message in ("play.luna_missing", "play.le_missing"):
            buttons.append(("settings", tr("play.go_settings")))
        # nothing to choose: just OK (a lone Cancel read oddly); otherwise Cancel beside the choices
        buttons.append(("cancel", tr("game.cancel")) if buttons else ("ok", tr("play.ok")))
        # (already running: it isn't refused - it can wait for the game - so not "Can't Start the Game")
        running = r.message in ("play.already_running", "play.already_running_luna")
        title = tr("play.title_running" if running else "play.title")
        choice = message(self, tr(r.message), title, buttons, done_when=done_when)
        without = tuple(already_without)
        if r.message == "play.already_running_luna":
            # (the pop-up said Luna closes too: once the game has closed, Luna is closed - if it's still certainly
            # free of other games - and the game starts through it)
            start = lambda: self._close_luna_then_read(gid, without, only_if_idle=True)
        else:
            start = lambda: self.play_selected(without, gid=gid)
        if choice == "force":
            try:
                launcher.force_close_running(game_dir)
            except Exception as e:                      # (nothing to end, or not ours to end: say so)
                report_failure(self, e)
                return
            self._start_when_closed(game_dir, start)
        elif choice == "done":                          # closed while the message was open
            start()
        elif choice == "close_luna":
            self._close_luna_then_read(gid, tuple(already_without))
        elif choice == "retry":
            self.play_selected(already_without)
        elif choice == "watch":
            self.play_selected(tuple(already_without), watch=True)
        elif choice == "without_luna":
            self.play_selected(tuple(already_without) + ("luna",))
        elif choice == "without_le":
            self.play_selected(tuple(already_without) + ("le",))
        elif choice == "settings":
            self.open_settings()

    def _close_luna_then_read(self, gid: Optional[str], without: tuple, only_if_idle: bool = False) -> None:
        """Close Luna (asked like its own X; in the background - it may take a few seconds), then start the game
        through Luna as usual. If Luna doesn't close, say so - nothing else happens. only_if_idle: checked again
        first (something may have changed since the pop-up); busy or not sure -> Read as usual (its own message)."""
        luna_exe = self.settings.luna_path
        self.statusBar().showMessage(tr("status.closing_luna"))

        def work() -> str:
            if only_if_idle and not luna.idle(luna_exe):
                return "busy"
            return "closed" if luna.close(luna_exe) else "open"

        def done(how) -> None:
            if how == "open":
                self.statusBar().showMessage(tr("status.luna_still_open"), 10000)
                return
            self.statusBar().clearMessage()
            self.play_selected(without, gid=gid)       # (busy: Read says why, as before)
        self._run(work, done, lambda _e: done("open"))

    def _start_when_closed(self, game_dir: Path, then: Callable[[], None], waited: float = 0.0) -> None:
        """After Force Close: go on (start the game) once its programs have really ended (a moment, usually). If
        it somehow keeps running, Read finds it again and asks as before."""
        if launcher.already_running(game_dir) and waited < 5.0:
            QTimer.singleShot(200, lambda: self._start_when_closed(game_dir, then, waited + 0.2))
            return
        then()

    @guarded
    def _confirm_detection(self, game: Game, r: PlayResult) -> None:
        exe = game.paths_for(self.settings.machine_name).exe
        d = DetectDialog(game, r.candidates, self.app_dir, self, cloud_root=self.settings.cloud_root,
                         game_dir=str(Path(exe).parent) if exe else "")
        if d.exec() and d.save_dir:
            res = finish_first_play(game, self.settings, self.library, self.engine, d.save_dir,
                                    d.registry_key, lambda info: self._conflict_choice(info, fresh=True),
                                    started_at=r.started_at)
            self.statusBar().showMessage(i18n.line(res.message), 10000)

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
        a.setEnabled(self.play_btn.isEnabled() and self.worker is None)
        m.addAction(tr("menu.edit"), self.edit_selected)
        m.addAction(tr("menu.notes" if g.notes.strip() else "menu.add_notes"), lambda: self.open_notes(g.game_id))
        sm = m.addMenu(tr("menu.status"))
        sm.setObjectName("ticks")       # (its words start where the other menus' do; the tick on the right: MenuLines)
        for s in STATUSES:
            act = sm.addAction(tr(f"status.{s or 'none'}"), lambda s=s: self._set_status(g.game_id, s))
            act.setData("current" if g.status == s else None)
        fm = m.addMenu(tr("menu.add_to_folder"))
        others = sorted({x.folder for x in self.games.values() if x.folder and x.folder != g.folder},
                        key=lambda f: self.folder_name(f).lower())
        for f in others:
            fm.addAction(self.folder_name(f), lambda f=f: self.move_to_folder(g.game_id, f))
        if others:
            fm.addSeparator()
        fm.addAction(tr("menu.new_folder"), lambda: self.move_to_folder(g.game_id, None))
        if g.folder:
            m.addAction(tr("menu.remove_from_folder"), lambda: self.move_to_folder(g.game_id, ""))
        m.addSeparator()
        if not self.settings.solo:                   # (solo: nothing syncs, and no earlier saves are kept)
            a = m.addAction(tr("menu.sync_now"), lambda: self.sync_now([g]))
            a.setEnabled(self.light_state(g.game_id) in ("push", "pull", "conflict") and self.worker is None
                         and self.syncer is None)
        a = m.addAction(tr("menu.open_saves"), lambda: open_folder(mine.save_dir))
        a.setEnabled(bool(mine.save_dir))
        a = m.addAction(tr("menu.open_game"), lambda: open_folder(str(Path(mine.exe).parent)))
        a.setEnabled(bool(mine.exe))
        if not self.settings.solo:
            a = m.addAction(tr("menu.restore"), self.restore_selected)
            a.setEnabled(bool(mine.save_dir) and self.worker is None)
        m.addSeparator()
        a = m.addAction(tr("game.remove"), lambda: self.remove_game(g.game_id))
        a.setEnabled(self.worker is None)             # not while a game is running or syncing
        m.exec(self.grid.viewport().mapToGlobal(pos))

    @guarded
    def remove_game(self, gid: str) -> None:
        g = self.games.get(gid)
        if g is None or self.worker is not None:
            return
        how = confirm_remove(self, self.library.get(gid) or g, self.settings.machine_name, solo=self.settings.solo)
        if how:
            before = self._positions()
            removed = remove_game_from(self.library, gid, self.settings.machine_name, how)
            self.clear_selection()
            self.refresh()
            self._slide_from(before)                     # the others close the gap
            self._offer_undo(g.title, removed, how)

    def _offer_undo(self, title: str, removed: dict, how: str = "both") -> None:
        """'Removed X. Undo' on the status line for 10 s (owner, 2026-10-17): one click puts the game back - file,
        cover, folder (or just its setup on this device) - where it was."""
        def undo() -> None:
            try:
                g = self.library.restore_removed(removed)
            except OSError as e:
                report_failure(self, e)
                return
            self.refresh()
            if g is not None:
                self._select(g.game_id)
                self.statusBar().showMessage(tr("status.restored", title=g.title), 6000)
        key = "status.removed_here" if removed.get("here") else "status.removed"   # (only here / gone everywhere)
        self.statusBar().show_action(tr(key, title=title), tr("status.undo"), undo, 10000)

    @guarded
    def _set_status(self, gid: str, status: str) -> None:
        """A new reading status: saved, then shown at once (its bookmark, the folder mosaic, the filter)
        without re-reading the whole library."""
        g = self.library.update(gid, status=status)
        self._replace_game(g)
        if self.settings.status_filter != "all":
            self._rebuild()
        else:
            self.grid.viewport().update()
            self._update_details()
        self._own_change()

    def _replace_game(self, g: Game) -> None:
        """A game just saved here: the copies in memory follow (no re-reading the whole library)."""
        self.games[g.game_id] = g
        for games in self.folder_games.values():
            for i, old in enumerate(games):
                if old.game_id == g.game_id:
                    games[i] = g

    # a game's notes: opened at once from memory and saved in the background (the cloud folder can stall for seconds -
    # never in the middle of the fade); saves of one game are written one after another, the latest last
    _notes_saving: frozenset = frozenset()       # games whose notes are being written now
    _notes_queue: dict = {}                      # game -> notes waiting for the write before them

    def open_notes(self, gid: str) -> None:
        g = self.games.get(gid)
        if g is None or self.findChild(DimLayer) is not None:
            return
        layer = NotesLayer(self, g.title, g.notes, lambda text, wait=False: self._save_notes(gid, text, wait))
        if gid in self._notes_saving or gid in self._notes_queue:
            return                                   # (what's in memory is the newest: it's being written)
        library = self.library

        def fresh(new: Optional[Game]) -> None:      # the other device may have written some since the last look
            if new is not None and shiboken6.isValid(layer) and gid not in self._notes_saving | set(self._notes_queue):
                layer.refresh(new.notes)
        self._run(lambda: library.get(gid), fresh, lambda _e: None)

    def _save_notes(self, gid: str, text: str, wait: bool = False) -> None:
        g = self.games.get(gid)
        if g is not None:
            self._replace_game(dataclasses.replace(g, notes=text))   # (at once: the menu says Notes, reopening shows them)
        self._notes_queue = {**self._notes_queue, gid: text}
        if wait:
            self._flush_notes()
        elif gid not in self._notes_saving:
            self._write_notes(gid)

    def _write_notes(self, gid: str) -> None:
        queue = dict(self._notes_queue)
        text = queue.pop(gid)
        self._notes_queue = queue
        self._notes_saving = self._notes_saving | {gid}
        library = self.library

        def finished() -> None:
            self._notes_saving = self._notes_saving - {gid}
            if gid in self._notes_queue:
                self._write_notes(gid)               # (changed again meanwhile)

        def done(g: Game) -> None:
            if library is self.library and gid not in self._notes_queue:
                self._replace_game(g)
                self._own_change()
            finished()

        def failed(e) -> None:
            finished()
            if gid not in self._notes_saving:
                # (KeyError: the other device removed the game meanwhile)
                message(self, tr("notes.gone") if isinstance(e, KeyError) else tr("notes.failed", error=e))
        self._notes_tasks = [t for t in getattr(self, "_notes_tasks", []) if t.isRunning()]
        self._notes_tasks.append(self._run(lambda: library.update(gid, notes=text), done, failed))

    def _flush_notes(self) -> None:
        """Closing: every note still being written or waiting is on disk before the program goes."""
        for t in getattr(self, "_notes_tasks", []):
            t.wait(10000)
        queue, self._notes_queue = dict(self._notes_queue), {}
        for gid, text in queue.items():
            try:
                self.library.update(gid, notes=text)
            except (KeyError, OSError):
                pass

    def _own_change(self) -> None:
        """Note the library as it is after a change made here, so the live check doesn't take it for news
        from the other device (and reload everything). In the background: the cloud folder can be slow."""
        self._live_checking = True                  # (no live check in between)
        library = self.library

        def done(sig) -> None:
            self._live_checking = False
            if library is self.library:
                self._live_sig = sig

        def failed(_e) -> None:
            self._live_checking = False
        self._run(library.change_signature, done, failed)

    # ------------------------------------------------------------------ updates
    def _update_folder(self) -> Path:
        return updater.update_dir(self.app_dir)

    def _check_for_update(self) -> None:
        self._run(lambda: updater.check(APP_VERSION), self._update_found, lambda _e: None)   # offline: quiet

    def _update_found(self, release: Optional["updater.Release"]) -> None:
        if release is None:
            return
        self._release = release
        if self.settings.auto_update and updater.FROZEN:
            folder = self._update_folder()
            self._run(lambda: updater.stage(updater.download(release, folder), folder),
                      self._update_downloaded, lambda _e: None)        # tried again next time
        else:
            self._offer_update()

    def _busy_for_update(self) -> bool:
        """Not while a game runs, saves sync, or a window / menu is open: asked again a little later."""
        return (self.worker is not None or self.syncer is not None or self._ghost is not None
                or QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None)

    def _offer_update(self) -> None:
        """Checkbox off: "Ikemen Reader 1.0.1 is available" - Later | Update Now."""
        r = self._release
        if r is None or self._update_asked:
            return
        if self._busy_for_update():
            QTimer.singleShot(5000, self._offer_update)
            return
        self._update_asked = True
        text = tr("update.available", version=r.version, current=APP_VERSION)    # (kept simple: no notes)
        if message(self, text, tr("update.available_title"),
                   [("now", tr("update.now")), ("later", tr("update.later"))], dismiss="later") != "now":
            return
        if not updater.FROZEN:
            message(self, tr("update.not_packaged"))
            return
        d = UpdateDialog(r, self._update_folder(), self)
        d.exec()
        if d.new is not None:
            self._restart_for_update(d.new)
        elif d.error is not None:
            message(self, tr("update.damaged" if str(d.error) == "update.damaged" else "update.failed"))

    def _update_downloaded(self, new: Path) -> None:
        """Checkbox on: downloaded and unpacked in the background - Restart Now, or it's installed on closing."""
        self._update_ready = new
        if self._busy_for_update():
            QTimer.singleShot(5000, lambda: self._update_downloaded(new))
            return
        if self._update_asked:
            return
        self._update_asked = True
        version = self._release.version if self._release else ""
        if message(self, tr("update.ready", version=version), tr("update.ready_title"),
                   [("now", tr("update.restart")), ("later", tr("update.later"))], dismiss="later") == "now":
            self._restart_for_update(new)

    def _restart_for_update(self, new: Path) -> None:
        if self.worker is not None or self.syncer is not None:
            self._update_ready = new
            message(self, tr("update.wait_game"))
            return
        try:
            updater.install_on_exit(self._update_folder(), new, restart=True)
        except (OSError, ValueError):
            message(self, tr("update.failed"))
            return
        self._update_ready = None                    # (the helper is already waiting: not twice on closing)
        self.close()

    # ------------------------------------------------------------------ Discord presence
    def _presence_look(self) -> Optional[tuple[str, str]]:
        """(first line, second line) for Discord right now, or None (off). Reading wins over everything; then Away
        (5 minutes without using Ikemen Reader - its Anki in front counts as using it); then Tweaking (a settings
        or edit window open); else Browsing."""
        if not self.settings.discord:
            return None
        g = self.games.get(self.playing_id) if self.playing_id and self.play_phase == "playing" else None
        reading = g is not None
        if reading != self._presence_reading:          # (the timer restarts when reading starts or ends)
            self._presence_reading = reading
            self._presence_since = time.time()
        if reading:
            return tr("presence.reading"), g.title
        now = time.monotonic()
        front = outside.foreground_pid()
        using = front == os.getpid() or (self.anki_from_here and front is not None
                                         and outside.is_anki_pid(front, self.settings.anki_path))
        if using and outside.seconds_since_input() < PRESENCE_CHECK_MS / 1000 + 1:
            self._last_active = now
        if now - self._last_active >= PRESENCE_AWAY_S:
            return tr("presence.away"), ""
        if not updater.FROZEN:
            # run from the project folder, not the packaged Ikemen Reader.exe: that's the owner working on it
            # (owner, 2026-10-17: "it would be cute") - instead of Browsing / Tweaking
            return tr("presence.coding"), ""
        tweaking = any(isinstance(w, PRESENCE_TWEAKING) and w.isVisible() for w in QApplication.topLevelWidgets())
        return tr("presence.tweaking" if tweaking else "presence.browsing"), ""

    def _update_presence(self) -> None:
        try:
            look = self._presence_look()
        except Exception:                               # noqa: BLE001 - never let Discord trouble the window
            look = None
        if look is None:
            self.presence.clear()
            return
        # (the name Discord shows comes from the application: イケメンリーダー when the interface is Japanese)
        self.presence.use_application(presence.CLIENT_IDS.get(i18n.language(), presence.CLIENT_ID))
        # the picture follows the logo colours in use: a dark logo (the Light theme, or a custom theme started from
        # it / recoloured that way) -> the light picture (owner, 2026-10-17)
        dark_logo = QColor(LOGO["logo_fill"]).lightness() < 128
        self.presence.show(look[0], look[1], start=self._presence_since,
                           image="logo_light" if dark_logo else "logo",
                           image_text=tr("presence.games_one") if len(self.games) == 1 else tr("presence.games", n=len(self.games)))

    def _log_launch_focus(self) -> None:
        f = QApplication.focusWidget()
        try:
            with open(default_app_dir() / "launch.log", "a", encoding="utf-8") as out:
                out.write(f"{time.strftime('%H:%M:%S')} focus={type(f).__name__ if f else None} "
                          f"search_focused={self.search.hasFocus()} selected={len(self.grid.selectedItems())} "
                          f"current={self.grid.currentItem() is not None} bar={self.selected_id()}\n")
        except OSError:
            pass

    def _remember_position(self) -> None:
        """Where the window is now (its normal place even if minimized or maximized): it opens there next time
        (owner, 2026-10-19 - Windows put it on whichever screen the mouse was on)."""
        g = self.normalGeometry() if (self.isMaximized() or self.isMinimized()) else self.geometry()
        if g.isValid() and [g.x(), g.y()] != self.settings.window_pos:
            self.settings.window_pos = [g.x(), g.y()]
            try:
                save_local_settings(self.settings)
            except OSError:
                pass

    def place_at_last_position(self) -> None:
        """Before the first show: back where it was when last closed, if that's still on a screen (a screen
        unplugged or rearranged since: Windows places it, as before). Kept inside that screen's usable area."""
        if len(self.settings.window_pos) != 2:
            return
        x, y = self.settings.window_pos
        screen = QApplication.screenAt(QPoint(x + 60, y + 10))           # (where its title bar would be)
        if screen is None:
            return
        room = screen.availableGeometry()
        frame = 32                                                        # (title bar above the client area)
        x = max(room.left(), min(x, room.right() + 1 - self.width()))
        y = max(room.top() + frame, min(y, room.bottom() + 1 - self.height()))
        self.setGeometry(x, y, self.width(), self.height())

    def closeEvent(self, e) -> None:
        if (self.worker and self.play_phase == "syncing") or self.syncer is not None:
            message(self, tr("close.syncing"))      # stopping mid-sync could leave saves half-uploaded
            e.ignore()
            return
        notes = self.findChild(NotesLayer)
        if notes is not None:
            notes.save_now(wait=True)               # (closed with a game's notes open: they're kept)
        self._flush_notes()                         # (and any still being written)
        self._flush_view_choice()                   # (a view choice made just now)
        self._remember_position()
        show_hidden_explorer_windows()
        if self.worker:
            g = self.games.get(self.playing_id)
            if message(self, tr("close.body", title=g.title if g else ""), tr("close.title"),
                       [("cancel", tr("game.cancel")), ("continue", tr("close.continue"))],
                       dismiss="cancel") != "continue":
                e.ignore()
                return
            self.presence.stop()                  # (Discord: no "Reading" left behind)
            if self.settings.solo:
                try:
                    backup_solo_library(self.app_dir)     # (as on any close: a solo library lives on one disk only)
                except OSError:
                    pass
            os._exit(0)  # the game keeps running; nothing of ours is mid-write while we only watch it
        self.presence.stop()                      # Discord shows nothing once Ikemen Reader has closed
        if self.settings.solo:
            try:
                backup_solo_library(self.app_dir)     # (a solo library lives on one disk only)
            except OSError:
                pass
        if self.checker:
            self.checker.wait(5000)       # (it may be uploading saves made outside Ikemen Reader)
        if self._update_ready is not None and updater.FROZEN:
            try:                          # downloaded earlier ("Later"): put in place once we've closed
                updater.install_on_exit(self._update_folder(), self._update_ready, restart=False)
            except (OSError, ValueError):
                pass
        e.accept()                        # other background work: see wait_for_tasks() in main()


# Discord presence says "Tweaking" while one of these is open (settings, edit and look windows; not the help guide
# or pop-up messages - owner, 2026-10-17)
PRESENCE_TWEAKING = (SettingsDialog, GameDialog, ThemeDialog, FontDialog, BackgroundDialog, VndbDialog,
                     CoverPickerDialog, NameDialog)

_DIAG: dict = {}


def _install_stall_log() -> None:
    """Diagnostics, only with IKEMEN_DEBUG_STALLS=1: whenever the window freezes for more than half a second,
    what every thread was doing is written to %APPDATA%\\IkemenReader\\stalls.log."""
    if os.environ.get("IKEMEN_DEBUG_STALLS") != "1":
        return
    import faulthandler
    f = open(default_app_dir() / "stalls.log", "a", encoding="utf-8", buffering=1)
    f.write(f"\n===== started {time.strftime('%H:%M:%S')} =====\n")
    _DIAG["file"] = f
    t = QTimer(QApplication.instance())
    t.setInterval(100)
    last = [time.monotonic()]

    def beat():
        now = time.monotonic()
        if now - last[0] > 0.5:
            f.write(f"{time.strftime('%H:%M:%S')} the window was frozen for {now - last[0]:.2f} s\n")
        last[0] = now
        faulthandler.dump_traceback_later(0.5, repeat=False, file=f)
    t.timeout.connect(beat)
    t.start()
    _DIAG["timer"] = t


def _install_frame_log() -> None:
    """Diagnostics, only with IKEMEN_DEBUG_FRAMES=1, from pressing Read until a few seconds after the game closes:
    every animation frame that comes over 20 ms after the last, and every paint over 8 ms, are written to
    %APPDATA%\\IkemenReader\\frames.log with what the bar showed; a hold-up over 40 ms also gets what every thread
    (the window's included) was doing (faulthandler)."""
    mode = os.environ.get("IKEMEN_DEBUG_FRAMES")
    if mode not in ("1", "always"):                      # (always: not just around a reading session)
        return
    import faulthandler
    f = open(default_app_dir() / "frames.log", "a", encoding="utf-8", buffering=1)
    f.write(f"\n===== started {time.strftime('%H:%M:%S')} =====\n")
    state = {"until": 0.0, "last": time.monotonic()}
    LATE_MS = float(os.environ.get("IKEMEN_DEBUG_LATE_MS", "20"))   # (lower for small hitches at a fast screen)

    def stamp() -> str:
        return f"{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}"

    def window() -> Optional["MainWindow"]:
        return next((x for x in QApplication.topLevelWidgets() if isinstance(x, MainWindow)), None)

    def recording() -> bool:
        if mode == "always":
            return True
        w = window()
        if w is not None and w.worker is not None:
            state["until"] = time.monotonic() + 4        # (and 4 s after the session ends)
        return time.monotonic() < state["until"]

    def what() -> str:
        w = window()
        if w is None:
            return ""
        g = w.bar_game()
        return (f"phase={w.play_phase} button={w.play_btn.text()!r} light="
                f"{w.light_state(g.game_id) if g else '-'} words={w.d_sync._shown[1]!r}")

    # the window's own animation frames (lights, covers): how late each one comes
    orig_tick = MainWindow._tick

    def tick(self) -> None:
        now = time.monotonic()
        gap = (now - state["last"]) * 1000
        state["last"] = now
        if recording():
            if gap > LATE_MS:
                f.write(f"{stamp()} late frame: {gap:.0f} ms since the last  {what()}\n")
            faulthandler.dump_traceback_later(max(0.012, LATE_MS / 1000), repeat=False, file=f)
        t = time.perf_counter()
        orig_tick(self)
        d = (time.perf_counter() - t) * 1000
        if d > 8 and recording():
            f.write(f"{stamp()} slow step: MainWindow._tick {d:.1f} ms\n")
    MainWindow._tick = tick

    def timed(cls) -> None:
        orig = cls.paintEvent

        def paint(self, ev) -> None:
            t = time.perf_counter()
            orig(self, ev)
            d = (time.perf_counter() - t) * 1000
            if d > 8 and recording():
                f.write(f"{stamp()} slow paint: {cls.__name__} {d:.1f} ms\n")
        cls.paintEvent = paint
    for cls in (LibraryGrid, Backdrop, SyncStatus, StatsLine, PulseButton, ThemedCombo):
        timed(cls)
    _DIAG["frames"] = f


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


_INSTANCE_LOCK: list = []          # the Windows lock this copy holds while it runs (see claim_instance)


def claim_instance() -> bool:
    """True if this is the only copy. Taken the moment the program starts, as a Windows named lock that only one
    process can hold: hand_over_to_running_copy alone came too late - a copy only answers once its window is built,
    so clicking the icon several times quickly opened several windows (owner's laptop, 2026-10-18)."""
    if sys.platform != "win32":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)   # (its own copy: the error code read reliably below)
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = k32.CreateMutexW(None, 0, "Local\\" + INSTANCE_KEY)
    already = ctypes.get_last_error() == 183                 # ERROR_ALREADY_EXISTS: another copy holds it
    if handle:
        _INSTANCE_LOCK.append(handle)                      # (kept for this copy's whole life; Windows frees it at exit)
    return not already


def hand_over_when_ready(seconds: float = 8.0) -> None:
    """Another copy is running or still starting: bring its window to the front once it can answer."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if hand_over_to_running_copy():
            return
        time.sleep(0.2)


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
    # Ikemen Reader ignores Windows' zoom (Display > Scale): it looks exactly as at 100 % on every screen and zoom,
    # and doesn't change when the zoom does (owner, 2026-10-17). Both before the QApplication: Qt's own scaling off,
    # and text sized at 100 %'s 96 dpi (otherwise points become more pixels at 125 / 150 %). Not passed on to games or
    # Luna (a Qt program too): see outside.own_setting.
    outside.own_setting("QT_ENABLE_HIGHDPI_SCALING", "0")
    outside.own_setting("QT_FONT_DPI", "96")
    # The window's thread gets Python's lock back within 0.5 ms (default 5 ms). Clicking back into the window froze
    # it 50-100 ms whenever the save check / library check was running Python in the background (owner, 2026-10-20):
    # Qt tells every widget about the focus change, each widget's handler needs the lock, and each time the window
    # waited up to 5 ms for a background thread to hand it over. Measured (focus storm with a busy background
    # thread): 80-90 ms -> 0.1 ms. (1 ms doesn't help: Windows rounds that wait up to its timer tick.)
    sys.setswitchinterval(0.0005)
    app = QApplication(sys.argv)
    app.setApplicationName("Ikemen Reader")
    app.setEffectEnabled(Qt.UI_AnimateCombo, False)   # drop-downs slide open by themselves (ThemedCombo)
    if not claim_instance():
        hand_over_when_ready()                  # already open (or opening): that window comes to the front instead
        return
    install_error_net()
    install_menu_lines(app)
    _install_stall_log()
    _install_frame_log()
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
    install_smooth_wheel(app)           # (every scrolling area glides like the library)
    install_smooth_tips(app)            # (hover popups: sooner, fading, following the mouse, inside the window)
    install_popups_to_front(app)        # (pop-ups come to the front of the screen when they open)
    app.setStyleSheet(build_style())
    if settings is not None:
        i18n.set_language(settings.language)
    if settings is None or (not settings.cloud_root and not settings.solo):
        # a new setup (never asked of existing two-device users): one computer = My Device (owner's plan)
        how = message(None, tr("first.body"), tr("first.title"),
                      [("one", tr("first.one")), ("two", tr("first.two"))], dismiss="quit") if settings is None else "two"
        if how == "quit":
            return
        d = SettingsDialog(None, welcome=True, solo=how == "one")
        if not d.exec():
            return
        settings = d.result_settings
    warm_title_bar()                            # (Windows' title bar ready before the real window appears)
    w = MainWindow(settings)
    listen_for_second_copies(w)
    w.place_at_last_position()                  # (where it was when last closed)
    w.prepare_first_screen()                    # (its first picture ready the moment it appears)
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
