from fancontrol.importer.fancontrol_json import WIN_PREFIX, FanControlImporter
from fancontrol.importer.mapping import HardwareMapper

from .test_importer import SAMPLE


def test_superio_temperature_maps_to_the_right_channel(registry):
    mapper = HardwareMapper(registry)
    best = mapper.suggest("/lpc/nct6798d/temperature/1", "temperature")[0]
    # LibreHardwareMonitor counts from zero, hwmon from one.
    assert best.target_id.endswith(":temp2")
    assert best.target_id.startswith("hwmon:nct6798")
    assert best.score >= 0.7


def test_superio_control_maps_to_the_matching_pwm(registry):
    mapper = HardwareMapper(registry)
    best = mapper.suggest("/lpc/nct6798d/control/1", "control")[0]
    assert best.target_id.endswith(":pwm2")


def test_chip_name_suffix_differences_are_tolerated(registry):
    """Windows says nct6798d, the kernel says nct6798."""

    mapper = HardwareMapper(registry)
    suggestions = mapper.suggest("/lpc/nct6798d/temperature/0", "temperature")
    assert suggestions and suggestions[0].target_id.startswith("hwmon:nct6798")


def test_a_different_chip_is_not_offered(registry):
    mapper = HardwareMapper(registry)
    suggestions = mapper.suggest("/lpc/it8688e/temperature/0", "temperature")
    assert suggestions == []


def test_cpu_package_is_found_by_its_label(registry):
    mapper = HardwareMapper(registry)
    best = mapper.suggest("/amdcpu/0/temperature/2", "temperature")[0]
    assert registry.temps[best.target_id].name == "Tctl"
    assert best.score >= 0.7


def test_gpu_temperature_prefers_the_core_sensor(registry):
    mapper = HardwareMapper(registry)
    best = mapper.suggest("/amdgpu/0/temperature/0", "temperature")[0]
    assert registry.temps[best.target_id].device.chip == "amdgpu"
    assert registry.temps[best.target_id].name == "GPU fan zone"


def test_hardware_that_is_absent_produces_no_suggestion(registry):
    mapper = HardwareMapper(registry)
    assert mapper.suggest("/nvidiagpu/0/control/0", "control") == []


def test_auto_map_resolves_what_it_can_and_reports_the_rest(registry):
    result = FanControlImporter().convert(SAMPLE)
    report = HardwareMapper(registry).auto_map(result)

    cpu_sensor = WIN_PREFIX + "/amdcpu/0/temperature/2"
    cpu_control = WIN_PREFIX + "/lpc/nct6798d/control/1"
    case_control = WIN_PREFIX + "/lpc/nct6798d/control/2"
    gpu_control = WIN_PREFIX + "/nvidiagpu/0/control/0"

    assert report.applied[cpu_sensor].endswith(":temp1")
    assert report.applied[cpu_control].endswith(":pwm2")
    assert report.applied[case_control].endswith(":pwm3")
    # There is no NVIDIA card in the simulated machine, so this one is left for
    # the user rather than guessed at.
    assert gpu_control in report.unmatched
    assert not report.fully_mapped


def test_apply_rewrites_the_configuration(registry):
    result = FanControlImporter().convert(SAMPLE)
    mapper = HardwareMapper(registry)
    report = mapper.auto_map(result)
    mapper.apply(result.config, report.applied)

    cpu = next(c for c in result.config.controls if c.name == "CPU cooler")
    assert cpu.output_id.startswith("hwmon:nct6798")
    # The tachometer on the same header is picked up automatically.
    assert cpu.fan_sensor_id.endswith(":fan2")

    curve = next(c for c in result.config.curves if c.name == "CPU curve")
    assert curve.sensor_id.startswith("hwmon:k10temp")

    # The GPU control had no Linux counterpart, so it keeps its Windows id and
    # is reported rather than silently pointed at some other fan.
    gpu = next(c for c in result.config.controls if c.name == "GPU Fan")
    assert gpu.output_id.startswith(WIN_PREFIX)


def test_nicknames_follow_the_sensor_they_belong_to(registry):
    result = FanControlImporter().convert(SAMPLE)
    mapper = HardwareMapper(registry)
    report = mapper.auto_map(result)
    mapper.apply(result.config, report.applied)

    target = report.applied[WIN_PREFIX + "/amdcpu/0/temperature/2"]
    assert result.config.sensor_names[target] == "Ryzen package"


def test_an_ambiguous_match_is_left_pending_not_guessed(registry):
    """Two equally good candidates must never be resolved automatically."""

    mapper = HardwareMapper(registry)
    suggestions = mapper.suggest("/lpc/nct6798d/temperature/99", "temperature")
    # Nothing lines up on the channel number, so every channel of the chip
    # scores the same and the winner is not clearly ahead.
    assert len(suggestions) > 1
    assert suggestions[0].score - suggestions[1].score < 0.1
