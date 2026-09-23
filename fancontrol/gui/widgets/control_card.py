"""One card per controllable fan."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.models import Config, Control

MANUAL = "__manual__"


class ControlSettingsDialog(QDialog):
    """The per-fan settings that do not belong on the card itself."""

    def __init__(self, control: Control, config: Config, inventory: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{control.name} — settings")
        self._control = control

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.min_percent = self._spin(0, 100, control.min_percent, " %")
        self.min_percent.setToolTip(
            "The fan never runs slower than this while it is running at all.\n"
            "Set it above the speed at which the fan stalls."
        )
        self.max_percent = self._spin(0, 100, control.max_percent, " %")
        self.offset = self._spin(-50, 50, control.offset_percent, " %")
        self.offset.setToolTip("Added to whatever the curve asks for.")

        self.allow_stop = QCheckBox("Let the fan stop completely")
        self.allow_stop.setChecked(control.allow_stop)
        self.allow_stop.setToolTip(
            "When the curve asks for less than the stop point, switch the fan\n"
            "off instead of holding the minimum."
        )

        self.stop_percent = self._spin(0, 100, control.stop_percent, " %")
        self.stop_percent.setToolTip(
            "Below this the fan is switched off rather than run slowly.\n"
            "0 uses the minimum speed as the threshold. Run Calibrate to\n"
            "measure where this fan actually stops."
        )

        self.start_percent = self._spin(0, 100, control.start_percent, " %")
        self.start_percent.setToolTip(
            "A fan that has stopped needs more than its running minimum to start\n"
            "turning. Run Calibrate to measure this."
        )
        self.start_duration = self._spin(0, 30, control.start_duration, " s", decimals=1)

        self.step_up = self._spin(0, 100, control.step_up, " %/s")
        self.step_up.setToolTip("How fast the fan may speed up. 0 means instantly.")
        self.step_down = self._spin(0, 100, control.step_down, " %/s")
        self.step_down.setToolTip("How fast the fan may slow down. 0 means instantly.")

        self.fan_sensor = QComboBox()
        self.fan_sensor.addItem("— none —", "")
        for entry in inventory.get("fans", []):
            self.fan_sensor.addItem(f"{entry['name']}  ({entry['device']['chip']})", entry["id"])
        index = self.fan_sensor.findData(control.fan_sensor_id)
        self.fan_sensor.setCurrentIndex(max(0, index))
        self.fan_sensor.setToolTip(
            "The tachometer on the same header. Used to show the RPM and to warn\n"
            "when the fan stops while being driven."
        )

        form.addRow("Minimum speed", self.min_percent)
        form.addRow("Maximum speed", self.max_percent)
        form.addRow("Offset", self.offset)
        form.addRow("", self.allow_stop)
        form.addRow("Stop below", self.stop_percent)
        form.addRow("Start at", self.start_percent)
        form.addRow("Start for", self.start_duration)
        form.addRow("Speed up limit", self.step_up)
        form.addRow("Slow down limit", self.step_down)
        form.addRow("Fan tachometer", self.fan_sensor)

        self.firmware_below = self._spin(0, 100, control.firmware_below, " °C")
        self.firmware_below.setSpecialValueText("never")
        self.firmware_below.setToolTip(
            "Below this temperature the firmware runs the fan instead - for a\n"
            "GPU that means its own curve, which can stop the fans at idle.\n"
            "Taken back 3 °C before it would be handed over again."
        )
        self.firmware_sensor = QComboBox()
        for entry in inventory.get("temperatures", []):
            self.firmware_sensor.addItem(
                f"{entry['name']}  ({entry['device']['chip']})", entry["id"]
            )
        index = self.firmware_sensor.findData(control.firmware_sensor_id)
        self.firmware_sensor.setCurrentIndex(max(0, index))
        form.addRow("Firmware runs it below", self.firmware_below)
        form.addRow("…measured on", self.firmware_sensor)
        layout.addLayout(form)

        if control.calibration:
            measured = QLabel(self._calibration_summary(control))
            measured.setWordWrap(True)
            measured.setEnabled(False)
            layout.addWidget(measured)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _calibration_summary(control: Control) -> str:
        """One line describing what this fan was measured doing."""

        samples = [(p[0], p[1]) for p in control.calibration if len(p) >= 2]
        if not samples:
            return ""
        spinning = [percent for percent, rpm in samples if rpm > 0]
        top = max(rpm for _percent, rpm in samples)
        if not spinning:
            return "Measured: never turned at any speed."
        lowest = min(spinning)
        if lowest <= min(percent for percent, _rpm in samples):
            return f"Measured: turns even at {lowest:.0f}%, up to {top:.0f} rpm."
        return f"Measured: turns from {lowest:.0f}% upwards, up to {top:.0f} rpm."

    @staticmethod
    def _spin(low, high, value, suffix, decimals=0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setSuffix(suffix)
        return spin

    def apply_to(self, control: Control) -> None:
        control.min_percent = self.min_percent.value()
        control.max_percent = self.max_percent.value()
        control.offset_percent = self.offset.value()
        control.allow_stop = self.allow_stop.isChecked()
        control.stop_percent = self.stop_percent.value()
        control.start_percent = self.start_percent.value()
        control.start_duration = self.start_duration.value()
        control.step_up = self.step_up.value()
        control.step_down = self.step_down.value()
        control.fan_sensor_id = self.fan_sensor.currentData()
        control.firmware_below = self.firmware_below.value()
        control.firmware_sensor_id = (
            self.firmware_sensor.currentData() if control.firmware_below > 0 else ""
        )


class ControlCard(QFrame):
    """Shows what one fan is doing and lets the user change it."""

    configEdited = Signal()
    overrideRequested = Signal(str, object)  # control id, percent or None
    calibrateRequested = Signal(str)
    editCurveRequested = Signal(str)  # curve id

    def __init__(self, control: Control, config: Config, inventory: dict, parent=None) -> None:
        super().__init__(parent)
        self.control = control
        self.config = config
        self.inventory = inventory
        self._loading = False
        self._overridden = False

        self.setFrameShape(QFrame.StyledPanel)
        self.setObjectName("controlCard")

        grid = QGridLayout(self)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)

        self.enabled = QCheckBox()
        self.enabled.setChecked(control.enabled)
        self.enabled.setToolTip("Let this program drive this fan")
        self.enabled.toggled.connect(self._on_enabled)
        grid.addWidget(self.enabled, 0, 0)

        title = QVBoxLayout()
        title.setSpacing(0)
        self.name = QLabel(control.name)
        font = QFont(self.name.font())
        font.setBold(True)
        self.name.setFont(font)
        self.hardware = QLabel(self._hardware_label())
        self.hardware.setEnabled(False)
        small = QFont(self.hardware.font())
        small.setPointSizeF(max(7.0, small.pointSizeF() - 1.0))
        self.hardware.setFont(small)
        title.addWidget(self.name)
        title.addWidget(self.hardware)
        grid.addLayout(title, 0, 1)

        self.reading = QLabel("—")
        reading_font = QFont(self.reading.font())
        reading_font.setPointSizeF(reading_font.pointSizeF() + 5)
        reading_font.setBold(True)
        self.reading.setFont(reading_font)
        self.reading.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(self.reading, 0, 2)

        self.rpm = QLabel("")
        self.rpm.setEnabled(False)
        self.rpm.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.rpm.setFont(small)
        grid.addWidget(self.rpm, 1, 2)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        grid.addWidget(self.bar, 1, 0, 1, 2)

        source = QHBoxLayout()
        self.curve = QComboBox()
        self.curve.setToolTip("Which curve drives this fan")
        self.curve.currentIndexChanged.connect(self._on_curve_changed)
        source.addWidget(self.curve, 1)

        self.edit_curve = QToolButton()
        self.edit_curve.setText("Edit…")
        self.edit_curve.setToolTip("Edit the selected curve")
        self.edit_curve.clicked.connect(
            lambda: self.editCurveRequested.emit(self.control.curve_id)
        )
        source.addWidget(self.edit_curve)

        self.settings = QToolButton()
        self.settings.setText("⚙")
        self.settings.setToolTip("Limits, spin-up and response settings")
        self.settings.clicked.connect(self._open_settings)
        source.addWidget(self.settings)

        self.calibrate = QToolButton()
        self.calibrate.setText("Calibrate")
        self.calibrate.setToolTip(
            "Measure where this fan stops and starts. Takes about a minute and\n"
            "spins the fan up and down while it runs."
        )
        self.calibrate.clicked.connect(lambda: self.calibrateRequested.emit(self.control.id))
        source.addWidget(self.calibrate)
        grid.addLayout(source, 2, 0, 1, 3)

        self.manual_row = QWidget()
        manual_layout = QHBoxLayout(self.manual_row)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(control.manual_percent))
        self.slider.valueChanged.connect(self._on_slider)
        self.slider_label = QLabel(f"{int(control.manual_percent)}%")
        self.slider_label.setMinimumWidth(38)
        manual_layout.addWidget(self.slider, 1)
        manual_layout.addWidget(self.slider_label)
        grid.addWidget(self.manual_row, 3, 0, 1, 3)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setFont(small)
        grid.addWidget(self.note, 4, 0, 1, 3)

        grid.setColumnStretch(1, 1)
        self.reload(control, config, inventory)

    # ------------------------------------------------------------------

    def _hardware_label(self) -> str:
        for entry in self.inventory.get("controls", []):
            if entry["id"] == self.control.output_id:
                return f"{entry['device']['chip']} · {entry['id'].rsplit(':', 1)[-1]}"
        return self.control.output_id or "no output"

    def reload(self, control: Control, config: Config, inventory: dict) -> None:
        """Rebuild the card from a (possibly changed) configuration."""

        self._loading = True
        self.control = control
        self.config = config
        self.inventory = inventory

        self.name.setText(control.name)
        self.hardware.setText(self._hardware_label())
        self.enabled.setChecked(control.enabled)

        self.curve.clear()
        self.curve.addItem("Manual", MANUAL)
        for curve in config.curves:
            self.curve.addItem(curve.name, curve.id)
        index = self.curve.findData(control.curve_id or MANUAL)
        self.curve.setCurrentIndex(max(0, index))

        self.slider.setValue(int(control.manual_percent))
        self.slider_label.setText(f"{int(control.manual_percent)}%")
        self._update_mode()
        self._loading = False

    def _update_mode(self) -> None:
        manual = not self.control.curve_id
        self.manual_row.setVisible(manual)
        self.edit_curve.setEnabled(not manual)
        for widget in (self.curve, self.settings, self.calibrate, self.bar):
            widget.setEnabled(self.control.enabled)
        self.manual_row.setEnabled(self.control.enabled)

    # ------------------------------------------------------------------
    # live values

    def update_status(self, entry: dict) -> None:
        percent = entry.get("applied_percent")
        if percent is None:
            percent = 0.0
        self.reading.setText(f"{percent:.0f}%")
        self.bar.setValue(int(percent))

        rpm = entry.get("rpm")
        if rpm is None:
            self.rpm.setText("")
        else:
            self.rpm.setText(f"{int(rpm)} rpm")

        palette = self.palette()
        note = ""
        colour = ""
        if entry.get("with_firmware"):
            note = "Cool enough: the firmware is running this fan."
        elif entry.get("paused"):
            note, colour = "Calibrating — the curve is standing down.", "#f67400"
        elif entry.get("error"):
            note, colour = entry["error"], "#da4453"
        elif entry.get("stalled"):
            note = "Reads 0 rpm while being driven — raise the minimum or the start speed."
            colour = "#da4453"
        elif not entry.get("available", True) and not entry.get("enabled"):
            note = "No matching hardware on this machine."
        elif entry.get("kicking"):
            note, colour = "Spinning up…", "#f67400"
        elif self._overridden:
            note, colour = "Driven by hand — the curve is not in control.", "#f67400"
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self.note.setStyleSheet(f"color: {colour};" if colour else "")

    def set_overridden(self, overridden: bool) -> None:
        self._overridden = overridden

    # ------------------------------------------------------------------
    # editing

    def _on_enabled(self, checked: bool) -> None:
        if self._loading:
            return
        self.control.enabled = checked
        self._update_mode()
        self.configEdited.emit()

    def _on_curve_changed(self, _index: int) -> None:
        if self._loading:
            return
        data = self.curve.currentData()
        self.control.curve_id = "" if data == MANUAL else data
        self._update_mode()
        self.configEdited.emit()

    def _on_slider(self, value: int) -> None:
        self.slider_label.setText(f"{value}%")
        if self._loading:
            return
        self.control.manual_percent = float(value)
        self.configEdited.emit()

    def _open_settings(self) -> None:
        dialog = ControlSettingsDialog(self.control, self.config, self.inventory, self)
        if dialog.exec() == QDialog.Accepted:
            dialog.apply_to(self.control)
            self.configEdited.emit()
