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


@pytest.fixture
def gigabyte_machine(tmp_path):
    """A machine shaped like the one the sample configuration came from.

    A Gigabyte board with an IT8689E super-I/O and six fan headers, an Intel
    CPU behind ``coretemp``, and an RTX 3070 with two fans.
    """

    from fancontrol.hw.simulator import Chip, Zone

    sim = Simulator(tmp_path / "hwmon")
    sim.chips = [
        Chip(
            dirname="hwmon0",
            name="it8689",
            zones=[
                Zone("fan1", max_rpm=3013), Zone("fan2", max_rpm=3013),
                Zone("fan3", max_rpm=1957), Zone("fan4", max_rpm=3013),
                Zone("fan5", max_rpm=422), Zone("fan6", max_rpm=3000),
            ],
        ),
        Chip(
            dirname="hwmon1",
            name="coretemp",
            # coretemp labels the package first, then one channel per core.
            extra_temps=["Package id 0", "Core 0", "Core 1", "Core 2", "Core 3"],
            has_pwm=False,
        ),
    ]
    sim.build()
    return sim


@pytest.fixture
def gigabyte_registry(gigabyte_machine):
    from .fake_nvidia import FakeNvidiaBackend

    reg = HardwareRegistry(
        backends=[HwmonBackend(root=gigabyte_machine.root), FakeNvidiaBackend()]
    )
    reg.discover()
    return reg
