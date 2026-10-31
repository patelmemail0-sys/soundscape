import json

import pytest

from soundscape.config import (
    Config,
    Speaker,
    acoustic_ms,
    mac_underscored,
    normalize_mac,
    parse_distance,
)
from soundscape.errors import ConfigError


def test_normalize_mac_accepts_common_spellings():
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac(" aa:bb:cc:dd:ee:ff ") == "AA:BB:CC:DD:EE:FF"
    assert mac_underscored("aa:bb:cc:dd:ee:ff") == "AA_BB_CC_DD_EE_FF"
    with pytest.raises(ConfigError):
        normalize_mac("not-a-mac")


def test_add_assigns_positions_and_rejects_duplicates():
    cfg = Config()
    cfg.add(Speaker("AA:BB:CC:DD:EE:01", "a", "left"))
    with pytest.raises(ConfigError):
        cfg.add(Speaker("AA:BB:CC:DD:EE:01", "a again", "right"))
    with pytest.raises(ConfigError):
        cfg.add(Speaker("AA:BB:CC:DD:EE:02", "b", "left"))
    cfg.add(Speaker("AA:BB:CC:DD:EE:02", "b", "right"))
    cfg.add(Speaker("AA:BB:CC:DD:EE:03", "c", "left"), replace=True)
    assert [s.address for s in cfg.speakers] == ["AA:BB:CC:DD:EE:03", "AA:BB:CC:DD:EE:02"]


def test_find_by_position_address_and_name_prefix():
    cfg = Config(speakers=[Speaker("AA:BB:CC:DD:EE:01", "Kitchen", "left"),
                           Speaker("AA:BB:CC:DD:EE:02", "Living Room", "right")])
    assert cfg.find("right").name == "Living Room"
    assert cfg.find("aa:bb:cc:dd:ee:01").name == "Kitchen"
    assert cfg.find("kit").name == "Kitchen"
    assert cfg.find("nope") is None
    with pytest.raises(ConfigError):
        cfg.require("nope")


def test_normalize_offsets_keeps_relative_delay_and_no_negatives():
    cfg = Config(speakers=[Speaker("AA:BB:CC:DD:EE:01", "a", "left", offset_ms=-7),
                           Speaker("AA:BB:CC:DD:EE:02", "b", "right", offset_ms=5)])
    cfg.normalize_offsets()
    assert [s.offset_ms for s in cfg.speakers] == [0, 12]


def test_round_trip_and_unknown_keys(tmp_path):
    cfg = Config(speakers=[Speaker("AA:BB:CC:DD:EE:01", "a", "left", offset_ms=3.5)], stereo_split=True)
    path = tmp_path / "c.json"
    cfg.save(path)
    loaded = Config.load(path)
    assert loaded == cfg
    path.write_text(json.dumps({"speakers": [], "bogus": 1}))
    with pytest.raises(ConfigError):
        Config.load(path)
    path.write_text("{not json")
    with pytest.raises(ConfigError):
        Config.load(path)
    assert Config.load(tmp_path / "missing.json") == Config()


def test_invalid_engine_and_position():
    with pytest.raises(ConfigError):
        Config(engine="jack")
    with pytest.raises(ConfigError):
        Speaker("AA:BB:CC:DD:EE:01", position="")
    with pytest.raises(ConfigError):
        Speaker("AA:BB:CC:DD:EE:01", position="3rd floor")  # must start with a letter
    assert Speaker("AA:BB:CC:DD:EE:01", position="Living Room").position == "living_room"


def test_positions_are_free_form_and_ordered():
    cfg = Config()
    for i, pos in enumerate(("patio", "right", "speaker4", "left", "kitchen"), 1):
        cfg.add(Speaker(f"AA:BB:CC:DD:EE:0{i}", pos, pos))
    assert [s.position for s in cfg.speakers] == ["left", "right", "speaker4", "kitchen", "patio"]
    assert cfg.next_position() == "speaker3"
    cfg.add(Speaker("AA:BB:CC:DD:EE:09", "x", cfg.next_position()))
    assert cfg.next_position() == "speaker5"
    assert cfg.find("Kitchen").address == "AA:BB:CC:DD:EE:05"
    assert cfg.find("living room") is None


def test_parse_distance_units_range_and_round_trip():
    assert parse_distance("1.2 m") == 1.2 and parse_distance("120cm") == 1.2 and parse_distance("3,5") == 3.5
    assert parse_distance(" 4 ft ") == pytest.approx(1.219, abs=0.001) and parse_distance("48in") == pytest.approx(1.219, abs=0.001)
    assert parse_distance(2) == 2.0 and parse_distance("") is None and parse_distance("none") is None and parse_distance(None) is None
    for bad in ("far", "-1", "250 m", "1.2 km", True):
        with pytest.raises(ConfigError):
            parse_distance(bad)
    assert acoustic_ms(1.0) == pytest.approx(2.915, abs=0.001)
    sp = Speaker(address="AA:BB:CC:DD:EE:01", position="left", distance_m="4 ft")  # type: ignore[arg-type]
    assert sp.distance_m == pytest.approx(1.219, abs=0.001) and sp.acoustic_ms == pytest.approx(3.55, abs=0.01)
    assert Speaker(address="AA:BB:CC:DD:EE:02", position="right").acoustic_ms is None
    cfg = Config(speakers=[sp])
    again = Config.from_dict(json.loads(json.dumps(cfg.to_dict())))
    assert again.speakers[0].distance_m == sp.distance_m
