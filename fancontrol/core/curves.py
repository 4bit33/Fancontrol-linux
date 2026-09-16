"""Curve evaluation.

Curves are not pure functions: hysteresis, response-time smoothing, target
curves and trigger curves all remember what they did on the previous tick. The
per-curve state lives in :class:`CurveState`, and :class:`CurveEvaluator` owns
one state object per curve id.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    BaseCurve,
    Config,
    FlatCurve,
    GraphCurve,
    LinearCurve,
    MixCurve,
    MixFunction,
    SyncCurve,
    TargetCurve,
    TriggerCurve,
    clamp,
    evaluation_order,
)


class SensorUnavailable(Exception):
    """Raised when a curve needs a sensor that is not currently readable."""


@dataclass
class CurveState:
    """Mutable per-curve memory carried between ticks."""

    #: Smoothed temperature after the response-time low-pass filter.
    smoothed_temp: float | None = None
    #: Temperature the curve last actually reacted to (hysteresis anchor).
    latched_temp: float | None = None
    #: Last percentage produced, used by target and trigger curves.
    last_percent: float | None = None
    #: Trigger curves latch into the "load" state.
    triggered: bool = False


@dataclass
class EvalContext:
    """Everything a curve needs to produce a number for one tick."""

    #: Current temperature per sensor id. A missing or ``None`` entry means the
    #: sensor could not be read.
    temperatures: dict[str, float | None]
    #: Seconds since the previous tick.
    dt: float
    #: Percentages produced by curves already evaluated this tick.
    curve_values: dict[str, float] = field(default_factory=dict)
    #: Percentages currently applied to each control, for sync curves.
    control_values: dict[str, float] = field(default_factory=dict)


def interpolate(points: list[tuple[float, float]], temperature: float) -> float:
    """Piecewise-linear interpolation, flat outside the point range."""

    if not points:
        return 0.0
    ordered = sorted(points, key=lambda p: p[0])
    if temperature <= ordered[0][0]:
        return ordered[0][1]
    if temperature >= ordered[-1][0]:
        return ordered[-1][1]
    for (t0, p0), (t1, p1) in zip(ordered, ordered[1:]):
        if t0 <= temperature <= t1:
            if t1 == t0:
                return p1
            ratio = (temperature - t0) / (t1 - t0)
            return p0 + (p1 - p0) * ratio
    return ordered[-1][1]


def _smooth(curve: BaseCurve, state: CurveState, raw: float, dt: float) -> float:
    """First-order low-pass, with its own time constant per direction.

    Rising and falling are smoothed separately because that is what people
    actually want: answer a sudden load quickly, come back down gently.
    """

    previous = state.smoothed_temp
    if previous is None or dt <= 0:
        state.smoothed_temp = raw
        return raw

    seconds = curve.response_time_up if raw > previous else curve.response_time_down
    if seconds <= 0:
        state.smoothed_temp = raw
        return raw

    alpha = min(1.0, dt / seconds)
    state.smoothed_temp = previous + (raw - previous) * alpha
    return state.smoothed_temp


def _at_limit(curve: BaseCurve, temp: float) -> bool:
    """True when the temperature is past either end of the curve.

    Out there the output is flat, so holding the reading back with hysteresis
    only delays the fans without changing where they end up.
    """

    if isinstance(curve, GraphCurve) and curve.points:
        return temp <= curve.points[0].temperature or temp >= curve.points[-1].temperature
    if isinstance(curve, LinearCurve):
        return temp <= curve.min_temperature or temp >= curve.max_temperature
    return False


def _apply_hysteresis(curve: BaseCurve, state: CurveState, temp: float) -> float:
    """Return the temperature the curve should act on.

    The curve keeps using the temperature it last acted on until the reading
    has moved further than the hysteresis for that direction. Setting
    ``hysteresis_up`` to zero - which is what FanControl's "only on drop"
    option amounts to - lets a rise through immediately.
    """

    if curve.ignore_hysteresis_at_limits and _at_limit(curve, temp):
        state.latched_temp = temp
        return temp

    latched = state.latched_temp
    if latched is None:
        state.latched_temp = temp
        return temp

    threshold = curve.hysteresis_up if temp > latched else curve.hysteresis_down
    if threshold <= 0 or abs(temp - latched) >= threshold:
        state.latched_temp = temp
        return temp
    return latched


def _sensor_temp(ctx: EvalContext, sensor_id: str) -> float:
    if not sensor_id:
        raise SensorUnavailable("curve has no sensor assigned")
    value = ctx.temperatures.get(sensor_id)
    if value is None:
        raise SensorUnavailable(f"sensor {sensor_id!r} is unavailable")
    return value


def _prepared_temp(curve: BaseCurve, state: CurveState, ctx: EvalContext, sensor_id: str) -> float:
    raw = _sensor_temp(ctx, sensor_id)
    smoothed = _smooth(curve, state, raw, ctx.dt)
    return _apply_hysteresis(curve, state, smoothed)


def _eval_graph(curve: GraphCurve, state: CurveState, ctx: EvalContext) -> float:
    temp = _prepared_temp(curve, state, ctx, curve.sensor_id)
    points = [(p.temperature, p.percent) for p in curve.points]
    return interpolate(points, temp)


def _eval_linear(curve: LinearCurve, state: CurveState, ctx: EvalContext) -> float:
    temp = _prepared_temp(curve, state, ctx, curve.sensor_id)
    lo_t, hi_t = curve.min_temperature, curve.max_temperature
    lo_p, hi_p = curve.min_percent, curve.max_percent
    if hi_t <= lo_t:
        return hi_p if temp >= hi_t else lo_p
    ratio = clamp((temp - lo_t) / (hi_t - lo_t), 0.0, 1.0)
    return lo_p + (hi_p - lo_p) * ratio


def _eval_target(curve: TargetCurve, state: CurveState, ctx: EvalContext) -> float:
    temp = _prepared_temp(curve, state, ctx, curve.sensor_id)
    current = state.last_percent
    if current is None:
        current = curve.idle_percent

    delta = temp - curve.target_temperature
    dt = max(ctx.dt, 0.0)
    if delta > curve.deadband:
        step = curve.step_up * dt if curve.step_up > 0 else abs(curve.load_percent - current)
        current = min(curve.load_percent, current + step)
    elif delta < -curve.deadband:
        step = curve.step_down * dt if curve.step_down > 0 else abs(current - curve.idle_percent)
        current = max(curve.idle_percent, current - step)

    lo = min(curve.idle_percent, curve.load_percent)
    hi = max(curve.idle_percent, curve.load_percent)
    current = clamp(current, lo, hi)
    state.last_percent = current
    return current


def _eval_trigger(curve: TriggerCurve, state: CurveState, ctx: EvalContext) -> float:
    # A trigger curve is hysteresis by construction, so the generic hysteresis
    # is deliberately not applied on top of it.
    raw = _sensor_temp(ctx, curve.sensor_id)
    temp = _smooth(curve, state, raw, ctx.dt)
    if temp >= curve.load_temperature:
        state.triggered = True
    elif temp <= curve.idle_temperature:
        state.triggered = False
    value = curve.load_percent if state.triggered else curve.idle_percent
    state.last_percent = value
    return value


def _eval_mix(curve: MixCurve, ctx: EvalContext) -> float:
    values = [ctx.curve_values[cid] for cid in curve.curve_ids if cid in ctx.curve_values]
    if not values:
        raise SensorUnavailable(f"mix curve {curve.name!r} has no usable inputs")
    function = curve.function
    if function == MixFunction.MAX.value:
        return max(values)
    if function == MixFunction.MIN.value:
        return min(values)
    if function == MixFunction.AVERAGE.value:
        return sum(values) / len(values)
    if function == MixFunction.SUM.value:
        return sum(values)
    if function == MixFunction.SUBTRACT.value:
        result = values[0]
        for value in values[1:]:
            result -= value
        return result
    return max(values)


def _eval_sync(curve: SyncCurve, ctx: EvalContext) -> float:
    if curve.control_id not in ctx.control_values:
        raise SensorUnavailable(
            f"sync curve {curve.name!r} follows control {curve.control_id!r} which has no value yet"
        )
    base = ctx.control_values[curve.control_id]
    return base * curve.multiplier + curve.offset_percent


class CurveEvaluator:
    """Evaluates every curve of a configuration, in dependency order."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._states: dict[str, CurveState] = {}
        self._order: list[str] = evaluation_order(config)

    def reconfigure(self, config: Config) -> None:
        """Swap in a new configuration, keeping state of curves that survived."""

        self._config = config
        self._order = evaluation_order(config)
        alive = {c.id for c in config.curves}
        self._states = {k: v for k, v in self._states.items() if k in alive}

    def state_for(self, curve_id: str) -> CurveState:
        return self._states.setdefault(curve_id, CurveState())

    def evaluate_all(self, ctx: EvalContext) -> tuple[dict[str, float], dict[str, str]]:
        """Evaluate every curve.

        Returns the percentage per curve id, plus a map of curve id to the
        reason it could not be evaluated. A curve that fails is simply absent
        from the values dict; callers decide what to do (the engine falls back
        to the failsafe percentage).
        """

        values: dict[str, float] = {}
        errors: dict[str, str] = {}
        ctx.curve_values = values

        by_id = {c.id: c for c in self._config.curves}
        for curve_id in self._order:
            curve = by_id.get(curve_id)
            if curve is None:
                continue
            state = self.state_for(curve_id)
            try:
                values[curve_id] = clamp(self._evaluate_one(curve, state, ctx), 0.0, 100.0)
            except SensorUnavailable as exc:
                errors[curve_id] = str(exc)
        return values, errors

    def _evaluate_one(self, curve: BaseCurve, state: CurveState, ctx: EvalContext) -> float:
        if isinstance(curve, FlatCurve):
            return curve.percent
        if isinstance(curve, GraphCurve):
            return _eval_graph(curve, state, ctx)
        if isinstance(curve, LinearCurve):
            return _eval_linear(curve, state, ctx)
        if isinstance(curve, TargetCurve):
            return _eval_target(curve, state, ctx)
        if isinstance(curve, TriggerCurve):
            return _eval_trigger(curve, state, ctx)
        if isinstance(curve, MixCurve):
            return _eval_mix(curve, ctx)
        if isinstance(curve, SyncCurve):
            return _eval_sync(curve, ctx)
        raise SensorUnavailable(f"curve type {curve.type!r} is not supported")
