"""The main window."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Slot
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QSystemTrayIcon,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.models import Config, CurvePoint, GraphCurve, validate
from .client import BaseProxy, ProxyError
from .widgets.control_card import ControlCard
from .widgets.curve_editor import (
    CURVE_DESCRIPTIONS,
    CurveEditorDialog,
    NewCurveDialog,
    curve_range,
    make_curve,
    sample_curve,
)
from .widgets.curve_graph import CurveGraph
from .widgets.import_dialog import ImportDialog
from .widgets.sensor_panel import SensorPanel

log = logging.getLogger(__name__)

#: How long to wait after the last edit before sending the configuration, so
#: dragging a slider does not produce one D-Bus round trip per pixel.
PUSH_DELAY_MS = 400


class SettingsDialog(QDialog):
    """Global settings: update rate, failsafe and the critical temperature."""

    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self._config = config

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0.2, 30.0)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" s")
        self.interval.setValue(config.settings.update_interval)
        self.interval.setToolTip("How often the curves are evaluated.")

        self.critical = QDoubleSpinBox()
        self.critical.setRange(0, 120)
        self.critical.setSuffix(" °C")
        self.critical.setValue(config.settings.critical_temperature)
        self.critical.setToolTip(
            "Every managed fan goes to 100% above this temperature.\n"
            "Set to 0 to switch the override off."
        )

        self.failsafe = QDoubleSpinBox()
        self.failsafe.setRange(0, 100)
        self.failsafe.setSuffix(" %")
        self.failsafe.setValue(config.settings.failsafe_percent)
        self.failsafe.setToolTip(
            "The speed used when a sensor a curve needs cannot be read.\n"
            "Leave this high: a fan running too fast is better than a hot chip."
        )

        self.restore = QCheckBox("Hand the fans back to the firmware when the daemon stops")
        self.restore.setChecked(config.settings.restore_on_exit)

        form.addRow("Update every", self.interval)
        form.addRow("Force full speed above", self.critical)
        form.addRow("Speed when a sensor fails", self.failsafe)
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
        self.latest_status: dict = {}
        self._overrides: dict[str, float] = {}

        self.setWindowTitle("Fan Control")
        self.resize(1180, 720)

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
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        # -- fans
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self._section_label("Fans"))

        self.card_area = QScrollArea()
        self.card_area.setWidgetResizable(True)
        self.card_area.setFrameShape(QScrollArea.NoFrame)
        # The cards carry a lot of controls; below this they start clipping.
        self.card_area.setMinimumWidth(360)
        self.card_host = QWidget()
        self.card_layout = QVBoxLayout(self.card_host)
        self.card_layout.setContentsMargins(4, 4, 4, 4)
        self.card_layout.setSpacing(8)
        self.card_layout.addStretch(1)
        self.card_area.setWidget(self.card_host)
        left_layout.addWidget(self.card_area, 1)
        splitter.addWidget(left)

        # -- curves
        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.addWidget(self._section_label("Curves"))

        self.curve_list = QListWidget()
        self.curve_list.currentItemChanged.connect(self._on_curve_selected)
        self.curve_list.itemDoubleClicked.connect(lambda _item: self._edit_selected_curve())
        middle_layout.addWidget(self.curve_list, 1)

        self.curve_preview = CurveGraph()
        self.curve_preview.set_editable(False)
        self.curve_preview.setMinimumHeight(220)
        middle_layout.addWidget(self.curve_preview, 3)

        curve_buttons = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self._add_curve)
        edit = QPushButton("Edit")
        edit.clicked.connect(self._edit_selected_curve)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_selected_curve)
        for button in (add, edit, remove):
            curve_buttons.addWidget(button)
        middle_layout.addLayout(curve_buttons)
        splitter.addWidget(middle)

        # -- sensors
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._section_label("Sensors"))
        self.sensors = SensorPanel()
        self.sensors.renamed.connect(self._on_sensor_renamed)
        right_layout.addWidget(self.sensors, 1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 3)
        splitter.setStretchFactor(2, 3)
        # Stretch factors alone leave the fan column squeezed to its minimum on
        # first show, so give the three panes a starting width as well.
        splitter.setSizes([430, 380, 370])

        self.setStatusBar(QStatusBar())
        self.status_label = QLabel("")
        self.statusBar().addPermanentWidget(self.status_label)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        font = QFont(label.font())
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 1)
        label.setFont(font)
        label.setContentsMargins(6, 6, 6, 2)
        return label

    def _build_actions(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.action_enabled = QAction("Fan control on", self)
        self.action_enabled.setCheckable(True)
        self.action_enabled.setToolTip(
            "When off, the fans go back to whatever the motherboard firmware does."
        )
        self.action_enabled.toggled.connect(self._on_master_toggled)
        toolbar.addAction(self.action_enabled)
        toolbar.addSeparator()

        action_import = QAction("Import from FanControl…", self)
        action_import.setToolTip("Read a userConfig.json from FanControl on Windows")
        action_import.triggered.connect(self._import_fancontrol)
        toolbar.addAction(action_import)

        action_rescan = QAction("Rescan hardware", self)
        action_rescan.triggered.connect(self._rescan)
        toolbar.addAction(action_rescan)

        action_settings = QAction("Settings…", self)
        action_settings.triggered.connect(self._open_settings)
        toolbar.addAction(action_settings)

        action_quit = QAction("Quit", self)
        action_quit.setShortcut(QKeySequence.Quit)
        action_quit.triggered.connect(QApplication.quit)
        self.addAction(action_quit)

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return
        icon = QIcon.fromTheme("sensors-fan", QIcon.fromTheme("computer"))
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Fan Control")

        menu = QMenu()
        self.tray_toggle = menu.addAction("Fan control on")
        self.tray_toggle.setCheckable(True)
        self.tray_toggle.toggled.connect(self._on_master_toggled)
        menu.addSeparator()
        menu.addAction("Show window", self._show_window)
        menu.addAction("Quit", QApplication.quit)
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
            QMessageBox.critical(self, "Fan Control", str(exc))
            return

        self.action_enabled.blockSignals(True)
        self.action_enabled.setChecked(self.config.settings.control_enabled)
        self.action_enabled.blockSignals(False)
        if getattr(self, "tray", None) is not None:
            self.tray_toggle.blockSignals(True)
            self.tray_toggle.setChecked(self.config.settings.control_enabled)
            self.tray_toggle.blockSignals(False)

        self.sensors.rebuild(self.inventory, self.config.sensor_names)
        self._rebuild_cards()
        self._rebuild_curve_list()
        self.status_label.setText(
            f"{len(self.inventory.get('controls', []))} outputs · "
            f"{self.inventory.get('config_path', '')}"
        )

    def _rebuild_cards(self) -> None:
        for card in self.cards.values():
            card.setParent(None)
            card.deleteLater()
        self.cards.clear()

        visible = [c for c in self.config.controls if not c.hidden]
        for control in visible:
            card = ControlCard(control, self.config, self.inventory)
            card.configEdited.connect(self._schedule_push)
            card.calibrateRequested.connect(self._calibrate)
            card.editCurveRequested.connect(self._edit_curve_by_id)
            self.card_layout.insertWidget(self.card_layout.count() - 1, card)
            self.cards[control.id] = card

        if not visible:
            empty = QLabel(
                "No controllable fans were found.\n\n"
                "On most desktop boards the super-I/O driver has to be loaded "
                "first:\n    sudo modprobe nct6775\n\n"
                "Run 'sudo sensors-detect' to find out which module your board "
                "needs, then use Rescan hardware."
            )
            empty.setAlignment(Qt.AlignCenter)
            empty.setWordWrap(True)
            empty.setEnabled(False)
            self.card_layout.insertWidget(0, empty)
            self.cards["__empty__"] = empty  # keeps it removable on the next rebuild

    def _rebuild_curve_list(self) -> None:
        current = self.curve_list.currentItem()
        selected = current.data(Qt.UserRole) if current else None

        self.curve_list.blockSignals(True)
        self.curve_list.clear()
        for curve in self.config.curves:
            title = CURVE_DESCRIPTIONS.get(curve.type, (curve.type, ""))[0]
            item = QListWidgetItem(f"{curve.name}   ·  {title}")
            item.setData(Qt.UserRole, curve.id)
            self.curve_list.addItem(item)
            if curve.id == selected:
                self.curve_list.setCurrentItem(item)
        self.curve_list.blockSignals(False)
        if self.curve_list.currentItem() is None and self.curve_list.count():
            self.curve_list.setCurrentRow(0)
        self._on_curve_selected(self.curve_list.currentItem(), None)

    # ------------------------------------------------------------------
    # live updates

    @Slot(dict)
    def _on_status(self, status: dict) -> None:
        if not status:
            return
        self.latest_status = status

        for control_id, entry in status.get("controls", {}).items():
            card = self.cards.get(control_id)
            if isinstance(card, ControlCard):
                card.set_overridden(control_id in self._overrides)
                card.update_status(entry)

        self.sensors.update_status(status)

        for row in range(self.curve_list.count()):
            item = self.curve_list.item(row)
            curve = self.config.curve_by_id(item.data(Qt.UserRole))
            if curve is None:
                continue
            title = CURVE_DESCRIPTIONS.get(curve.type, (curve.type, ""))[0]
            value = status.get("curve_values", {}).get(curve.id)
            error = status.get("curve_errors", {}).get(curve.id)
            if error:
                item.setText(f"{curve.name}   ·  {title}   ·  unavailable")
                item.setToolTip(error)
            elif value is not None:
                item.setText(f"{curve.name}   ·  {title}   ·  {value:.0f}%")
                item.setToolTip("")

        self._update_preview_reading()

        messages = status.get("messages", [])
        if messages:
            self.statusBar().showMessage(messages[0], 4000)

    def _update_preview_reading(self) -> None:
        item = self.curve_list.currentItem()
        if item is None:
            return
        curve = self.config.curve_by_id(item.data(Qt.UserRole))
        if curve is None:
            return
        sensor_id = getattr(curve, "sensor_id", "")
        temperature = self.latest_status.get("temperatures", {}).get(sensor_id)
        value = self.latest_status.get("curve_values", {}).get(curve.id)
        self.curve_preview.set_reading(temperature, value)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Fan Control", message)

    # ------------------------------------------------------------------
    # editing

    def _schedule_push(self) -> None:
        self._push_timer.start()

    def _push_config(self) -> None:
        problems = validate(self.config)
        if problems:
            QMessageBox.warning(
                self, "Fan Control",
                "This configuration cannot be applied:\n\n  " + "\n  ".join(problems),
            )
            return
        if self.proxy.push_config(self.config.to_dict()):
            self.statusBar().showMessage("Saved", 1500)

    def _on_master_toggled(self, enabled: bool) -> None:
        result = self.proxy.set_control_enabled(enabled)
        if not result.get("ok"):
            QMessageBox.warning(self, "Fan Control", result.get("error", "failed"))
            return
        self.config.settings.control_enabled = enabled
        label = "Fan control on" if enabled else "Fan control off"
        self.action_enabled.setText(label)
        if getattr(self, "tray", None) is not None:
            self.tray_toggle.blockSignals(True)
            self.tray_toggle.setChecked(enabled)
            self.tray_toggle.blockSignals(False)
        self.action_enabled.blockSignals(True)
        self.action_enabled.setChecked(enabled)
        self.action_enabled.blockSignals(False)

    def _on_sensor_renamed(self, sensor_id: str, name: str) -> None:
        if name:
            self.config.sensor_names[sensor_id] = name
        else:
            self.config.sensor_names.pop(sensor_id, None)
        self._schedule_push()

    def _on_curve_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            self.curve_preview.set_points([])
            return
        curve = self.config.curve_by_id(current.data(Qt.UserRole))
        if curve is None:
            return
        samples = sample_curve(curve)
        self.curve_preview.set_temperature_range(*curve_range(curve))
        self.curve_preview.set_points(
            [CurvePoint(t, p) for t, p in samples] if samples else []
        )
        self._update_preview_reading()

    def _add_curve(self) -> None:
        chooser = NewCurveDialog(self)
        if chooser.exec() != QDialog.Accepted:
            return
        name = f"Curve {len(self.config.curves) + 1}"
        curve = make_curve(chooser.selected_type(), name)
        dialog = CurveEditorDialog(
            curve, self.config, self.inventory,
            self.latest_status.get("temperatures", {}), self,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        self.config.curves.append(curve)
        self._rebuild_curve_list()
        for card in self.cards.values():
            if isinstance(card, ControlCard):
                card.reload(card.control, self.config, self.inventory)
        self._schedule_push()

    def _edit_selected_curve(self) -> None:
        item = self.curve_list.currentItem()
        if item is not None:
            self._edit_curve_by_id(item.data(Qt.UserRole))

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
        self._rebuild_curve_list()
        for card in self.cards.values():
            if isinstance(card, ControlCard):
                card.reload(card.control, self.config, self.inventory)
        self._schedule_push()

    def _remove_selected_curve(self) -> None:
        item = self.curve_list.currentItem()
        if item is None:
            return
        curve_id = item.data(Qt.UserRole)
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
                self, "Fan Control",
                f"{curve.name!r} is still in use by: " + ", ".join(sorted(set(users)))
                + ".\n\nPoint those at another curve first.",
            )
            return

        if QMessageBox.question(
            self, "Fan Control", f"Remove the curve {curve.name!r}?"
        ) != QMessageBox.Yes:
            return
        self.config.curves = [c for c in self.config.curves if c.id != curve_id]
        self._rebuild_curve_list()
        for card in self.cards.values():
            if isinstance(card, ControlCard):
                card.reload(card.control, self.config, self.inventory)
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
            QMessageBox.warning(self, "Fan Control", result.get("error", "rescan failed"))
            return
        self.refresh_all()
        self.statusBar().showMessage("Hardware re-enumerated", 2000)

    def _calibrate(self, control_id: str) -> None:
        control = self.config.control_by_id(control_id)
        if control is None:
            return
        if QMessageBox.question(
            self, "Calibrate",
            f"This spins {control.name} all the way up and down to find out "
            "where it stops and starts turning.\n\nIt takes about a minute and "
            "the fan will be noisy. Continue?",
        ) != QMessageBox.Yes:
            return

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.proxy.calibrate(control_id)
        finally:
            QApplication.restoreOverrideCursor()

        if not result.get("ok"):
            QMessageBox.warning(self, "Calibrate", result.get("error", "calibration failed"))
            return

        minimum = result.get("suggested_min_percent")
        start = result.get("suggested_start_percent")
        if minimum is None and start is None:
            QMessageBox.information(
                self, "Calibrate",
                f"{control.name} kept turning all the way down to 0%, so it has "
                "no minimum to speak of.",
            )
            return

        lines = [f"{control.name}:"]
        if result.get("stop_percent") is not None:
            lines.append(f"  stops turning below {result['stop_percent']:.0f}%")
        if result.get("start_percent") is not None:
            lines.append(f"  starts turning at {result['start_percent']:.0f}%")
        lines.append("")
        lines.append("Apply the suggested settings?")
        if minimum is not None:
            lines.append(f"  minimum speed → {minimum:.0f}%")
        if start is not None:
            lines.append(f"  start speed   → {start:.0f}%")

        if QMessageBox.question(self, "Calibrate", "\n".join(lines)) == QMessageBox.Yes:
            if minimum is not None:
                control.min_percent = float(minimum)
            if start is not None:
                control.start_percent = float(start)
            self._push_config()
            self.refresh_all()

    def _import_fancontrol(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, "Open a FanControl configuration", str(Path.home()),
            "FanControl configuration (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            QMessageBox.warning(self, "Import", str(exc))
            return

        result = self.proxy.import_fancontrol(text)
        if not result.get("ok"):
            QMessageBox.warning(
                self, "Import",
                result.get("error", "this file could not be read as a FanControl configuration"),
            )
            return

        dialog = ImportDialog(result, self.inventory, self)
        if dialog.exec() != QDialog.Accepted:
            return

        outcome = self.proxy.apply_import(
            dialog.config(), dialog.mapping(), dialog.merge_requested()
        )
        if not outcome.get("ok"):
            message = outcome.get("error", "the imported configuration was rejected")
            for problem in outcome.get("problems", []):
                message += f"\n  {problem}"
            QMessageBox.warning(self, "Import", message)
            return

        self.refresh_all()
        skipped = outcome.get("skipped") or []
        if skipped:
            lines = [f"{entry['name']} — {entry['reason']}" for entry in skipped]
            QMessageBox.information(
                self, "Import",
                "Imported. These fans were left switched off:\n\n  "
                + "\n  ".join(lines),
            )
        else:
            self.statusBar().showMessage("Imported", 3000)

    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._push_timer.isActive():
            self._push_timer.stop()
            self._push_config()
        super().closeEvent(event)
