"""A fake hwmon tree for development and for trying the UI safely.

It writes a directory that looks exactly like ``/sys/class/hwmon`` and then
simulates a plausible thermal system: each PWM output drives a fan, each fan
cools a temperature zone, and the zones heat up according to a configurable
load. Point the daemon at it with::

    FANCONTROL_HWMON_ROOT=/tmp/fake-hwmon FANCONTROL_DISABLE=nvidia ...

Nothing here touches real hardware.
"""

from __future__ import annotations

import argparse
import math
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Zone:
    """One simulated temperature zone cooled by one simulated fan."""

    name: str
    ambient: float = 28.0
    temperature: float = 35.0
    #: Watts of heat the zone produces at 100% load.
    max_power: float = 120.0
    load: float = 0.25
    #: Degrees per second per watt, i.e. how fast the zone heats up.
    heat_rate: float = 0.02
    #: Cooling effectiveness at 0% and 100% fan speed.
    cool_min: float = 0.02
    cool_max: float = 0.20
    max_rpm: int = 1800
    min_start_percent: float = 12.0
    fan_percent: float = 0.0
    rpm: float = 0.0

    def step(self, dt: float, percent: float) -> None:
        self.fan_percent = percent
        # A real fan does not start below a few percent and spins down slowly.
        target_rpm = 0.0
        if percent >= self.min_start_percent:
            target_rpm = self.max_rpm * (percent / 100.0)
        self.rpm += (target_rpm - self.rpm) * min(1.0, dt * 3.0)
        if self.rpm < 60:
            self.rpm = 0.0

        heating = self.max_power * self.load * self.heat_rate
        effectiveness = self.cool_min + (self.cool_max - self.cool_min) * (
            self.rpm / self.max_rpm if self.max_rpm else 0.0
        )
        cooling = (self.temperature - self.ambient) * effectiveness
        self.temperature += (heating - cooling) * dt
        self.temperature = max(self.ambient, min(110.0, self.temperature))


@dataclass
class Chip:
    """A simulated hwmon chip directory."""

    dirname: str
    name: str
    zones: list[Zone] = field(default_factory=list)
    #: Extra temperature channels that are not tied to a fan.
    extra_temps: list[str] = field(default_factory=list)
    has_pwm: bool = True


def _write(path: Path, value: str) -> None:
    path.write_text(f"{value}\n")


class Simulator:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.chips: list[Chip] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def default_layout(self) -> None:
        """A layout close to a typical desktop: super-I/O, CPU, GPU, NVMe."""

        self.chips = [
            Chip(
                dirname="hwmon0",
                name="nct6798",
                zones=[
                    Zone("CPU fan", max_power=180, load=0.30, max_rpm=2200),
                    Zone("Front fans", max_power=90, load=0.20, max_rpm=1500, ambient=27),
                    Zone("Rear fan", max_power=60, load=0.18, max_rpm=1400, ambient=27),
                ],
                extra_temps=["SYSTIN", "AUXTIN0"],
            ),
            Chip(dirname="hwmon1", name="k10temp", extra_temps=["Tctl"], has_pwm=False),
            Chip(
                dirname="hwmon2",
                name="amdgpu",
                zones=[Zone("GPU fan", max_power=250, load=0.35, max_rpm=3000, ambient=30)],
                extra_temps=["junction", "mem"],
                has_pwm=True,
            ),
            Chip(dirname="hwmon3", name="nvme", extra_temps=["Composite"], has_pwm=False),
        ]

    def build(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        for chip in self.chips:
            chip_dir = self.root / chip.dirname
            chip_dir.mkdir()
            _write(chip_dir / "name", chip.name)
            index = 1
            for zone in chip.zones:
                _write(chip_dir / f"temp{index}_input", str(int(zone.temperature * 1000)))
                _write(chip_dir / f"temp{index}_label", f"{zone.name} zone")
                _write(chip_dir / f"fan{index}_input", "0")
                _write(chip_dir / f"fan{index}_label", zone.name)
                if chip.has_pwm:
                    _write(chip_dir / f"pwm{index}", "128")
                    _write(chip_dir / f"pwm{index}_enable", "2")
                index += 1
            for label in chip.extra_temps:
                _write(chip_dir / f"temp{index}_input", "40000")
                _write(chip_dir / f"temp{index}_label", label)
                index += 1

    def _read_pwm(self, chip_dir: Path, index: int) -> float:
        try:
            enable = int((chip_dir / f"pwm{index}_enable").read_text().strip())
        except (OSError, ValueError):
            enable = 1
        try:
            raw = int((chip_dir / f"pwm{index}").read_text().strip())
        except (OSError, ValueError):
            raw = 128
        if enable >= 2:
            # Firmware automatic mode: pretend the board runs a gentle curve.
            return 40.0
        if enable == 0:
            return 100.0
        return raw / 255.0 * 100.0

    def step(self, dt: float) -> None:
        for chip in self.chips:
            chip_dir = self.root / chip.dirname
            for index, zone in enumerate(chip.zones, start=1):
                percent = self._read_pwm(chip_dir, index) if chip.has_pwm else 40.0
                zone.step(dt, percent)
                _write(chip_dir / f"temp{index}_input", str(int(zone.temperature * 1000)))
                _write(chip_dir / f"fan{index}_input", str(int(zone.rpm)))
            # Idle channels wander a little so the UI does not look frozen.
            base = self.root / chip.dirname
            for offset, _label in enumerate(chip.extra_temps):
                index = len(chip.zones) + offset + 1
                wobble = 40.0 + 3.0 * math.sin(time.time() / 7.0 + offset)
                _write(base / f"temp{index}_input", str(int(wobble * 1000)))

    def set_load(self, load: float) -> None:
        for chip in self.chips:
            for zone in chip.zones:
                zone.load = load

    def run(self, interval: float = 0.5) -> None:
        last = time.monotonic()
        while not self._stop.is_set():
            time.sleep(interval)
            now = time.monotonic()
            self.step(now - last)
            last = now

    def start(self, interval: float = 0.5) -> None:
        self._thread = threading.Thread(target=self.run, args=(interval,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fancontrol-sim",
        description="Run a simulated hwmon tree so the daemon and UI can be "
        "tried without touching real hardware.",
    )
    parser.add_argument(
        "--root",
        default="/tmp/fancontrol-fake-hwmon",
        help="directory to build the fake hwmon tree in",
    )
    parser.add_argument("--load", type=float, default=0.3, help="simulated system load, 0..1")
    parser.add_argument("--interval", type=float, default=0.5, help="simulation tick in seconds")
    args = parser.parse_args(argv)

    sim = Simulator(Path(args.root))
    sim.default_layout()
    sim.set_load(args.load)
    sim.build()
    print(f"Simulated hwmon tree ready at {args.root}")
    print("Run the daemon against it with:")
    print(f"  FANCONTROL_HWMON_ROOT={args.root} FANCONTROL_DISABLE=nvidia fancontrold --session")
    try:
        sim.run(args.interval)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
