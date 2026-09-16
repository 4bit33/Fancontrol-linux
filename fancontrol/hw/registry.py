"""Aggregates all hardware backends behind one lookup table."""

from __future__ import annotations

import logging
import os

from .base import Backend, FanSensor, PwmOutput, TempSensor
from .hwmon import HwmonBackend

log = logging.getLogger(__name__)


def default_backends() -> list[Backend]:
    """Build the backend list, honouring the ``FANCONTROL_DISABLE`` switch.

    ``FANCONTROL_DISABLE=nvidia`` (comma separated) skips a backend entirely,
    which is the escape hatch when a driver misbehaves.
    """

    disabled = {
        part.strip().lower()
        for part in os.environ.get("FANCONTROL_DISABLE", "").split(",")
        if part.strip()
    }
    backends: list[Backend] = []
    if "hwmon" not in disabled:
        backends.append(HwmonBackend())
    if "nvidia" not in disabled:
        # Imported lazily so that a broken driver cannot stop the daemon from
        # starting with hwmon-only support.
        try:
            from .nvidia import NvidiaBackend

            backends.append(NvidiaBackend())
        except Exception:
            log.exception("NVIDIA backend unavailable")
    return backends


class HardwareRegistry:
    """Owns the backends and indexes everything they discovered."""

    def __init__(self, backends: list[Backend] | None = None) -> None:
        self.backends = backends if backends is not None else default_backends()
        self.temps: dict[str, TempSensor] = {}
        self.fans: dict[str, FanSensor] = {}
        self.controls: dict[str, PwmOutput] = {}

    def discover(self) -> None:
        self.temps.clear()
        self.fans.clear()
        self.controls.clear()
        for backend in self.backends:
            try:
                temps, fans, controls = backend.discover()
            except Exception:
                log.exception("backend %s failed to enumerate", getattr(backend, "name", backend))
                continue
            for sensor in temps:
                self.temps[sensor.id] = sensor
            for sensor in fans:
                self.fans[sensor.id] = sensor
            for control in controls:
                self.controls[control.id] = control
        log.info(
            "discovered %d temperature sensors, %d fan sensors, %d controls",
            len(self.temps),
            len(self.fans),
            len(self.controls),
        )

    def read_temperatures(self) -> dict[str, float | None]:
        return {sensor_id: sensor.read() for sensor_id, sensor in self.temps.items()}

    def read_fans(self) -> dict[str, float | None]:
        return {sensor_id: sensor.read() for sensor_id, sensor in self.fans.items()}

    def read_controls(self) -> dict[str, float | None]:
        return {control_id: control.read_percent() for control_id, control in self.controls.items()}

    def inventory(self) -> dict[str, list[dict]]:
        """A JSON-friendly description of everything that was found."""

        return {
            "temperatures": [s.to_dict() for s in self.temps.values()],
            "fans": [s.to_dict() for s in self.fans.values()],
            "controls": [c.to_dict() for c in self.controls.values()],
        }

    def release_all(self) -> None:
        for control in self.controls.values():
            try:
                control.release()
            except Exception:
                log.exception("failed to release %s", control.id)

    def close(self) -> None:
        for backend in self.backends:
            try:
                backend.close()
            except Exception:
                log.exception("failed to close backend %s", getattr(backend, "name", backend))
