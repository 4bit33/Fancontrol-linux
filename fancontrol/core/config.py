"""Loading, saving and bootstrapping the configuration file."""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from ..hw.registry import HardwareRegistry
from .models import Config, Control, CurvePoint, GraphCurve, new_id

log = logging.getLogger(__name__)

SYSTEM_CONFIG = Path("/etc/fancontrol-linux/config.json")
APP_DIR = "fancontrol-linux"


def user_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / APP_DIR / "config.json"


def default_config_path(session: bool = False) -> Path:
    """Where the daemon reads its configuration from.

    ``FANCONTROL_CONFIG`` overrides everything; otherwise the system daemon
    uses ``/etc`` and a session-mode daemon uses the user's config directory.
    """

    override = os.environ.get("FANCONTROL_CONFIG")
    if override:
        return Path(override)
    if session or os.geteuid() != 0:
        return user_config_path()
    return SYSTEM_CONFIG


def load(path: Path) -> Config:
    """Read a configuration file, returning an empty one when it is missing."""

    if not path.exists():
        log.info("no configuration at %s, starting empty", path)
        return Config()
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read configuration {path}: {exc}") from exc
    return Config.from_dict(raw)


def save(config: Config, path: Path) -> None:
    """Write the configuration atomically, keeping one backup copy.

    A half-written config would leave the machine without fan control after a
    reboot, so the new file is written beside the old one and renamed into
    place only once it is complete and on disk.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            log.warning("could not back up %s", path)

    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(config.to_dict(), indent=2, ensure_ascii=False)
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    log.info("configuration written to %s", path)


#: A conservative starting curve: quiet until the mid sixties, full speed by 85.
DEFAULT_POINTS = [
    (30.0, 20.0),
    (45.0, 25.0),
    (55.0, 35.0),
    (65.0, 55.0),
    (75.0, 80.0),
    (85.0, 100.0),
]


def _best_temperature_sensor(registry: HardwareRegistry) -> str:
    """Pick a sane default temperature source: the CPU package if we find it."""

    preferred_chips = ("k10temp", "coretemp", "zenpower")
    for chip in preferred_chips:
        for sensor_id, sensor in registry.temps.items():
            if sensor.device.chip == chip:
                return sensor_id
    for sensor_id in registry.temps:
        return sensor_id
    return ""


def _pair_fan_sensor(registry: HardwareRegistry, output_id: str) -> str:
    """Guess which tachometer belongs to a PWM output.

    On every hwmon chip ``pwm2`` and ``fan2`` are the same header, so matching
    the channel number on the same device is right far more often than not.
    """

    control = registry.controls.get(output_id)
    if control is None:
        return ""
    channel = output_id.rsplit(":", 1)[-1]
    if not channel.startswith("pwm"):
        return ""
    candidate = f"{output_id.rsplit(':', 1)[0]}:fan{channel[3:]}"
    if candidate in registry.fans:
        return candidate
    return ""


def bootstrap(registry: HardwareRegistry) -> Config:
    """Build a usable configuration for freshly discovered hardware.

    Every writable PWM output gets its own graph curve driven by the CPU
    temperature, and starts out disabled so that nothing changes speed until
    the user has looked at it.
    """

    config = Config()
    sensor_id = _best_temperature_sensor(registry)

    for output_id, output in registry.controls.items():
        curve = GraphCurve(
            id=new_id("curve"),
            name=f"{output.name} curve",
            sensor_id=sensor_id,
            points=[CurvePoint(t, p) for t, p in DEFAULT_POINTS],
            hysteresis_up=0.0,
            hysteresis_down=2.0,
            response_time_up=1.0,
            response_time_down=4.0,
        )
        config.curves.append(curve)
        config.controls.append(
            Control(
                id=new_id("control"),
                name=output.name,
                output_id=output_id,
                curve_id=curve.id,
                enabled=False,
                min_percent=20.0,
                max_percent=100.0,
                start_percent=40.0,
                step_up=20.0,
                step_down=8.0,
                allow_stop=False,
                fan_sensor_id=_pair_fan_sensor(registry, output_id),
            )
        )

    log.info("bootstrapped configuration with %d controls", len(config.controls))
    return config
