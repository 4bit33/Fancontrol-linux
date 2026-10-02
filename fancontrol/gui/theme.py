"""Window theme: the system's, dark, light, plus an optional background image.

The cards and graphs are drawn from the palette, so forcing a dark or light
Fusion palette repaints the whole window consistently no matter what the
desktop uses. A background image is stretched behind the main window; the
cards keep their solid backgrounds, so the text stays readable over it.

The choice is kept per user in ``~/.config``, next to the language, and the
settings dialog applies it right away - no restart needed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

#: What the theme chooser offers; "system" follows the desktop.
THEMES = ("system", "dark", "light")

THEME_KEY = "theme/name"
BACKGROUND_KEY = "theme/background"

#: Pictures the background chooser offers.
IMAGE_PATTERNS = "*.png *.jpg *.jpeg *.bmp *.webp"

BASE_STYLE = """
QFrame#controlCard, QFrame#card {
    border: 1px solid palette(mid);
    border-radius: 10px;
    background: palette(base);
}
QFrame#card:hover {
    border-color: palette(highlight);
}
QFrame#addCard {
    border: 2px dashed palette(mid);
    border-radius: 10px;
    background: transparent;
}
QFrame#addCard:hover {
    border-color: palette(highlight);
}
QFrame#banner {
    background: palette(highlight);
    color: palette(highlighted-text);
}
QFrame#banner QLabel {
    color: palette(highlighted-text);
}
QToolButton#segment {
    border: 1px solid palette(mid);
    border-radius: 4px;
    padding: 2px 10px;
    background: palette(button);
}
QToolButton#segment:checked {
    background: palette(highlight);
    color: palette(highlighted-text);
    border-color: palette(highlight);
}
QLabel#chip {
    border-radius: 8px;
    padding: 1px 8px;
    background: palette(alternate-base);
    color: palette(text);
}
QProgressBar {
    border: none;
    border-radius: 3px;
    background: palette(alternate-base);
}
QProgressBar::chunk {
    border-radius: 3px;
    background: palette(highlight);
}
"""


def settings():
    from PySide6.QtCore import QSettings

    from . import i18n

    return QSettings(*i18n.SETTINGS_SCOPE)


def saved_theme(store=None) -> str:
    """The theme chosen in the settings dialog, or "system"."""

    value = (store or settings()).value(THEME_KEY, "system")
    return value if value in THEMES else "system"


def saved_background(store=None) -> str:
    """The background image chosen in the settings dialog, or "" for none."""

    value = (store or settings()).value(BACKGROUND_KEY, "")
    return value if isinstance(value, str) else ""


def save_theme(name: str, store=None) -> None:
    """Remember ``name`` for the next start; "system" clears the choice."""

    store = store or settings()
    if name in THEMES and name != "system":
        store.setValue(THEME_KEY, name)
    else:
        store.remove(THEME_KEY)
    store.sync()


def save_background(path: str, store=None) -> None:
    """Remember the background image; "" clears it."""

    store = store or settings()
    if path:
        store.setValue(BACKGROUND_KEY, path)
    else:
        store.remove(BACKGROUND_KEY)
    store.sync()


def choose_theme(requested: str | None = None, saved: str | None = None) -> str:
    """Pick a theme: the request, the environment, the saved choice, then the
    system. Anything unknown falls back to "system"."""

    if saved is None and not requested and not os.environ.get("FANCONTROL_THEME"):
        saved = saved_theme()
    for candidate in (requested, os.environ.get("FANCONTROL_THEME"), saved):
        if candidate and candidate.strip().lower() in THEMES:
            return candidate.strip().lower()
    return "system"


def effective(store=None) -> tuple[str, str]:
    """The (theme, background) currently in force."""

    return choose_theme(saved=saved_theme(store)), saved_background(store)


def make_palette(name: str):
    """A full Fusion palette for "dark" or "light"; None for "system"."""

    if name not in ("dark", "light"):
        return None
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette

    dark = name == "dark"
    window = QColor("#353535" if dark else "#efefef")
    text = QColor("#f0f0f0" if dark else "#202020")
    base = QColor("#2b2b2b" if dark else "#ffffff")
    alternate = QColor("#3a3a3a" if dark else "#e4e4e4")
    mid = QColor("#555555" if dark else "#c0c0c0")
    faint = QColor("#aaaaaa" if dark else "#707070")

    palette = QPalette()
    palette.setColor(QPalette.Window, window)
    palette.setColor(QPalette.WindowText, text)
    palette.setColor(QPalette.Base, base)
    palette.setColor(QPalette.AlternateBase, alternate)
    palette.setColor(QPalette.ToolTipBase, base)
    palette.setColor(QPalette.ToolTipText, text)
    palette.setColor(QPalette.Text, text)
    palette.setColor(QPalette.Button, window)
    palette.setColor(QPalette.ButtonText, text)
    palette.setColor(QPalette.BrightText, QColor("#ff0000"))
    palette.setColor(QPalette.Highlight, QColor("#2a82da"))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.Mid, mid)
    palette.setColor(QPalette.Dark, QColor(mid).darker(140))
    palette.setColor(QPalette.Midlight, QColor(mid).lighter(140))
    palette.setColor(QPalette.Light, QColor(mid).lighter(180))
    palette.setColor(QPalette.Shadow, QColor("#000000" if dark else "#777777"))
    palette.setColor(QPalette.PlaceholderText, faint)
    palette.setColor(QPalette.Disabled, QPalette.Text, faint)
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, faint)
    palette.setColor(QPalette.Disabled, QPalette.WindowText, faint)
    return palette


def background_rule(path: str | Path) -> str:
    """The stylesheet bits stretching ``path`` behind the main window.

    The picture is painted on the window itself; the central widget, the
    scroll area, its viewport and the page are made transparent so it shows
    through. The cards keep their solid backgrounds, so the text stays
    readable over it.
    """

    if not path:
        return ""
    return (
        f'\nQMainWindow {{\n    border-image: url("{path}") 0 0 0 0 stretch stretch;\n}}\n'
        "QWidget#central, QScrollArea, QWidget#qt_scrollarea_viewport,\n"
        "QWidget#page {\n    background: transparent;\n}\n"
    )


#: The desktop style, remembered before a forced theme replaces it, so that
#: going back to "system" restores exactly what was there. A separate flag
#: tracks forcing, because a set stylesheet clears the style's object name.
_original_style: str | None = None
_forced = False


def apply(app, name: str, background: str = "", base: str = BASE_STYLE) -> None:
    """Repaint ``app`` with ``name`` and ``background``; persists nothing."""

    global _original_style, _forced
    if _original_style is None:
        _original_style = app.style().objectName() or "fusion"
    if name in ("dark", "light"):
        if not _forced:
            app.setStyle("Fusion")
            _forced = True
        app.setPalette(make_palette(name))
    else:
        if _forced:
            app.setStyle(_original_style)
            _forced = False
        app.setPalette(app.style().standardPalette())
    app.setStyleSheet(base + background_rule(background))


def install(app, requested: str | None = None) -> str:
    """Apply the theme for this start and return it."""

    name = choose_theme(requested)
    if name not in ("dark", "light", "system"):
        log.warning("unknown theme %r, using the system's", requested)
        name = "system"
    apply(app, name, saved_background())
    return name
