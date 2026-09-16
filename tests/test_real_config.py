"""The importer against a real FanControl configuration.

``tests/data/fancontrol-v270.json`` is an actual userConfig.json from
FanControl 270: a Gigabyte board with an IT8689E driving six fans, an Intel
CPU, and an RTX 3070 whose two fans are handled by FanControl's NvAPI plugin
rather than by LibreHardwareMonitor.

It is the file that showed the first importer was written against a schema
FanControl no longer uses, so every assertion here is a shape the real format
actually has.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fancontrol.core.models import GraphCurve, MixCurve
from fancontrol.importer.fancontrol_json import WIN_PREFIX, import_file
from fancontrol.importer.mapping import HardwareMapper

CONFIG = Path(__file__).resolve().parent / "data" / "fancontrol-v270.json"


@pytest.fixture(scope="module")
def imported():
    return import_file(CONFIG)


# ----------------------------------------------------------------------
# structure


def test_exactly_the_real_fans_are_imported(imported):
    """Six board headers plus two GPU fans, and nothing invented.

    The first version of this importer produced 24 controls, because a
    PairedFanSensor reference and a FanSensors entry both carry an identifier
    and were mistaken for fans.
    """

    names = [c.name for c in imported.config.controls]
    assert names == [
        "Fan #1", "Fan #2", "Fan #3", "Fan #4", "Fan #5", "Fan #6",
        "Control 1 - NVIDIA GeForce RTX 3070",
        "Control 2 - NVIDIA GeForce RTX 3070",
    ]


def test_all_six_curves_are_imported(imported):
    names = {c.name for c in imported.config.curves}
    assert names == {"Графік", "CPU", "Функція", "літо", "відюха", "відюха нагрузка"}


def test_the_import_produces_no_warnings_about_the_data(imported):
    # The only warning this file should raise is the mix function note.
    assert all("mix curve" in w for w in imported.warnings)


# ----------------------------------------------------------------------
# curves


def test_points_stored_as_strings_are_read(imported):
    curve = next(c for c in imported.config.curves if c.name == "Графік")
    assert isinstance(curve, GraphCurve)
    assert len(curve.points) == 10
    first, last = curve.points[0], curve.points[-1]
    assert (round(first.temperature, 2), round(first.percent, 2)) == (20.45, 20.95)
    assert (round(last.temperature, 2), round(last.percent, 2)) == (73.68, 55.72)


def test_points_come_out_in_temperature_order(imported):
    for curve in imported.config.curves:
        if isinstance(curve, GraphCurve):
            temperatures = [p.temperature for p in curve.points]
            assert temperatures == sorted(temperatures)


def test_the_hysteresis_config_object_is_read(imported):
    curve = next(c for c in imported.config.curves if c.name == "Графік")
    assert (curve.hysteresis_up, curve.hysteresis_down) == (2.0, 2.0)
    assert (curve.response_time_up, curve.response_time_down) == (1.0, 1.0)
    assert curve.ignore_hysteresis_at_limits is False

    summer = next(c for c in imported.config.curves if c.name == "літо")
    assert summer.ignore_hysteresis_at_limits is True


def test_the_graph_axis_range_is_kept(imported):
    """The GPU curve runs to 116 C; on a 0..100 graph its points would be lost."""

    gpu = next(c for c in imported.config.curves if c.name == "відюха")
    assert gpu.axis_max_temperature >= 116
    assert max(p.temperature for p in gpu.points) <= gpu.axis_max_temperature

    cpu = next(c for c in imported.config.curves if c.name == "CPU")
    assert cpu.axis_max_temperature == pytest.approx(105, abs=1)


def test_the_mix_curve_is_found_and_linked(imported):
    mix = next(c for c in imported.config.curves if c.name == "Функція")
    assert isinstance(mix, MixCurve)
    referenced = {imported.config.curve_by_id(cid).name for cid in mix.curve_ids}
    assert referenced == {"Графік", "CPU"}


def test_the_mix_function_number_is_reported_not_silently_assumed(imported):
    """It is the one field that cannot be confirmed from a config file."""

    assert any("function number 2" in w for w in imported.warnings)


def test_curves_referenced_by_a_name_object_are_linked(imported):
    """v270 writes SelectedFanCurve as {"Name": "..."} rather than a GUID."""

    by_id = {c.id: c.name for c in imported.config.curves}
    for control in imported.config.controls:
        assert control.curve_id, f"{control.name} lost its curve"
    board = next(c for c in imported.config.controls if c.name == "Fan #1")
    gpu = next(c for c in imported.config.controls if c.name.startswith("Control 1"))
    assert by_id[board.curve_id] == "Графік"
    assert by_id[gpu.curve_id] == "відюха"


# ----------------------------------------------------------------------
# controls


def test_the_selected_field_names_are_read(imported):
    fan1 = next(c for c in imported.config.controls if c.name == "Fan #1")
    assert fan1.enabled is True                  # "Enable", not "Enabled"
    assert fan1.start_percent == 35              # "SelectedStart"
    assert fan1.stop_percent == 26               # "SelectedStop"
    assert fan1.offset_percent == 0              # "SelectedOffset"
    assert fan1.step_up == 8                     # "SelectedCommandStepUp"
    assert fan1.step_down == 8
    assert fan1.min_percent == 0
    assert fan1.manual_percent == 50             # "ManualControlValue"


def test_a_fan_calibrated_as_never_stopping_is_not_allowed_to_stop(imported):
    """Fan #3 turns even at 0%, and FanControl recorded no stop point."""

    fan3 = next(c for c in imported.config.controls if c.name == "Fan #3")
    assert (fan3.start_percent, fan3.stop_percent) == (0, 0)
    assert fan3.allow_stop is False

    fan2 = next(c for c in imported.config.controls if c.name == "Fan #2")
    assert fan2.stop_percent == 7
    assert fan2.allow_stop is True


def test_the_paired_tachometer_is_taken_from_the_file(imported):
    """FanControl already knows which tachometer belongs to which header."""

    fan1 = next(c for c in imported.config.controls if c.name == "Fan #1")
    assert fan1.fan_sensor_id == WIN_PREFIX + "/lpc/it8689e/fan/0"
    gpu = next(c for c in imported.config.controls if c.name.startswith("Control 2"))
    assert gpu.fan_sensor_id == WIN_PREFIX + "NVApiWrapper/0-GA104-A/fan/1"


def test_the_measured_speed_table_is_imported(imported):
    fan1 = next(c for c in imported.config.controls if c.name == "Fan #1")
    assert fan1.calibration[0] == [20.0, 0.0]
    assert fan1.calibration[-1] == [100.0, 3013.0]
    # The third element of each FanControl entry is a flag, not data.
    assert all(len(sample) == 2 for sample in fan1.calibration)


# ----------------------------------------------------------------------
# mapping onto real Linux hardware


def test_the_board_fans_map_onto_the_it8689(imported, gigabyte_registry):
    report = HardwareMapper(gigabyte_registry).auto_map(import_file(CONFIG))
    for index in range(6):
        win = f"{WIN_PREFIX}/lpc/it8689e/control/{index}"
        assert report.applied[win].endswith(f":pwm{index + 1}")
        assert report.applied[win].startswith("hwmon:it8689")


def test_the_nvapi_plugin_identifiers_map_onto_nvml(gigabyte_registry):
    """NVApiWrapper/... is FanControl's own scheme, not LibreHardwareMonitor's."""

    report = HardwareMapper(gigabyte_registry).auto_map(import_file(CONFIG))
    assert report.applied[f"{WIN_PREFIX}NVApiWrapper/0-GA104-A/control/0"].endswith(":pwm0")
    assert report.applied[f"{WIN_PREFIX}NVApiWrapper/0-GA104-A/control/1"].endswith(":pwm1")
    assert report.applied[f"{WIN_PREFIX}NVApiWrapper/0-GA104-A/sensor/0"].endswith(":temp")


def test_the_two_gpu_fans_do_not_collapse_onto_one(gigabyte_registry):
    report = HardwareMapper(gigabyte_registry).auto_map(import_file(CONFIG))
    first = report.applied[f"{WIN_PREFIX}NVApiWrapper/0-GA104-A/control/0"]
    second = report.applied[f"{WIN_PREFIX}NVApiWrapper/0-GA104-A/control/1"]
    assert first != second


def test_two_windows_sensors_are_never_pointed_at_one_linux_sensor(gigabyte_registry):
    """Both Intel CPU channels score best on "Package id 0"; neither may win.

    They were separate sensors on Windows, so at most one of them belongs
    there. Rather than guess, both are handed to the user with a ranked list.
    """

    report = HardwareMapper(gigabyte_registry).auto_map(import_file(CONFIG))
    assert f"{WIN_PREFIX}/intelcpu/0/temperature/0" in report.pending
    assert f"{WIN_PREFIX}/intelcpu/0/temperature/1" in report.pending
    assert len(set(report.applied.values())) == len(report.applied)

    candidates = report.pending[f"{WIN_PREFIX}/intelcpu/0/temperature/1"]
    labels = [gigabyte_registry.temps[c.target_id].name for c in candidates]
    assert "Package id 0" in labels and "Core 0" in labels


def test_two_controls_may_share_one_tachometer(imported):
    """The file pairs both Fan #5 and Fan #6 with fan/5, so fan/4 goes unused.

    That is how the configuration was saved on Windows, and importing it
    faithfully matters more than tidying it up.
    """

    fan5 = next(c for c in imported.config.controls if c.name == "Fan #5")
    fan6 = next(c for c in imported.config.controls if c.name == "Fan #6")
    assert fan5.fan_sensor_id == fan6.fan_sensor_id == WIN_PREFIX + "/lpc/it8689e/fan/5"
    assert not any(
        c.fan_sensor_id.endswith("/fan/4") for c in imported.config.controls
    )


def test_everything_else_is_resolved_automatically(gigabyte_registry):
    result = import_file(CONFIG)
    report = HardwareMapper(gigabyte_registry).auto_map(result)
    # 6 board controls + 5 distinct board tachometers (two fans share one)
    # + 2 GPU controls + 2 GPU fans + 1 GPU temperature = 16.
    # Only the two Intel CPU channels need a decision.
    assert len(report.applied) == 16
    assert report.unmatched == []
    assert len(report.pending) == 2


def test_applying_the_mapping_rewrites_the_configuration(gigabyte_registry):
    result = import_file(CONFIG)
    mapper = HardwareMapper(gigabyte_registry)
    report = mapper.auto_map(result)
    mapper.apply(result.config, report.applied)

    fan1 = next(c for c in result.config.controls if c.name == "Fan #1")
    assert fan1.output_id.startswith("hwmon:it8689")
    assert fan1.fan_sensor_id.endswith(":fan1")

    gpu = next(c for c in result.config.controls if c.name.startswith("Control 1"))
    assert gpu.output_id.startswith("nvidia:")
    assert gpu.fan_sensor_id.endswith(":fan0")

    gpu_curve = next(c for c in result.config.curves if c.name == "відюха")
    assert gpu_curve.sensor_id.endswith(":temp")


def test_the_windows_nicknames_follow_their_sensors(gigabyte_registry):
    result = import_file(CONFIG)
    mapper = HardwareMapper(gigabyte_registry)
    report = mapper.auto_map(result)
    mapper.apply(result.config, report.applied)

    target = report.applied[f"{WIN_PREFIX}/lpc/it8689e/fan/0"]
    assert result.config.sensor_names[target] == "Fan #1"
