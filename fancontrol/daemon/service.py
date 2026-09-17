"""The daemon's logic, with no D-Bus in sight.

Keeping the service free of any transport makes it testable and lets the CLI
drive it in-process, while :mod:`fancontrol.daemon.dbus_service` exposes the
same methods on the system bus.

Every method returns plain dictionaries so the D-Bus layer only has to encode
them as JSON.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..core import config as config_module
from ..core.engine import ControlEngine
from ..core.models import Config, validate
from ..hw.registry import HardwareRegistry
from ..importer.fancontrol_json import FanControlImporter
from ..importer.mapping import HardwareMapper
from . import state

log = logging.getLogger(__name__)


def ok(**extra: Any) -> dict[str, Any]:
    return {"ok": True, **extra}


def fail(error: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": error, **extra}


class FanControlService:
    """Owns the hardware, the configuration and the control loop."""

    def __init__(
        self,
        config_path: Path | None = None,
        registry: HardwareRegistry | None = None,
        session: bool = False,
    ) -> None:
        self.config_path = config_path or config_module.default_config_path(session)
        self.registry = registry if registry is not None else HardwareRegistry()
        self.config = Config()
        self.engine: ControlEngine | None = None
        self.calibration: dict[str, Any] = {}
        #: Seconds to wait after each PWM step during calibration, so the fan
        #: has time to reach its new speed before the tachometer is read.
        self.calibration_settle = 1.5

        #: Outputs recorded as held, so the file is only rewritten on a change.
        self._held: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

    # ------------------------------------------------------------------
    # lifecycle

    def start(self) -> None:
        """Discover hardware and load (or create) the configuration."""

        self.registry.discover()
        self._recover_outputs()
        try:
            self.config = config_module.load(self.config_path)
        except ValueError as exc:
            log.error("%s; starting with an empty configuration", exc)
            self.config = Config()

        if not self.config.controls:
            log.info("no controls configured yet, bootstrapping from the hardware")
            self.config = config_module.bootstrap(self.registry)
            try:
                config_module.save(self.config, self.config_path)
            except OSError as exc:
                log.warning("could not write the initial configuration: %s", exc)

        self.engine = ControlEngine(self.registry, self.config)

    def _recover_outputs(self) -> None:
        """Hand back anything a previous run was holding when it was killed."""

        held = state.load()
        if not held:
            return
        for output_id, saved in held.items():
            output = self.registry.controls.get(output_id)
            if output is None:
                continue
            try:
                output.adopt(saved)
                output.release()
                log.warning(
                    "%s was still held by a previous run; handed it back to the firmware",
                    output_id,
                )
            except (OSError, NotImplementedError):
                log.exception("could not hand %s back", output_id)
        state.clear()

    def _remember_outputs(self) -> None:
        """Keep the on-disk record of held outputs in step with reality."""

        held = {
            output_id: output.saved_state()
            for output_id, output in self.registry.controls.items()
            if output.acquired
        }
        if held != self._held:
            state.save(held)
            self._held = held

    def run_forever(self) -> None:
        """Drive the control loop until :meth:`stop` is called."""

        if self.engine is None:
            self.start()
        log.info("control loop running every %.1fs", self.config.settings.update_interval)
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                status = self.tick()
            except Exception:
                log.exception("control loop tick failed")
                status = None
            if status is not None:
                self._notify(status)
            interval = max(0.2, self.config.settings.update_interval)
            self._stop.wait(max(0.0, interval - (time.monotonic() - started)))

    def start_background(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, name="fancontrol", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def shutdown(self) -> None:
        """Stop the loop and give every fan back to the firmware."""

        self.stop()
        with self._lock:
            if self.engine is not None:
                self.engine.shutdown()
            self.registry.close()
            # Everything is back with the firmware, so there is nothing left
            # for a future run to recover.
            state.clear()
            self._held = {}
        log.info("daemon stopped, fan control handed back to the firmware")

    def tick(self) -> dict[str, Any]:
        with self._lock:
            if self.engine is None:
                self.start()
            assert self.engine is not None
            status = self.engine.tick().to_dict()
            self._remember_outputs()
            return status

    # ------------------------------------------------------------------
    # notifications

    def add_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._listeners.append(callback)

    def _notify(self, status: dict[str, Any]) -> None:
        for callback in list(self._listeners):
            try:
                callback(status)
            except Exception:
                log.exception("status listener failed")

    # ------------------------------------------------------------------
    # read-only API

    def get_inventory(self) -> dict[str, Any]:
        with self._lock:
            inventory = self.registry.inventory()
        inventory["config_path"] = str(self.config_path)
        return inventory

    def get_status(self) -> dict[str, Any]:
        with self._lock:
            if self.engine is None:
                return {}
            return self.engine.status.to_dict()

    def get_config(self) -> dict[str, Any]:
        with self._lock:
            return self.config.to_dict()

    # ------------------------------------------------------------------
    # mutating API

    def set_config(self, data: dict[str, Any], save: bool = True) -> dict[str, Any]:
        """Validate and apply a new configuration.

        A configuration that does not validate is rejected outright: applying
        half of it would leave some fans driven and others not.
        """

        try:
            new_config = Config.from_dict(data)
        except (ValueError, TypeError, KeyError) as exc:
            return fail(f"configuration could not be read: {exc}")

        problems = validate(new_config)
        if problems:
            return fail("configuration is not valid", problems=problems)

        with self._lock:
            self.config = new_config
            if self.engine is not None:
                self.engine.set_config(new_config)
            if save:
                try:
                    config_module.save(new_config, self.config_path)
                except OSError as exc:
                    return fail(f"configuration applied but not saved: {exc}")
        return ok()

    def save_config(self) -> dict[str, Any]:
        with self._lock:
            try:
                config_module.save(self.config, self.config_path)
            except OSError as exc:
                return fail(str(exc))
        return ok(path=str(self.config_path))

    def reload_config(self) -> dict[str, Any]:
        with self._lock:
            try:
                new_config = config_module.load(self.config_path)
            except ValueError as exc:
                return fail(str(exc))
            problems = validate(new_config)
            if problems:
                return fail("configuration on disk is not valid", problems=problems)
            self.config = new_config
            if self.engine is not None:
                self.engine.set_config(new_config)
        return ok()

    def rescan(self) -> dict[str, Any]:
        """Re-enumerate the hardware, e.g. after loading a kernel module."""

        with self._lock:
            if self.engine is not None:
                self.engine.shutdown()
            self.registry.discover()
            if self.engine is not None:
                self.engine.registry = self.registry
        return ok(**self.get_inventory())

    def set_override(self, control_id: str, percent: float | None) -> dict[str, Any]:
        """Drive a control by hand, or pass ``None`` to give it back to its curve."""

        with self._lock:
            if self.engine is None:
                return fail("daemon is not running yet")
            if self.config.control_by_id(control_id) is None:
                return fail(f"no control with id {control_id!r}")
            self.engine.set_override(control_id, percent)
        return ok()

    def set_control_enabled(self, enabled: bool) -> dict[str, Any]:
        """The master switch: stop driving anything, or start again."""

        with self._lock:
            self.config.settings.control_enabled = bool(enabled)
            try:
                config_module.save(self.config, self.config_path)
            except OSError as exc:
                log.warning("could not persist the master switch: %s", exc)
        return ok(control_enabled=bool(enabled))

    # ------------------------------------------------------------------
    # import

    def import_fancontrol(self, path: str = "", text: str = "") -> dict[str, Any]:
        """Read a Windows FanControl config and propose a mapping.

        Nothing is applied here — the caller gets the converted configuration
        plus the mapping report and decides what to do with the identifiers
        that could not be resolved automatically.
        """

        importer = FanControlImporter()
        try:
            result = importer.load(path) if path else importer.loads(text)
        except (OSError, ValueError) as exc:
            return fail(str(exc))

        with self._lock:
            mapper = HardwareMapper(self.registry)
            report = mapper.auto_map(result)

        return ok(
            config=result.config.to_dict(),
            mapping=report.to_dict(),
            warnings=result.warnings,
            summary={
                "curves": len(result.config.curves),
                "controls": len(result.config.controls),
                "mapped": len(report.applied),
                "needs_attention": len(report.pending) + len(report.unmatched),
            },
        )

    def apply_import(
        self, config_data: dict[str, Any], mapping: dict[str, str], merge: bool = False
    ) -> dict[str, Any]:
        """Apply an imported configuration once the user has confirmed the mapping."""

        try:
            imported = Config.from_dict(config_data)
        except (ValueError, TypeError, KeyError) as exc:
            return fail(f"imported configuration could not be read: {exc}")

        with self._lock:
            HardwareMapper(self.registry).apply(imported, mapping)

            # A control that cannot reach real hardware, or whose curve has no
            # temperature to read, is switched off rather than left pointing at
            # a dead identifier. The two are different problems, so say which.
            skipped: dict[str, str] = {}
            for control in imported.controls:
                if (
                    control.output_id.startswith("win:")
                    or control.output_id not in self.registry.controls
                ):
                    control.enabled = False
                    skipped[control.name] = "no fan output on this machine matches it"

            unresolved_curves = {
                curve.id
                for curve in imported.curves
                if getattr(curve, "sensor_id", "").startswith("win:")
            }
            for control in imported.controls:
                if control.curve_id in unresolved_curves and control.name not in skipped:
                    control.enabled = False
                    curve = next(c for c in imported.curves if c.id == control.curve_id)
                    skipped[control.name] = (
                        f"its curve {curve.name!r} has no temperature source yet"
                    )

            if merge:
                imported = self._merge(self.config, imported)

            problems = validate(imported)
            if problems:
                return fail("imported configuration is not valid", problems=problems)

        result = self.set_config(imported.to_dict(), save=True)
        if not result.get("ok"):
            return result
        return ok(
            skipped=[{"name": name, "reason": reason} for name, reason in sorted(skipped.items())]
        )

    @staticmethod
    def _merge(current: Config, imported: Config) -> Config:
        """Add the imported curves and controls to what is already configured."""

        merged = Config.from_dict(current.to_dict())
        existing_outputs = {c.output_id for c in merged.controls}
        merged.curves.extend(imported.curves)
        for control in imported.controls:
            if control.output_id in existing_outputs:
                # Replace the control that already drives this output.
                merged.controls = [c for c in merged.controls if c.output_id != control.output_id]
            merged.controls.append(control)
        merged.sensor_names.update(imported.sensor_names)
        return merged

    # ------------------------------------------------------------------
    # calibration

    def calibrate(self, control_id: str) -> dict[str, Any]:
        """Find out how a fan actually behaves: where it starts and stops.

        The control is stepped from full speed down to zero and back up while
        the tachometer is watched, which is the only reliable way to learn the
        minimum percentage at which a particular fan keeps turning.
        """

        with self._lock:
            control = self.config.control_by_id(control_id)
            if control is None:
                return fail(f"no control with id {control_id!r}")
            if not control.fan_sensor_id:
                return fail(
                    f"{control.name} has no fan tachometer assigned, so its speed "
                    "cannot be measured"
                )
            output = self.registry.controls.get(control.output_id)
            fan = self.registry.fans.get(control.fan_sensor_id)
            if output is None or fan is None:
                return fail("the hardware for this control is not available")

        # The control loop has to stop writing to this output for the duration,
        # otherwise it overwrites every step as soon as it is set.
        if self.engine is not None:
            self.engine.pause(control_id)

        samples: list[dict[str, float]] = []
        stop_percent: float | None = None
        start_percent: float | None = None

        try:
            output.acquire()
            # Ramp down: the lowest percentage at which the fan still turns.
            for percent in range(100, -1, -5):
                output.set_percent(float(percent))
                time.sleep(self.calibration_settle)
                rpm = fan.read() or 0.0
                samples.append({"percent": float(percent), "rpm": rpm})
                if rpm <= 0 and stop_percent is None:
                    stop_percent = float(percent)
                    break
            # Ramp up: the lowest percentage at which it starts from standstill.
            output.set_percent(0.0)
            time.sleep(self.calibration_settle * 1.5)
            for percent in range(0, 101, 5):
                output.set_percent(float(percent))
                time.sleep(self.calibration_settle)
                rpm = fan.read() or 0.0
                if rpm > 0:
                    start_percent = float(percent)
                    break
        except OSError as exc:
            return fail(f"calibration failed: {exc}")
        finally:
            with self._lock:
                if self.engine is not None:
                    self.engine.resume(control_id)

        # Keep the measurement on the control, the way an imported FanControl
        # calibration is kept, so the UI can show what this fan actually does.
        with self._lock:
            control = self.config.control_by_id(control_id)
            if control is not None:
                control.calibration = sorted(
                    ([s["percent"], s["rpm"]] for s in samples), key=lambda pair: pair[0]
                )

        result = {
            "control_id": control_id,
            "samples": samples,
            "stop_percent": stop_percent,
            "start_percent": start_percent,
            # Leave a little headroom above the measured stop point so the fan
            # does not sit right on the edge of stalling.
            "suggested_min_percent": (stop_percent + 10.0) if stop_percent is not None else None,
            "suggested_stop_percent": stop_percent,
            "suggested_start_percent": (start_percent + 5.0) if start_percent is not None else None,
        }
        self.calibration[control_id] = result
        return ok(**result)
