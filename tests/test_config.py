import json

import pytest

from soundscape.config import Config, Speaker, mac_underscored, normalize_mac
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
