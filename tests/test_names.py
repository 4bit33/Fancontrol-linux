"""Friendly sensor names: the kernel's riddles become CPU, GPU and friends.

Needs no Qt: the mapping is pure data.
"""

from __future__ import annotations

import pytest

from fancontrol.gui import names


@pytest.mark.parametrize("chip, label, friendly", [
    ("k10temp", "Tctl", "CPU"),
    ("coretemp", "Package id 0", "CPU"),
    ("amdgpu", "edge", "GPU"),
    ("amdgpu", "junction", "GPU hotspot"),
    ("nvme", "Composite", "SSD"),
    ("nvme", "Sensor 1", "SSD 2"),
    ("nvme", "Sensor 2", "SSD 3"),
    ("hp", "hp fan1", "Fan 1"),
    ("hp", "hp fan2", "Fan 2"),
    ("acpitz", "temp1", "Board"),
    ("acpitz", "temp2", "Board 2"),
])
def test_known_riddles_get_friendly_names(chip, label, friendly):
    assert names.friendly_name(chip, label) == friendly


@pytest.mark.parametrize("chip, label", [
    ("coretemp", "Core 0"),
    ("it8689", "temp1"),
    ("it8689", "fan1"),
    ("nvme", "temp1"),
    ("k10temp", "Tdie"),
    ("hp", "temp1"),
    ("acpitz", "TZ01"),
])
def test_anything_else_keeps_its_name(chip, label):
    assert names.friendly_name(chip, label) is None
