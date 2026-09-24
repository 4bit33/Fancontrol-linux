"""One card per controllable fan."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
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
from ..i18n import tr
from .cards import icon_button

MANUAL = "__manual__"

CONTROL_CARD_WIDTH = 340

#: Who drives a fan. Stored as ``enabled`` and ``firmware_below`` on the
#: control, which the daemon already understands.
FIRMWARE = "firmware"   # not managed: the firmware's own curve
CURVE = "curve"         # always the chosen curve
BOTH = "both"           # the firmware while cool, the curve above a temperature


def control_mode(control: Control) -> str:
    if not control.enabled:
        return FIRMWARE
    return BOTH if control.firmware_below > 0 else CURVE


def is_gpu(control: Control, inventory: dict) -> bool:
    return _output_device(control, inventory).get("chip", "").startswith("nvidia") \
        or control.output_id.startswith("nvidia")


def _output_device(control: Control, inventory: dict) -> dict:
    for entry in inventory.get("controls", []):
        if entry["id"] == control.output_id:
            return entry.get("device", {})
    return {}


def default_handover_sensor(control: Control, inventory: dict) -> str:
    """The temperature a "both" fan most likely wants: its own chip's (a GPU
    fan follows the GPU), otherwise the CPU package."""

    temperatures = inventory.get("temperatures", [])
    device = _output_device(control, inventory)
    for entry in temperatures:
        if device.get("key") and entry.get("device", {}).get("key") == device.get("key"):
            return entry["id"]
    cpu_chips = ("coretemp", "k10temp", "zenpower")
    preferred = ("package", "tctl", "tdie")
    cpu = [e for e in temperatures if e.get("device", {}).get("chip", "").startswith(cpu_chips)]
    for entry in cpu:
        if entry["name"].lower().startswith(preferred):
            return entry["id"]
    if cpu:
        return cpu[0]["id"]
    return temperatures[0]["id"] if temperatures else ""


def default_threshold(control: Control, inventory: dict) -> float:
    """GPUs idle around 35-40 °C; CPUs sit higher and swing more."""
    return 40.0 if is_gpu(control, inventory) else 50.0


class ControlSettingsDialog(QDialog):
    """The per-fan settings that do not belong on the card itself."""

    def __init__(self, control: Control, config: Config, inventory: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("{name} — settings", name=control.name))
        self._control = control

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.min_percent = self._spin(0, 100, control.min_percent, " %")
        self.min_percent.setToolTip(
            tr("The fan never runs slower than this while it is running at all.\n"
            "Set it above the speed at which the fan stalls.")
        )
        self.max_percent = self._spin(0, 100, control.max_percent, " %")
        self.offset = self._spin(-50, 50, control.offset_percent, " %")
        self.offset.setToolTip(tr("Added to whatever the curve asks for."))

        self.allow_stop = QCheckBox(tr("Let the fan stop completely"))
        self.allow_stop.setChecked(control.allow_stop)
        self.allow_stop.setToolTip(
            tr("When the curve asks for less than the stop point, switch the fan\n"
            "off instead of holding the minimum.")
        )

        self.stop_percent = self._spin(0, 100, control.stop_percent, " %")
        self.stop_percent.setToolTip(
            tr("Below this the fan is switched off rather than run slowly.\n"
            "0 uses the minimum speed as the threshold. Run Calibrate to\n"
            "measure where this fan actually stops.")
        )

        self.start_percent = self._spin(0, 100, control.start_percent, " %")
        self.start_percent.setToolTip(
            tr("A fan that has stopped needs more than its running minimum to start\n"
            "turning. Run Calibrate to measure this.")
        )
        self.start_duration = self._spin(0, 30, control.start_duration, " s", decimals=1)

        self.step_up = self._spin(0, 100, control.step_up, " %/s")
        self.step_up.setToolTip(tr("How fast the fan may speed up. 0 means instantly."))
        self.step_down = self._spin(0, 100, control.step_down, " %/s")
        self.step_down.setToolTip(tr("How fast the fan may slow down. 0 means instantly."))

        self.fan_sensor = QComboBox()
        self.fan_sensor.addItem(tr("— none —"), "")
        for entry in inventory.get("fans", []):
            self.fan_sensor.addItem(f"{entry['name']}  ({entry['device']['chip']})", entry["id"])
        index = self.fan_sensor.findData(control.fan_sensor_id)
        self.fan_sensor.setCurrentIndex(max(0, index))
        self.fan_sensor.setToolTip(
            tr("The tachometer on the same header. Used to show the RPM and to warn\n"
            "when the fan stops while being driven.")
        )

        form.addRow(tr("Minimum speed"), self.min_percent)
        form.addRow(tr("Maximum speed"), self.max_percent)
        form.addRow(tr("Offset"), self.offset)
        form.addRow("", self.allow_stop)
        form.addRow(tr("Stop below"), self.stop_percent)
        form.addRow(tr("Start at"), self.start_percent)
        form.addRow(tr("Start for"), self.start_duration)
        form.addRow(tr("Speed up limit"), self.step_up)
        form.addRow(tr("Slow down limit"), self.step_down)
        form.addRow(tr("Fan tachometer"), self.fan_sensor)

        self.firmware_hysteresis = self._spin(0.5, 20, control.firmware_hysteresis, " °C",
                                              decimals=1)
        self.firmware_hysteresis.setToolTip(
            tr("In “Both” mode the firmware gets the fan back only once the\n"
            "temperature is this far below the threshold, so it does not switch\n"
            "back and forth around it.")
        )
        form.addRow(tr("Hand-back margin"), self.firmware_hysteresis)
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
            return tr("Measured: never turned at any speed.")
        lowest = min(spinning)
        if lowest <= min(percent for percent, _rpm in samples):
            return tr("Measured: turns even at {percent}%, up to {rpm} rpm.",
                      percent=f"{lowest:.0f}", rpm=f"{top:.0f}")
        return tr("Measured: turns from {percent}% upwards, up to {rpm} rpm.",
                  percent=f"{lowest:.0f}", rpm=f"{top:.0f}")

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
        control.firmware_hysteresis = self.firmware_hysteresis.value()


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
        # The width comes from the contents; Section.equalize() then gives
        # every card in the grid the same one.
        self.setMinimumWidth(CONTROL_CARD_WIDTH)

        grid = QGridLayout(self)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)

        self._last_threshold = 0.0

        title = QVBoxLayout()
        title.setSpacing(0)
        self.name = QLabel(control.name)
        self.name.setWordWrap(True)
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
        grid.addLayout(title, 0, 0, 1, 2)

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

        modes = QHBoxLayout()
        modes.setSpacing(2)
        modes_label = QLabel(tr("Driven by"))
        modes_label.setEnabled(False)
        modes_label.setFont(small)
        modes.addWidget(modes_label)
        modes.addSpacing(6)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons: dict[str, QToolButton] = {}
        for mode, text, tip in (
            (FIRMWARE, tr("Firmware"),
             tr("The motherboard or graphics card runs this fan by itself,\n"
                "exactly as without this program.")),
            (CURVE, tr("My curve"), tr("The curve chosen below always drives this fan.")),
            (BOTH, tr("Both"),
             tr("The firmware while it is cool, your curve from a temperature\n"
                "you choose. For a graphics card that keeps its fans stopped at idle.")),
        ):
            button = QToolButton()
            button.setObjectName("segment")
            button.setText(text)
            button.setToolTip(tip)
            button.setCheckable(True)
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            button.clicked.connect(lambda _checked, m=mode: self._on_mode(m))
            modes.addWidget(button)
        modes.addStretch(1)
        grid.addLayout(modes, 2, 0, 1, 3)

        source = QHBoxLayout()
        self.curve = QComboBox()
        self.curve.setToolTip(tr("Which curve drives this fan"))
        # Wide enough for the longest curve name; the card grows to fit.
        self.curve.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.curve.currentIndexChanged.connect(self._on_curve_changed)
        source.addWidget(self.curve, 1)

        self.edit_curve = icon_button("document-edit", "✎", tr("Edit the selected curve"))
        self.edit_curve.clicked.connect(
            lambda: self.editCurveRequested.emit(self.control.curve_id)
        )
        source.addWidget(self.edit_curve)

        self.settings = icon_button("configure", "⚙", tr("Limits, spin-up and response settings"))
        self.settings.clicked.connect(self._open_settings)
        source.addWidget(self.settings)

        self.calibrate = QToolButton()
        self.calibrate.setText(tr("Calibrate"))
        self.calibrate.setToolTip(
            tr("Measure where this fan stops and starts. Takes a few minutes and\n"
            "spins the fan up and down while it runs.")
        )
        self.calibrate.clicked.connect(lambda: self.calibrateRequested.emit(self.control.id))
        source.addWidget(self.calibrate)
        grid.addLayout(source, 3, 0, 1, 3)

        self.handover_row = QWidget()
        # Two lines, threshold then sensor, so the row is no wider than the
        # curve row above it.
        handover = QGridLayout(self.handover_row)
        handover.setContentsMargins(0, 0, 0, 0)
        handover.setVerticalSpacing(4)
        below = QLabel(tr("Firmware below"))
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(1, 110)
        self.threshold.setDecimals(0)
        self.threshold.setSuffix(" °C")
        self.threshold.setToolTip(tr("From this temperature up, your curve drives the fan."))
        self.threshold.valueChanged.connect(self._on_threshold)
        on = QLabel(tr("on"))
        self.handover_sensor = QComboBox()
        # Sensor names run long ("NVIDIA GeForce RTX 3070 GPU"); let the list
        # shorten them rather than widen every card in the grid.
        self.handover_sensor.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.handover_sensor.setMinimumContentsLength(10)
        self.handover_sensor.setToolTip(tr("The temperature that decides who drives the fan"))
        self.handover_sensor.currentIndexChanged.connect(self._on_handover_sensor)
        handover.addWidget(below, 0, 0)
        handover.addWidget(self.threshold, 0, 1, Qt.AlignLeft)
        handover.addWidget(on, 1, 0)
        handover.addWidget(self.handover_sensor, 1, 1)
        handover.setColumnStretch(1, 1)
        grid.addWidget(self.handover_row, 4, 0, 1, 3)

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
        grid.addWidget(self.manual_row, 5, 0, 1, 3)

        # Who has the fan right now, in the modes where that can change.
        self.state = QLabel("")
        self.state.setWordWrap(True)
        self.state.setFont(small)
        self.state.setEnabled(False)
        grid.addWidget(self.state, 6, 0, 1, 3)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setFont(small)
        grid.addWidget(self.note, 7, 0, 1, 3)

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
        if control.firmware_below > 0:
            self._last_threshold = control.firmware_below

        names = config.sensor_names
        self.handover_sensor.clear()
        for entry in inventory.get("temperatures", []):
            label = names.get(entry["id"]) or entry["name"]
            self.handover_sensor.addItem(label, entry["id"])
            self.handover_sensor.setItemData(
                self.handover_sensor.count() - 1,
                f"{label}  ({entry['device']['chip']})", Qt.ToolTipRole)
        sensor = control.firmware_sensor_id or default_handover_sensor(control, inventory)
        self.handover_sensor.setCurrentIndex(max(0, self.handover_sensor.findData(sensor)))
        self.threshold.setValue(control.firmware_below or self._threshold_to_offer())

        self.curve.clear()
        self.curve.addItem(tr("Manual"), MANUAL)
        for curve in config.curves:
            self.curve.addItem(curve.name, curve.id)
        index = self.curve.findData(control.curve_id or MANUAL)
        self.curve.setCurrentIndex(max(0, index))

        self.slider.setValue(int(control.manual_percent))
        self.slider_label.setText(f"{int(control.manual_percent)}%")
        self._update_mode()
        self._loading = False

    def _threshold_to_offer(self) -> float:
        return self._last_threshold or default_threshold(self.control, self.inventory)

    def _update_mode(self) -> None:
        mode = control_mode(self.control)
        self.mode_buttons[mode].setChecked(True)
        manual = not self.control.curve_id
        managed = mode != FIRMWARE
        self.manual_row.setVisible(manual)
        self.edit_curve.setEnabled(managed and not manual)
        for widget in (self.curve, self.settings, self.calibrate, self.bar):
            widget.setEnabled(managed)
        self.manual_row.setEnabled(managed)
        self.handover_row.setVisible(mode == BOTH)
        if mode == FIRMWARE:
            self.state.setText(tr("The firmware runs this fan; this program only watches it."))
        self.state.setVisible(mode != CURVE)

    # ------------------------------------------------------------------
    # live values

    def update_status(self, entry: dict, temperatures: dict | None = None) -> None:
        percent = entry.get("applied_percent")
        if percent is None:
            percent = 0.0
        self.reading.setText(f"{percent:.0f}%")
        self.bar.setValue(int(percent))

        rpm = entry.get("rpm")
        if rpm is None:
            self.rpm.setText("")
        else:
            self.rpm.setText(tr("{rpm} rpm", rpm=int(rpm)))

        if control_mode(self.control) == BOTH:
            self.state.setText(self._handover_state(entry, temperatures or {}))

        note = ""
        colour = ""
        if entry.get("paused"):
            note, colour = tr("Calibrating — the curve is standing down."), "#f67400"
        elif entry.get("error"):
            note, colour = entry["error"], "#da4453"
        elif entry.get("stalled"):
            note = tr("Reads 0 rpm while being driven — raise the minimum or the start speed.")
            colour = "#da4453"
        elif not entry.get("available", True) and not entry.get("enabled"):
            note = tr("No matching hardware on this machine.")
        elif entry.get("kicking"):
            note, colour = tr("Spinning up…"), "#f67400"
        elif self._overridden:
            note, colour = tr("Driven by hand — the curve is not in control."), "#f67400"
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self.note.setStyleSheet(f"color: {colour};" if colour else "")

    def _handover_state(self, entry: dict, temperatures: dict) -> str:
        control = self.control
        temperature = temperatures.get(control.firmware_sensor_id)
        if temperature is None:
            return tr("Sensor unavailable — your curve drives the fan to be safe.")
        values = dict(
            temperature=f"{temperature:.0f}",
            threshold=f"{control.firmware_below:.0f}",
            back=f"{control.firmware_below - control.firmware_hysteresis:.0f}",
        )
        if entry.get("with_firmware"):
            return tr("Now: firmware · {temperature}\u00a0°C, your curve takes over at "
                      "{threshold}\u00a0°C", **values)
        return tr("Now: your curve · {temperature}\u00a0°C, back to the firmware below "
                  "{back}\u00a0°C", **values)

    def set_overridden(self, overridden: bool) -> None:
        self._overridden = overridden

    # ------------------------------------------------------------------
    # editing

    def _on_mode(self, mode: str) -> None:
        if self._loading:
            return
        control = self.control
        if control.firmware_below > 0:
            self._last_threshold = control.firmware_below
        control.enabled = mode != FIRMWARE
        if mode == BOTH:
            control.firmware_below = self.threshold.value() or self._threshold_to_offer()
            control.firmware_sensor_id = self.handover_sensor.currentData() or ""
        elif mode == CURVE:
            control.firmware_below = 0.0
        # In FIRMWARE the threshold is kept, so "Both" comes back as it was.
        self._update_mode()
        self.configEdited.emit()

    def _on_threshold(self, value: float) -> None:
        if self._loading or control_mode(self.control) != BOTH:
            return
        self.control.firmware_below = float(value)
        self._last_threshold = float(value)
        self.configEdited.emit()

    def _on_handover_sensor(self, _index: int) -> None:
        if self._loading or control_mode(self.control) != BOTH:
            return
        self.control.firmware_sensor_id = self.handover_sensor.currentData() or ""
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
