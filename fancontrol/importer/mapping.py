"""Maps Windows hardware identifiers onto the Linux hardware we found.

FanControl stores LibreHardwareMonitor identifiers such as
``/lpc/nct6798d/temperature/2`` or ``/nvidiagpu/0/control/0``. None of those
mean anything to the kernel, so after importing a configuration every
identifier has to be pointed at a real hwmon channel or NVML fan.

Most of it can be worked out automatically: the chip name in the identifier is
usually the same chip the kernel reports, and the channel numbering differs by
a constant. What cannot be worked out is reported with candidates so the user
picks from a short list instead of a long one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

from ..core.models import Config
from ..hw.registry import HardwareRegistry
from .fancontrol_json import WIN_PREFIX, ImportResult

log = logging.getLogger(__name__)

#: Score at or above which a suggestion is applied without asking.
AUTO_APPLY_SCORE = 0.70


@dataclass(frozen=True)
class WinIdentifier:
    """A parsed LibreHardwareMonitor identifier."""

    raw: str
    hardware: str = ""
    device: str = ""
    kind: str = ""
    index: int = -1

    @property
    def is_valid(self) -> bool:
        return bool(self.hardware and self.kind)


def parse_identifier(raw: str) -> WinIdentifier:
    """Split ``/lpc/nct6798d/temperature/2`` into its parts."""

    text = raw.strip()
    if text.startswith(WIN_PREFIX):
        text = text[len(WIN_PREFIX):]
    parts = [p for p in text.strip("/").split("/") if p]
    if len(parts) < 3:
        return WinIdentifier(raw=raw)

    hardware = parts[0].lower()
    # /lpc/nct6798d/temperature/2  ->  device is the chip name
    # /amdcpu/0/temperature/2      ->  device is the adapter index
    device = parts[1].lower()
    kind = parts[-2].lower()
    try:
        index = int(parts[-1])
    except ValueError:
        index = -1
    return WinIdentifier(raw=raw, hardware=hardware, device=device, kind=kind, index=index)


#: Windows hardware prefix -> the Linux hwmon chip names that can serve it, and
#: how the channel numbering lines up.
CHIP_FAMILIES: dict[str, tuple[str, ...]] = {
    "amdcpu": ("k10temp", "zenpower", "zenpower3"),
    "intelcpu": ("coretemp",),
    "cpu": ("k10temp", "coretemp", "zenpower"),
    "amdgpu": ("amdgpu",),
    "atigpu": ("amdgpu",),
    "gpu-amd": ("amdgpu",),
    "nvidiagpu": ("nvidia",),
    "gpu-nvidia": ("nvidia",),
    "nvidia": ("nvidia",),
    "gpu-intel": ("i915", "xe"),
    "nvme": ("nvme",),
    "storage": ("nvme", "drivetemp"),
    "hdd": ("drivetemp", "nvme"),
    "mainboard": (),
    "lpc": (),
    "ram": (),
    "psu": (),
}

#: Labels that identify the package/overall temperature of a CPU, best match
#: first. LibreHardwareMonitor reports one "Core (Tctl/Tdie)" style sensor and
#: the kernel usually labels the same thing.
CPU_PACKAGE_LABELS = ("tctl", "tdie", "package id 0", "package", "cpu")
GPU_CORE_LABELS = ("edge", "gpu", "temp1")


@dataclass
class Suggestion:
    target_id: str
    score: float
    reason: str


@dataclass
class MappingReport:
    """What the automatic mapping managed to do, and what is left."""

    applied: dict[str, str] = field(default_factory=dict)
    #: Identifiers with no confident answer, each with its candidate list.
    pending: dict[str, list[Suggestion]] = field(default_factory=dict)
    #: Identifiers with no candidates at all.
    unmatched: list[str] = field(default_factory=list)

    @property
    def fully_mapped(self) -> bool:
        return not self.pending and not self.unmatched

    def to_dict(self) -> dict:
        return {
            "applied": dict(self.applied),
            "pending": {
                key: [{"target_id": s.target_id, "score": s.score, "reason": s.reason} for s in value]
                for key, value in self.pending.items()
            },
            "unmatched": list(self.unmatched),
        }


def _channel_index(sensor_id: str) -> int:
    """The trailing number of a Linux channel, e.g. ``temp3`` -> 3."""

    match = re.search(r"(\d+)$", sensor_id.rsplit(":", 1)[-1])
    return int(match.group(1)) if match else -1


def _chip_of(sensor) -> str:
    return sensor.device.chip.lower()


class HardwareMapper:
    """Suggests Linux targets for Windows identifiers."""

    def __init__(self, registry: HardwareRegistry) -> None:
        self.registry = registry

    # -- public -------------------------------------------------------

    def suggest(self, identifier: str, kind: str) -> list[Suggestion]:
        """Rank Linux candidates for one identifier.

        ``kind`` is ``"temperature"``, ``"fan"`` or ``"control"``.
        """

        parsed = parse_identifier(identifier)
        pool = self._pool(kind)
        if not parsed.is_valid:
            return []

        suggestions: list[Suggestion] = []
        for sensor_id, sensor in pool.items():
            score, reason = self._score(parsed, kind, sensor_id, sensor)
            if score > 0:
                suggestions.append(Suggestion(sensor_id, round(score, 3), reason))
        suggestions.sort(key=lambda s: (-s.score, s.target_id))
        return suggestions[:8]

    def auto_map(self, result: ImportResult) -> MappingReport:
        """Resolve as many identifiers of an import as possible."""

        report = MappingReport()
        for win_id, identifier in sorted(result.unmapped_sensors.items()):
            self._resolve(win_id, identifier, "temperature", report)
        for win_id, identifier in sorted(result.unmapped_controls.items()):
            self._resolve(win_id, identifier, "control", report)
        return report

    def apply(self, config: Config, mapping: dict[str, str]) -> None:
        """Rewrite every ``win:`` id in ``config`` using ``mapping``."""

        for curve in config.curves:
            sensor_id = getattr(curve, "sensor_id", None)
            if sensor_id and sensor_id in mapping:
                curve.sensor_id = mapping[sensor_id]
        for control in config.controls:
            if control.output_id in mapping:
                control.output_id = mapping[control.output_id]
                control.fan_sensor_id = control.fan_sensor_id or self._paired_fan(control.output_id)
            if control.fan_sensor_id in mapping:
                control.fan_sensor_id = mapping[control.fan_sensor_id]
        for win_id, target in mapping.items():
            label = config.sensor_names.pop(win_id, None)
            if label:
                config.sensor_names[target] = label

    # -- internals ----------------------------------------------------

    def _pool(self, kind: str) -> dict:
        if kind == "temperature":
            return self.registry.temps
        if kind == "fan":
            return self.registry.fans
        return self.registry.controls

    def _resolve(self, win_id: str, identifier: str, kind: str, report: MappingReport) -> None:
        suggestions = self.suggest(identifier, kind)
        if not suggestions:
            report.unmatched.append(win_id)
            return
        best = suggestions[0]
        runner_up = suggestions[1].score if len(suggestions) > 1 else 0.0
        # Only auto-apply when the winner is both good and clearly ahead, so a
        # wrong guess never silently drives the wrong fan.
        if best.score >= AUTO_APPLY_SCORE and best.score - runner_up >= 0.1:
            report.applied[win_id] = best.target_id
        else:
            report.pending[win_id] = suggestions

    def _paired_fan(self, output_id: str) -> str:
        channel = output_id.rsplit(":", 1)[-1]
        if not channel.startswith("pwm"):
            return ""
        candidate = f"{output_id.rsplit(':', 1)[0]}:fan{channel[3:]}"
        return candidate if candidate in self.registry.fans else ""

    def _score(self, parsed: WinIdentifier, kind: str, sensor_id: str, sensor) -> tuple[float, str]:
        chip = _chip_of(sensor)
        families = CHIP_FAMILIES.get(parsed.hardware)

        if parsed.hardware == "lpc":
            return self._score_lpc(parsed, kind, sensor_id, sensor, chip)

        if families is None:
            return 0.0, ""
        if not families:
            # Known-but-unmappable hardware class (mainboard, ram, psu).
            return 0.0, ""

        if chip not in families:
            return 0.0, ""

        score = 0.55
        reason = f"chip family {parsed.hardware} matches {chip}"

        if parsed.hardware in {"nvidiagpu", "gpu-nvidia", "nvidia"}:
            # There is normally exactly one NVIDIA fan/temperature per GPU, and
            # the adapter index in the identifier orders the cards.
            score = 0.85
            reason = "NVIDIA GPU reported by NVML"
            if kind == "temperature" and sensor_id.endswith(":temp"):
                score = 0.92
            if kind == "control" and sensor_id.endswith(f":pwm{max(parsed.index, 0)}"):
                score = 0.92
            return score, reason

        if kind == "temperature":
            label = sensor.name.lower()
            preferred = CPU_PACKAGE_LABELS if "cpu" in parsed.hardware else GPU_CORE_LABELS
            for position, needle in enumerate(preferred):
                if needle in label:
                    score = max(score, 0.80 - position * 0.03)
                    reason = f"{chip} channel labelled {sensor.name!r}"
                    break
            else:
                # Fall back to lining the channel numbers up (LHM counts from
                # zero, hwmon from one).
                if _channel_index(sensor_id) == parsed.index + 1:
                    score = max(score, 0.62)
                    reason = f"{chip}, channel number lines up"
        elif _channel_index(sensor_id) == parsed.index + 1:
            score = max(score, 0.72)
            reason = f"{chip}, channel number lines up"

        return score, reason

    def _score_lpc(
        self, parsed: WinIdentifier, kind: str, sensor_id: str, sensor, chip: str
    ) -> tuple[float, str]:
        """Super-I/O chips: the identifier names the chip directly."""

        win_chip = parsed.device  # e.g. "nct6798d"
        if not win_chip:
            return 0.0, ""

        normalised_win = re.sub(r"[^a-z0-9]", "", win_chip)
        normalised_linux = re.sub(r"[^a-z0-9]", "", chip)
        if not normalised_linux:
            return 0.0, ""

        # Windows says "nct6798d", the kernel says "nct6798"; Windows says
        # "it8688e", the kernel says "it8688". Longest common prefix decides.
        common = 0
        for a, b in zip(normalised_win, normalised_linux):
            if a != b:
                break
            common += 1
        if common < 5:
            return 0.0, ""

        ratio = common / max(len(normalised_win), len(normalised_linux))
        score = 0.55 + 0.25 * ratio
        reason = f"super-I/O chip {win_chip} matches {chip}"

        if _channel_index(sensor_id) == parsed.index + 1:
            score += 0.15
            reason += ", channel number lines up"
        elif _channel_index(sensor_id) == parsed.index:
            score += 0.05
            reason += ", channel number is close"

        return min(score, 0.97), reason
