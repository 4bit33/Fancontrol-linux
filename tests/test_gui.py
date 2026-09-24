"""Smoke tests for the window and its dialogs.

They run against the simulated hardware with Qt's offscreen platform, so no
display and no real fans are involved. Everything here is about the wiring:
that the window builds from a configuration, that edits reach the service, and
that the import dialog reports the mapping it was given.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="the GUI tests need PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from fancontrol.core.models import Config, CurvePoint, GraphCurve  # noqa: E402
from fancontrol.daemon.service import FanControlService  # noqa: E402
from fancontrol.gui.client import BaseProxy  # noqa: E402
from fancontrol.gui.main_window import MainWindow  # noqa: E402
from fancontrol.gui.widgets.control_card import ControlCard  # noqa: E402
from fancontrol.gui.widgets.curve_editor import make_curve, sample_curve  # noqa: E402
from fancontrol.gui.widgets.curve_graph import CurveGraph  # noqa: E402
from fancontrol.gui.widgets.import_dialog import ImportDialog  # noqa: E402

from .test_importer import SAMPLE  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class ServiceProxy(BaseProxy):
    """A proxy over an in-process service, with no timer of its own."""

    def __init__(self, service: FanControlService) -> None:
        super().__init__()
        self._service = service

    def inventory(self):
        return self._service.get_inventory()

    def status(self):
        return self._service.get_status()

    def config(self):
        return self._service.get_config()

    def set_config(self, config):
        return self._service.set_config(config)

    def set_override(self, control_id, percent):
        return self._service.set_override(control_id, percent)

    def set_control_enabled(self, enabled):
        return self._service.set_control_enabled(enabled)

    def rescan(self):
        return self._service.rescan()

    def calibrate(self, control_id):
        return self._service.calibrate(control_id)

    def import_fancontrol(self, text):
        return self._service.import_fancontrol(text=text)

    def apply_import(self, config, mapping, merge):
        return self._service.apply_import(config, mapping, merge=merge)


@pytest.fixture
def window(qapp, registry, tmp_path):
    service = FanControlService(config_path=tmp_path / "config.json", registry=registry)
    service.start()
    service.tick()
    proxy = ServiceProxy(service)
    win = MainWindow(proxy)
    yield win
    win.close()
    service.shutdown()


# ----------------------------------------------------------------------
# the window


def test_a_card_is_built_for_every_control(window):
    cards = [c for c in window.cards.values() if isinstance(c, ControlCard)]
    assert len(cards) == len(window.config.controls)
    assert all(card.name.text() for card in cards)


def test_curves_and_sensors_get_cards(window):
    assert set(window.curve_cards) == {curve.id for curve in window.config.curves}
    assert window.sensor_cards
    # The curve grid ends with the "add curve" card.
    assert window.curves_section.flow.count() == len(window.config.curves) + 1


def test_status_reaches_every_card(window):
    status = window.proxy.status()
    window._on_status(status)

    card = next(iter(window.cards.values()))
    assert card.reading.text().endswith("%")
    readings = [card.value.text() for card in window.sensor_cards.values()]
    assert any("°C" in text for text in readings)
    assert any(card.reading.text().endswith("%") for card in window.curve_cards.values())


def test_enabling_a_control_reaches_the_service(window):
    card = next(c for c in window.cards.values() if isinstance(c, ControlCard))
    assert card.control.enabled is False

    card.enabled.setChecked(True)
    window._push_timer.stop()
    window._push_config()

    saved = window.proxy.config()
    entry = next(c for c in saved["controls"] if c["id"] == card.control.id)
    assert entry["enabled"] is True


def test_switching_a_control_to_manual_shows_the_slider(window):
    card = next(c for c in window.cards.values() if isinstance(c, ControlCard))
    card.curve.setCurrentIndex(0)  # "Manual"
    assert card.control.curve_id == ""
    assert card.manual_row.isVisibleTo(card)


def test_an_invalid_edit_is_not_sent_to_the_service(window, monkeypatch):
    sent = []
    monkeypatch.setattr(window.proxy, "set_config", lambda config: sent.append(config) or {"ok": True})
    monkeypatch.setattr(
        "fancontrol.gui.main_window.QMessageBox.warning", lambda *a, **k: None
    )

    window.config.controls[0].curve_id = "does-not-exist"
    window._push_config()
    assert sent == []


def test_the_master_switch_goes_through(window):
    window._on_master_toggled(False)
    assert window.proxy.config()["settings"]["control_enabled"] is False
    window._on_master_toggled(True)
    assert window.proxy.config()["settings"]["control_enabled"] is True


def test_renaming_a_sensor_is_remembered(window):
    sensor_id = next(iter(window.proxy.status()["temperatures"]))
    window._on_sensor_renamed(sensor_id, "Kitchen sink")
    window._push_timer.stop()
    window._push_config()
    assert window.proxy.config()["sensor_names"][sensor_id] == "Kitchen sink"


def test_a_curve_still_in_use_is_not_removed(window, monkeypatch):
    shown = []
    monkeypatch.setattr(
        "fancontrol.gui.main_window.QMessageBox.information",
        lambda *args, **kwargs: shown.append(args[2]),
    )
    before = len(window.config.curves)
    window._remove_curve(window.config.curves[0].id)
    assert len(window.config.curves) == before
    assert shown and "still used by" in shown[0]


def test_the_window_speaks_the_chosen_language(qapp, window):
    from fancontrol.gui import i18n

    i18n.set_language("uk")
    try:
        translated = MainWindow(window.proxy)
        assert translated.controls_section.title.text() == "Вентилятори"
        assert translated.action_enabled.text().startswith("Керування")
        translated.close()
    finally:
        i18n.set_language("en")


# ----------------------------------------------------------------------
# widgets


def test_the_graph_widget_round_trips_its_points(qapp):
    graph = CurveGraph()
    points = [CurvePoint(30, 20), CurvePoint(60, 60), CurvePoint(85, 100)]
    graph.set_points(points)
    assert [(p.temperature, p.percent) for p in graph.points()] == [
        (30, 20), (60, 60), (85, 100)
    ]


def test_the_graph_widget_keeps_its_points_sorted(qapp):
    graph = CurveGraph()
    graph.set_points([CurvePoint(80, 90), CurvePoint(30, 20)])
    assert [p.temperature for p in graph.points()] == [30, 80]


def test_pixel_mapping_is_reversible(qapp):
    graph = CurveGraph()
    graph.resize(400, 300)
    pixel = graph._to_pixel(55, 42)
    temperature, percent = graph._to_value(pixel)
    assert (temperature, percent) == (55, 42)


@pytest.mark.parametrize(
    "curve_type", ["graph", "flat", "linear", "target", "trigger", "mix", "sync"]
)
def test_every_curve_type_can_be_created_and_previewed(qapp, curve_type):
    curve = make_curve(curve_type, "test")
    assert curve.type == curve_type
    samples = sample_curve(curve)
    # Mix, sync and target have no shape of their own; the rest must draw.
    if curve_type in {"mix", "sync", "target"}:
        assert samples is None
    else:
        assert samples and all(0 <= p <= 100 for _t, p in samples)


def test_the_import_dialog_offers_the_automatic_answer(qapp, window):
    result = window.proxy.import_fancontrol(json.dumps(SAMPLE))
    dialog = ImportDialog(result, window.inventory)

    mapping = dialog.mapping()
    # The three identifiers the mapper was confident about are pre-selected.
    assert len(mapping) == len(result["mapping"]["applied"])
    assert all(target for target in mapping.values())
    dialog.close()


def test_the_import_dialog_leaves_unmatched_identifiers_unassigned(qapp, window):
    result = window.proxy.import_fancontrol(json.dumps(SAMPLE))
    dialog = ImportDialog(result, window.inventory)
    for win_id in result["mapping"]["unmatched"]:
        assert win_id not in dialog.mapping()
    dialog.close()


def test_importing_through_the_window_replaces_the_configuration(window, monkeypatch):
    from PySide6.QtWidgets import QDialog

    monkeypatch.setattr(
        "fancontrol.gui.main_window.QFileDialog.getOpenFileName",
        lambda *a, **k: (str(_write_sample(window)), ""),
    )
    monkeypatch.setattr(ImportDialog, "exec", lambda self: QDialog.Accepted)
    monkeypatch.setattr(
        "fancontrol.gui.main_window.QMessageBox.information", lambda *a, **k: None
    )

    window._import_fancontrol()
    names = {c.name for c in window.config.controls}
    assert {"CPU cooler", "Case Fans", "GPU Fan"} <= names


def _write_sample(window):
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "userConfig.json"
    path.write_text(json.dumps(SAMPLE))
    return path


def test_no_card_is_narrower_than_its_contents(window):
    """Cards are measured, so nothing on them is cut off in any style or language."""

    for section in window._sections():
        cards = section.cards()
        assert len({card.width() for card in cards}) <= 1, "a grid should line up"
        for card in cards:
            assert card.width() >= card.sizeHint().width()
    widest = max(card.width() for section in window._sections() for card in section.cards())
    assert window.minimumWidth() > widest


def _saved(window):
    window._push_timer.stop()
    window._push_config()
    return window.proxy.config()


def test_a_hidden_sensor_leaves_the_grid_and_is_remembered(qapp, window):
    sensor_id = next(iter(window.sensor_cards))
    window._set_hidden("sensor", sensor_id, True)
    qapp.processEvents()
    assert sensor_id not in window.sensor_cards
    assert window.temperatures_section.show_hidden.isVisibleTo(window)
    assert _saved(window)["hidden_sensors"] == [sensor_id]

    # "Show hidden" brings it back, dimmed, and it can be un-hidden from there.
    window.temperatures_section.show_hidden.setChecked(True)
    assert window.sensor_cards[sensor_id].graphicsEffect() is not None
    window._set_hidden("sensor", sensor_id, False)
    qapp.processEvents()
    assert window.sensor_cards[sensor_id].graphicsEffect() is None
    assert _saved(window)["hidden_sensors"] == []


def test_hiding_a_curve_does_not_change_what_the_fans_do(qapp, window):
    curve = window.config.curves[0]
    users = [c.id for c in window.config.controls if c.curve_id == curve.id]
    window._set_hidden("curve", curve.id, True)
    qapp.processEvents()
    assert curve.id not in window.curve_cards
    saved = _saved(window)
    assert next(c for c in saved["curves"] if c["id"] == curve.id)["hidden"] is True
    assert [c["id"] for c in saved["controls"] if c["curve_id"] == curve.id] == users


def test_a_hidden_fan_can_be_brought_back(qapp, window):
    control = window.config.controls[0]
    window._set_hidden("control", control.id, True)
    qapp.processEvents()
    assert control.id not in window.cards
    window.controls_section.show_hidden.setChecked(True)
    assert control.id in window.cards
    window._set_hidden("control", control.id, False)
    qapp.processEvents()
    # With nothing left hidden the switch turns itself off.
    assert not window.controls_section.show_hidden.isChecked()
    assert control.id in window.cards
