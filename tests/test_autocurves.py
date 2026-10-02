"""Automatic curves: one per fan, each on a suitable sensor.

Needs no Qt: planning and applying are pure configuration work.
"""

from __future__ import annotations

import pytest

from fancontrol.core import autocurves
from fancontrol.core.models import Config, Control, GraphCurve, validate


def _temps():
    def temp(id, name, chip):
        return {"id": id, "name": name,
                "device": {"chip": chip, "label": chip}}
    return [
        temp("hwmon:k10temp:temp1", "Tctl", "k10temp"),
        temp("hwmon:amdgpu:temp1", "edge", "amdgpu"),
        temp("hwmon:acpitz:temp1", "acpitz temp1", "acpitz"),
    ]


def _config(*controls):
    config = Config()
    config.controls = list(controls)
    return config


def test_a_cpu_fan_reads_the_cpu():
    config = _config(Control(id="c1", name="CPU cooler", output_id="hwmon:it8689:pwm1"))
    [assignment] = autocurves.plan(config, _temps())
    assert assignment.sensor_id == "hwmon:k10temp:temp1"
    assert assignment.curve_name == "CPU cooler"


def test_a_gpu_fan_reads_the_gpu():
    config = _config(Control(id="g1", name="GPU fan", output_id="nvidia:GPU-1:pwm0"))
    [assignment] = autocurves.plan(config, _temps())
    assert assignment.sensor_id == "hwmon:amdgpu:temp1"


def test_a_plain_fan_falls_back_to_the_cpu():
    config = _config(Control(id="f1", name="Rear", output_id="hwmon:it8689:pwm2"))
    [assignment] = autocurves.plan(config, _temps())
    assert assignment.sensor_id == "hwmon:k10temp:temp1"


def test_a_fan_with_a_curve_is_left_alone():
    config = _config(Control(id="c1", name="CPU cooler", output_id="hwmon:x:pwm1"))
    config.curves = [GraphCurve(id="k1", name="Mine", sensor_id="hwmon:k10temp:temp1")]
    config.controls[0].curve_id = "k1"
    assert autocurves.plan(config, _temps()) == []


def test_a_fan_with_a_broken_reference_gets_a_curve():
    config = _config(Control(id="c1", name="CPU cooler", output_id="hwmon:x:pwm1",
                             curve_id="missing"))
    assert len(autocurves.plan(config, _temps())) == 1


def test_curve_names_stay_unique():
    config = _config(
        Control(id="c1", name="Fan", output_id="hwmon:x:pwm1"),
        Control(id="c2", name="Fan", output_id="hwmon:x:pwm2"),
    )
    config.curves = [GraphCurve(id="k0", name="Fan", sensor_id="hwmon:k10temp:temp1")]
    names = [a.curve_name for a in autocurves.plan(config, _temps())]
    assert names == ["Fan (2)", "Fan (3)"]


def test_applying_points_the_fans_and_switches_them_on():
    config = _config(
        Control(id="c1", name="CPU cooler", output_id="hwmon:x:pwm1", enabled=False),
        Control(id="g1", name="GPU fan", output_id="nvidia:GPU-1:pwm0"),
    )
    created = autocurves.apply(config, autocurves.plan(config, _temps()))
    assert [c.name for c in created] == ["CPU cooler", "GPU fan"]
    assert all(isinstance(c, GraphCurve) and len(c.points) == 4 for c in created)
    assert all(c.sensor_id for c in created)
    assert config.controls[0].curve_id == created[0].id
    assert config.controls[0].enabled is True
    assert config.controls[1].curve_id == created[1].id
    assert validate(config) == []


def test_no_temperatures_means_no_plan():
    config = _config(Control(id="c1", name="CPU cooler", output_id="hwmon:x:pwm1"))
    assert autocurves.plan(config, []) == []
    assert autocurves.plan_standalone(config, []) == []


def test_standalone_curves_watch_cpu_and_gpu():
    config = _config()
    assignments = autocurves.plan_standalone(config, _temps())
    assert [(a.curve_name, a.sensor_id) for a in assignments] == [
        ("CPU", "hwmon:k10temp:temp1"),
        ("GPU", "hwmon:amdgpu:temp1"),
    ]
    created = autocurves.apply(config, assignments)
    assert [c.name for c in created] == ["CPU", "GPU"]
    assert all(c.sensor_id for c in created)
    assert validate(config) == []


def test_standalone_skips_sensors_that_have_curves():
    config = _config()
    config.curves = [GraphCurve(id="k1", name="Mine", sensor_id="hwmon:k10temp:temp1")]
    assignments = autocurves.plan_standalone(config, _temps())
    assert [(a.curve_name, a.sensor_id) for a in assignments] == [
        ("GPU", "hwmon:amdgpu:temp1"),
    ]
