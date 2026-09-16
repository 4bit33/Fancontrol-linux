import pytest

from fancontrol.hw.hwmon import HwmonBackend
from fancontrol.hw.registry import HardwareRegistry
from fancontrol.hw.simulator import Simulator


@pytest.fixture
def simulator(tmp_path):
    """A fake hwmon tree that reacts to the PWM values written to it."""

    sim = Simulator(tmp_path / "hwmon")
    sim.default_layout()
    sim.build()
    return sim


@pytest.fixture
def registry(simulator):
    reg = HardwareRegistry(backends=[HwmonBackend(root=simulator.root)])
    reg.discover()
    return reg
