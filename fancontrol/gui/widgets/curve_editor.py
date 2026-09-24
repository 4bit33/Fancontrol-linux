"""Curve editing: a type-specific form beside a live preview of the shape."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ...core.models import (
    BaseCurve,
    Config,
    CurvePoint,
    CurveType,
    FlatCurve,
    GraphCurve,
    LinearCurve,
    MixCurve,
    MixFunction,
    SyncCurve,
    TargetCurve,
    TriggerCurve,
    new_id,
)
from ..i18n import N_, tr
from .curve_graph import MAX_TEMP, MIN_TEMP, CurveGraph

#: What each curve type is for, shown when creating one.
CURVE_DESCRIPTIONS = {
    CurveType.GRAPH.value: (
        N_("Graph"),
        N_("Points you place yourself, joined by straight lines. The one to reach "
           "for unless you need something else."),
    ),
    CurveType.FLAT.value: (
        N_("Fixed speed"),
        N_("One speed, always. Useful as an input to a mix curve, or for a pump."),
    ),
    CurveType.LINEAR.value: (
        N_("Linear"),
        N_("A straight ramp between two temperatures. The same as a two point "
           "graph, but easier to type in exactly."),
    ),
    CurveType.TARGET.value: (
        N_("Target temperature"),
        N_("Speeds up while the sensor is above the target and slows down while it "
           "is below, instead of mapping temperature to speed directly."),
    ),
    CurveType.TRIGGER.value: (
        N_("Trigger"),
        N_("Two speeds with a gap between the thresholds, so the fan does not "
           "oscillate around one temperature."),
    ),
    CurveType.MIX.value: (
        N_("Mix"),
        N_("Combines other curves - usually the highest of a CPU and a GPU curve, "
           "so case fans follow whichever is hotter."),
    ),
    CurveType.SYNC.value: (
        N_("Sync"),
        N_("Follows another fan, optionally offset or scaled. Good for keeping a "
           "second fan on the same radiator in step."),
    ),
}

#: How a mix curve combines its inputs.
MIX_FUNCTION_LABELS = {
    MixFunction.MAX.value: N_("Highest of them"),
    MixFunction.MIN.value: N_("Lowest of them"),
    MixFunction.AVERAGE.value: N_("Average"),
    MixFunction.SUM.value: N_("Sum"),
    MixFunction.SUBTRACT.value: N_("First minus the rest"),
}


def curve_type_title(curve_type: str) -> str:
    """The translated name of a curve type, such as "Graph"."""
    return tr(CURVE_DESCRIPTIONS.get(curve_type, (curve_type, ""))[0])


def curve_range(curve: BaseCurve) -> tuple[float, float]:
    """The temperature range this curve should be drawn over."""

    if isinstance(curve, GraphCurve):
        low, high = curve.axis_min_temperature, curve.axis_max_temperature
        if curve.points:
            low = min(low, curve.points[0].temperature)
            high = max(high, curve.points[-1].temperature)
        return low, high
    if isinstance(curve, LinearCurve):
        return min(MIN_TEMP, curve.min_temperature), max(MAX_TEMP, curve.max_temperature)
    if isinstance(curve, TriggerCurve):
        return (
            min(MIN_TEMP, curve.idle_temperature),
            max(MAX_TEMP, curve.load_temperature),
        )
    if isinstance(curve, TargetCurve):
        return MIN_TEMP, max(MAX_TEMP, curve.target_temperature)
    return MIN_TEMP, MAX_TEMP


def sample_curve(curve: BaseCurve) -> list[tuple[float, float]] | None:
    """Points describing the curve's shape, for the preview.

    Returns ``None`` for curves whose output depends on more than the current
    temperature, because drawing them as a line would be a lie.
    """

    low, high = curve_range(curve)
    if isinstance(curve, GraphCurve):
        return [(p.temperature, p.percent) for p in curve.points]
    if isinstance(curve, FlatCurve):
        return [(low, curve.percent), (high, curve.percent)]
    if isinstance(curve, LinearCurve):
        return [
            (low, curve.min_percent),
            (curve.min_temperature, curve.min_percent),
            (curve.max_temperature, curve.max_percent),
            (high, curve.max_percent),
        ]
    if isinstance(curve, TriggerCurve):
        # Drawn as the rising edge; the falling edge sits at the idle
        # temperature and is what the gap between the two thresholds buys.
        return [
            (low, curve.idle_percent),
            (curve.load_temperature, curve.idle_percent),
            (curve.load_temperature, curve.load_percent),
            (high, curve.load_percent),
        ]
    return None


def _spin(low, high, value, suffix, decimals=0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(low, high)
    spin.setDecimals(decimals)
    spin.setValue(value)
    spin.setSuffix(suffix)
    return spin


class NewCurveDialog(QDialog):
    """Pick the type of a new curve."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("New curve"))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("What kind of curve?")))

        self._buttons: list[tuple[QRadioButton, str]] = []
        for curve_type, (title, description) in CURVE_DESCRIPTIONS.items():
            button = QRadioButton(tr(title))
            label = QLabel(tr(description))
            label.setWordWrap(True)
            label.setEnabled(False)
            label.setContentsMargins(24, 0, 0, 8)
            layout.addWidget(button)
            layout.addWidget(label)
            self._buttons.append((button, curve_type))
        self._buttons[0][0].setChecked(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_type(self) -> str:
        for button, curve_type in self._buttons:
            if button.isChecked():
                return curve_type
        return CurveType.GRAPH.value


#: Quiet, unfussy defaults: react to a rise straight away, come back down
#: gently, and ignore the small wobble of a sensor reading.
_DEFAULT_SMOOTHING = {
    "hysteresis_up": 0.0,
    "hysteresis_down": 2.0,
    "response_time_up": 1.0,
    "response_time_down": 4.0,
}


def make_curve(curve_type: str, name: str) -> BaseCurve:
    """A new curve of ``curve_type`` with sensible starting values."""

    curve_id = new_id("curve")
    if curve_type == CurveType.GRAPH.value:
        return GraphCurve(
            id=curve_id, name=name, **_DEFAULT_SMOOTHING,
            points=[CurvePoint(30, 20), CurvePoint(50, 30), CurvePoint(70, 65), CurvePoint(85, 100)],
        )
    if curve_type == CurveType.FLAT.value:
        return FlatCurve(id=curve_id, name=name, percent=50)
    if curve_type == CurveType.LINEAR.value:
        return LinearCurve(id=curve_id, name=name, **_DEFAULT_SMOOTHING)
    if curve_type == CurveType.TARGET.value:
        return TargetCurve(id=curve_id, name=name, **_DEFAULT_SMOOTHING)
    if curve_type == CurveType.TRIGGER.value:
        return TriggerCurve(id=curve_id, name=name, **_DEFAULT_SMOOTHING)
    if curve_type == CurveType.MIX.value:
        return MixCurve(id=curve_id, name=name)
    if curve_type == CurveType.SYNC.value:
        return SyncCurve(id=curve_id, name=name)
    raise ValueError(f"unknown curve type {curve_type!r}")


class CurveEditorDialog(QDialog):
    """Edit one curve. The preview redraws as the fields change."""

    def __init__(
        self,
        curve: BaseCurve,
        config: Config,
        inventory: dict,
        temperatures: dict | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.curve = curve
        self.config = config
        self.inventory = inventory
        self.temperatures = temperatures or {}

        self.setWindowTitle(tr("{name} — {title} curve", name=curve.name,
                               title=curve_type_title(curve.type)))
        self.resize(760, 460)

        outer = QVBoxLayout(self)
        body = QHBoxLayout()
        outer.addLayout(body, 1)

        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setLabelAlignment(Qt.AlignRight)
        self.form_host.setMinimumWidth(300)
        body.addWidget(self.form_host)

        right = QVBoxLayout()
        self.graph = CurveGraph()
        self.graph.pointsChanged.connect(self._on_points_changed)
        right.addWidget(self.graph, 1)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setEnabled(False)
        right.addWidget(self.hint)
        body.addLayout(right, 1)

        self.name = QLineEdit(curve.name)
        self.name.textChanged.connect(self._on_name_changed)
        self.form.addRow(tr("Name"), self.name)

        self._build_form()
        self._refresh_preview()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ------------------------------------------------------------------

    def _sensor_combo(self, selected: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem(tr("— choose a sensor —"), "")
        names = self.config.sensor_names
        for entry in self.inventory.get("temperatures", []):
            label = names.get(entry["id"]) or entry["name"]
            combo.addItem(f"{label}  ({entry['device']['chip']})", entry["id"])
        index = combo.findData(selected)
        if index < 0 and selected:
            # The configuration points at a sensor this machine does not have;
            # keep it visible rather than silently switching to another one.
            combo.addItem(tr("{sensor}  (not present)", sensor=selected), selected)
            index = combo.count() - 1
        combo.setCurrentIndex(max(0, index))
        return combo

    def _add_smoothing_rows(self) -> None:
        """Hysteresis and smoothing, separately for rising and falling."""

        self.hysteresis_up = _spin(0, 20, self.curve.hysteresis_up, " °C", decimals=1)
        self.hysteresis_up.setToolTip(
            tr("How far the temperature must rise before the fan speeds up.\n"
            "Leave at 0 to react to a rise immediately.")
        )
        self.hysteresis_down = _spin(0, 20, self.curve.hysteresis_down, " °C", decimals=1)
        self.hysteresis_down.setToolTip(
            tr("How far it must fall before the fan slows down. Raise this if the\n"
            "fan keeps hunting up and down around one temperature.")
        )
        self.response_up = _spin(0, 60, self.curve.response_time_up, " s", decimals=1)
        self.response_up.setToolTip(
            tr("Smooths a rising temperature over this many seconds. 0 is off.")
        )
        self.response_down = _spin(0, 60, self.curve.response_time_down, " s", decimals=1)
        self.response_down.setToolTip(
            tr("Smooths a falling temperature. Making this larger than the rising\n"
            "one is what keeps the fans from dropping the moment a load ends.")
        )
        self.ignore_at_limits = QCheckBox(tr("Ignore hysteresis past the ends of the curve"))
        self.ignore_at_limits.setChecked(self.curve.ignore_hysteresis_at_limits)
        self.ignore_at_limits.setToolTip(
            tr("Out past the first and last point the speed is flat anyway, so\n"
            "holding the reading back there only delays the fans.")
        )

        for widget in (self.hysteresis_up, self.hysteresis_down,
                       self.response_up, self.response_down):
            widget.valueChanged.connect(self._on_field_changed)
        self.ignore_at_limits.toggled.connect(self._on_field_changed)

        self.form.addRow(tr("Hysteresis rising"), self.hysteresis_up)
        self.form.addRow(tr("Hysteresis falling"), self.hysteresis_down)
        self.form.addRow(tr("Response rising"), self.response_up)
        self.form.addRow(tr("Response falling"), self.response_down)
        self.form.addRow("", self.ignore_at_limits)

    def _build_form(self) -> None:
        curve = self.curve

        if isinstance(curve, (GraphCurve, LinearCurve, TargetCurve, TriggerCurve)):
            self.sensor = self._sensor_combo(curve.sensor_id)
            self.sensor.currentIndexChanged.connect(self._on_field_changed)
            self.form.addRow(tr("Temperature source"), self.sensor)

        if isinstance(curve, GraphCurve):
            self.graph.set_editable(True)
            self.hint.setText(
                tr("Drag a point to move it, double-click the graph to add one, "
                "right-click a point to remove it.")
            )
            self.axis_min = _spin(0, 200, curve.axis_min_temperature, " °C")
            self.axis_max = _spin(10, 200, curve.axis_max_temperature, " °C")
            for widget in (self.axis_min, self.axis_max):
                widget.setToolTip(
                    tr("The temperature range the graph covers. A GPU curve usually\n"
                    "needs more than a CPU one. It only affects the drawing.")
                )
                widget.valueChanged.connect(self._on_field_changed)
            self.form.addRow(tr("Graph starts at"), self.axis_min)
            self.form.addRow(tr("Graph ends at"), self.axis_max)
            self._add_smoothing_rows()

        elif isinstance(curve, FlatCurve):
            self.percent = _spin(0, 100, curve.percent, " %")
            self.percent.valueChanged.connect(self._on_field_changed)
            self.form.addRow(tr("Speed"), self.percent)
            self.graph.set_editable(False)

        elif isinstance(curve, LinearCurve):
            self.min_temperature = _spin(0, 120, curve.min_temperature, " °C")
            self.max_temperature = _spin(0, 120, curve.max_temperature, " °C")
            self.min_percent = _spin(0, 100, curve.min_percent, " %")
            self.max_percent = _spin(0, 100, curve.max_percent, " %")
            for widget in (self.min_temperature, self.max_temperature,
                           self.min_percent, self.max_percent):
                widget.valueChanged.connect(self._on_field_changed)
            self.form.addRow(tr("Ramp starts at"), self.min_temperature)
            self.form.addRow(tr("Ramp ends at"), self.max_temperature)
            self.form.addRow(tr("Speed at the start"), self.min_percent)
            self.form.addRow(tr("Speed at the end"), self.max_percent)
            self._add_smoothing_rows()
            self.graph.set_editable(False)

        elif isinstance(curve, TriggerCurve):
            self.load_temperature = _spin(0, 120, curve.load_temperature, " °C")
            self.idle_temperature = _spin(0, 120, curve.idle_temperature, " °C")
            self.load_percent = _spin(0, 100, curve.load_percent, " %")
            self.idle_percent = _spin(0, 100, curve.idle_percent, " %")
            for widget in (self.load_temperature, self.idle_temperature,
                           self.load_percent, self.idle_percent):
                widget.valueChanged.connect(self._on_field_changed)
            self.form.addRow(tr("Speed up above"), self.load_temperature)
            self.form.addRow(tr("Slow down below"), self.idle_temperature)
            self.form.addRow(tr("Speed when loaded"), self.load_percent)
            self.form.addRow(tr("Speed when idle"), self.idle_percent)
            self._add_smoothing_rows()
            self.graph.set_editable(False)
            self.hint.setText(
                tr("Between the two temperatures the fan keeps whatever speed it "
                "already had. The preview shows the rising edge.")
            )

        elif isinstance(curve, TargetCurve):
            self.target_temperature = _spin(0, 120, curve.target_temperature, " °C")
            self.idle_percent = _spin(0, 100, curve.idle_percent, " %")
            self.load_percent = _spin(0, 100, curve.load_percent, " %")
            self.step_up = _spin(0.1, 50, curve.step_up, " %/s", decimals=1)
            self.step_down = _spin(0.1, 50, curve.step_down, " %/s", decimals=1)
            self.deadband = _spin(0, 10, curve.deadband, " °C", decimals=1)
            for widget in (self.target_temperature, self.idle_percent, self.load_percent,
                           self.step_up, self.step_down, self.deadband):
                widget.valueChanged.connect(self._on_field_changed)
            self.form.addRow(tr("Target temperature"), self.target_temperature)
            self.form.addRow(tr("Slowest"), self.idle_percent)
            self.form.addRow(tr("Fastest"), self.load_percent)
            self.form.addRow(tr("Speeds up by"), self.step_up)
            self.form.addRow(tr("Slows down by"), self.step_down)
            self.form.addRow(tr("Dead band"), self.deadband)
            self._add_smoothing_rows()
            self.graph.set_editable(False)

        elif isinstance(curve, MixCurve):
            self.function = QComboBox()
            for value, label in MIX_FUNCTION_LABELS.items():
                self.function.addItem(tr(label), value)
            index = self.function.findData(curve.function)
            self.function.setCurrentIndex(max(0, index))
            self.function.currentIndexChanged.connect(self._on_field_changed)
            self.form.addRow(tr("Combine using"), self.function)

            self.inputs = QListWidget()
            for other in self.config.curves:
                if other.id == curve.id:
                    continue  # a curve cannot mix itself
                item = QListWidgetItem(other.name)
                item.setData(Qt.UserRole, other.id)
                item.setCheckState(
                    Qt.Checked if other.id in curve.curve_ids else Qt.Unchecked
                )
                self.inputs.addItem(item)
            self.inputs.itemChanged.connect(self._on_field_changed)
            self.form.addRow(tr("Inputs"), self.inputs)
            self.graph.set_editable(False)

        elif isinstance(curve, SyncCurve):
            self.control = QComboBox()
            self.control.addItem(tr("— choose a fan —"), "")
            for control in self.config.controls:
                self.control.addItem(control.name, control.id)
            index = self.control.findData(curve.control_id)
            self.control.setCurrentIndex(max(0, index))
            self.control.currentIndexChanged.connect(self._on_field_changed)

            self.multiplier = _spin(0.1, 3.0, curve.multiplier, "×", decimals=2)
            self.offset = _spin(-50, 50, curve.offset_percent, " %")
            self.multiplier.valueChanged.connect(self._on_field_changed)
            self.offset.valueChanged.connect(self._on_field_changed)

            self.form.addRow(tr("Follow"), self.control)
            self.form.addRow(tr("Scaled by"), self.multiplier)
            self.form.addRow(tr("Offset by"), self.offset)
            self.graph.set_editable(False)

    # ------------------------------------------------------------------

    def _on_name_changed(self, text: str) -> None:
        self.curve.name = text.strip() or self.curve.name

    def _on_points_changed(self) -> None:
        if isinstance(self.curve, GraphCurve):
            self.curve.points = self.graph.points()

    def _on_field_changed(self, *_args) -> None:
        self._collect()
        self._refresh_preview()

    def _collect(self) -> None:
        """Copy the form back into the curve object."""

        curve = self.curve
        if hasattr(self, "sensor"):
            curve.sensor_id = self.sensor.currentData() or ""
        if hasattr(self, "hysteresis_up"):
            curve.hysteresis_up = self.hysteresis_up.value()
            curve.hysteresis_down = self.hysteresis_down.value()
            curve.response_time_up = self.response_up.value()
            curve.response_time_down = self.response_down.value()
            curve.ignore_hysteresis_at_limits = self.ignore_at_limits.isChecked()

        if isinstance(curve, GraphCurve):
            curve.points = self.graph.points()
            if hasattr(self, "axis_min"):
                low, high = self.axis_min.value(), self.axis_max.value()
                if high > low:
                    curve.axis_min_temperature = low
                    curve.axis_max_temperature = high
        elif isinstance(curve, FlatCurve):
            curve.percent = self.percent.value()
        elif isinstance(curve, LinearCurve):
            curve.min_temperature = self.min_temperature.value()
            curve.max_temperature = self.max_temperature.value()
            curve.min_percent = self.min_percent.value()
            curve.max_percent = self.max_percent.value()
        elif isinstance(curve, TriggerCurve):
            curve.load_temperature = self.load_temperature.value()
            curve.idle_temperature = self.idle_temperature.value()
            curve.load_percent = self.load_percent.value()
            curve.idle_percent = self.idle_percent.value()
        elif isinstance(curve, TargetCurve):
            curve.target_temperature = self.target_temperature.value()
            curve.idle_percent = self.idle_percent.value()
            curve.load_percent = self.load_percent.value()
            curve.step_up = self.step_up.value()
            curve.step_down = self.step_down.value()
            curve.deadband = self.deadband.value()
        elif isinstance(curve, MixCurve):
            curve.function = self.function.currentData()
            curve.curve_ids = [
                self.inputs.item(row).data(Qt.UserRole)
                for row in range(self.inputs.count())
                if self.inputs.item(row).checkState() == Qt.Checked
            ]
        elif isinstance(curve, SyncCurve):
            curve.control_id = self.control.currentData() or ""
            curve.multiplier = self.multiplier.value()
            curve.offset_percent = self.offset.value()

    def _refresh_preview(self) -> None:
        samples = sample_curve(self.curve)
        if samples is None:
            self.graph.set_points([])
            self.graph.setVisible(False)
            if isinstance(self.curve, MixCurve):
                self.hint.setText(
                    tr("A mix curve has no shape of its own — it follows whichever "
                    "of its inputs the chosen function picks.")
                )
            elif isinstance(self.curve, TargetCurve):
                self.hint.setText(
                    tr("A target curve has no fixed shape: its speed depends on how "
                    "long the sensor has been above or below the target, not only "
                    "on the current temperature.")
                )
            else:
                self.hint.setText(tr("This curve follows another fan, so it has no shape of its own."))
            return

        self.graph.setVisible(True)
        self.graph.set_temperature_range(*curve_range(self.curve))
        self.graph.set_points([CurvePoint(t, p) for t, p in samples])

        sensor_id = getattr(self.curve, "sensor_id", "")
        reading = self.temperatures.get(sensor_id)
        self.graph.set_reading(reading, None if reading is None else self._value_at(reading))

    def _value_at(self, temperature: float) -> float | None:
        samples = sample_curve(self.curve)
        if not samples:
            return None
        from ...core.curves import interpolate

        return interpolate(samples, temperature)

    # ------------------------------------------------------------------

    def accept(self) -> None:
        self._collect()
        super().accept()
