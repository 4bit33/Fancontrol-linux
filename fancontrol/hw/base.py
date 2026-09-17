"""Hardware abstraction.

A backend discovers three kinds of objects:

``TempSensor``  something with a temperature in degrees Celsius
``FanSensor``   something with an RPM reading
``PwmOutput``   something whose speed we can set, expressed in percent

Identifiers are strings that must stay stable across reboots, because they are
what a saved configuration refers to. Each backend documents its own scheme;
the common shape is ``<backend>:<device>:<channel>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol


def sanitize(part: str) -> str:
    """Make a string safe to embed in an identifier."""

    return re.sub(r"[^A-Za-z0-9_.+-]", "_", part.strip()) or "unknown"


@dataclass
class DeviceInfo:
    """The physical device a sensor or output belongs to."""

    #: Stable device key used inside identifiers.
    key: str
    #: Chip name as reported by the kernel, e.g. ``nct6798``.
    chip: str
    #: Human readable label shown in the UI.
    label: str
    #: Backend name, e.g. ``hwmon`` or ``nvidia``.
    backend: str
    #: Free-form extra information (bus address, driver, ...).
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "chip": self.chip,
            "label": self.label,
            "backend": self.backend,
            "details": dict(self.details),
        }


@dataclass
class TempSensor:
    id: str
    name: str
    device: DeviceInfo

    def read(self) -> float | None:
        """Temperature in degrees Celsius, or ``None`` if unreadable."""
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "device": self.device.to_dict(),
            "kind": "temperature",
        }


@dataclass
class FanSensor:
    id: str
    name: str
    device: DeviceInfo

    def read(self) -> float | None:
        """Fan speed in RPM, or ``None`` if unreadable."""
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "device": self.device.to_dict(),
            "kind": "fan",
        }


@dataclass
class PwmOutput:
    id: str
    name: str
    device: DeviceInfo
    #: True when the hardware can actually stop the fan at 0%.
    can_stop: bool = True

    def read_percent(self) -> float | None:
        """Current duty cycle in percent, or ``None`` if unreadable."""
        raise NotImplementedError

    def set_percent(self, percent: float) -> None:
        """Write a duty cycle. Takes manual control if it does not have it."""
        raise NotImplementedError

    def acquire(self) -> None:
        """Remember the firmware's settings and switch to manual control."""
        raise NotImplementedError

    def release(self) -> None:
        """Hand the output back to the firmware."""
        raise NotImplementedError

    @property
    def acquired(self) -> bool:
        raise NotImplementedError

    def saved_state(self) -> dict[str, Any]:
        """What ``release`` would need to put this output back as it was.

        Written to a file while the output is held, so that a daemon which was
        killed rather than stopped can still hand the fan back next time it
        starts.
        """
        return {}

    def adopt(self, state: dict[str, Any]) -> None:
        """Take ownership of an output a previous run left behind.

        After this, ``release`` restores ``state`` exactly as the earlier run
        would have.
        """
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "device": self.device.to_dict(),
            "kind": "control",
            "can_stop": self.can_stop,
        }


class Backend(Protocol):
    """A source of sensors and controls."""

    name: str

    def discover(self) -> tuple[list[TempSensor], list[FanSensor], list[PwmOutput]]:
        ...

    def close(self) -> None:
        ...
