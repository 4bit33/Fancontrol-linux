import pytest

from fancontrol.core.curves import CurveEvaluator, EvalContext, interpolate
from fancontrol.core.models import (
    Config,
    Control,
    CurvePoint,
    FlatCurve,
    GraphCurve,
    LinearCurve,
    MixCurve,
    SyncCurve,
    TargetCurve,
    TriggerCurve,
    validate,
)


def graph(**kwargs):
    defaults = dict(
        id="c1",
        name="graph",
        sensor_id="s1",
        points=[CurvePoint(30, 20), CurvePoint(60, 60), CurvePoint(80, 100)],
    )
    defaults.update(kwargs)
    return GraphCurve(**defaults)


def test_interpolate_is_flat_outside_the_point_range():
    points = [(30.0, 20.0), (60.0, 60.0)]
    assert interpolate(points, 10) == 20
    assert interpolate(points, 90) == 60
    assert interpolate(points, 45) == pytest.approx(40)


def test_graph_curve_interpolates_between_points():
    config = Config(curves=[graph()])
    evaluator = CurveEvaluator(config)
    values, errors = evaluator.evaluate_all(EvalContext(temperatures={"s1": 45.0}, dt=1.0))
    assert errors == {}
    assert values["c1"] == pytest.approx(40)


def test_missing_sensor_is_reported_not_guessed():
    config = Config(curves=[graph()])
    evaluator = CurveEvaluator(config)
    values, errors = evaluator.evaluate_all(EvalContext(temperatures={"s1": None}, dt=1.0))
    assert "c1" not in values
    assert "unavailable" in errors["c1"]


def test_hysteresis_holds_the_value_while_the_temperature_drops():
    config = Config(curves=[graph(hysteresis_down=5.0, hysteresis_up=0.0)])
    evaluator = CurveEvaluator(config)
    ctx = EvalContext(temperatures={"s1": 60.0}, dt=1.0)
    assert evaluator.evaluate_all(ctx)[0]["c1"] == pytest.approx(60)

    # A small drop is ignored ...
    ctx = EvalContext(temperatures={"s1": 57.0}, dt=1.0)
    assert evaluator.evaluate_all(ctx)[0]["c1"] == pytest.approx(60)
    # ... a big one is not: 54 C sits 24/30 of the way from 30 C to 60 C,
    # so the curve gives 20 + 24/30 * 40 = 52%.
    ctx = EvalContext(temperatures={"s1": 54.0}, dt=1.0)
    assert evaluator.evaluate_all(ctx)[0]["c1"] == pytest.approx(52)


def test_hysteresis_on_drop_only_lets_the_fans_speed_up_immediately():
    config = Config(curves=[graph(hysteresis_down=5.0, hysteresis_up=0.0)])
    evaluator = CurveEvaluator(config)
    evaluator.evaluate_all(EvalContext(temperatures={"s1": 45.0}, dt=1.0))
    # A 2 degree rise is smaller than the hysteresis but must still get through,
    # otherwise the fans lag behind a sudden load.
    values, _ = evaluator.evaluate_all(EvalContext(temperatures={"s1": 47.0}, dt=1.0))
    assert values["c1"] == pytest.approx(20 + 17 / 30 * 40)


def test_hysteresis_applies_to_a_rise_too_when_it_is_set():
    config = Config(curves=[graph(hysteresis_up=5.0, hysteresis_down=5.0)])
    evaluator = CurveEvaluator(config)
    first = evaluator.evaluate_all(EvalContext(temperatures={"s1": 45.0}, dt=1.0))[0]["c1"]
    values, _ = evaluator.evaluate_all(EvalContext(temperatures={"s1": 47.0}, dt=1.0))
    assert values["c1"] == pytest.approx(first)


def test_trigger_curve_latches_between_its_thresholds():
    curve = TriggerCurve(
        id="t", name="trigger", sensor_id="s1",
        idle_temperature=50, load_temperature=60,
        idle_percent=20, load_percent=100,
    )
    evaluator = CurveEvaluator(Config(curves=[curve]))

    def run(temp):
        return evaluator.evaluate_all(EvalContext(temperatures={"s1": temp}, dt=1.0))[0]["t"]

    assert run(40) == 20
    assert run(55) == 20          # inside the window, stays idle
    assert run(61) == 100         # crosses the load threshold
    assert run(55) == 100         # inside the window, stays loaded
    assert run(49) == 20          # falls below the idle threshold


def test_target_curve_walks_towards_the_target():
    curve = TargetCurve(
        id="t", name="target", sensor_id="s1",
        target_temperature=60, idle_percent=20, load_percent=100,
        step_up=10, step_down=5, deadband=1,
    )
    evaluator = CurveEvaluator(Config(curves=[curve]))

    def run(temp, dt=1.0):
        return evaluator.evaluate_all(EvalContext(temperatures={"s1": temp}, dt=dt))[0]["t"]

    assert run(70) == pytest.approx(30)   # 20 + 10 * 1s
    assert run(70) == pytest.approx(40)
    assert run(60) == pytest.approx(40)   # inside the dead band, holds
    assert run(50) == pytest.approx(35)   # 40 - 5 * 1s


def test_mix_curve_applies_its_function():
    curves = [
        FlatCurve(id="a", name="a", percent=30),
        FlatCurve(id="b", name="b", percent=70),
        MixCurve(id="m", name="mix", curve_ids=["a", "b"], function="max"),
    ]
    evaluator = CurveEvaluator(Config(curves=curves))
    assert evaluator.evaluate_all(EvalContext(temperatures={}, dt=1.0))[0]["m"] == 70

    curves[2].function = "average"
    evaluator.reconfigure(Config(curves=curves))
    assert evaluator.evaluate_all(EvalContext(temperatures={}, dt=1.0))[0]["m"] == 50


def test_linear_curve_clamps_at_both_ends():
    curve = LinearCurve(
        id="l", name="linear", sensor_id="s1",
        min_temperature=40, max_temperature=80, min_percent=25, max_percent=100,
    )
    evaluator = CurveEvaluator(Config(curves=[curve]))

    def run(temp):
        return evaluator.evaluate_all(EvalContext(temperatures={"s1": temp}, dt=1.0))[0]["l"]

    assert run(20) == 25
    assert run(60) == pytest.approx(62.5)
    assert run(100) == 100


def test_sync_curve_follows_a_control():
    config = Config(
        curves=[SyncCurve(id="s", name="sync", control_id="ctl", offset_percent=5)],
        controls=[Control(id="ctl", name="cpu")],
    )
    evaluator = CurveEvaluator(config)
    ctx = EvalContext(temperatures={}, dt=1.0, control_values={"ctl": 40.0})
    assert evaluator.evaluate_all(ctx)[0]["s"] == pytest.approx(45)


def test_mix_curves_are_evaluated_after_their_inputs():
    curves = [
        MixCurve(id="m", name="mix", curve_ids=["a"], function="max"),
        FlatCurve(id="a", name="a", percent=42),
    ]
    evaluator = CurveEvaluator(Config(curves=curves))
    values, errors = evaluator.evaluate_all(EvalContext(temperatures={}, dt=1.0))
    assert errors == {}
    assert values["m"] == 42


def test_validate_rejects_dependency_loops():
    config = Config(
        curves=[
            MixCurve(id="a", name="a", curve_ids=["b"]),
            MixCurve(id="b", name="b", curve_ids=["a"]),
        ]
    )
    problems = validate(config)
    assert any("loop" in p for p in problems)


def test_validate_reports_dangling_references():
    config = Config(
        curves=[graph()],
        controls=[Control(id="ctl", name="cpu", curve_id="nope")],
    )
    problems = validate(config)
    assert any("unknown curve" in p for p in problems)


def test_response_time_smooths_a_step_change():
    config = Config(curves=[graph(response_time_up=10.0, response_time_down=10.0)])
    evaluator = CurveEvaluator(config)
    evaluator.evaluate_all(EvalContext(temperatures={"s1": 30.0}, dt=1.0))
    values, _ = evaluator.evaluate_all(EvalContext(temperatures={"s1": 80.0}, dt=1.0))
    # One second into a ten second filter, the curve has only moved a tenth of
    # the way, so it must be far below the 100% the raw reading would give.
    assert values["c1"] < 40


def test_hysteresis_can_differ_in_each_direction():
    """The usual setting: answer a rise at once, come down only after a while."""

    config = Config(curves=[graph(hysteresis_up=0.0, hysteresis_down=6.0)])
    evaluator = CurveEvaluator(config)

    def run(temp):
        return evaluator.evaluate_all(EvalContext(temperatures={"s1": temp}, dt=1.0))[0]["c1"]

    # 62 C sits on the 60..80 segment, which runs from 60% to 100%.
    assert run(60) == pytest.approx(60)
    assert run(62) == pytest.approx(64)                  # a rise gets through at once
    assert run(58) == pytest.approx(64)                  # a 4 degree drop does not
    assert run(55) == pytest.approx(20 + 25 / 30 * 40)   # a 7 degree drop does


def test_response_time_can_differ_in_each_direction():
    config = Config(curves=[graph(response_time_up=0.0, response_time_down=20.0)])
    evaluator = CurveEvaluator(config)
    evaluator.evaluate_all(EvalContext(temperatures={"s1": 80.0}, dt=1.0))
    # Rising is not smoothed at all ...
    assert evaluator.evaluate_all(
        EvalContext(temperatures={"s1": 80.0}, dt=1.0)
    )[0]["c1"] == pytest.approx(100)
    # ... but the way back down is.
    values, _ = evaluator.evaluate_all(EvalContext(temperatures={"s1": 30.0}, dt=1.0))
    assert values["c1"] > 90


def test_ignore_hysteresis_at_limits_lets_the_ends_through():
    """Past the last point the speed is flat, so holding it back only delays."""

    config = Config(curves=[graph(hysteresis_down=10.0, ignore_hysteresis_at_limits=True)])
    evaluator = CurveEvaluator(config)

    def run(temp):
        return evaluator.evaluate_all(EvalContext(temperatures={"s1": temp}, dt=1.0))[0]["c1"]

    run(90)                       # above the last point at 80 C
    assert run(85) == pytest.approx(100)
    # Back inside the curve, the hysteresis applies again.
    assert run(70) == pytest.approx(80)
    assert run(66) == pytest.approx(80)
