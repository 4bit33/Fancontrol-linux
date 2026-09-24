"""The window is fully translated, and stays that way.

Two checks, both done by reading the source rather than running the window:

* every string passed to ``tr()`` or ``N_()`` has a Ukrainian translation, and
  the translation keeps the same ``{placeholders}``;
* no user-visible Qt call - a label, a button, a tooltip, a message box - is
  given a bare string literal. Such a string would silently stay English.
"""

from __future__ import annotations

import ast
import re
import string
from pathlib import Path

import pytest

from fancontrol.gui import i18n
from fancontrol.gui.locales import uk

GUI = Path(__file__).resolve().parent.parent / "fancontrol" / "gui"
SOURCES = [p for p in GUI.rglob("*.py") if "locales" not in p.parts and p.name != "i18n.py"]

#: Qt calls whose string arguments end up on screen.
VISIBLE_CALLS = {
    "QLabel", "QPushButton", "QToolButton", "QCheckBox", "QRadioButton", "QAction",
    "QGroupBox", "setText", "setToolTip", "setWindowTitle", "setPlaceholderText",
    "setSpecialValueText", "addRow", "addItem", "addAction", "showMessage",
    "setHeaderLabels", "information", "warning", "critical", "question",
    "getOpenFileName", "getText", "setTitle", "setSuffix",
}

#: Only these receivers show text; a logger's warning() does not.
MESSAGE_BOX = {"information", "warning", "critical", "question"}

#: Strings that are the same in every language.
NEUTRAL = re.compile(r"^[\s\W\d%°]*(rpm|s|°C|%|%/s|×)?[\s\W\d]*$")


def _calls(tree: ast.AST, names: set[str]):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in names:
                yield node


def _marked_strings() -> set[str]:
    found: set[str] = set()
    for path in SOURCES:
        tree = ast.parse(path.read_text())
        for call in _calls(tree, {"tr", "N_"}):
            if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                found.add(call.args[0].value)
    return found


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_there_is_something_to_translate():
    assert len(_marked_strings()) > 100


@pytest.mark.parametrize("text", sorted(_marked_strings()))
def test_every_string_has_a_ukrainian_translation(text):
    assert text in uk.CATALOG, f"no Ukrainian for: {text!r}"
    assert _fields(uk.CATALOG[text]) == _fields(text), "placeholders differ"


def test_the_catalog_has_no_leftovers():
    """Entries nobody uses any more only rot."""

    unused = set(uk.CATALOG) - _marked_strings()
    assert not unused, f"unused translations: {sorted(unused)[:10]}"


def test_no_visible_string_escapes_translation():
    offenders = []
    for path in SOURCES:
        tree = ast.parse(path.read_text())
        for call in _calls(tree, VISIBLE_CALLS):
            func = call.func
            if getattr(func, "attr", None) in MESSAGE_BOX and not (
                isinstance(func.value, ast.Name) and func.value.id == "QMessageBox"
            ):
                continue  # log.warning and friends stay in English
            for arg in call.args:
                literal = None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    literal = arg.value
                elif isinstance(arg, ast.JoinedStr):
                    # Only the literal parts of an f-string are text to translate.
                    literal = "".join(
                        part.value for part in arg.values
                        if isinstance(part, ast.Constant) and isinstance(part.value, str)
                    )
                if literal and any(c.isalpha() for c in literal) and not NEUTRAL.match(literal):
                    offenders.append(f"{path.name}:{call.lineno}: {literal[:50]!r}")
    assert not offenders, "\n".join(offenders)


def test_the_language_follows_the_environment(monkeypatch):
    monkeypatch.setenv("FANCONTROL_LANG", "uk_UA.UTF-8")
    assert i18n.choose_language() == "uk"
    monkeypatch.setenv("FANCONTROL_LANG", "de_DE.UTF-8")
    assert i18n.choose_language() == "en"


def test_tr_falls_back_to_english_and_fills_placeholders():
    i18n.set_language("uk")
    try:
        assert i18n.tr("Controls") != "Controls"
        assert i18n.tr("not in any catalog") == "not in any catalog"
        assert "CPU" in i18n.tr("Calibrating {name}…", name="CPU")
    finally:
        i18n.set_language("en")
    assert i18n.tr("Controls") == "Controls"


def test_a_saved_choice_beats_the_system_but_not_the_environment(monkeypatch):
    monkeypatch.delenv("FANCONTROL_LANG", raising=False)
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    assert i18n.choose_language(saved="uk") == "uk"
    assert i18n.choose_language(saved="en") == "en"
    monkeypatch.setenv("FANCONTROL_LANG", "en")
    assert i18n.choose_language(saved="uk") == "en"
    assert i18n.choose_language(requested="uk", saved="en") == "uk"
