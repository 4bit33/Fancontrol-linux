"""Translating the window into the system's language.

Every string the window shows goes through :func:`tr`, keyed by its English
text. A language is a module in :mod:`fancontrol.gui.locales` holding one dict
from English to that language; anything missing falls back to English, so a
half-finished translation still works.

Text that is defined before the language is known - module-level tables such
as the curve descriptions - is marked with :func:`N_` and passed through
:func:`tr` where it is shown. ``tests/test_i18n.py`` collects both kinds of
call and fails when a string has no translation, or when a translation drops
or renames a ``{placeholder}``.

The language comes from ``FANCONTROL_LANG`` if set, otherwise from the system
locale, so a Ukrainian desktop gets Ukrainian and everything else English.
"""

from __future__ import annotations

import importlib
import logging
import os

log = logging.getLogger(__name__)

#: Languages with a catalog, besides English.
AVAILABLE = ("uk",)

_catalog: dict[str, str] = {}
_language = "en"


def N_(text: str) -> str:  # noqa: N802 - the conventional gettext name
    """Mark text for translation without translating it yet."""
    return text


def tr(text: str, **values: object) -> str:
    """The text in the current language, with ``{placeholders}`` filled in."""

    translated = _catalog.get(text, text)
    return translated.format(**values) if values else translated


def language() -> str:
    return _language


def choose_language(requested: str | None = None) -> str:
    """Pick a language code from an explicit request or the environment."""

    candidates = [requested, os.environ.get("FANCONTROL_LANG")]
    if not any(candidates):
        try:
            from PySide6.QtCore import QLocale

            candidates.append(QLocale.system().name())
        except ImportError:
            pass
        candidates += [os.environ.get(name) for name in ("LC_ALL", "LC_MESSAGES", "LANG")]
    for candidate in candidates:
        if not candidate:
            continue
        code = candidate.split(".")[0].split("_")[0].split("-")[0].lower()
        if code in AVAILABLE or code == "en":
            return code
    return "en"


def set_language(code: str) -> None:
    """Load the catalog for ``code``; English needs none."""

    global _catalog, _language
    _language = code
    if code == "en":
        _catalog = {}
        return
    try:
        module = importlib.import_module(f"{__package__}.locales.{code}")
        _catalog = dict(module.CATALOG)
    except (ImportError, AttributeError):
        log.warning("no translation for %r, using English", code)
        _catalog = {}
        _language = "en"


def install(app, requested: str | None = None) -> str:
    """Set the language for the program and for Qt's own dialogs.

    Qt ships translations for its standard buttons and dialogs (OK, Cancel,
    the file chooser); without loading them those would stay in English in an
    otherwise Ukrainian window.
    """

    code = choose_language(requested)
    set_language(code)
    if code != "en":
        from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator

        path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        for name in ("qt", "qtbase"):
            translator = QTranslator(app)
            if translator.load(QLocale(code), name, "_", path):
                app.installTranslator(translator)
                break
    return code
