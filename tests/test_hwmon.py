from fancontrol.hw.hwmon import ENABLE_MANUAL, HwmonPwmOutput


def test_discovers_the_simulated_chips(registry):
    chips = {sensor.device.chip for sensor in registry.temps.values()}
    assert {"nct6798", "k10temp", "amdgpu", "nvme"} <= chips


def test_identifiers_carry_the_chip_name_not_the_hwmon_number(registry):
    assert any(sensor_id.startswith("hwmon:nct6798") for sensor_id in registry.temps)
    assert not any(":hwmon0:" in sensor_id for sensor_id in registry.temps)


def test_labels_are_used_as_sensor_names(registry):
    names = {sensor.name for sensor in registry.temps.values()}
    assert "CPU fan zone" in names
    assert "Tctl" in names


def test_chips_without_pwm_expose_no_controls(registry):
    for control_id, control in registry.controls.items():
        assert control.device.chip in {"nct6798", "amdgpu"}


def test_reading_a_temperature(registry):
    sensor = next(s for s in registry.temps.values() if s.name == "CPU fan zone")
    value = sensor.read()
    assert value is not None and 20 < value < 110


def test_acquire_switches_the_chip_into_manual_mode(registry):
    control = next(iter(registry.controls.values()))
    assert isinstance(control, HwmonPwmOutput)
    assert control.enable_path is not None
    assert control.enable_path.read_text().strip() == "2"

    control.acquire()
    assert control.enable_path.read_text().strip() == str(ENABLE_MANUAL)

    control.set_percent(75)
    assert abs(control.read_percent() - 75) < 1

    control.release()
    # The firmware's original mode has to come back, otherwise a crash would
    # leave the machine with the fans stuck wherever we left them.
    assert control.enable_path.read_text().strip() == "2"


def test_release_all_restores_every_control(registry):
    for control in registry.controls.values():
        control.acquire()
    registry.release_all()
    for control in registry.controls.values():
        assert not control.acquired


def test_implausible_temperatures_are_reported_as_unavailable(registry, simulator):
    sensor = next(iter(registry.temps.values()))
    sensor.path.write_text("-273000\n")
    assert sensor.read() is None
