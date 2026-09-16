"""Importer for FanControl (Windows) ``userConfig.json`` files.

The file is a serialised .NET object graph and its shape has drifted between
releases, so nothing here assumes one schema. The interesting objects are found
structurally - an object with ``Points`` and a temperature source is a graph
curve wherever it sits in the document - and every field is read through a list
of aliases.

Formats seen so far, all of which this reads:

* curve points as ``"20.4,21.0"`` strings (version 270), as a
  ``{"30": 20}`` dictionary, and as ``[{"X": 30, "Y": 20}]`` objects;
* curves referenced by GUID, by bare name, and by ``{"Name": "..."}``;
* hysteresis as one number, and as a ``HysteresisConfig`` object with separate
  up and down values;
* identifiers from LibreHardwareMonitor (``/lpc/it8689e/control/0``) and from
  FanControl's NvAPI plugin (``NVApiWrapper/0-GA104-A/control/0``).

Hardware identifiers are kept verbatim as ``win:<identifier>`` ids. They are
meaningless on Linux, so :mod:`fancontrol.importer.mapping` resolves them
against the machine's real hardware afterwards; anything it cannot resolve is
reported for the user to fix by hand instead of being silently dropped.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..core.models import (
    Config,
    Control,
    CurvePoint,
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

log = logging.getLogger(__name__)

WIN_PREFIX = "win:"


@dataclass
class ImportResult:
    config: Config
    #: Windows identifiers that still need to be mapped to Linux hardware,
    #: keyed by the ``win:`` sensor id.
    unmapped_sensors: dict[str, str] = field(default_factory=dict)
    unmapped_controls: dict[str, str] = field(default_factory=dict)
    #: Tachometers a control was paired with on Windows.
    unmapped_fans: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.config.controls or self.config.curves)


# ----------------------------------------------------------------------
# generic JSON helpers


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    """Yield every dictionary in the document, depth first."""

    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _get(obj: dict[str, Any], *names: str, default: Any = None) -> Any:
    """Case-insensitive lookup that accepts several spellings of a field."""

    lowered = {k.lower(): v for k, v in obj.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value is not None:
            return value
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        if isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in {"true", "1", "yes"}:
            return True
        if value.strip().lower() in {"false", "0", "no"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _type_name(obj: dict[str, Any]) -> str:
    """The short .NET type name from a ``$type`` discriminator, if present."""

    raw = _get(obj, "$type", "Type", "CurveType", default="")
    if not isinstance(raw, str):
        return ""
    # "FanControl.Curves.GraphCurveConfig, FanControl" -> "GraphCurveConfig"
    head = raw.split(",", 1)[0]
    return head.rsplit(".", 1)[-1]


# ----------------------------------------------------------------------
# identifiers


def _identifier_of(value: Any) -> str:
    """Pull a hardware identifier out of whatever shape it was stored in."""

    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("Identifier", "Id", "SensorId", "Name"):
            found = _get(value, key)
            if isinstance(found, str) and found.strip():
                return found.strip()
    return ""


def _temp_source(obj: dict[str, Any]) -> str:
    raw = _get(
        obj,
        "SelectedTempSource",
        "SelectedTempSrc",
        "TempSource",
        "SelectedSensor",
        "Sensor",
        "SelectedTempSourceId",
        "SelectedTempSensorId",
        "TempSourceId",
    )
    return _identifier_of(raw)


# ----------------------------------------------------------------------
# curve points


def _parse_point_string(text: str) -> CurvePoint | None:
    """Parse ``"20.44826019013703,20.950755555555546"``.

    Version 270 stores each point as one string of temperature and percentage.
    The numbers use a dot for the decimal point regardless of the machine's
    locale, so a single comma always separates the two values.
    """

    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        return CurvePoint(float(parts[0]), float(parts[1]))
    except ValueError:
        return None


def _parse_points(raw: Any) -> list[CurvePoint]:
    """Accept every point encoding FanControl has used."""

    points: list[CurvePoint] = []
    if isinstance(raw, dict):
        # {"30": 20, "40": 35} — temperature keys, percentage values.
        for key, value in raw.items():
            try:
                points.append(CurvePoint(float(key), float(value)))
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                temp = _get(item, "X", "Temperature", "Temp", "Key", "Item1")
                percent = _get(item, "Y", "Speed", "Percent", "Value", "Item2")
                if temp is None or percent is None:
                    continue
                try:
                    points.append(CurvePoint(float(temp), float(percent)))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, str):
                point = _parse_point_string(item)
                if point is not None:
                    points.append(point)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    points.append(CurvePoint(float(item[0]), float(item[1])))
                except (TypeError, ValueError):
                    continue
    points.sort(key=lambda p: p.temperature)
    return points


def _parse_calibration(raw: Any) -> list[list[float]]:
    """FanControl's measured speed table: ``[[percent, rpm, interpolated], ...]``.

    Only the first two numbers are kept; the third flag marks points FanControl
    filled in itself rather than measured.
    """

    if not isinstance(raw, list):
        return []
    samples: list[list[float]] = []
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        try:
            samples.append([float(entry[0]), float(entry[1])])
        except (TypeError, ValueError):
            continue
    samples.sort(key=lambda pair: pair[0])
    return samples


#: FanControl stores the mix function as a number. This is the order its enum
#: is documented with; it is the one thing in this importer that cannot be
#: confirmed from a config file alone, so an imported mix curve always says in
#: the warnings which function it ended up with.
MIX_FUNCTIONS = {
    0: MixFunction.MAX,
    1: MixFunction.MIN,
    2: MixFunction.AVERAGE,
    3: MixFunction.SUM,
    4: MixFunction.SUBTRACT,
    "max": MixFunction.MAX,
    "maximum": MixFunction.MAX,
    "min": MixFunction.MIN,
    "minimum": MixFunction.MIN,
    "avg": MixFunction.AVERAGE,
    "average": MixFunction.AVERAGE,
    "sum": MixFunction.SUM,
    "subtract": MixFunction.SUBTRACT,
    "difference": MixFunction.SUBTRACT,
}


def _parse_mix_function(raw: Any) -> str:
    if isinstance(raw, str):
        return MIX_FUNCTIONS.get(raw.strip().lower(), MixFunction.MAX).value
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return MIX_FUNCTIONS.get(int(raw), MixFunction.MAX).value
    return MixFunction.MAX.value


# ----------------------------------------------------------------------
# object recognition


CURVE_TYPE_HINTS = (
    ("graph", "graph"),
    ("mix", "mix"),
    ("target", "target"),
    ("trigger", "trigger"),
    ("linear", "linear"),
    ("flat", "flat"),
    ("sync", "sync"),
)


#: Fields that only ever appear on a control. An object carrying one of these
#: is a fan, not a sensor entry that happens to have an identifier.
CONTROL_MARKERS = (
    "SelectedFanCurve",
    "SelectedCurveId",
    "SelectedCurveName",
    "SelectedCurve",
    "CurveId",
    "ManualControl",
    "MinimumPercent",
    "SelectedStart",
    "SelectedStop",
    "PairedFanSensor",
    "SelectedCommandStepUp",
    "Calibration",
    "ForceApply",
)

MIX_INPUT_FIELDS = ("SelectedFanCurves", "SelectedCurves", "Curves", "CurveIds", "SelectedCurveIds")


def _looks_like_curve(obj: dict[str, Any]) -> bool:
    type_name = _type_name(obj).lower()
    if "curve" in type_name:
        return True
    # A bare {"Name": "..."} is a reference to a curve, not a curve.
    if _get(obj, "Name") is None and _get(obj, "Id") is None:
        return False
    markers = (
        _get(obj, "Points") is not None,
        _temp_source(obj) != "" and _get(obj, "Identifier") is None,
        _get(obj, *MIX_INPUT_FIELDS) is not None,
        _get(obj, "TargetTemperature", "TargetTemp") is not None,
        _get(obj, "IdleTemperature") is not None and _get(obj, "LoadTemperature") is not None,
    )
    return any(markers)


def _curve_kind(obj: dict[str, Any]) -> str:
    type_name = _type_name(obj).lower()
    for needle, kind in CURVE_TYPE_HINTS:
        if needle in type_name:
            return kind
    # No usable discriminator: fall back to the fields that are present.
    if _get(obj, "Points") is not None:
        return "graph"
    if _get(obj, *MIX_INPUT_FIELDS) is not None:
        return "mix"
    if _get(obj, "TargetTemperature", "TargetTemp") is not None:
        return "target"
    if _get(obj, "IdleTemperature") is not None and _get(obj, "LoadTemperature") is not None:
        return "trigger"
    if _get(obj, "SelectedControl", "SelectedFan", "ControlId") is not None:
        return "sync"
    if _get(obj, "MinimumTemperature", "StartTemperature") is not None:
        return "linear"
    if _get(obj, "Value", "Speed", "Percent") is not None:
        return "flat"
    return "graph"


def _looks_like_control(obj: dict[str, Any]) -> bool:
    """Is this object a fan we can drive?

    Identifiers alone are not enough to tell. A ``PairedFanSensor`` reference
    and an entry in ``FanSensors`` both carry a ``/fan/`` identifier, and
    treating those as controls produced three phantom fans per real one. So a
    control either says ``/control/`` in its identifier, or carries a field
    that only a control has.
    """

    identifier = _identifier_of(_get(obj, "Identifier", "ControlIdentifier"))
    if not identifier:
        return False
    if "/control/" in identifier.lower():
        return True
    return any(_get(obj, marker) is not None for marker in CONTROL_MARKERS)


# ----------------------------------------------------------------------
# the importer


class FanControlImporter:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        #: Original Windows curve key (GUID or name) -> our curve id.
        self._curve_keys: dict[str, str] = {}
        #: Original Windows control identifier -> our control id.
        self._control_keys: dict[str, str] = {}
        self._sensor_labels: dict[str, str] = {}
        self._control_labels: dict[str, str] = {}

    # -- public -------------------------------------------------------

    def load(self, path: str | Path) -> ImportResult:
        text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        return self.loads(text)

    def loads(self, text: str) -> ImportResult:
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not a valid JSON document: {exc}") from exc
        return self.convert(document)

    def convert(self, document: Any) -> ImportResult:
        self.warnings = []
        self._curve_keys = {}
        self._control_keys = {}
        self._sensor_labels = {}
        self._control_labels = {}

        self._collect_labels(document)

        curve_objects = self._find_curves(document)
        control_objects = self._find_controls(document)

        if not curve_objects and not control_objects:
            raise ValueError(
                "no FanControl curves or controls found in this file — is it a "
                "FanControl userConfig.json?"
            )

        config = Config()

        # Two passes: the first assigns ids so that mix and sync curves can be
        # resolved in the second, whatever order they appear in the file.
        prepared: list[tuple[dict[str, Any], str, str]] = []
        for obj in curve_objects:
            curve_id = new_id("curve")
            name = self._curve_name(obj, len(prepared))
            for key in self._curve_key_candidates(obj, name):
                self._curve_keys.setdefault(key, curve_id)
            prepared.append((obj, curve_id, name))

        for obj in control_objects:
            identifier = _identifier_of(_get(obj, "Identifier", "ControlIdentifier"))
            control_id = new_id("control")
            self._control_keys.setdefault(identifier, control_id)
            name = _get(obj, "NickName", "Name", default="") or identifier
            self._control_keys.setdefault(str(name), control_id)

        for obj, curve_id, name in prepared:
            curve = self._build_curve(obj, curve_id, name)
            if curve is not None:
                config.curves.append(curve)

        for obj in control_objects:
            config.controls.append(self._build_control(obj))

        # Only names the user actually chose on Windows are carried over; a
        # raw identifier is worse than the name Linux gives the sensor.
        config.sensor_names.update(self._sensor_labels)

        result = ImportResult(config=config, warnings=list(self.warnings))
        self._fill_unmapped(result)
        return result

    # -- discovery ----------------------------------------------------

    def _find_curves(self, document: Any) -> list[dict[str, Any]]:
        seen: list[dict[str, Any]] = []
        identity: set[int] = set()
        for obj in _walk(document):
            if id(obj) in identity:
                continue
            if _looks_like_control(obj):
                continue
            if _looks_like_curve(obj):
                identity.add(id(obj))
                seen.append(obj)
        return seen

    def _find_controls(self, document: Any) -> list[dict[str, Any]]:
        seen: list[dict[str, Any]] = []
        identity: set[int] = set()
        for obj in _walk(document):
            if id(obj) in identity:
                continue
            if _looks_like_control(obj):
                identity.add(id(obj))
                seen.append(obj)
        return seen

    def _collect_labels(self, document: Any) -> None:
        """Remember the nicknames the user gave to sensors on Windows."""

        for obj in _walk(document):
            identifier = _identifier_of(_get(obj, "Identifier"))
            if not identifier:
                continue
            nickname = _get(obj, "NickName", "Nickname", "CustomName", "Name")
            if isinstance(nickname, str) and nickname.strip():
                lowered = identifier.lower()
                if "/control" in lowered:
                    self._control_labels[identifier] = nickname.strip()
                else:
                    self._sensor_labels[WIN_PREFIX + identifier] = nickname.strip()

    # -- curves -------------------------------------------------------

    def _curve_name(self, obj: dict[str, Any], index: int) -> str:
        name = _get(obj, "Name", "NickName", "CurveName")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return f"Imported curve {index + 1}"

    def _curve_key_candidates(self, obj: dict[str, Any], name: str) -> list[str]:
        keys: list[str] = []
        for field_name in ("Id", "Guid", "CurveId", "Identifier"):
            value = _get(obj, field_name)
            if isinstance(value, str) and value.strip():
                keys.append(value.strip())
        keys.append(name)
        return keys

    def _smoothing(self, obj: dict[str, Any]) -> dict[str, Any]:
        """Read hysteresis and response time in either of the two layouts.

        Version 270 nests them in a ``HysteresisConfig`` object with separate
        up and down values; older files had one number for each, plus a flag
        that meant "only apply it while the temperature drops".
        """

        config = _get(obj, "HysteresisConfig", "Hysteresis")
        if isinstance(config, dict):
            return {
                "hysteresis_up": _as_float(_get(config, "HysteresisValueUp", "ValueUp", "Up"), 0.0),
                "hysteresis_down": _as_float(
                    _get(config, "HysteresisValueDown", "ValueDown", "Down"), 0.0
                ),
                "response_time_up": _as_float(_get(config, "ResponseTimeUp"), 0.0),
                "response_time_down": _as_float(_get(config, "ResponseTimeDown"), 0.0),
                "ignore_hysteresis_at_limits": _as_bool(
                    _get(config, "IgnoreHysteresisAtLimits", "IgnoreAtLimits"), False
                ),
            }

        value = _as_float(_get(obj, "Hysteresis", "HysteresisDegrees", "TempHysteresis"), 0.0)
        drop_only = _as_bool(
            _get(obj, "HysteresisOnlyOnDrop", "HysteresisDrop", "ApplyHysteresisOnDropOnly"),
            True,
        )
        response = _as_float(_get(obj, "ResponseTime", "ResponseTimeSeconds", "Smoothing"), 0.0)
        return {
            "hysteresis_up": 0.0 if drop_only else value,
            "hysteresis_down": value,
            "response_time_up": response,
            "response_time_down": response,
            "ignore_hysteresis_at_limits": False,
        }

    def _build_curve(self, obj: dict[str, Any], curve_id: str, name: str):
        kind = _curve_kind(obj)
        common = {"id": curve_id, "name": name, **self._smoothing(obj)}

        sensor = _temp_source(obj)
        sensor_id = WIN_PREFIX + sensor if sensor else ""

        if kind == "graph":
            points = _parse_points(_get(obj, "Points", "Point", "GraphPoints"))
            if not points:
                self.warnings.append(f"curve {name!r} has no points; using a safe default ramp")
                points = [CurvePoint(30, 30), CurvePoint(60, 60), CurvePoint(80, 100)]
            # The axis range matters for editing: a GPU curve drawn up to 120 C
            # would have points off the end of a 0..100 graph.
            axis_min = _as_float(_get(obj, "MinimumTemperature", "MinTemperature"), 0.0)
            axis_max = _as_float(_get(obj, "MaximumTemperature", "MaxTemperature"), 100.0)
            if axis_max <= axis_min:
                axis_min, axis_max = 0.0, 100.0
            # Make sure every imported point is actually reachable on the graph.
            axis_min = min(axis_min, min(p.temperature for p in points))
            axis_max = max(axis_max, max(p.temperature for p in points))
            return GraphCurve(
                **common,
                sensor_id=sensor_id,
                points=points,
                axis_min_temperature=float(int(axis_min)),
                axis_max_temperature=float(int(axis_max + 0.999)),
            )

        if kind == "flat":
            percent = _as_float(_get(obj, "Value", "Speed", "Percent", "FanSpeed"), 50.0)
            return FlatCurve(**common, percent=percent)

        if kind == "linear":
            return LinearCurve(
                **common,
                sensor_id=sensor_id,
                min_temperature=_as_float(
                    _get(obj, "MinimumTemperature", "StartTemperature", "TempStart", "MinTemp"), 30.0
                ),
                max_temperature=_as_float(
                    _get(obj, "MaximumTemperature", "StopTemperature", "TempEnd", "MaxTemp"), 70.0
                ),
                min_percent=_as_float(
                    _get(obj, "MinimumSpeed", "StartSpeed", "MinSpeed", "SpeedStart"), 20.0
                ),
                max_percent=_as_float(
                    _get(obj, "MaximumSpeed", "StopSpeed", "MaxSpeed", "SpeedEnd"), 100.0
                ),
            )

        if kind == "target":
            return TargetCurve(
                **common,
                sensor_id=sensor_id,
                target_temperature=_as_float(
                    _get(obj, "TargetTemperature", "TargetTemp", "Target"), 60.0
                ),
                idle_percent=_as_float(_get(obj, "IdleFanSpeed", "IdleSpeed", "MinimumSpeed"), 20.0),
                load_percent=_as_float(_get(obj, "LoadFanSpeed", "LoadSpeed", "MaximumSpeed"), 100.0),
                step_up=_as_float(_get(obj, "StepUp", "SpeedUpStep"), 5.0),
                step_down=_as_float(_get(obj, "StepDown", "SpeedDownStep"), 2.0),
                deadband=_as_float(_get(obj, "Deadband", "DeadZone"), 1.0),
            )

        if kind == "trigger":
            return TriggerCurve(
                **common,
                sensor_id=sensor_id,
                idle_temperature=_as_float(_get(obj, "IdleTemperature", "IdleTemp"), 50.0),
                load_temperature=_as_float(_get(obj, "LoadTemperature", "LoadTemp"), 60.0),
                idle_percent=_as_float(_get(obj, "IdleFanSpeed", "IdleSpeed"), 20.0),
                load_percent=_as_float(_get(obj, "LoadFanSpeed", "LoadSpeed"), 100.0),
            )

        if kind == "mix":
            raw_refs = _get(obj, *MIX_INPUT_FIELDS, default=[])
            refs: list[str] = []
            if isinstance(raw_refs, list):
                for item in raw_refs:
                    key = _identifier_of(item) or (item if isinstance(item, str) else "")
                    if key:
                        refs.append(key)
            resolved = []
            for key in refs:
                target = self._curve_keys.get(key)
                if target:
                    resolved.append(target)
                else:
                    self.warnings.append(
                        f"mix curve {name!r} references curve {key!r} which is not in the file"
                    )
            raw_function = _get(obj, "SelectedMixFunction", "MixFunction", "Function", "Mode")
            function = _parse_mix_function(raw_function)
            if isinstance(raw_function, (int, float)) and not isinstance(raw_function, bool):
                self.warnings.append(
                    f"mix curve {name!r} was stored as function number {int(raw_function)}, "
                    f"read as {function!r} — worth checking against FanControl"
                )
            return MixCurve(**common, curve_ids=resolved, function=function)

        if kind == "sync":
            target = _identifier_of(_get(obj, "SelectedControl", "SelectedFan", "ControlId"))
            control_id = self._control_keys.get(target, "")
            if target and not control_id:
                self.warnings.append(
                    f"sync curve {name!r} follows control {target!r} which is not in the file"
                )
            return SyncCurve(
                **common,
                control_id=control_id,
                offset_percent=_as_float(_get(obj, "Offset", "OffsetPercent"), 0.0),
                multiplier=_as_float(_get(obj, "Multiplier", "Scale"), 1.0),
            )

        self.warnings.append(f"curve {name!r} has an unsupported type and was skipped")
        return None

    # -- controls -----------------------------------------------------

    def _build_control(self, obj: dict[str, Any]) -> Control:
        identifier = _identifier_of(_get(obj, "Identifier", "ControlIdentifier"))
        control_id = self._control_keys.get(identifier) or new_id("control")
        name = _get(obj, "NickName", "Name", default="") or identifier

        curve_ref = _identifier_of(
            _get(obj, "SelectedFanCurve", "SelectedCurveId", "SelectedCurveName",
                 "SelectedCurve", "CurveId")
        )
        curve_id = self._curve_keys.get(curve_ref, "")
        if curve_ref and not curve_id:
            self.warnings.append(
                f"control {name!r} selects curve {curve_ref!r} which is not in the file"
            )

        manual = _as_bool(_get(obj, "ManualControl", "IsManual", "Manual"), False)
        minimum = _as_float(_get(obj, "MinimumPercent", "Minimum", "MinSpeed", "MinimumSpeed"), 0.0)
        maximum = _as_float(_get(obj, "MaximumPercent", "Maximum", "MaxSpeed", "MaximumSpeed"), 100.0)
        if maximum <= 0:
            maximum = 100.0

        start = _as_float(
            _get(obj, "SelectedStart", "StartPercent", "StartSpeed", "FanStartSpeed"), 0.0
        )
        stop = _as_float(_get(obj, "SelectedStop", "StopPercent", "StopSpeed"), 0.0)

        # Version 270 has no "may this fan stop" flag: a stop point above zero
        # is what says the fan can be switched off. Fans calibrated as never
        # stopping have both numbers at zero.
        allow_stop = _as_bool(
            _get(obj, "StopEnabled", "CanStop", "FanStop", "AllowStop"), stop > 0
        )

        paired = _identifier_of(_get(obj, "PairedFanSensor", "FanSensor", "PairedFan"))

        return Control(
            id=control_id,
            name=str(name),
            output_id=WIN_PREFIX + identifier if identifier else "",
            curve_id="" if manual else curve_id,
            enabled=_as_bool(_get(obj, "Enable", "Enabled", "IsEnabled"), True),
            manual_percent=_as_float(
                _get(obj, "ManualControlValue", "ManualValue", "CurrentValue", "Speed"), 50.0
            ),
            min_percent=minimum,
            max_percent=maximum,
            offset_percent=_as_float(_get(obj, "SelectedOffset", "Offset", "OffsetPercent"), 0.0),
            allow_stop=allow_stop,
            stop_percent=stop,
            # FanControl limits the command change per update cycle; with the
            # default roughly one second cycle that is the same number per
            # second, which is how this program expresses it.
            step_up=_as_float(_get(obj, "SelectedCommandStepUp", "StepUp", "SpeedUpStep"), 0.0),
            step_down=_as_float(
                _get(obj, "SelectedCommandStepDown", "StepDown", "SpeedDownStep"), 0.0
            ),
            start_percent=start,
            fan_sensor_id=WIN_PREFIX + paired if paired else "",
            hidden=_as_bool(_get(obj, "IsHidden", "Hidden"), False),
            calibration=_parse_calibration(_get(obj, "Calibration")),
        )

    # -- reporting ----------------------------------------------------

    def _fill_unmapped(self, result: ImportResult) -> None:
        config = result.config
        for curve in config.curves:
            for sensor_id in curve.sensor_ids():
                if sensor_id.startswith(WIN_PREFIX):
                    result.unmapped_sensors[sensor_id] = sensor_id[len(WIN_PREFIX):]
        for control in config.controls:
            if control.output_id.startswith(WIN_PREFIX):
                result.unmapped_controls[control.output_id] = control.output_id[len(WIN_PREFIX):]
            if control.fan_sensor_id.startswith(WIN_PREFIX):
                result.unmapped_fans[control.fan_sensor_id] = control.fan_sensor_id[len(WIN_PREFIX):]


def import_file(path: str | Path) -> ImportResult:
    """Convenience wrapper around :class:`FanControlImporter`."""

    return FanControlImporter().load(path)
