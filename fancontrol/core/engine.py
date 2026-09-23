"""The control loop.

One :meth:`ControlEngine.tick` does the whole job: read every sensor, evaluate
every curve, shape the result for each control and write the PWM values.
Everything that makes fan control safe rather than merely functional lives
here — the failsafe when a sensor disappears, the critical-temperature
override, the slew limiting and the spin-up kick for fans that were stopped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..hw.registry import HardwareRegistry
from .curves import CurveEvaluator, EvalContext
from .models import Config, Control, clamp

log = logging.getLogger(__name__)

#: How many consecutive ticks a fan may read 0 RPM while being driven before
#: we flag it as stalled.
STALL_TICKS = 5


@dataclass
class ControlRuntime:
    """Per-control state that has to survive between ticks."""

    #: Percentage we last wrote to the hardware.
    applied_percent: float = 0.0
    #: Percentage the curve asked for, before slew limiting.
    requested_percent: float = 0.0
    #: While set, the control is being kicked to ``start_percent`` to get a
    #: stopped fan moving again.
    kick_until: float = 0.0
    stall_ticks: int = 0
    stalled: bool = False
    last_error: str = ""
    #: The output is currently with the firmware because it is cool enough.
    with_firmware: bool = False


@dataclass
class EngineStatus:
    """A snapshot of the last tick, handed to the UI over D-Bus."""

    timestamp: float = 0.0
    control_enabled: bool = True
    critical: bool = False
    temperatures: dict[str, float | None] = field(default_factory=dict)
    fans: dict[str, float | None] = field(default_factory=dict)
    curve_values: dict[str, float] = field(default_factory=dict)
    curve_errors: dict[str, str] = field(default_factory=dict)
    controls: dict[str, dict[str, Any]] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "control_enabled": self.control_enabled,
            "critical": self.critical,
            "temperatures": self.temperatures,
            "fans": self.fans,
            "curve_values": self.curve_values,
            "curve_errors": self.curve_errors,
            "controls": self.controls,
            "messages": self.messages,
        }


class ControlEngine:
    def __init__(self, registry: HardwareRegistry, config: Config) -> None:
        self.registry = registry
        self.config = config
        self.evaluator = CurveEvaluator(config)
        self.runtime: dict[str, ControlRuntime] = {}
        self.status = EngineStatus()
        self._last_tick: float | None = None
        #: Readings from the current tick, for decisions made per control.
        self._temperatures: dict[str, float | None] = {}
        #: Controls the user is temporarily driving by hand from the UI,
        #: mapped to the percentage they asked for. Cleared on release.
        self.overrides: dict[str, float] = {}
        #: Controls the loop must not touch at all, because something else is
        #: driving them directly - currently only the calibration routine.
        self.paused: set[str] = set()

    # ------------------------------------------------------------------
    # configuration

    def set_config(self, config: Config) -> None:
        """Swap the configuration in, releasing controls that are gone."""

        old_outputs = {c.output_id for c in self.config.controls if c.enabled}
        new_outputs = {c.output_id for c in config.controls if c.enabled}
        for output_id in old_outputs - new_outputs:
            output = self.registry.controls.get(output_id)
            if output is not None:
                try:
                    output.release()
                except OSError:
                    log.exception("failed to release %s", output_id)

        self.config = config
        self.evaluator.reconfigure(config)
        alive = {c.id for c in config.controls}
        self.runtime = {k: v for k, v in self.runtime.items() if k in alive}

    # ------------------------------------------------------------------
    # manual override

    def set_override(self, control_id: str, percent: float | None) -> None:
        if percent is None:
            self.overrides.pop(control_id, None)
        else:
            self.overrides[control_id] = clamp(percent, 0.0, 100.0)

    # ------------------------------------------------------------------
    # pausing

    def pause(self, control_id: str) -> None:
        """Stop writing to a control so something else can drive it.

        Without this the loop keeps writing its own value every tick and fights
        whatever set the PWM, which is exactly what calibration does.
        """

        self.paused.add(control_id)

    def resume(self, control_id: str) -> None:
        runtime = self.runtime.get(control_id)
        if runtime is not None:
            # The hardware is wherever the other writer left it, so forget what
            # we thought was applied: otherwise the slew limiter would ramp from
            # a value that was never there.
            runtime.applied_percent = 0.0
            runtime.kick_until = 0.0
        self.paused.discard(control_id)

    # ------------------------------------------------------------------
    # the loop

    def tick(self) -> EngineStatus:
        now = time.monotonic()
        dt = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now

        temperatures = self.registry.read_temperatures()
        self._temperatures = temperatures
        fans = self.registry.read_fans()

        ctx = EvalContext(
            temperatures=temperatures,
            dt=dt,
            control_values={
                control_id: rt.applied_percent for control_id, rt in self.runtime.items()
            },
        )
        curve_values, curve_errors = self.evaluator.evaluate_all(ctx)

        critical, critical_messages = self._check_critical(temperatures)

        status = EngineStatus(
            timestamp=time.time(),
            control_enabled=self.config.settings.control_enabled,
            critical=critical,
            temperatures=temperatures,
            fans=fans,
            curve_values=curve_values,
            curve_errors=curve_errors,
            messages=list(critical_messages),
        )

        for control in self.config.controls:
            entry = self._apply_control(control, curve_values, curve_errors, fans, dt, critical, now)
            status.controls[control.id] = entry

        self.status = status
        return status

    def _check_critical(self, temperatures: dict[str, float | None]) -> tuple[bool, list[str]]:
        """Any sensor a curve actually uses being too hot forces full speed."""

        limit = self.config.settings.critical_temperature
        if limit <= 0:
            return False, []

        used: set[str] = set()
        for curve in self.config.curves:
            used.update(curve.sensor_ids())

        messages: list[str] = []
        critical = False
        for sensor_id in used:
            value = temperatures.get(sensor_id)
            if value is not None and value >= limit:
                critical = True
                messages.append(
                    f"{sensor_id} is at {value:.1f} °C (limit {limit:.0f} °C): forcing full speed"
                )
        return critical, messages

    def _target_percent(
        self,
        control: Control,
        curve_values: dict[str, float],
        curve_errors: dict[str, str],
    ) -> tuple[float, str]:
        """The raw percentage asked for, plus an error string if any."""

        if control.id in self.overrides:
            return self.overrides[control.id], ""
        if not control.curve_id:
            return control.manual_percent, ""
        if control.curve_id in curve_values:
            return curve_values[control.curve_id], ""
        reason = curve_errors.get(control.curve_id, "curve could not be evaluated")
        return self.config.settings.failsafe_percent, reason

    def _leave_to_firmware(
        self,
        control: Control,
        rt: ControlRuntime,
        output: Any,
        critical: bool,
        entry: dict[str, Any],
    ) -> bool:
        """Hand the output to the firmware while it is cool, and back when not.

        Returns True when the firmware has it this tick. A sensor that cannot
        be read, or a critical temperature anywhere, always means we take it:
        the firmware keeping a fan stopped is exactly wrong in either case.
        """

        if control.firmware_below <= 0:
            rt.with_firmware = False
            return False

        temperature = self._temperatures.get(control.firmware_sensor_id)
        if critical or temperature is None:
            hand_over = False
        elif temperature >= control.firmware_below:
            hand_over = False
        elif temperature < control.firmware_below - control.firmware_hysteresis:
            hand_over = True
        else:
            hand_over = rt.with_firmware  # in between: keep what we had

        if not hand_over:
            if rt.with_firmware:
                # Start the slew limiter from wherever the firmware left the fan
                # rather than from a speed we last wrote minutes ago.
                rt.applied_percent = entry.get("hardware_percent") or 0.0
                rt.kick_until = 0.0
            rt.with_firmware = False
            return False

        if output.acquired:
            try:
                output.release()
            except OSError as exc:
                entry["error"] = str(exc)
        rt.with_firmware = True
        entry["with_firmware"] = True
        entry["requested_percent"] = 0.0
        entry["applied_percent"] = entry["hardware_percent"] or 0.0
        return True

    def _apply_control(
        self,
        control: Control,
        curve_values: dict[str, float],
        curve_errors: dict[str, str],
        fans: dict[str, float | None],
        dt: float,
        critical: bool,
        now: float,
    ) -> dict[str, Any]:
        rt = self.runtime.setdefault(control.id, ControlRuntime())
        output = self.registry.controls.get(control.output_id)

        entry: dict[str, Any] = {
            "id": control.id,
            "name": control.name,
            "output_id": control.output_id,
            "enabled": control.enabled,
            "managed": False,
            "available": output is not None,
            "rpm": fans.get(control.fan_sensor_id) if control.fan_sensor_id else None,
            "stalled": False,
            "error": "",
            "kicking": False,
            "paused": False,
            "with_firmware": False,
        }

        if output is None:
            # A control the user deliberately turned off is not a problem worth
            # shouting about, even when its hardware is missing - that is the
            # normal state of an imported control with nothing to drive here.
            if control.enabled:
                rt.last_error = f"output {control.output_id!r} was not found"
                entry["error"] = rt.last_error
            entry["requested_percent"] = 0.0
            entry["applied_percent"] = 0.0
            entry["hardware_percent"] = None
            return entry

        entry["hardware_percent"] = output.read_percent()

        if control.id in self.paused:
            entry["paused"] = True
            entry["requested_percent"] = 0.0
            entry["applied_percent"] = entry["hardware_percent"] or 0.0
            return entry

        if not control.enabled or not self.config.settings.control_enabled:
            if output.acquired:
                try:
                    output.release()
                except OSError as exc:
                    entry["error"] = str(exc)
            entry["requested_percent"] = 0.0
            entry["applied_percent"] = entry["hardware_percent"] or 0.0
            return entry

        if self._leave_to_firmware(control, rt, output, critical, entry):
            return entry

        requested, error = self._target_percent(control, curve_values, curve_errors)
        if critical:
            requested = 100.0
        rt.requested_percent = requested

        shaped = self._shape(control, output, rt, requested, dt, now)

        try:
            output.set_percent(shaped)
            rt.applied_percent = shaped
            rt.last_error = error
        except OSError as exc:
            rt.last_error = str(exc)
            log.warning("cannot drive %s: %s", control.output_id, exc)

        self._update_stall(control, rt, fans, shaped)

        entry.update(
            {
                "managed": True,
                "requested_percent": round(requested, 2),
                "applied_percent": round(rt.applied_percent, 2),
                "stalled": rt.stalled,
                "error": rt.last_error,
                "kicking": now < rt.kick_until,
            }
        )
        return entry

    def _shape(
        self,
        control: Control,
        output: Any,
        rt: ControlRuntime,
        requested: float,
        dt: float,
        now: float,
    ) -> float:
        """Turn the curve's number into the value actually written."""

        value = requested + control.offset_percent

        # Stopping the fan is opt-in. FanControl keeps the stop point separate
        # from the running minimum, because the speed at which a fan stops and
        # the slowest speed you want it to run at are not the same number.
        stop_threshold = control.stop_percent if control.stop_percent > 0 else control.min_percent
        if control.allow_stop and output.can_stop and value < stop_threshold:
            value = 0.0
        else:
            # Never leave it between zero and the floor, which is exactly where
            # a fan stalls instead of turning slowly.
            value = max(value, control.min_percent)
        value = clamp(value, 0.0, control.max_percent)

        # A fan that was stopped needs more than its running minimum to start
        # turning at all, so hold start_percent briefly before settling down.
        if value > 0.0 and rt.applied_percent <= 0.0 and control.start_percent > value:
            rt.kick_until = now + max(0.0, control.start_duration)
        if now < rt.kick_until and value > 0.0:
            value = max(value, control.start_percent)
        elif value <= 0.0:
            rt.kick_until = 0.0

        # Slew limiting smooths out audible jumps. It is deliberately skipped
        # while kicking and when going to a full stop.
        if dt > 0 and now >= rt.kick_until:
            if value > rt.applied_percent and control.step_up > 0:
                value = min(value, rt.applied_percent + control.step_up * dt)
            elif value < rt.applied_percent and control.step_down > 0 and value > 0.0:
                value = max(value, rt.applied_percent - control.step_down * dt)

        return clamp(value, 0.0, 100.0)

    def _update_stall(
        self,
        control: Control,
        rt: ControlRuntime,
        fans: dict[str, float | None],
        applied: float,
    ) -> None:
        if not control.fan_sensor_id:
            rt.stalled = False
            return
        rpm = fans.get(control.fan_sensor_id)
        if applied <= 0.0 or rpm is None:
            rt.stall_ticks = 0
            rt.stalled = False
            return
        if rpm <= 0.0:
            rt.stall_ticks += 1
        else:
            rt.stall_ticks = 0
        was_stalled = rt.stalled
        rt.stalled = rt.stall_ticks >= STALL_TICKS
        if rt.stalled and not was_stalled:
            log.warning(
                "%s reads 0 RPM while driven at %.0f%%; raise its minimum or start percentage",
                control.name,
                applied,
            )

    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Hand every control back to the firmware."""

        if self.config.settings.restore_on_exit:
            self.registry.release_all()
