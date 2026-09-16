import json

import pytest

from fancontrol.core.models import (
    FlatCurve,
    GraphCurve,
    LinearCurve,
    MixCurve,
    TargetCurve,
    TriggerCurve,
)
from fancontrol.importer.fancontrol_json import FanControlImporter, WIN_PREFIX
from fancontrol.importer.mapping import HardwareMapper, parse_identifier

# A document shaped like a real FanControl userConfig.json: a .NET type
# discriminator on each curve, GUID cross references, and points stored as a
# dictionary keyed by temperature.
SAMPLE = {
    "Main": {
        "Controls": [
            {
                "Identifier": "/lpc/nct6798d/control/1",
                "Name": "CPU Fan",
                "NickName": "CPU cooler",
                "Enabled": True,
                "SelectedCurveId": "11111111-1111-1111-1111-111111111111",
                "IsHidden": False,
                "Offset": 0,
                "MinimumPercent": 25,
                "MaximumPercent": 100,
                "StopEnabled": False,
                "StartPercent": 45,
                "ManualControl": False,
            },
            {
                "Identifier": "/lpc/nct6798d/control/2",
                "Name": "Case Fans",
                "Enabled": True,
                "SelectedCurveId": "33333333-3333-3333-3333-333333333333",
                "StopEnabled": True,
                "StartPercent": 35,
            },
            {
                "Identifier": "/nvidiagpu/0/control/0",
                "Name": "GPU Fan",
                "Enabled": True,
                "SelectedCurveId": "44444444-4444-4444-4444-444444444444",
            },
        ],
        "Curves": [
            {
                "$type": "FanControl.Curves.GraphCurveConfig, FanControl",
                "Id": "11111111-1111-1111-1111-111111111111",
                "Name": "CPU curve",
                "Points": {"30": 25, "50": 35, "70": 70, "85": 100},
                "SelectedTempSource": {"Identifier": "/amdcpu/0/temperature/2"},
                "Hysteresis": 3,
                "HysteresisOnlyOnDrop": True,
                "ResponseTime": 4,
            },
            {
                "$type": "FanControl.Curves.GraphCurveConfig, FanControl",
                "Id": "22222222-2222-2222-2222-222222222222",
                "Name": "GPU based",
                "Points": {"35": 20, "75": 100},
                "SelectedTempSource": {"Identifier": "/nvidiagpu/0/temperature/0"},
            },
            {
                "$type": "FanControl.Curves.MixCurveConfig, FanControl",
                "Id": "33333333-3333-3333-3333-333333333333",
                "Name": "Case mix",
                "SelectedCurves": [
                    "11111111-1111-1111-1111-111111111111",
                    "22222222-2222-2222-2222-222222222222",
                ],
                "MixFunction": 0,
            },
            {
                "$type": "FanControl.Curves.TargetCurveConfig, FanControl",
                "Id": "44444444-4444-4444-4444-444444444444",
                "Name": "GPU target",
                "SelectedTempSource": {"Identifier": "/nvidiagpu/0/temperature/0"},
                "TargetTemperature": 65,
                "IdleFanSpeed": 30,
                "LoadFanSpeed": 90,
            },
        ],
        "TempSensors": [
            {"Identifier": "/amdcpu/0/temperature/2", "NickName": "Ryzen package"}
        ],
    }
}


def test_imports_controls_and_curves():
    result = FanControlImporter().convert(SAMPLE)
    assert len(result.config.controls) == 3
    assert len(result.config.curves) == 4

    cpu = next(c for c in result.config.controls if c.name == "CPU cooler")
    assert cpu.output_id == WIN_PREFIX + "/lpc/nct6798d/control/1"
    assert cpu.min_percent == 25
    assert cpu.start_percent == 45
    assert cpu.allow_stop is False

    case = next(c for c in result.config.controls if c.name == "Case Fans")
    assert case.allow_stop is True


def test_curve_types_are_recognised_from_the_dotnet_type():
    result = FanControlImporter().convert(SAMPLE)
    by_name = {c.name: c for c in result.config.curves}
    assert isinstance(by_name["CPU curve"], GraphCurve)
    assert isinstance(by_name["Case mix"], MixCurve)
    assert isinstance(by_name["GPU target"], TargetCurve)


def test_points_stored_as_a_dictionary_are_parsed_in_order():
    result = FanControlImporter().convert(SAMPLE)
    curve = next(c for c in result.config.curves if c.name == "CPU curve")
    assert [(p.temperature, p.percent) for p in curve.points] == [
        (30.0, 25.0),
        (50.0, 35.0),
        (70.0, 70.0),
        (85.0, 100.0),
    ]
    assert curve.hysteresis == 3
    assert curve.response_time == 4


def test_guid_cross_references_are_resolved():
    result = FanControlImporter().convert(SAMPLE)
    by_name = {c.name: c for c in result.config.curves}
    mix = by_name["Case mix"]
    referenced = {by_name["CPU curve"].id, by_name["GPU based"].id}
    assert set(mix.curve_ids) == referenced

    case = next(c for c in result.config.controls if c.name == "Case Fans")
    assert case.curve_id == mix.id


def test_windows_identifiers_are_reported_as_unmapped():
    result = FanControlImporter().convert(SAMPLE)
    assert WIN_PREFIX + "/amdcpu/0/temperature/2" in result.unmapped_sensors
    assert WIN_PREFIX + "/lpc/nct6798d/control/1" in result.unmapped_controls
    assert WIN_PREFIX + "/nvidiagpu/0/control/0" in result.unmapped_controls


def test_nicknames_are_kept():
    result = FanControlImporter().convert(SAMPLE)
    assert result.config.sensor_names[WIN_PREFIX + "/amdcpu/0/temperature/2"] == "Ryzen package"


def test_points_stored_as_a_list_of_xy_objects():
    document = {
        "Controls": [],
        "Curves": [
            {
                "$type": "FanControl.Curves.GraphCurve, FanControl",
                "Name": "list style",
                "Points": [{"X": 40, "Y": 30}, {"X": 70, "Y": 90}],
                "SelectedTempSource": "/lpc/it8688e/temperature/0",
            }
        ],
    }
    result = FanControlImporter().convert(document)
    curve = result.config.curves[0]
    assert [(p.temperature, p.percent) for p in curve.points] == [(40.0, 30.0), (70.0, 90.0)]


def test_curve_referenced_by_name_instead_of_guid():
    document = {
        "Controls": [
            {
                "Identifier": "/lpc/nct6798d/control/1",
                "Name": "CPU",
                "SelectedCurveName": "Old style curve",
            }
        ],
        "Curves": [
            {
                "$type": "FanControl.Curves.GraphCurve, FanControl",
                "Name": "Old style curve",
                "Points": {"30": 20, "80": 100},
                "SelectedTempSource": "/amdcpu/0/temperature/2",
            }
        ],
    }
    result = FanControlImporter().convert(document)
    assert result.config.controls[0].curve_id == result.config.curves[0].id


def test_unknown_fields_and_extra_nesting_do_not_break_the_import():
    document = {"Whatever": {"Deeply": {"Nested": SAMPLE["Main"]}}, "SomeNewKey": 42}
    result = FanControlImporter().convert(document)
    assert len(result.config.controls) == 3


def test_a_file_that_is_not_a_fancontrol_config_is_rejected():
    with pytest.raises(ValueError):
        FanControlImporter().convert({"hello": "world"})


def test_broken_json_is_rejected_with_a_clear_message():
    with pytest.raises(ValueError, match="valid JSON"):
        FanControlImporter().loads("{not json")


def test_mix_function_names_as_well_as_numbers():
    document = {
        "Controls": [{"Identifier": "/lpc/nct6798d/control/1", "SelectedCurveId": "m"}],
        "Curves": [
            {"$type": "FlatCurve", "Id": "a", "Name": "a", "Value": 30},
            {"$type": "MixCurve", "Id": "m", "Name": "m", "SelectedCurves": ["a"],
             "MixFunction": "Average"},
        ],
    }
    result = FanControlImporter().convert(document)
    mix = next(c for c in result.config.curves if isinstance(c, MixCurve))
    assert mix.function == "average"


def test_manual_controls_import_without_a_curve():
    document = {
        "Controls": [
            {
                "Identifier": "/lpc/nct6798d/control/3",
                "Name": "Manual fan",
                "ManualControl": True,
                "ManualValue": 65,
                "SelectedCurveId": "unused",
            }
        ],
        "Curves": [{"$type": "FlatCurve", "Id": "unused", "Name": "x", "Value": 10}],
    }
    result = FanControlImporter().convert(document)
    control = result.config.controls[0]
    assert control.curve_id == ""
    assert control.manual_percent == 65


def test_trigger_and_linear_curves_round_trip():
    document = {
        "Controls": [],
        "Curves": [
            {
                "$type": "FanControl.Curves.TriggerCurve, FanControl",
                "Name": "trig", "SelectedTempSource": "/amdcpu/0/temperature/2",
                "IdleTemperature": 45, "LoadTemperature": 65,
                "IdleFanSpeed": 20, "LoadFanSpeed": 80,
            },
            {
                "$type": "FanControl.Curves.LinearCurve, FanControl",
                "Name": "lin", "SelectedTempSource": "/amdcpu/0/temperature/2",
                "MinimumTemperature": 35, "MaximumTemperature": 75,
                "MinimumSpeed": 15, "MaximumSpeed": 95,
            },
        ],
    }
    result = FanControlImporter().convert(document)
    trig = next(c for c in result.config.curves if isinstance(c, TriggerCurve))
    lin = next(c for c in result.config.curves if isinstance(c, LinearCurve))
    assert (trig.idle_temperature, trig.load_temperature) == (45, 65)
    assert (lin.min_temperature, lin.max_percent) == (35, 95)


def test_identifier_parsing():
    parsed = parse_identifier("/lpc/nct6798d/temperature/2")
    assert (parsed.hardware, parsed.device, parsed.kind, parsed.index) == (
        "lpc", "nct6798d", "temperature", 2,
    )
    parsed = parse_identifier("win:/nvidiagpu/0/control/0")
    assert (parsed.hardware, parsed.kind, parsed.index) == ("nvidiagpu", "control", 0)
