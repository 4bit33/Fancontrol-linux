"""The window theme: choosing, remembering, and repainting.

The palette checks need Qt; choosing and the stylesheet bits do not.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="the theme needs Qt")

from PySide6.QtCore import QSettings  # noqa: E402

from fancontrol.gui import theme  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path):
    return QSettings(str(tmp_path / "gui.conf"), QSettings.IniFormat)


def test_the_system_is_the_default(monkeypatch):
    monkeypatch.delenv("FANCONTROL_THEME", raising=False)
    assert theme.choose_theme() == "system"
    assert theme.choose_theme(requested="nope") == "system"
    assert theme.choose_theme(saved="nope") == "system"


def test_request_beats_environment_beats_saved(monkeypatch):
    monkeypatch.setenv("FANCONTROL_THEME", "dark")
    assert theme.choose_theme(saved="light") == "dark"
    assert theme.choose_theme(requested="light", saved="dark") == "light"


def test_a_saved_choice_is_remembered(store):
    assert theme.saved_theme(store) == "system"
    theme.save_theme("dark", store)
    assert theme.saved_theme(store) == "dark"
    assert theme.effective(store) == ("dark", "")
    theme.save_theme("system", store)
    assert theme.saved_theme(store) == "system"


def test_a_background_is_remembered(store):
    assert theme.saved_background(store) == ""
    theme.save_background("/tmp/wallpaper.png", store)
    assert theme.saved_background(store) == "/tmp/wallpaper.png"
    theme.save_background("", store)
    assert theme.saved_background(store) == ""


def test_the_background_rule_is_empty_without_an_image():
    assert theme.background_rule("") == ""


def test_the_background_rule_stretches_the_image():
    rule = theme.background_rule("/tmp/wallpaper.png")
    assert 'url("/tmp/wallpaper.png")' in rule
    assert "stretch stretch" in rule
    assert "transparent" in rule


def test_dark_and_light_paint_differently(qapp):
    from PySide6.QtGui import QPalette

    dark = theme.make_palette("dark")
    light = theme.make_palette("light")
    assert theme.make_palette("system") is None
    assert dark.color(QPalette.Window).lightness() < 128
    assert light.color(QPalette.Window).lightness() > 128
    assert dark.color(QPalette.WindowText) != light.color(QPalette.WindowText)


def test_applying_repaints_the_application(qapp):
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    theme.apply(app, "dark", "", theme.BASE_STYLE)
    dark_window = app.palette().color(QPalette.Window).lightness()
    assert dark_window < 128
    assert "border-image" not in app.styleSheet()

    theme.apply(app, "dark", "/tmp/wallpaper.png", theme.BASE_STYLE)
    assert 'url("/tmp/wallpaper.png")' in app.styleSheet()

    theme.apply(app, "light", "", theme.BASE_STYLE)
    assert app.palette().color(QPalette.Window).lightness() > dark_window

    theme.apply(app, "system", "", theme.BASE_STYLE)
    assert "border-image" not in app.styleSheet()
