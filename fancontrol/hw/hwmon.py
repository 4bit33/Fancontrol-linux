"""Linux ``hwmon`` backend.

Reads and writes ``/sys/class/hwmon``. This covers motherboard super-I/O chips
(Nuvoton, ITE, ...), ``k10temp``/``coretemp``, ``amdgpu``, NVMe drives and
anything else that exposes the standard sysfs interface.

Identifier scheme: ``hwmon:<device-key>:<channel>``, for example
``hwmon:nct6798-platform-nct6775.656:temp2``. The device key is built from the
chip name and the stable device directory name rather than the ``hwmonN``
number, because that number changes between boots.

Set ``FANCONTROL_HWMON_ROOT`` to point the backend at a directory tree other
than ``/sys/class/hwmon``; the test-suite and the bundled simulator use it.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .base import DeviceInfo, FanSensor, PwmOutput, TempSensor, sanitize

log = logging.getLogger(__name__)

DEFAULT_ROOT = "/sys/class/hwmon"

#: ``pwmN_enable`` values. 1 is manual on every driver that implements the
#: interface; 2 and above are the firmware's own automatic modes.
ENABLE_MANUAL = 1
ENABLE_AUTOMATIC = 2

PWM_MAX_RAW = 255


def hwmon_root() -> Path:
    return Path(os.environ.get("FANCONTROL_HWMON_ROOT", DEFAULT_ROOT))


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace").strip()
    except (OSError, ValueError):
        return None


def _read_int(path: Path) -> int | None:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _write_int(path: Path, value: int) -> bool:
    try:
        path.write_text(f"{value}\n")
        return True
    except OSError as exc:
        log.warning("cannot write %s = %s: %s", path, value, exc)
        return False


def _device_key(chip_dir: Path, chip_name: str) -> str:
    """Build an identifier component that survives a reboot.

    ``/sys/class/hwmon/hwmon3`` is a symlink into ``/sys/devices/...``; the
    directory two levels up from the resolved path names the actual device
    (a platform device such as ``nct6775.656`` or a PCI address such as
    ``0000:2f:00.0``).
    """

    parts = [sanitize(chip_name)]
    try:
        resolved = chip_dir.resolve()
        # .../<device>/hwmon/hwmonN  ->  <device>
        candidate = resolved.parent.parent
        if candidate.name and candidate.name != "hwmon":
            parts.append(sanitize(candidate.name))
        subsystem = candidate / "subsystem"
        if subsystem.exists():
            parts.insert(1, sanitize(os.path.basename(os.path.realpath(subsystem))))
    except OSError:
        pass
    return "-".join(parts)


def _label(chip_dir: Path, channel: str, fallback: str) -> str:
    label = _read_text(chip_dir / f"{channel}_label")
    return label or fallback


@dataclass
class HwmonTempSensor(TempSensor):
    path: Path = field(default_factory=Path)

    def read(self) -> float | None:
        raw = _read_int(self.path)
        if raw is None:
            return None
        value = raw / 1000.0
        # Disconnected thermistors report obviously bogus values; treat them as
        # unavailable so a curve falls back to its failsafe instead of idling.
        if value <= -50.0 or value >= 200.0:
            return None
        return value


@dataclass
class HwmonFanSensor(FanSensor):
    path: Path = field(default_factory=Path)

    def read(self) -> float | None:
        raw = _read_int(self.path)
        if raw is None:
            return None
        return float(raw)


@dataclass
class HwmonPwmOutput(PwmOutput):
    path: Path = field(default_factory=Path)
    enable_path: Path | None = None
    #: ``pwmN_enable`` value the firmware had before we took over.
    _saved_enable: int | None = None
    _saved_value: int | None = None
    _acquired: bool = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    @property
    def writable(self) -> bool:
        return os.access(self.path, os.W_OK)

    def read_percent(self) -> float | None:
        raw = _read_int(self.path)
        if raw is None:
            return None
        return raw / PWM_MAX_RAW * 100.0

    def acquire(self) -> None:
        if self._acquired:
            return
        self._saved_value = _read_int(self.path)
        if self.enable_path is not None and self.enable_path.exists():
            self._saved_enable = _read_int(self.enable_path)
            if self._saved_enable != ENABLE_MANUAL:
                if not _write_int(self.enable_path, ENABLE_MANUAL):
                    raise OSError(f"cannot switch {self.enable_path} to manual mode")
        self._acquired = True
        log.info("took manual control of %s (was enable=%s)", self.id, self._saved_enable)

    def release(self) -> None:
        if not self._acquired:
            return
        if self.enable_path is not None and self.enable_path.exists():
            # Fall back to the firmware's automatic mode when we never managed
            # to read the original value.
            target = self._saved_enable if self._saved_enable is not None else ENABLE_AUTOMATIC
            if target == ENABLE_MANUAL and self._saved_value is not None:
                # It was already in manual mode; put the old duty cycle back.
                _write_int(self.path, self._saved_value)
            _write_int(self.enable_path, target)
        elif self._saved_value is not None:
            _write_int(self.path, self._saved_value)
        self._acquired = False
        log.info("released %s", self.id)

    def set_percent(self, percent: float) -> None:
        if not self._acquired:
            self.acquire()
        raw = int(round(max(0.0, min(100.0, percent)) / 100.0 * PWM_MAX_RAW))
        if not _write_int(self.path, raw):
            raise OSError(f"cannot write {self.path}")


class HwmonBackend:
    """Discovers every hwmon chip below :func:`hwmon_root`."""

    name = "hwmon"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or hwmon_root()
        self._outputs: list[HwmonPwmOutput] = []

    def discover(self) -> tuple[list[TempSensor], list[FanSensor], list[PwmOutput]]:
        temps: list[TempSensor] = []
        fans: list[FanSensor] = []
        pwms: list[PwmOutput] = []

        if not self.root.exists():
            log.warning("%s does not exist; no hwmon devices", self.root)
            return temps, fans, pwms

        for chip_dir in sorted(self.root.iterdir(), key=lambda p: p.name):
            chip_name = _read_text(chip_dir / "name") or chip_dir.name
            key = _device_key(chip_dir, chip_name)
            device = DeviceInfo(
                key=key,
                chip=chip_name,
                label=chip_name,
                backend=self.name,
                details={"path": str(chip_dir)},
            )
            temps.extend(self._temps(chip_dir, device))
            fans.extend(self._fans(chip_dir, device))
            pwms.extend(self._pwms(chip_dir, device))

        self._outputs = [p for p in pwms if isinstance(p, HwmonPwmOutput)]
        return temps, fans, pwms

    def _temps(self, chip_dir: Path, device: DeviceInfo) -> list[TempSensor]:
        result: list[TempSensor] = []
        for path in sorted(chip_dir.glob("temp*_input"), key=_channel_sort_key):
            channel = path.name[: -len("_input")]
            name = _label(chip_dir, channel, f"{device.label} {channel}")
            result.append(
                HwmonTempSensor(
                    id=f"hwmon:{device.key}:{channel}",
                    name=name,
                    device=device,
                    path=path,
                )
            )
        return result

    def _fans(self, chip_dir: Path, device: DeviceInfo) -> list[FanSensor]:
        result: list[FanSensor] = []
        for path in sorted(chip_dir.glob("fan*_input"), key=_channel_sort_key):
            channel = path.name[: -len("_input")]
            name = _label(chip_dir, channel, f"{device.label} {channel}")
            result.append(
                HwmonFanSensor(
                    id=f"hwmon:{device.key}:{channel}",
                    name=name,
                    device=device,
                    path=path,
                )
            )
        return result

    def _pwms(self, chip_dir: Path, device: DeviceInfo) -> list[PwmOutput]:
        result: list[PwmOutput] = []
        for path in sorted(chip_dir.glob("pwm[0-9]*"), key=_channel_sort_key):
            if not re.fullmatch(r"pwm\d+", path.name):
                continue
            channel = path.name
            enable_path = chip_dir / f"{channel}_enable"
            name = _label(chip_dir, channel, f"{device.label} {channel}")
            result.append(
                HwmonPwmOutput(
                    id=f"hwmon:{device.key}:{channel}",
                    name=name,
                    device=device,
                    path=path,
                    enable_path=enable_path if enable_path.exists() else None,
                    can_stop=True,
                )
            )
        return result

    def close(self) -> None:
        for output in self._outputs:
            try:
                output.release()
            except OSError:
                log.exception("failed to release %s", output.id)


def _channel_sort_key(path: Path) -> tuple[str, int]:
    match = re.match(r"([a-z]+)(\d+)", path.name)
    if not match:
        return (path.name, 0)
    return (match.group(1), int(match.group(2)))
