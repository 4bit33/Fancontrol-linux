import pytest

from fancontrol.core.config import bootstrap
from fancontrol.core.engine import STALL_TICKS, ControlEngine
from fancontrol.core.models import (
    Config,
    Control,
    CurvePoint,
    FlatCurve,
    GraphCurve,
    Settings,
)


def build(registry, **control_kwargs):
    """A configuration driving the first control from the first sensor."""

    output_id = next(iter(registry.controls))
    sensor_id = next(s for s in registry.temps if s.endswith(":temp1"))
    curve = GraphCurve(
        id="curve",
        name="curve",
        sensor_id=sensor_id,
        points=[CurvePoint(30, 20), CurvePoint(80, 100)],
    )
    kwargs = dict(
        id="control",
        name="fan",
        output_id=output_id,
        curve_id="curve",
        enabled=True,
        min_percent=0,
        start_percent=0,
    )
    kwargs.update(control_kwargs)
    return Config(curves=[curve], controls=[Control(**kwargs)]), output_id, sensor_id


def test_tick_drives_the_control_from_the_curve(registry):
    config, output_id, sensor_id = build(registry)
    registry.temps[sensor_id].path.write_text("55000\n")
    engine = ControlEngine(registry, config)
    status = engine.tick()

    entry = status.controls["control"]
    assert entry["managed"] is True
    # 55 C is half way between 30 and 80, so half way between 20% and 100%.
    assert entry["requested_percent"] == pytest.approx(60, abs=0.5)
    assert abs(registry.controls[output_id].read_percent() - 60) < 1


def test_a_disabled_control_is_handed_back_to_the_firmware(registry):
    config, output_id, _ = build(registry)
    engine = ControlEngine(registry, config)
    engine.tick()
    assert registry.controls[output_id].acquired

    config.controls[0].enabled = False
    engine.tick()
    assert not registry.controls[output_id].acquired


def test_the_master_switch_releases_everything(registry):
    config, output_id, _ = build(registry)
    engine = ControlEngine(registry, config)
    engine.tick()
    assert registry.controls[output_id].acquired

    config.settings.control_enabled = False
    engine.tick()
    assert not registry.controls[output_id].acquired


def test_an_unreadable_sensor_falls_back_to_the_failsafe(registry):
    config, output_id, sensor_id = build(registry)
    config.settings.failsafe_percent = 100.0
    registry.temps[sensor_id].path.write_text("-273000\n")

    engine = ControlEngine(registry, config)
    status = engine.tick()

    entry = status.controls["control"]
    assert entry["requested_percent"] == 100
    assert "unavailable" in entry["error"]
    assert abs(registry.controls[output_id].read_percent() - 100) < 1


def test_critical_temperature_forces_full_speed(registry):
    config, output_id, sensor_id = build(registry)
    config.settings.critical_temperature = 90.0
    registry.temps[sensor_id].path.write_text("95000\n")

    engine = ControlEngine(registry, config)
    status = engine.tick()

    assert status.critical is True
    assert status.messages and "full speed" in status.messages[0]
    assert abs(registry.controls[output_id].read_percent() - 100) < 1


def test_minimum_percent_is_a_floor_unless_stopping_is_allowed(registry):
    config, output_id, sensor_id = build(registry, min_percent=30, allow_stop=False)
    registry.temps[sensor_id].path.write_text("20000\n")
    engine = ControlEngine(registry, config)
    engine.tick()
    assert abs(registry.controls[output_id].read_percent() - 30) < 1

    config.controls[0].allow_stop = True
    engine.tick()
    assert registry.controls[output_id].read_percent() == pytest.approx(0, abs=0.5)


def test_maximum_percent_caps_the_output(registry):
    config, output_id, sensor_id = build(registry, max_percent=60)
    registry.temps[sensor_id].path.write_text("95000\n")
    config.settings.critical_temperature = 0  # disable the override for this test
    engine = ControlEngine(registry, config)
    engine.tick()
    assert abs(registry.controls[output_id].read_percent() - 60) < 1


def test_step_up_limits_how_fast_the_fan_ramps(registry):
    config, output_id, sensor_id = build(registry, step_up=10, min_percent=0)
    registry.temps[sensor_id].path.write_text("30000\n")
    engine = ControlEngine(registry, config)
    engine.tick()  # settles at 20%

    registry.temps[sensor_id].path.write_text("80000\n")
    engine._last_tick -= 1.0  # pretend one second passed
    status = engine.tick()
    entry = status.controls["control"]
    assert entry["requested_percent"] == 100
    # One second at 10%/s from 20% may only reach 30%.
    assert entry["applied_percent"] == pytest.approx(30, abs=1)


def test_a_stopped_fan_gets_a_start_kick(registry):
    config, output_id, sensor_id = build(
        registry, min_percent=30, allow_stop=True, start_percent=45, start_duration=5
    )
    # At 20 C the curve asks for 20%, which is below the 30% floor, so with
    # stopping allowed the fan is switched off entirely.
    registry.temps[sensor_id].path.write_text("20000\n")
    engine = ControlEngine(registry, config)
    engine.tick()
    assert registry.controls[output_id].read_percent() == pytest.approx(0, abs=0.5)

    registry.temps[sensor_id].path.write_text("40000\n")
    status = engine.tick()
    entry = status.controls["control"]
    assert entry["kicking"] is True
    # The curve only asks for 36%, but a fan that was stopped needs more than
    # that to start turning, so it is held at 45% for a moment.
    assert entry["applied_percent"] == pytest.approx(45, abs=0.5)


def test_manual_override_wins_over_the_curve(registry):
    config, output_id, sensor_id = build(registry)
    registry.temps[sensor_id].path.write_text("55000\n")
    engine = ControlEngine(registry, config)

    engine.set_override("control", 80)
    engine.tick()
    assert abs(registry.controls[output_id].read_percent() - 80) < 1

    engine.set_override("control", None)
    engine.tick()
    assert abs(registry.controls[output_id].read_percent() - 60) < 1


def test_stalled_fans_are_flagged(registry):
    config, output_id, sensor_id = build(registry, min_percent=50)
    fan_id = output_id.rsplit(":", 1)[0] + ":fan1"
    config.controls[0].fan_sensor_id = fan_id
    registry.fans[fan_id].path.write_text("0\n")

    engine = ControlEngine(registry, config)
    for _ in range(STALL_TICKS + 1):
        status = engine.tick()
    assert status.controls["control"]["stalled"] is True


def test_a_missing_output_is_reported_instead_of_crashing(registry):
    config, _, _ = build(registry)
    config.controls[0].output_id = "hwmon:nonexistent:pwm9"
    engine = ControlEngine(registry, config)
    status = engine.tick()
    assert "was not found" in status.controls["control"]["error"]


def test_shutdown_restores_the_firmware(registry):
    config, output_id, _ = build(registry)
    engine = ControlEngine(registry, config)
    engine.tick()
    assert registry.controls[output_id].acquired
    engine.shutdown()
    assert not registry.controls[output_id].acquired


def test_bootstrap_creates_a_disabled_control_per_output(registry):
    config = bootstrap(registry)
    assert len(config.controls) == len(registry.controls)
    assert all(not c.enabled for c in config.controls)
    assert all(c.curve_id for c in config.controls)
    # Each PWM should have found its matching tachometer on the same chip.
    assert any(c.fan_sensor_id for c in config.controls)


def test_the_loop_actually_cools_the_simulated_machine(registry, simulator):
    """An end to end sanity check: hot machine, curve applied, temperature falls."""

    config, output_id, sensor_id = build(registry, min_percent=0)
    for zone in simulator.chips[0].zones:
        zone.temperature = 85.0
        zone.load = 0.5
    simulator.step(0.0)

    engine = ControlEngine(registry, config)
    start = registry.temps[sensor_id].read()
    for _ in range(200):
        engine.tick()
        simulator.step(1.0)
    end = registry.temps[sensor_id].read()

    assert end < start - 5
    assert registry.controls[output_id].read_percent() > 20


def test_a_paused_control_is_left_alone(registry):
    """While something else drives a fan, the loop must not write to it."""

    config, output_id, sensor_id = build(registry)
    registry.temps[sensor_id].path.write_text("55000\n")
    engine = ControlEngine(registry, config)
    engine.tick()

    engine.pause("control")
    registry.controls[output_id].set_percent(17.0)
    status = engine.tick()

    assert status.controls["control"]["paused"] is True
    assert registry.controls[output_id].read_percent() == pytest.approx(17, abs=1)


def test_resuming_does_not_ramp_from_a_stale_value(registry):
    """After a pause the hardware is wherever the other writer left it."""

    config, output_id, sensor_id = build(registry, step_down=1.0, min_percent=0)
    registry.temps[sensor_id].path.write_text("80000\n")
    engine = ControlEngine(registry, config)
    engine.tick()  # settles at 100%

    engine.pause("control")
    registry.controls[output_id].set_percent(10.0)
    engine.tick()
    engine.resume("control")

    registry.temps[sensor_id].path.write_text("30000\n")
    status = engine.tick()
    # The slew limiter must not believe the fan is still at 100% and crawl down
    # from there at 1%/s; it starts from where the hardware actually is.
    assert status.controls["control"]["applied_percent"] < 50


def test_the_firmware_gets_the_fan_while_it_is_cool(registry):
    """Below the threshold the output goes back; at it, we take it again."""

    config, output_id, sensor_id = build(registry, min_percent=0)
    control = config.controls[0]
    control.firmware_below = 40.0
    control.firmware_sensor_id = sensor_id
    control.firmware_hysteresis = 3.0
    engine = ControlEngine(registry, config)
    output = registry.controls[output_id]

    def at(temp):
        registry.temps[sensor_id].path.write_text(f"{int(temp * 1000)}\n")
        return engine.tick().controls["control"]

    assert at(30)["with_firmware"] is True
    assert not output.acquired

    assert at(41)["with_firmware"] is False        # warm: ours
    assert output.acquired

    assert at(38.5)["with_firmware"] is False      # inside the gap: stays ours
    assert at(36)["with_firmware"] is True         # below 40 - 3: firmware again
    assert not output.acquired

    assert at(38.5)["with_firmware"] is True       # inside the gap: stays theirs


def test_an_unreadable_sensor_never_leaves_the_fan_to_the_firmware(registry):
    config, output_id, sensor_id = build(registry)
    control = config.controls[0]
    control.firmware_below = 40.0
    control.firmware_sensor_id = sensor_id
    registry.temps[sensor_id].path.write_text("-273000\n")
    status = ControlEngine(registry, config).tick()
    assert status.controls["control"]["with_firmware"] is False
    assert registry.controls[output_id].acquired


def test_a_critical_temperature_takes_the_fan_back(registry):
    config, output_id, sensor_id = build(registry)
    control = config.controls[0]
    control.firmware_below = 40.0
    # Watch a cool sensor for the hand-over, while the curve's sensor is hot.
    cool = next(s for s in registry.temps if s != sensor_id)
    control.firmware_sensor_id = cool
    registry.temps[cool].path.write_text("30000\n")
    registry.temps[sensor_id].path.write_text("95000\n")
    status = ControlEngine(registry, config).tick()
    assert status.critical is True
    assert status.controls["control"]["with_firmware"] is False
