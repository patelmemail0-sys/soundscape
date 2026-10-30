"""Regression tests for the issues found by review, fuzzing and the macOS platform audit."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from soundscape import engine as engine_mod
from soundscape import tones
from soundscape.config import Config, Speaker
from soundscape.errors import ConfigError
from soundscape.mac.bluetooth import parse_blueutil_json, parse_system_profiler
from soundscape.pulse import (
    PulseCtl,
    parse_cards_json,
    parse_cards_text,
    parse_sinks_json,
    parse_sinks_text,
)
from soundscape.session import Session
from tests.conftest import MAC_A, MAC_B, make_config

# -- parsers never crash on garbage -------------------------------------------


@pytest.mark.parametrize("text", ["Sink #\n\tName: x", "Sink #x\n", "Sink #1\n\tVolume: nonsense", "garbage"])
def test_sinks_text_tolerates_garbage(text):
    assert isinstance(parse_sinks_text(text), list)
    assert isinstance(parse_cards_text(text.replace("Sink", "Card")), list)


@pytest.mark.parametrize("payload", ['[1, "x", null]', '[{"index": "x", "name": "s"}]', '[{"index": 1}]',
                                     '[{"index": 1, "name": "s", "volume": 5, "ports": 3, "properties": "p"}]'])
def test_sinks_json_skips_malformed_entries(payload):
    sinks = parse_sinks_json(payload)
    assert all(s.name for s in sinks)
    assert isinstance(parse_cards_json(payload), list)


def test_sinks_json_rejects_non_list_and_pulsectl_falls_back(runner):
    with pytest.raises(ValueError):
        parse_sinks_json('{"a": 1}')
    runner.on(("pactl", "-f", "json"), '{"a": 1}')
    runner.on(("pactl", "list", "sinks"), "Sink #3\n\tName: ok\n")
    assert [s.name for s in PulseCtl(runner).sinks()] == ["ok"]


@pytest.mark.parametrize("payload", ["[]", "[5]", '{"SPBluetoothDataType": "x"}', '{"SPBluetoothDataType": [1, {"device_connected": [1, {"n": 2}]}]}',
                                     '{"SPBluetoothDataType": [{"device_connected": {"n": {"device_address": "bad"}}}]}'])
def test_mac_parsers_tolerate_garbage(payload):
    assert isinstance(parse_system_profiler(payload), dict)
    assert all(isinstance(d, dict) for d in parse_blueutil_json(payload))


# -- process helpers work without /proc --------------------------------------------

def test_cmdline_falls_back_to_ps_when_proc_is_missing(monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="python -m soundscape.mac.ioproc --socket x\n", stderr="")

    monkeypatch.setattr(os.path, "isdir", lambda p: False if p == "/proc" else os.path.exists(p))
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert engine_mod.pid_is_ours(4242, "soundscape.mac.ioproc")
    assert calls[-1][:3] == ["ps", "-o", "command="]
    assert not engine_mod.pid_is_ours(0, "x")


def test_find_pids_uses_pgrep_without_proc(monkeypatch):
    monkeypatch.setattr(os.path, "isdir", lambda p: False if p == "/proc" else os.path.exists(p))
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout=f"111\n{os.getpid()}\n", stderr=""))
    assert engine_mod.find_pids("soundscape-engine.conf") == [111]


def test_find_pids_real_proc_finds_marker():
    """A real child process whose command line carries the marker is found through /proc."""
    if not os.path.isdir("/proc"):
        pytest.skip("no /proc")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "--marker", "soundscape-selftest-marker"])
    try:
        assert proc.pid in engine_mod.find_pids("soundscape-selftest-marker")
        assert engine_mod.pid_is_ours(proc.pid, "soundscape-selftest-marker")
        assert engine_mod.kill_pid(proc.pid, "soundscape-selftest-marker", grace=2.0)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


# -- delay node names agree between config and state -----------------------------------

def test_hyphenated_position_has_matching_delay_node(runner, world, paths):
    world.two_speakers()
    cfg = make_config()
    cfg.speakers[1].position = "living-room"
    s = Session(runner=runner, paths=paths, config=cfg, sleep=lambda _s: None)
    state = s.up()
    node = state.output_for(MAC_B).delay_node
    assert node == "soundscape_living_room_delay" and node in world.delays
    s.set_offset("living-room", 4)
    assert world.delays[node] == pytest.approx(0.004)


# -- offsets are validated ---------------------------------------------------------------

def test_offset_range_is_enforced(session):
    with pytest.raises(ConfigError):
        session.set_offset("left", 5000)
    with pytest.raises(ConfigError):
        session.set_offset("left", float("nan"))
    session.set_offset("left", 900)
    with pytest.raises(ConfigError):
        session.set_offset("right", -200)  # would push left past 1000
    assert [sp.offset_ms for sp in session.config.speakers] == [900, 0]


# -- lifecycle lock -------------------------------------------------------------------------

def test_second_up_is_refused_while_lock_is_held(session):
    lock = session._lock()
    try:
        with pytest.raises(ConfigError) as exc:
            session.up()
        assert "another soundscape command" in exc.value.message
    finally:
        lock.close()
    assert session.up().engine == "pipewire"


# -- tones --------------------------------------------------------------------------------

def test_tone_slots_never_collide():
    slots = tones.tone_slots(["right", "kitchen"])
    assert slots == {"right": 1, "kitchen": 2}
    slots = tones.tone_slots(["left", "right", "third", "patio", "garage"])
    assert sorted(slots.values()) == [0, 1, 2, 3, 4]
    assert len(set(tones.tone_slots(["a", "b", "c", "d", "e", "f"]).values())) == 6


# -- CLI number handling -------------------------------------------------------------------

def test_cli_rejects_bad_numbers(runner, world, tmp_path, capsys):
    from soundscape import cli
    world.two_speakers()
    root = str(tmp_path)
    assert cli.main(["--root", root, "volume", "loud"], runner=runner) == 2
    assert "needs a number" in capsys.readouterr().err
    assert cli.main(["--root", root, "volume", "500"], runner=runner) == 2
    assert cli.main(["--root", root, "config", "set", "resample_quality", "abc"], runner=runner) == 2
    assert "expects a int" in capsys.readouterr().err
    assert cli.main(["--root", root, "config", "set", "stereo_split", "maybe"], runner=runner) == 2
    assert cli.main(["--root", root, "config", "set", "stereo_split", "off"], runner=runner) == 0


def test_record_cleans_temp_file(runner, world, tmp_path, monkeypatch):
    import tempfile

    from tests.conftest import FakeProcess
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    def factory(argv, kwargs):
        kwargs["stdout"].write(b"\x00\x01" * 100)
        proc = FakeProcess(argv, kwargs)
        proc.returncode = 0
        return proc

    runner.on_popen("parec", factory)
    samples, rate = PulseCtl(runner).record(0.01, "mic")
    assert rate == 16000 and len(samples) == 100
    assert not list(tmp_path.glob("soundscape-mic-*"))


def test_engine_state_device_id_roundtrip(tmp_path):
    from soundscape.engine import EngineState
    st = EngineState(engine="aggregate", sink_name="com.soundscape.soundscape", device_id=77)
    st.save(tmp_path / "s.json")
    assert EngineState.load(tmp_path / "s.json").device_id == 77
    assert json.loads((tmp_path / "s.json").read_text())["pid"] is None


def test_config_positions_with_hyphen_are_valid():
    assert Speaker(MAC_A, "x", "living-room").position == "living-room"
    assert Config().next_position() == "left"
