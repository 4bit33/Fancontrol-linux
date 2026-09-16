"""A stand-in for the NVIDIA backend, so the mapping can be tested without a GPU."""

from __future__ import annotations

from dataclasses import dataclass, field

from fancontrol.hw.base import DeviceInfo, FanSensor, PwmOutput, TempSensor

UUID = "GPU-11112222-3333-4444-5555-666677778888"


def _device(name: str) -> DeviceInfo:
    return DeviceInfo(
        key=UUID, chip="nvidia", label=name, backend="nvidia",
        details={"uuid": UUID, "index": "0"},
    )


@dataclass
class FakeTemp(TempSensor):
    value: float = 45.0

    def read(self) -> float | None:
        return self.value


@dataclass
class FakeFan(FanSensor):
    value: float = 1200.0

    def read(self) -> float | None:
        return self.value


@dataclass
class FakePwm(PwmOutput):
    percent: float = 40.0
    _acquired: bool = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    def read_percent(self) -> float | None:
        return self.percent

    def set_percent(self, percent: float) -> None:
        self.percent = percent
        self._acquired = True

    def acquire(self) -> None:
        self._acquired = True

    def release(self) -> None:
        self._acquired = False


class FakeNvidiaBackend:
    """One card with two fans, like the RTX 3070 in the sample configuration."""

    name = "nvidia"

    def __init__(self, card: str = "NVIDIA GeForce RTX 3070", fans: int = 2) -> None:
        self.card = card
        self.fans = fans

    def discover(self):
        device = _device(self.card)
        temps = [FakeTemp(id=f"nvidia:{UUID}:temp", name=f"{self.card} GPU", device=device)]
        fan_sensors = []
        controls = []
        for index in range(self.fans):
            fan_sensors.append(
                FakeFan(id=f"nvidia:{UUID}:fan{index}",
                        name=f"{self.card} fan #{index + 1}", device=device)
            )
            controls.append(
                FakePwm(id=f"nvidia:{UUID}:pwm{index}",
                        name=f"{self.card} fan #{index + 1}", device=device, can_stop=False)
            )
        return temps, fan_sensors, controls

    def close(self) -> None:
        pass
