import pytest

from airadio import config


def test_parse_example_file():
    cfg = config.load("config/radio.example.toml")
    assert cfg.model == "claude-sonnet-5-5"
    assert {d.name for d in cfg.dayparts} == {"reggel", "nappal", "este", "ejjel"}
    # minden óra pontosan egy napszakba esik
    for hour in range(24):
        assert sum(d.contains(hour) for d in cfg.dayparts) == 1, hour


def test_daypart_across_midnight(cfg):
    assert cfg.daypart_for(23).name == "ejjel"
    assert cfg.daypart_for(3).name == "ejjel"
    assert cfg.daypart_for(6).name == "reggel"
    assert cfg.daypart_for(12) is None


def test_invalid_hour_rejected(raw_config):
    raw_config["dayparts"][0]["start"] = 24
    with pytest.raises(config.ConfigError):
        config.parse(raw_config)


def test_unknown_effort_rejected(raw_config):
    raw_config["claude"]["effort"] = "turbo"
    with pytest.raises(config.ConfigError):
        config.parse(raw_config)
