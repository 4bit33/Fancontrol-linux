import json
import threading
import time

import pytest

from fancontrol.core import config as config_module
from fancontrol.daemon.service import FanControlService
from fancontrol.importer.fancontrol_json import WIN_PREFIX

from .test_importer import SAMPLE


@pytest.fixture
def service(registry, tmp_path):
    svc = FanControlService(config_path=tmp_path / "config.json", registry=registry)
    svc.calibration_settle = 0.01
    svc.start()
    return svc


def test_start_bootstraps_and_writes_a_config(service, tmp_path):
    assert (tmp_path / "config.json").exists()
    config = service.get_config()
    assert len(config["controls"]) == len(service.registry.controls)
    # Nothing is driven until the user enables it.
    assert all(not c["enabled"] for c in config["controls"])


def test_inventory_lists_the_hardware(service):
    inventory = service.get_inventory()
    assert inventory["temperatures"] and inventory["controls"]
    assert all("id" in entry and "device" in entry for entry in inventory["temperatures"])


def test_set_config_validates_before_applying(service):
    config = service.get_config()
    config["controls"][0]["curve_id"] = "does-not-exist"
    result = service.set_config(config)
    assert result["ok"] is False
    assert any("unknown curve" in p for p in result["problems"])
    # The daemon is still running the configuration it had before.
    assert service.get_config()["controls"][0]["curve_id"] != "does-not-exist"


def test_set_config_persists_to_disk(service, tmp_path):
    config = service.get_config()
    config["controls"][0]["enabled"] = True
    config["controls"][0]["name"] = "Renamed"
    assert service.set_config(config)["ok"] is True

    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk["controls"][0]["name"] == "Renamed"


def test_saving_keeps_a_backup_of_the_previous_file(service, tmp_path):
    service.save_config()
    service.save_config()
    assert (tmp_path / "config.json.bak").exists()


def test_reload_picks_up_an_edited_file(service, tmp_path):
    config = service.get_config()
    config["settings"]["update_interval"] = 5.0
    config_module.save(config_module.Config.from_dict(config), tmp_path / "config.json")

    assert service.reload_config()["ok"] is True
    assert service.config.settings.update_interval == 5.0


def test_reload_refuses_a_broken_file(service, tmp_path):
    (tmp_path / "config.json").write_text('{"curves": [{"type": "graph", "id": "a", '
                                          '"name": "a", "points": []}], "controls": []}')
    result = service.reload_config()
    assert result["ok"] is False
    assert any("no points" in p for p in result["problems"])


def test_override_and_release(service):
    control_id = service.config.controls[0].id
    service.config.controls[0].enabled = True
    service.engine.set_config(service.config)

    assert service.set_override(control_id, 77)["ok"] is True
    status = service.tick()
    assert status["controls"][control_id]["applied_percent"] == pytest.approx(77, abs=1)

    assert service.set_override(control_id, None)["ok"] is True
    status = service.tick()
    # The curve takes over immediately ...
    assert status["controls"][control_id]["requested_percent"] < 50
    # ... but the fan is only allowed to slow down at step_down percent per
    # second, so it is still near 77% until some time has passed.
    assert status["controls"][control_id]["applied_percent"] == pytest.approx(77, abs=1)

    service.engine._last_tick -= 30.0
    status = service.tick()
    assert status["controls"][control_id]["applied_percent"] < 50


def test_override_on_an_unknown_control_is_rejected(service):
    assert service.set_override("nope", 50)["ok"] is False


def test_master_switch_stops_driving_the_fans(service):
    control = service.config.controls[0]
    control.enabled = True
    service.engine.set_config(service.config)
    service.tick()
    assert service.registry.controls[control.output_id].acquired

    service.set_control_enabled(False)
    service.tick()
    assert not service.registry.controls[control.output_id].acquired


def test_import_reports_what_it_mapped(service):
    result = service.import_fancontrol(text=json.dumps(SAMPLE))
    assert result["ok"] is True
    assert result["summary"]["controls"] == 3
    assert result["summary"]["curves"] == 4
    assert result["summary"]["mapped"] >= 3
    # The NVIDIA control cannot be resolved on this simulated machine.
    assert result["summary"]["needs_attention"] >= 1


def test_import_of_a_bad_file_fails_cleanly(service):
    assert service.import_fancontrol(text="{oops")["ok"] is False
    assert service.import_fancontrol(text='{"unrelated": 1}')["ok"] is False


def test_apply_import_replaces_the_configuration(service):
    imported = service.import_fancontrol(text=json.dumps(SAMPLE))
    result = service.apply_import(imported["config"], imported["mapping"]["applied"])
    assert result["ok"] is True

    config = service.get_config()
    assert {c["name"] for c in config["controls"]} == {"CPU cooler", "Case Fans", "GPU Fan"}

    cpu = next(c for c in config["controls"] if c["name"] == "CPU cooler")
    assert cpu["output_id"].startswith("hwmon:nct6798")
    assert cpu["enabled"] is True


def test_apply_import_disables_controls_it_could_not_map(service):
    imported = service.import_fancontrol(text=json.dumps(SAMPLE))
    result = service.apply_import(imported["config"], imported["mapping"]["applied"])

    skipped = {entry["name"]: entry["reason"] for entry in result["skipped"]}
    assert "GPU Fan" in skipped
    assert "no fan output" in skipped["GPU Fan"]
    gpu = next(c for c in service.get_config()["controls"] if c["name"] == "GPU Fan")
    assert gpu["enabled"] is False
    assert gpu["output_id"].startswith(WIN_PREFIX)


def test_apply_import_can_merge_into_the_existing_config(service):
    before = len(service.get_config()["controls"])
    imported = service.import_fancontrol(text=json.dumps(SAMPLE))
    service.apply_import(imported["config"], imported["mapping"]["applied"], merge=True)

    config = service.get_config()
    names = {c["name"] for c in config["controls"]}
    assert "CPU cooler" in names
    # The two imported hwmon controls replaced the bootstrapped ones that drive
    # the same outputs; the GPU one was added.
    assert len(config["controls"]) == before + 1
    outputs = [c["output_id"] for c in config["controls"]]
    assert len(outputs) == len(set(outputs)), "a PWM output must not be driven twice"


def _drive_simulation_on_read(service, simulator, control, also_tick=False):
    """Advance the simulation whenever calibration reads the tachometer.

    Stepping the simulator from a background thread made these tests depend on
    the scheduler: under load the thread would not run between two PWM writes
    and the fan would read a stale zero. Driving it from the read itself is
    deterministic.
    """

    fan = service.registry.fans[control.fan_sensor_id]
    original = type(fan).read
    stepping = False

    def stepping_read(self=fan):
        # service.tick() reads every fan, including this one, so guard against
        # stepping the simulation from inside a step.
        nonlocal stepping
        if stepping:
            return original(self)
        stepping = True
        try:
            for _ in range(20):
                simulator.step(0.5)
                if also_tick:
                    service.tick()
        finally:
            stepping = False
        return original(self)

    fan.read = stepping_read
    service.calibration_settle = 0.0


def test_calibration_measures_where_the_fan_stops_and_starts(service, simulator):
    control = service.config.controls[0]
    control.enabled = True
    service.engine.set_config(service.config)
    _drive_simulation_on_read(service, simulator, control)

    result = service.calibrate(control.id)

    assert result["ok"] is True
    # The simulated fan needs 12% to keep turning, so calibration has to find
    # the stop point just below that and suggest a minimum above it.
    assert result["stop_percent"] is not None
    assert 0 < result["stop_percent"] <= 15
    assert result["suggested_min_percent"] > result["stop_percent"]
    assert result["start_percent"] is not None


def test_calibration_is_recorded_on_the_control(service, simulator):
    control = service.config.controls[0]
    control.enabled = True
    service.engine.set_config(service.config)
    _drive_simulation_on_read(service, simulator, control)

    service.calibrate(control.id)

    assert control.calibration
    assert all(len(sample) == 2 for sample in control.calibration)
    percents = [sample[0] for sample in control.calibration]
    assert percents == sorted(percents)


def test_calibration_needs_a_tachometer(service):
    control = service.config.controls[0]
    control.fan_sensor_id = ""
    result = service.calibrate(control.id)
    assert result["ok"] is False
    assert "tachometer" in result["error"]


def test_rescan_reenumerates(service):
    assert service.rescan()["ok"] is True
    assert service.get_inventory()["controls"]


def test_status_listeners_are_called_each_tick(service):
    received = []
    service.add_listener(received.append)
    service._notify(service.tick())
    assert received and "controls" in received[0]


def test_calibration_is_not_fought_by_the_control_loop(service, simulator):
    """The loop must stand down while calibration drives the fan itself.

    Without that, every step calibration writes is overwritten on the next tick
    and the fan never stops, so no stop point is ever found.
    """

    control = service.config.controls[0]
    control.enabled = True
    control.min_percent = 40.0  # the loop would hold the fan well above stopping
    service.engine.set_config(service.config)
    _drive_simulation_on_read(service, simulator, control, also_tick=True)

    result = service.calibrate(control.id)

    assert result["ok"] is True
    assert result["stop_percent"] is not None
    assert 0 < result["stop_percent"] <= 15


def test_the_loop_takes_the_fan_back_after_calibration(service, simulator):
    control = service.config.controls[0]
    control.enabled = True
    service.engine.set_config(service.config)
    service.calibration_settle = 0.01

    service.calibrate(control.id)
    assert control.id not in service.engine.paused

    status = service.tick()
    assert status["controls"][control.id]["paused"] is False
    assert status["controls"][control.id]["managed"] is True
