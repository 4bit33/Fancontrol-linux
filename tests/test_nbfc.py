"""Fan speed percent from nbfc: parsing and remembering the choice.

Querying runs a process, so it is faked here; parsing is pure.
"""

from __future__ import annotations

import pytest

from fancontrol.gui import nbfc  # noqa: E402

STATUS = """\
Read-only                : false
Selected Config Name     : HP Pavilion Gaming Laptop 15-ec1xxx

Fan Display Name         : CPU Fan
Temperature              : 54.50
Auto Control Enabled     : true
Critical Mode Enabled    : false
Current Fan Speed        : 35.00
Target Fan Speed         : 50.00
Fan Speed Steps          : 80
"""


def test_speeds_are_parsed_in_order():
    assert nbfc.parse_speeds(STATUS) == [35.0]
    assert nbfc.parse_speeds("nothing here\n") == []
    assert nbfc.parse_speeds("") == []


def test_negative_readings_parse_too():
    assert nbfc.parse_speeds("Current Fan Speed        : -28.57\n") == [-28.57]


def test_no_binary_means_unavailable(monkeypatch):
    monkeypatch.setattr(nbfc.shutil, "which", lambda _exe: None)
    assert nbfc.query() is None


def test_a_failing_nbfc_means_unavailable(monkeypatch):
    monkeypatch.setattr(nbfc.shutil, "which", lambda _exe: "/usr/bin/nbfc")

    class _Failed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(nbfc.subprocess, "run", lambda *a, **k: _Failed())
    assert nbfc.query() is None


def test_a_working_nbfc_reports_its_speeds(monkeypatch):
    monkeypatch.setattr(nbfc.shutil, "which", lambda _exe: "/usr/bin/nbfc")

    class _Ok:
        returncode = 0
        stdout = STATUS

    monkeypatch.setattr(nbfc.subprocess, "run", lambda *a, **k: _Ok())
    assert nbfc.query() == [35.0]


def test_the_choice_defaults_to_revolutions(tmp_path):
    from PySide6.QtCore import QSettings

    store = QSettings(str(tmp_path / "gui.conf"), QSettings.IniFormat)
    assert nbfc.saved_display(store) == "rpm"
    nbfc.save_display("percent", store)
    assert nbfc.saved_display(store) == "percent"
    nbfc.save_display("rpm", store)
    assert nbfc.saved_display(store) == "rpm"
