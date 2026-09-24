

def test_hidden_cards_survive_a_round_trip():
    from fancontrol.core.models import Config, GraphCurve

    config = Config(curves=[GraphCurve(id="c", name="c", hidden=True)], hidden_sensors=["hwmon:x"])
    again = Config.from_dict(config.to_dict())
    assert again.curves[0].hidden is True
    assert again.hidden_sensors == ["hwmon:x"]
    # Older files have neither field.
    assert Config.from_dict({}).hidden_sensors == []
