"""The main window: one scrolling page of cards, the way FanControl does it.

Four sections, each a grid that wraps to the window's width:

* **Controls** - one card per fan, with its speed, curve and settings;
* **Curves** - one card per curve, with a small graph and what it outputs now;
* **Temperatures** and **Fan speeds** - every sensor as a small tile.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QScrollArea,
    QStatusBar,
    QSystemTrayIcon,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core.models import Config, validate
from .client import BaseProxy, ProxyError
from .i18n import tr
from .widgets.cards import CURVE_CARD_WIDTH, AddCard, CurveCard, Section, SensorCard
from .widgets.control_card import ControlCard
from .widgets.curve_editor import CurveEditorDialog, NewCurveDialog, make_curve
from .widgets.import_dialog import ImportDialog

log = logging.getLogger(__name__)

#: How long to wait after the last edit before sending the configuration, so
#: dragging a slider does not produce one D-Bus round trip per pixel.
PUSH_DELAY_MS = 400


class SettingsDialog(QDialog):
    """Global settings: update rate, failsafe and the critical temperature."""

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Settings"))

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 30.0)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" s")
        self.interval.setValue(config.settings.update_interval)
        self.interval.setToolTip(tr("How often the curves are evaluated."))

        self.critical = QDoubleSpinBox()
        self.critical.setRange(0, 120)
        self.critical.setSuffix(" °C")
        self.critical.setValue(config.settings.critical_temperature)
        self.critical.setToolTip(tr(
            "Every managed fan goes to 100% above this temperature.\n"
            "Set to 0 to switch the override off."
        ))

        self.failsafe = QDoubleSpinBox()
        self.failsafe.setRange(0, 100)
        self.failsafe.setSuffix(" %")
        self.failsafe.setValue(config.settings.failsafe_percent)
        self.failsafe.setToolTip(tr(
            "The speed used when a sensor a curve needs cannot be read.\n"
            "Leave this high: a fan running too fast is better than a hot chip."
        ))

        self.restore = QCheckBox(tr("Hand the fans back to the firmware when the daemon stops"))
        self.restore.setChecked(config.settings.restore_on_exit)

        form.addRow(tr("Update every"), self.interval)
        form.addRow(tr("Force full speed above"), self.critical)
        form.addRow(tr("Speed when a sensor fails"), self.failsafe)
        form.addRow("", self.restore)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to(self, config: Config) -> None:
        config.settings.update_interval = self.interval.value()
        config.settings.critical_temperature = self.critical.value()
        config.settings.failsafe_percent = self.failsafe.value()
        config.settings.restore_on_exit = self.restore.isChecked()


class MainWindow(QMainWindow):
    def __init__(self, proxy: BaseProxy) -> None:
        super().__init__()
        self.proxy = proxy
        self.config = Config()
        self.inventory: dict = {}
        self.cards: dict[str, ControlCard] = {}
        self.curve_cards: dict[str, CurveCard] = {}
        self.sensor_cards: dict[str, SensorCard] = {}
        self.latest_status: dict = {}
        self._overrides: dict[str, float] = {}
        #: Controls whose calibration we are waiting on.
        self._calibrating: set[str] = set()

        self.setWindowTitle(tr("Fan Control"))
        self.resize(1280, 820)

        self._push_timer = QTimer(self)
        self._push_timer.setSingleShot(True)
        self._push_timer.setInterval(PUSH_DELAY_MS)
        self._push_timer.timeout.connect(self._push_config)

        self._build_ui()
        self._build_actions()
        self._build_tray()

        proxy.statusChanged.connect(self._on_status)
        proxy.failed.connect(self._on_failed)

        self.refresh_all()

    # ------------------------------------------------------------------
    # construction

    def _build_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self.setCentralWidget(scroll)

        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(22)

        self.controls_section = Section(tr("Controls"))
        self.curves_section = Section(tr("Curves"))
        self.temperatures_section = Section(tr("Temperatures"))
        self.speeds_section = Section(tr("Fan speeds"))
        for section in (self.controls_section, self.curves_section,
                        self.temperatures_section, self.speeds_section):
            layout.addWidget(section)
        layout.addStretch(1)
        scroll.setWidget(page)

        self.setStatusBar(QStatusBar())
        self.status_label = QLabel("")
        self.status_label.setEnabled(False)
        self.statusBar().addPermanentWidget(self.status_label)

    def _build_actions(self) -> None:
        toolbar = QToolBar(tr("Main"))
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        self.action_enabled = QAction(
            QIcon.fromTheme("media-playback-start"), tr("Fan control on"), self
        )
        self.action_enabled.setCheckable(True)
        self.action_enabled.setToolTip(
            tr("When off, the fans go back to whatever the motherboard firmware does.")
        )
        self.action_enabled.toggled.connect(self._on_master_toggled)
        toolbar.addAction(self.action_enabled)
        toolbar.addSeparator()

        action_add = QAction(QIcon.fromTheme("list-add"), tr("Add curve"), self)
        action_add.triggered.connect(self._add_curve)
        toolbar.addAction(action_add)

        action_import = QAction(
            QIcon.fromTheme("document-import"), tr("Import from FanControl…"), self
        )
        action_import.setToolTip(tr("Read a userConfig.json from FanControl on Windows"))
        action_import.triggered.connect(self._import_fancontrol)
        toolbar.addAction(action_import)

        action_rescan = QAction(QIcon.fromTheme("view-refresh"), tr("Rescan hardware"), self)
        action_rescan.triggered.connect(self._rescan)
        toolbar.addAction(action_rescan)

        action_settings = QAction(QIcon.fromTheme("configure"), tr("Settings…"), self)
        action_settings.triggered.connect(self._open_settings)
        toolbar.addAction(action_settings)

        action_quit = QAction(tr("Quit"), self)
        action_quit.setShortcut(QKeySequence.Quit)
        action_quit.triggered.connect(QApplication.quit)
        self.addAction(action_quit)

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        icon = QIcon.fromTheme("sensors-fan", QIcon.fromTheme("computer"))
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip(tr("Fan Control"))

        menu = QMenu()
        self.tray_toggle = menu.addAction(tr("Fan control on"))
        self.tray_toggle.setCheckable(True)
        self.tray_toggle.toggled.connect(self._on_master_toggled)
        menu.addSeparator()
        menu.addAction(tr("Show window"), self._show_window)
        menu.addAction(tr("Quit"), QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_window()
            if reason == QSystemTrayIcon.Trigger else None
        )
        self.tray.show()

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # ------------------------------------------------------------------
    # loading

    def refresh_all(self) -> None:
        try:
            self.inventory = self.proxy.inventory()
            self.config = Config.from_dict(self.proxy.config())
        except ProxyError as exc:
            QMessageBox.critical(self, tr("Fan Control"), str(exc))
            return

        self._set_master(self.config.settings.control_enabled)
        self._rebuild_controls()
        self._rebuild_curves()
        self._rebuild_sensors()
        self.status_label.setText(tr(
            "{count} outputs · {path}",
            count=len(self.inventory.get("controls", [])),
            path=self.inventory.get("config_path", ""),
        ))
        if self.latest_status:
            self._on_status(self.latest_status)
        self._fit_minimum_width()

    def _sections(self) -> list[Section]:
        return [self.controls_section, self.curves_section,
                self.temperatures_section, self.speeds_section]

    def _fit_minimum_width(self) -> None:
        """Never let the window get narrower than its widest card."""

        widest = max((card.minimumWidth() for section in self._sections()
                      for card in section.cards()), default=0)
        margins = self.centralWidget().widget().layout().contentsMargins()
        scrollbar = self.style().pixelMetric(self.style().PixelMetric.PM_ScrollBarExtent)
        self.setMinimumWidth(widest + margins.left() + margins.right() + scrollbar + 8)

    def _fit_to_screen(self) -> None:
        """Open wide enough for three fan cards or four curves in a row,
        if the screen allows."""

        screen = self.screen().availableGeometry() if self.screen() else None
        if screen is None:
            return
        spacing = self.controls_section.flow.spacing()
        widths = [
            per_row * card.minimumWidth() + (per_row - 1) * spacing
            for section, per_row in ((self.controls_section, 3), (self.curves_section, 4))
            for card in section.cards()[:1]
        ]
        margins = self.centralWidget().widget().layout().contentsMargins()
        scrollbar = self.style().pixelMetric(self.style().PixelMetric.PM_ScrollBarExtent)
        want = max(widths, default=self.width()) + margins.left() + margins.right() + scrollbar + 8
        width = max(self.minimumWidth(), min(want, int(screen.width() * 0.95)))
        height = min(max(self.height(), 820), int(screen.height() * 0.9))
        self.resize(width, height)

    def showEvent(self, event) -> None:  # noqa: N802
        if not getattr(self, "_sized", False):
            self._sized = True
            self._fit_to_screen()
        super().showEvent(event)

    def _rebuild_controls(self) -> None:
        self.controls_section.clear()
        self.cards.clear()

        visible = [c for c in self.config.controls if not c.hidden]
        for control in visible:
            card = ControlCard(control, self.config, self.inventory)
            card.configEdited.connect(self._on_control_edited)
            card.calibrateRequested.connect(self._calibrate)
            card.editCurveRequested.connect(self._edit_curve_by_id)
            self.controls_section.add(card)
            self.cards[control.id] = card
        self.controls_section.set_count(len(visible))
        self.controls_section.equalize()

        if not visible:
            empty = QLabel(tr(
                "No controllable fans were found.\n\n"
                "On most desktop boards the super-I/O driver has to be loaded "
                "first. Run 'fanctl doctor' to see what is missing, then use "
                "Rescan hardware."
            ))
            empty.setWordWrap(True)
            empty.setEnabled(False)
            empty.setFixedWidth(520)
            self.controls_section.add(empty)

    def _rebuild_curves(self) -> None:
        self.curves_section.clear()
        self.curve_cards.clear()
        for curve in self.config.curves:
            card = CurveCard(curve, self.config, self.inventory)
            card.editRequested.connect(self._edit_curve_by_id)
            card.removeRequested.connect(self._remove_curve)
            self.curves_section.add(card)
            self.curve_cards[curve.id] = card
        add = AddCard(tr("Add curve"), CURVE_CARD_WIDTH, 110)
        add.clicked.connect(self._add_curve)
        self.curves_section.add(add)
        self.curves_section.set_count(len(self.config.curves))
        self.curves_section.equalize()
        if self.latest_status:
            for card in self.curve_cards.values():
                card.update_status(self.latest_status)

    def _rebuild_sensors(self) -> None:
        for section in (self.temperatures_section, self.speeds_section):
            section.clear()
        self.sensor_cards.clear()
        names = self.config.sensor_names

        for kind, section, key in (
            ("temperature", self.temperatures_section, "temperatures"),
            ("fan", self.speeds_section, "fans"),
        ):
            entries = self.inventory.get(key, [])
            for entry in entries:
                card = SensorCard(
                    entry["id"], names.get(entry["id"]) or entry["name"],
                    entry.get("device", {}).get("label", ""), kind,
                )
                card.renameRequested.connect(self._rename_sensor)
                section.add(card)
                self.sensor_cards[entry["id"]] = card
            section.set_count(len(entries))
            section.equalize()

    def _reload_control_cards(self) -> None:
        for card in self.cards.values():
            card.reload(card.control, self.config, self.inventory)

    # ------------------------------------------------------------------
    # live updates

    @Slot(dict)
    def _on_status(self, status: dict) -> None:
        if not status:
            return
        self.latest_status = status

        for control_id, entry in status.get("controls", {}).items():
            card = self.cards.get(control_id)
            if card is not None:
                card.set_overridden(control_id in self._overrides)
                card.update_status(entry)

        for card in self.curve_cards.values():
            card.update_status(status)

        readings = {**status.get("temperatures", {}), **status.get("fans", {})}
        for sensor_id, card in self.sensor_cards.items():
            card.update_value(readings.get(sensor_id))

        self._follow_calibrations(status)

        messages = status.get("messages", [])
        if messages:
            self.statusBar().showMessage(messages[0], 4000)

    def _follow_calibrations(self, status: dict) -> None:
        """Show progress while a calibration runs, and react when it ends."""

        reports = status.get("calibration", {})
        for control_id in list(self._calibrating):
            report = reports.get(control_id)
            if not report:
                continue
            if report.get("state") == "running":
                control = self.config.control_by_id(control_id)
                self.statusBar().showMessage(tr(
                    "Calibrating {name} — now at {percent}%",
                    name=control.name if control else control_id,
                    percent=f"{report.get('percent', 0):.0f}",
                ))
                continue
            self._calibrating.discard(control_id)
            self._on_calibration_finished(control_id, report)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        QMessageBox.warning(self, tr("Fan Control"), message)

    # ------------------------------------------------------------------
    # editing

    def _schedule_push(self) -> None:
        self._push_timer.start()

    def _on_control_edited(self) -> None:
        # Another curve may now drive this fan, which changes the curve
        # cards' "drives" line; rebuild them once the edit has returned.
        self._schedule_push()
        QTimer.singleShot(0, self._rebuild_curves)

    def _push_config(self) -> None:
        problems = validate(self.config)
        if problems:
            QMessageBox.warning(
                self, tr("Fan Control"),
                tr("This configuration cannot be applied:") + "\n\n  " + "\n  ".join(problems),
            )
            return
        if self.proxy.push_config(self.config.to_dict()):
            self.statusBar().showMessage(tr("Saved"), 1500)

    def _set_master(self, enabled: bool) -> None:
        label = tr("Fan control on") if enabled else tr("Fan control off")
        for action in (self.action_enabled, getattr(self, "tray_toggle", None)):
            if action is None:
                continue
            action.blockSignals(True)
            action.setChecked(enabled)
            action.setText(label)
            action.blockSignals(False)

    def _on_master_toggled(self, enabled: bool) -> None:
        result = self.proxy.set_control_enabled(enabled)
        if not result.get("ok"):
            QMessageBox.warning(self, tr("Fan Control"), result.get("error", tr("failed")))
            self._set_master(not enabled)
            return
        self.config.settings.control_enabled = enabled
        self._set_master(enabled)

    def _sensor_name(self, sensor_id: str) -> str:
        if sensor_id in self.config.sensor_names:
            return self.config.sensor_names[sensor_id]
        for key in ("temperatures", "fans"):
            for entry in self.inventory.get(key, []):
                if entry["id"] == sensor_id:
                    return entry["name"]
        return ""

    def _rename_sensor(self, sensor_id: str) -> None:
        name, accepted = QInputDialog.getText(
            self, tr("Rename sensor"), tr("Name (leave empty for the original):"),
            text=self._sensor_name(sensor_id),
        )
        if not accepted:
            return
        self._on_sensor_renamed(sensor_id, name.strip())
        self._rebuild_sensors()
        self._rebuild_curves()
        if self.latest_status:
            self._on_status(self.latest_status)

    def _on_sensor_renamed(self, sensor_id: str, name: str) -> None:
        original = next(
            (entry["name"] for key in ("temperatures", "fans")
             for entry in self.inventory.get(key, []) if entry["id"] == sensor_id),
            None,
        )
        if name and name != original:
            self.config.sensor_names[sensor_id] = name
        else:
            self.config.sensor_names.pop(sensor_id, None)
        self._schedule_push()

    def _add_curve(self) -> None:
        chooser = NewCurveDialog(self)
        if chooser.exec() != QDialog.Accepted:
            return
        name = tr("Curve {number}", number=len(self.config.curves) + 1)
        curve = make_curve(chooser.selected_type(), name)
        dialog = CurveEditorDialog(
            curve, self.config, self.inventory,
            self.latest_status.get("temperatures", {}), self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.config.curves.append(curve)
        self._after_curves_changed()

    def _edit_curve_by_id(self, curve_id: str) -> None:
        curve = self.config.curve_by_id(curve_id)
        if curve is None:
            return
        dialog = CurveEditorDialog(
            curve, self.config, self.inventory,
            self.latest_status.get("temperatures", {}), self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self._after_curves_changed()

    def _remove_curve(self, curve_id: str) -> None:
        curve = self.config.curve_by_id(curve_id)
        if curve is None:
            return

        users = [c.name for c in self.config.controls if c.curve_id == curve_id]
        users += [
            other.name for other in self.config.curves
            if curve_id in other.curve_refs()
        ]
        if users:
            QMessageBox.information(
                self, tr("Fan Control"),
                tr("“{name}” is still used by: {users}.\n\n"
                   "Point those at another curve first.",
                   name=curve.name, users=", ".join(sorted(set(users)))),
            )
            return

        if QMessageBox.question(
            self, tr("Fan Control"), tr("Remove the curve “{name}”?", name=curve.name)
        ) != QMessageBox.Yes:
            return
        self.config.curves = [c for c in self.config.curves if c.id != curve_id]
        self._after_curves_changed()

    def _after_curves_changed(self) -> None:
        self._rebuild_curves()
        self._reload_control_cards()
        self._schedule_push()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self)
        if dialog.exec() == QDialog.Accepted:
            dialog.apply_to(self.config)
            self._push_config()

    # ------------------------------------------------------------------
    # actions

    def _rescan(self) -> None:
        result = self.proxy.rescan()
        if not result.get("ok"):
            QMessageBox.warning(self, tr("Fan Control"), result.get("error", tr("rescan failed")))
            return
        self.refresh_all()
        self.statusBar().showMessage(tr("Hardware re-enumerated"), 2000)

    def _calibrate(self, control_id: str) -> None:
        control = self.config.control_by_id(control_id)
        if control is None:
            return
        if QMessageBox.question(
            self, tr("Calibrate"),
            tr("This spins {name} all the way up and down to find out where it "
               "stops and starts turning.\n\nIt takes a few minutes and the fan "
               "will be noisy. Continue?", name=control.name),
        ) != QMessageBox.Yes:
            return

        try:
            result = self.proxy.calibrate(control_id)
        except ProxyError as exc:
            QMessageBox.warning(self, tr("Calibrate"), str(exc))
            return

        if not result.get("ok"):
            QMessageBox.warning(
                self, tr("Calibrate"), result.get("error", tr("calibration failed"))
            )
            return

        # It runs in the daemon and reports through the status, so the window
        # stays live while the fan is stepped up and down.
        self._calibrating.add(control_id)
        self.statusBar().showMessage(tr("Calibrating {name}…", name=control.name))

    def _on_calibration_finished(self, control_id: str, result: dict) -> None:
        control = self.config.control_by_id(control_id)
        if control is None:
            return
        self.statusBar().clearMessage()

        if not result.get("ok"):
            QMessageBox.warning(
                self, tr("Calibrate"), result.get("error", tr("calibration failed"))
            )
            return

        minimum = result.get("suggested_min_percent")
        start = result.get("suggested_start_percent")
        if minimum is None and start is None:
            QMessageBox.information(
                self, tr("Calibrate"),
                tr("{name} kept turning all the way down to 0%, so it has no "
                   "minimum to speak of.", name=control.name),
            )
            return

        lines = [f"{control.name}:"]
        if result.get("stop_percent") is not None:
            lines.append(tr("  stops turning below {percent}%",
                            percent=f"{result['stop_percent']:.0f}"))
        if result.get("start_percent") is not None:
            lines.append(tr("  starts turning at {percent}%",
                            percent=f"{result['start_percent']:.0f}"))
        lines.append("")
        lines.append(tr("Apply the suggested settings?"))
        if minimum is not None:
            lines.append(tr("  minimum speed → {percent}%", percent=f"{minimum:.0f}"))
        if start is not None:
            lines.append(tr("  start speed → {percent}%", percent=f"{start:.0f}"))

        if QMessageBox.question(self, tr("Calibrate"), "\n".join(lines)) == QMessageBox.Yes:
            if minimum is not None:
                control.min_percent = float(minimum)
            if start is not None:
                control.start_percent = float(start)
            if result.get("suggested_stop_percent") is not None:
                control.stop_percent = float(result["suggested_stop_percent"])
            control.calibration = [[s["percent"], s["rpm"]] for s in result.get("samples", [])]
            self._push_config()
            self.refresh_all()

    def _import_fancontrol(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, tr("Open a FanControl configuration"), str(Path.home()),
            tr("FanControl configuration (*.json);;All files (*)"),
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            QMessageBox.warning(self, tr("Import"), str(exc))
            return

        result = self.proxy.import_fancontrol(text)
        if not result.get("ok"):
            QMessageBox.warning(
                self, tr("Import"),
                result.get("error", tr("This file could not be read as a FanControl configuration.")),
            )
            return

        dialog = ImportDialog(result, self.inventory, self)
        if dialog.exec() != QDialog.Accepted:
            return

        outcome = self.proxy.apply_import(
            dialog.config(), dialog.mapping(), dialog.merge_requested()
        )
        if not outcome.get("ok"):
            message = outcome.get("error", tr("The imported configuration was rejected."))
            for problem in outcome.get("problems", []):
                message += f"\n  {problem}"
            QMessageBox.warning(self, tr("Import"), message)
            return

        self.refresh_all()
        skipped = outcome.get("skipped") or []
        if skipped:
            lines = [f"{entry['name']} — {entry['reason']}" for entry in skipped]
            QMessageBox.information(
                self, tr("Import"),
                tr("Imported. These fans were left switched off:") + "\n\n  " + "\n  ".join(lines),
            )
        else:
            self.statusBar().showMessage(tr("Imported"), 3000)

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._push_timer.isActive():
            self._push_timer.stop()
            self._push_config()
        super().closeEvent(event)
