"""Data model for the fan control configuration.

The model mirrors the concepts used by FanControl on Windows so that a
``userConfig.json`` exported there can be represented losslessly enough to keep
the same behaviour on Linux:

``Sensor``      a temperature source (hwmon chip channel, GPU, ...)
``FanSensor``   an RPM reading
``Control``     a PWM output that is driven by a curve
``Curve``       a function that turns sensor readings into a fan percentage

Everything is plain dataclasses with ``to_dict``/``from_dict`` so the whole
configuration round-trips through JSON without any external dependency.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def clamp(value: float, low: float, high: float) -> float:
    if value < low:
        return low
    if value > high:
        return high
    return value


class MixFunction(str, Enum):
    MAX = "max"
    MIN = "min"
    AVERAGE = "average"
    SUM = "sum"
    SUBTRACT = "subtract"


class CurveType(str, Enum):
    GRAPH = "graph"
    FLAT = "flat"
    LINEAR = "linear"
    TARGET = "target"
    TRIGGER = "trigger"
    MIX = "mix"
    SYNC = "sync"


@dataclass
class CurvePoint:
    """A single point of a graph curve: at ``temperature`` run at ``percent``."""

    temperature: float
    percent: float

    def to_dict(self) -> dict[str, Any]:
        return {"temperature": self.temperature, "percent": self.percent}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CurvePoint":
        return cls(
            temperature=float(raw["temperature"]),
            percent=float(raw["percent"]),
        )


@dataclass
class BaseCurve:
    """Fields shared by every curve type.

    Smoothing and hysteresis are separate for rising and falling temperatures,
    the way FanControl stores them: most people want the fans to answer a rise
    quickly and to come back down slowly.
    """

    id: str
    name: str
    type: str = ""
    #: Temperature smoothing in seconds while the temperature rises. 0 is off.
    response_time_up: float = 0.0
    #: Temperature smoothing in seconds while it falls.
    response_time_down: float = 0.0
    #: Degrees the temperature must rise before the curve reacts to the rise.
    hysteresis_up: float = 0.0
    #: Degrees it must fall before the curve reacts to the fall.
    hysteresis_down: float = 0.0
    #: Skip the hysteresis once the temperature is past either end of the
    #: curve, where the output is flat anyway and holding it back only delays
    #: the fans for no benefit.
    ignore_hysteresis_at_limits: bool = False

    def sensor_ids(self) -> list[str]:
        """Temperature sensors this curve reads directly."""
        return []

    def curve_refs(self) -> list[str]:
        """Other curves this curve depends on."""
        return []

    def control_refs(self) -> list[str]:
        """Controls this curve follows (sync curves)."""
        return []

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type
        return data


@dataclass
class GraphCurve(BaseCurve):
    """Point-by-point curve, linearly interpolated between the points."""

    type: str = CurveType.GRAPH.value
    sensor_id: str = ""
    points: list[CurvePoint] = field(default_factory=list)
    #: The temperature range the editor shows. It does not change what the
    #: curve does - points outside it would simply be impossible to reach with
    #: the mouse - but a GPU curve needs a different range from a CPU one.
    axis_min_temperature: float = 0.0
    axis_max_temperature: float = 100.0

    def sensor_ids(self) -> list[str]:
        return [self.sensor_id] if self.sensor_id else []

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["type"] = self.type
        data["points"] = [p.to_dict() for p in self.points]
        return data


@dataclass
class FlatCurve(BaseCurve):
    """Constant percentage, independent of any sensor."""

    type: str = CurveType.FLAT.value
    percent: float = 50.0


@dataclass
class LinearCurve(BaseCurve):
    """Straight ramp between two temperature/percentage pairs."""

    type: str = CurveType.LINEAR.value
    sensor_id: str = ""
    min_temperature: float = 30.0
    max_temperature: float = 70.0
    min_percent: float = 20.0
    max_percent: float = 100.0

    def sensor_ids(self) -> list[str]:
        return [self.sensor_id] if self.sensor_id else []


@dataclass
class TargetCurve(BaseCurve):
    """Holds a target temperature by drifting between idle and load speed."""

    type: str = CurveType.TARGET.value
    sensor_id: str = ""
    target_temperature: float = 60.0
    idle_percent: float = 20.0
    load_percent: float = 100.0
    #: How fast the output moves towards the load/idle speed, in percent/second.
    step_up: float = 5.0
    step_down: float = 2.0
    #: Dead band around the target where the output is held steady.
    deadband: float = 1.0

    def sensor_ids(self) -> list[str]:
        return [self.sensor_id] if self.sensor_id else []


@dataclass
class TriggerCurve(BaseCurve):
    """Two-state curve with an explicit hysteresis window.

    Above ``load_temperature`` the output latches to ``load_percent``; it only
    returns to ``idle_percent`` once the temperature falls below
    ``idle_temperature``. Between the two it keeps whatever state it was in.
    """

    type: str = CurveType.TRIGGER.value
    sensor_id: str = ""
    idle_temperature: float = 50.0
    load_temperature: float = 60.0
    idle_percent: float = 20.0
    load_percent: float = 100.0

    def sensor_ids(self) -> list[str]:
        return [self.sensor_id] if self.sensor_id else []


@dataclass
class MixCurve(BaseCurve):
    """Combines several other curves with one of the mix functions."""

    type: str = CurveType.MIX.value
    curve_ids: list[str] = field(default_factory=list)
    function: str = MixFunction.MAX.value

    def curve_refs(self) -> list[str]:
        return list(self.curve_ids)


@dataclass
class SyncCurve(BaseCurve):
    """Mirrors the current output of another control, scaled and offset."""

    type: str = CurveType.SYNC.value
    control_id: str = ""
    offset_percent: float = 0.0
    multiplier: float = 1.0

    def control_refs(self) -> list[str]:
        return [self.control_id] if self.control_id else []


CURVE_CLASSES: dict[str, type[BaseCurve]] = {
    CurveType.GRAPH.value: GraphCurve,
    CurveType.FLAT.value: FlatCurve,
    CurveType.LINEAR.value: LinearCurve,
    CurveType.TARGET.value: TargetCurve,
    CurveType.TRIGGER.value: TriggerCurve,
    CurveType.MIX.value: MixCurve,
    CurveType.SYNC.value: SyncCurve,
}


def _upgrade_curve_fields(raw: dict[str, Any]) -> dict[str, Any]:
    """Accept configurations written before hysteresis was split up and down."""

    data = dict(raw)
    if "response_time" in data:
        value = data.pop("response_time")
        data.setdefault("response_time_up", value)
        data.setdefault("response_time_down", value)
    if "hysteresis" in data:
        value = data.pop("hysteresis")
        drop_only = data.pop("hysteresis_on_drop_only", True)
        data.setdefault("hysteresis_down", value)
        data.setdefault("hysteresis_up", 0.0 if drop_only else value)
    data.pop("hysteresis_on_drop_only", None)
    return data


def curve_from_dict(raw: dict[str, Any]) -> BaseCurve:
    ctype = raw.get("type", CurveType.GRAPH.value)
    cls = CURVE_CLASSES.get(ctype)
    if cls is None:
        raise ValueError(f"unknown curve type: {ctype!r}")
    raw = _upgrade_curve_fields(raw)
    kwargs = {k: v for k, v in raw.items() if k != "points"}
    # Drop keys the dataclass does not know about so that configs written by a
    # newer version still load instead of blowing up.
    known = {f for f in cls.__dataclass_fields__}
    kwargs = {k: v for k, v in kwargs.items() if k in known}
    curve = cls(**kwargs)
    if isinstance(curve, GraphCurve):
        curve.points = [CurvePoint.from_dict(p) for p in raw.get("points", [])]
        curve.points.sort(key=lambda p: p.temperature)
    return curve


@dataclass
class Control:
    """A PWM output plus the post-processing applied to the curve result."""

    id: str
    name: str
    #: Hardware identifier of the PWM output, e.g. ``hwmon:nct6798:pwm2``.
    output_id: str = ""
    #: Curve driving this control; empty means manual mode.
    curve_id: str = ""
    #: Control is managed by us at all. Disabled controls are handed back to
    #: the firmware.
    enabled: bool = True
    #: Used when ``curve_id`` is empty.
    manual_percent: float = 50.0
    #: Output clamping and shaping.
    min_percent: float = 0.0
    max_percent: float = 100.0
    offset_percent: float = 0.0
    #: Allow the fan to stop completely.
    allow_stop: bool = False
    #: Below this percentage the fan is switched off rather than run slowly,
    #: which is what FanControl calls the stop point. 0 means use
    #: ``min_percent`` as the threshold.
    stop_percent: float = 0.0
    #: When a stopped fan restarts, kick it at this percentage for
    #: ``start_duration`` seconds so it actually spins up.
    start_percent: float = 40.0
    start_duration: float = 2.0
    #: Slew rate limiting, percent per second. 0 disables it.
    step_up: float = 0.0
    step_down: float = 0.0
    #: Fan tachometer paired with this output, used by the UI and by the
    #: stall detection.
    fan_sensor_id: str = ""
    hidden: bool = False
    #: Measured (percent, rpm) pairs, either from this program's calibration or
    #: imported from FanControl's. Shown in the UI; nothing depends on it.
    calibration: list[list[float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Control":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


@dataclass
class Settings:
    """Global daemon behaviour."""

    #: Seconds between evaluations of the control loop.
    update_interval: float = 1.0
    #: Above this temperature every managed fan is forced to 100%.
    critical_temperature: float = 90.0
    #: Percentage used when a sensor a curve depends on is unavailable.
    failsafe_percent: float = 100.0
    #: Restore the firmware's automatic fan control when the daemon exits.
    restore_on_exit: bool = True
    #: Master switch; when false the daemon keeps reading sensors but does not
    #: write any PWM value.
    control_enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Settings":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


CONFIG_VERSION = 1


@dataclass
class Config:
    """The full configuration: curves, controls and global settings."""

    version: int = CONFIG_VERSION
    settings: Settings = field(default_factory=Settings)
    curves: list[BaseCurve] = field(default_factory=list)
    controls: list[Control] = field(default_factory=list)
    #: Friendly names the user gave to hardware sensors, keyed by sensor id.
    sensor_names: dict[str, str] = field(default_factory=dict)

    def curve_by_id(self, curve_id: str) -> BaseCurve | None:
        for curve in self.curves:
            if curve.id == curve_id:
                return curve
        return None

    def control_by_id(self, control_id: str) -> Control | None:
        for control in self.controls:
            if control.id == control_id:
                return control
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "settings": self.settings.to_dict(),
            "curves": [c.to_dict() for c in self.curves],
            "controls": [c.to_dict() for c in self.controls],
            "sensor_names": dict(self.sensor_names),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        return cls(
            version=int(raw.get("version", CONFIG_VERSION)),
            settings=Settings.from_dict(raw.get("settings", {})),
            curves=[curve_from_dict(c) for c in raw.get("curves", [])],
            controls=[Control.from_dict(c) for c in raw.get("controls", [])],
            sensor_names=dict(raw.get("sensor_names", {})),
        )


def validate(config: Config) -> list[str]:
    """Return a list of human readable problems found in ``config``.

    The daemon refuses to apply a configuration that has errors, so this is the
    single place that knows what "broken" means.
    """

    problems: list[str] = []
    curve_ids = {c.id for c in config.curves}
    control_ids = {c.id for c in config.controls}

    if len(curve_ids) != len(config.curves):
        problems.append("duplicate curve ids")
    if len(control_ids) != len(config.controls):
        problems.append("duplicate control ids")

    for curve in config.curves:
        for ref in curve.curve_refs():
            if ref not in curve_ids:
                problems.append(f"curve {curve.name!r} references unknown curve {ref!r}")
        for ref in curve.control_refs():
            if ref not in control_ids:
                problems.append(
                    f"curve {curve.name!r} references unknown control {ref!r}"
                )
        if isinstance(curve, GraphCurve) and not curve.points:
            problems.append(f"graph curve {curve.name!r} has no points")
        if isinstance(curve, MixCurve) and not curve.curve_ids:
            problems.append(f"mix curve {curve.name!r} has no inputs")
        if isinstance(curve, TriggerCurve) and curve.idle_temperature > curve.load_temperature:
            problems.append(
                f"trigger curve {curve.name!r}: idle temperature is above load temperature"
            )

    for control in config.controls:
        if control.curve_id and control.curve_id not in curve_ids:
            problems.append(
                f"control {control.name!r} references unknown curve {control.curve_id!r}"
            )
        if control.min_percent > control.max_percent:
            problems.append(f"control {control.name!r}: min percent above max percent")
        if control.stop_percent > control.max_percent:
            problems.append(f"control {control.name!r}: stop percent above max percent")

    problems.extend(_find_cycles(config))
    return problems


def _find_cycles(config: Config) -> list[str]:
    """Detect dependency loops between curves (mix/sync chains)."""

    control_curve = {c.id: c.curve_id for c in config.controls}
    graph: dict[str, list[str]] = {}
    for curve in config.curves:
        deps = list(curve.curve_refs())
        for control_id in curve.control_refs():
            target = control_curve.get(control_id)
            if target:
                deps.append(target)
        graph[curve.id] = deps

    problems: list[str] = []
    WHITE, GREY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)

    def visit(node: str, stack: list[str]) -> None:
        colour[node] = GREY
        for dep in graph.get(node, []):
            if dep not in colour:
                continue
            if colour[dep] == GREY:
                loop = " -> ".join(stack[stack.index(dep):] + [dep])
                problems.append(f"curve dependency loop: {loop}")
            elif colour[dep] == WHITE:
                visit(dep, stack + [dep])
        colour[node] = BLACK

    for node in list(graph):
        if colour[node] == WHITE:
            visit(node, [node])
    return problems


def evaluation_order(config: Config) -> list[str]:
    """Curve ids sorted so that dependencies come before their dependants."""

    control_curve = {c.id: c.curve_id for c in config.controls}
    graph: dict[str, list[str]] = {}
    for curve in config.curves:
        deps = list(curve.curve_refs())
        for control_id in curve.control_refs():
            target = control_curve.get(control_id)
            if target:
                deps.append(target)
        graph[curve.id] = [d for d in deps if d in {c.id for c in config.curves}]

    order: list[str] = []
    seen: set[str] = set()
    visiting: set[str] = set()

    def visit(node: str) -> None:
        if node in seen or node in visiting:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            visit(dep)
        visiting.discard(node)
        seen.add(node)
        order.append(node)

    for curve in config.curves:
        visit(curve.id)
    return order
