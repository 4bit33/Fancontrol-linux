"""One curve per fan, created automatically.

For every control that has no working curve, builds an individual graph
curve reading a suitable temperature sensor and points the control at it.
Controls that already have a curve are left alone.

Needs no Qt: the window and ``fanctl`` share this.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import BaseCurve, Config, CurvePoint, GraphCurve, new_id

#: The same starting shape a hand-made graph curve gets.
_DEFAULT_POINTS = ((30, 20), (50, 30), (70, 65), (85, 100))

_GPU_HINTS = ("gpu", "nvidia", "geforce", "radeon", "nvml")


@dataclass
class Assignment:
    """A control, the sensor its new curve will read, and the curve's name."""

    control_id: str
    control_name: str
    sensor_id: str
    sensor_name: str
    curve_name: str


def _cpu_sensor(temps: list[dict]) -> dict | None:
    for chip, label in (("k10temp", "Tctl"), ("coretemp", "Package id 0")):
        for temp in temps:
            device = temp.get("device", {})
            if device.get("chip") == chip and temp.get("name") == label:
                return temp
    return None


def _gpu_sensor(temps: list[dict]) -> dict | None:
    for chip, label in (("amdgpu", "edge"), ("nvidia", None)):
        for temp in temps:
            device = temp.get("device", {})
            if device.get("chip") != chip:
                continue
            if label is None or temp.get("name") == label:
                return temp
    return None


def choose_sensor(control_name: str, output_id: str,
                  temps: list[dict]) -> dict | None:
    """The temperature a new curve for this control should read."""

    text = f"{control_name} {output_id}".lower()
    if output_id.startswith("nvidia:") or any(h in text for h in _GPU_HINTS):
        return _gpu_sensor(temps) or _cpu_sensor(temps) or (temps[0] if temps else None)
    return _cpu_sensor(temps) or (temps[0] if temps else None)


def _unique_name(taken: set[str], base: str) -> str:
    if base not in taken:
        return base
    number = 2
    while f"{base} ({number})" in taken:
        number += 1
    return f"{base} ({number})"


def plan(config: Config, temps: list[dict]) -> list[Assignment]:
    """One assignment per control that has no working curve."""

    if not temps:
        return []
    taken = {curve.name for curve in config.curves}
    assignments = []
    for control in config.controls:
        if control.curve_id and config.curve_by_id(control.curve_id) is not None:
            continue
        sensor = choose_sensor(control.name, control.output_id, temps)
        if sensor is None:
            continue
        name = _unique_name(taken, control.name)
        taken.add(name)
        assignments.append(Assignment(
            control_id=control.id,
            control_name=control.name,
            sensor_id=sensor["id"],
            sensor_name=sensor.get("name", sensor["id"]),
            curve_name=name,
        ))
    return assignments


def plan_standalone(config: Config, temps: list[dict]) -> list[Assignment]:
    """Curves that watch temperatures without driving any fan.

    For machines with nothing controllable: a CPU curve and a GPU curve, so
    the window shows live graphs. Sensors already watched by a curve are
    skipped.
    """

    watched = set()
    for curve in config.curves:
        watched.update(curve.sensor_ids())
    candidates = []
    cpu, gpu = _cpu_sensor(temps), _gpu_sensor(temps)
    if cpu is not None:
        candidates.append(("CPU", cpu))
    if gpu is not None and (gpu.get("id") != (cpu or {}).get("id")):
        candidates.append(("GPU", gpu))
    taken = {curve.name for curve in config.curves}
    assignments = []
    for name, sensor in candidates:
        if sensor["id"] in watched:
            continue
        curve_name = _unique_name(taken, name)
        taken.add(curve_name)
        assignments.append(Assignment(
            control_id="",
            control_name="",
            sensor_id=sensor["id"],
            sensor_name=sensor.get("name", sensor["id"]),
            curve_name=curve_name,
        ))
    return assignments


def apply(config: Config, assignments: list[Assignment]) -> list[BaseCurve]:
    """Build the curves, point the controls at them and switch them on."""

    created = []
    by_id = {control.id: control for control in config.controls}
    for assignment in assignments:
        curve = GraphCurve(
            id=new_id("curve"),
            name=assignment.curve_name,
            sensor_id=assignment.sensor_id,
            points=[CurvePoint(temperature, percent)
                    for temperature, percent in _DEFAULT_POINTS],
            hysteresis_up=0.0,
            hysteresis_down=2.0,
            response_time_up=1.0,
            response_time_down=4.0,
        )
        config.curves.append(curve)
        created.append(curve)
        control = by_id.get(assignment.control_id)
        if control is not None:
            control.curve_id = curve.id
            control.enabled = True
    return created
