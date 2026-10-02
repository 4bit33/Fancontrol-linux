"""Friendly display names for cryptic sensor labels.

The inventory keeps the kernel's names: the Windows importer matches on
them, so renaming there would change what an import finds. The window,
instead, shows a friendly name wherever the label is a known riddle - and a
manual rename always wins over it.
"""

from __future__ import annotations

import re

#: (chip, label) the kernel hands out -> what a person reads.
FRIENDLY = {
    ("k10temp", "Tctl"): "CPU",
    ("coretemp", "Package id 0"): "CPU",
    ("amdgpu", "edge"): "GPU",
    ("amdgpu", "junction"): "GPU hotspot",
    ("nvme", "Composite"): "SSD",
}

_FALLBACK_FAN = re.compile(r"^hp fan(\d+)$")
_FALLBACK_BOARD = re.compile(r"^acpitz temp(\d+)$")
_SSD_SENSOR = re.compile(r"^Sensor (\d+)$")


def friendly_name(chip: str, name: str) -> str | None:
    """A readable name for ``name`` on ``chip``, or None to keep it."""

    if (chip, name) in FRIENDLY:
        return FRIENDLY[chip, name]
    match = _FALLBACK_FAN.match(name)
    if chip == "hp" and match:
        return f"Fan {match.group(1)}"
    match = _FALLBACK_BOARD.match(name)
    if chip == "acpitz" and match:
        number = int(match.group(1))
        return "Board" if number == 1 else f"Board {number}"
    match = _SSD_SENSOR.match(name)
    if chip == "nvme" and match:
        return f"SSD {int(match.group(1)) + 1}"
    return None
