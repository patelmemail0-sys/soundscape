import pytest

from soundscape.config import Speaker
from soundscape.engine import EngineState
from soundscape.errors import AudioError, ConfigError
from soundscape.session import Session
from tests.conftest import MAC_A, MAC_B, make_config


def test_up_pipewire_engine_builds_everything(session, world, runner):
    state = session.up()
    assert state.engine == "pipewire" and state.with_delays
    assert world.devices[MAC_A]["connected"] and world.devices[MAC_B]["connected"]
    assert "soundscape" in world.sinks and world.default_sink == "soundscape"
    assert state.previous_default_sink == "alsa_output.pci-0000_00_1f.3.analog-stereo"
    assert [o.sink for o in state.outputs] == [world.bt_sink_name(MAC_A), world.bt_sink_name(MAC_B)]
    assert set(world.delays) == {"soundscape_left_delay", "soundscape_right_delay"}
    assert session.paths.state_file.exists() and session.paths.config_file.exists()
    conf = session.paths.engine_conf.read_text()
    assert f'target.object = "{world.bt_sink_name(MAC_A)}"' in conf
    env = [p for p in runner.procs if p.argv[0] == "pipewire"][0].kwargs["env"]
    assert env["PIPEWIRE_CONFIG_DIR"] == str(session.paths.state_dir)
    assert any("ready" in m for m in session.logs)


def test_up_then_down_restores_default(session, world):
    session.up()
    assert session.down()
    assert "soundscape" not in world.sinks and world.default_sink.startswith("alsa_output")
    assert session.state() is None and not session.paths.engine_conf.exists()
    assert not world.live_pids
    assert session.down() is False


def test_up_twice_rebuilds_cleanly(session, world):
    first = session.up()
    second = session.up()
    assert first.pid != second.pid and len(world.live_pids) == 1
    assert session.up(rebuild=False).pid == second.pid  # already up, no new process


def test_up_requires_two_speakers(runner, world, paths):
    cfg = make_config()
    cfg.speakers = cfg.speakers[:1]
    s = Session(runner=runner, paths=paths, config=cfg, sleep=lambda _s: None)
    with pytest.raises(ConfigError):
        s.up()


def test_no_sink_after_connect_gives_profile_hint(session, world):
    world.no_sink_for.add(MAC_B)
    with pytest.raises(AudioError) as exc:
        session.up()
    assert "a2dp" in exc.value.hint.lower()


def test_engine_falls_back_without_delay_filters(session, world):
    world.engine_breaks_with_delays = True
    state = session.up()
    assert state.with_delays is False and "soundscape" in world.sinks
    assert "filter-chain" not in session.paths.engine_conf.read_text()
    with pytest.raises(AudioError) as exc:
        session.set_offset("left", 5)
    assert "PipeWire" in exc.value.hint


def test_live_offset_updates_delay_filters_and_config(session, world):
    session.up()
    sp = session.set_offset("right", 12.5)
    assert sp.address == MAC_B
    assert world.delays["soundscape_right_delay"] == pytest.approx(0.0125)
    assert world.delays["soundscape_left_delay"] == 0.0
    # negative offset shifts the other speaker instead
    session.set_offset("left", -4)
    assert world.delays["soundscape_left_delay"] == 0.0
    assert world.delays["soundscape_right_delay"] == pytest.approx(0.0165)
    session.set_offset("right", 2, relative=True)
    assert world.delays["soundscape_right_delay"] == pytest.approx(0.0185)
    saved = session.state()
    assert saved.output_for(MAC_B).offset_ms == pytest.approx(18.5)
    cfg_offsets = {s.position: s.offset_ms for s in session.config.speakers}
    assert cfg_offsets == {"left": 0.0, "right": pytest.approx(18.5)}


def test_offset_when_down_only_saves(session):
    session.set_offset("left", 7)
    assert session.config.speakers[0].offset_ms == 7
    assert session.state() is None


def test_apply_measured_gap(session, world):
    session.up()
    left, right = session.config.speakers
    session.apply_measured_gap(early=left, late=right, gap_ms=9.0)
    assert world.delays["soundscape_left_delay"] == pytest.approx(0.009)


def test_volume_and_mute(session, world):
    session.up()
    assert session.set_volume("all", 35) == "soundscape"
    assert world.sinks["soundscape"]["volume"] == 35
    assert session.set_volume("kit", 80) == world.bt_sink_name(MAC_A)
    assert session.config.speakers[0].volume == 80
    session.set_mute("right", True)
    assert world.sinks[world.bt_sink_name(MAC_B)]["mute"]
    session.set_mute("all", True)
    assert world.sinks["soundscape"]["mute"]
    with pytest.raises(ConfigError):
        session.set_volume("nope", 1)


def test_saved_volume_applied_on_up(session, world):
    session.config.speakers[1].volume = 55
    session.up()
    assert world.sinks[world.bt_sink_name(MAC_B)]["volume"] == 55


def test_up_runs_speakers_at_full_level_unless_trimmed(session, world):
    """A speaker left at 60% by a previous session must not stay limited."""
    world.two_speakers(connected=True)
    world.sinks[world.bt_sink_name(MAC_A)]["volume"] = 60
    session.up()
    assert world.sinks[world.bt_sink_name(MAC_A)]["volume"] == 100
    assert world.sinks[world.bt_sink_name(MAC_B)]["volume"] == 100
    assert any("full level" in m and "60% -> 100%" in m for m in session.logs)


def test_status_reports_everything(session, world):
    before = session.status()
    assert before["up"] is False and before["speakers"][0]["connected"] is False
    session.up()
    st = session.status()
    assert st["up"] and st["engine"] == "pipewire" and st["is_default"] and st["manual_delays"]
    assert st["volume"] == 100 and st["muted"] is False
    left = st["speakers"][0]
    assert left["connected"] and left["paired"] and left["sink"] == world.bt_sink_name(MAC_A)
    assert st["server"]["name"].startswith("PulseAudio (on PipeWire")


def test_status_survives_broken_bluetooth(session, runner):
    runner.on(("busctl",), (1, "", "Failed to connect to bus"))
    st = session.status()
    assert "bluetooth_error" in st and st["speakers"][0]["connected"] is False


def test_ensure_up_rebuilds_when_speaker_drops(session, world):
    state = session.up()
    assert session.ensure_up().pid == state.pid
    # speaker B drops: its sink disappears
    world.devices[MAC_B]["connected"] = False
    world._drop_bt_sink(MAC_B)
    new = session.ensure_up()
    assert new.pid != state.pid and world.devices[MAC_B]["connected"]
    assert any("lost" in m for m in session.logs)


def test_ensure_up_rebuilds_when_speaker_returns_without_its_delay_filter(session, world):
    """PipeWire destroys the delay filter with the speaker (node.dont-reconnect); when the
    speaker reconnects on its own, the engine must notice the missing filter and rebuild."""
    state = session.up()
    world._drop_bt_sink(MAC_B)
    world._ensure_bt_sink(MAC_B)  # speaker came back by itself, sink present again
    assert session.pulse.sink(world.bt_sink_name(MAC_B)) is not None
    assert "soundscape_right_delay" not in world.pw_nodes
    new = session.ensure_up()
    assert new.pid != state.pid and "soundscape_right_delay" in world.pw_nodes
    assert any("delay filter gone" in m or "sink re-created" in m for m in session.logs)
    assert session.ensure_up().pid == new.pid  # healthy now


def test_ensure_up_pulse_rebuilds_on_recreated_sink(pulse_session, pulse_world):
    state = pulse_session.up()
    assert pulse_session.ensure_up().modules == state.modules
    pulse_world._drop_bt_sink(MAC_A)
    pulse_world._ensure_bt_sink(MAC_A)  # new index
    new = pulse_session.ensure_up()
    assert new.modules != state.modules
    assert any("re-created" in m for m in pulse_session.logs)


def test_scan_add_remove(session, world):
    world.add_device("AA:BB:CC:DD:EE:03", "Bedroom", rssi=-40)
    found = session.scan(2)
    assert [d.address for d in found][:2] == [MAC_A, MAC_B]  # paired first
    assert all(d.is_audio_sink for d in found)
    sp = session.add_speaker("AA:BB:CC:DD:EE:03")
    assert sp.position == "speaker3" and sp.name == "Bedroom" and world.devices["AA:BB:CC:DD:EE:03"]["connected"]
    removed = session.remove_speaker("speaker3", forget=True)
    assert removed.address == "AA:BB:CC:DD:EE:03" and "AA:BB:CC:DD:EE:03" not in world.devices
    assert len(session.config.speakers) == 2


def test_add_speaker_keeps_numbering_after_legacy_names(runner, world, paths):
    cfg = make_config()
    cfg.speakers = [Speaker(f"AA:BB:CC:DD:EE:0{i}", f"s{i}", p) for i, p in enumerate(("left", "right", "third", "fourth"), 1)]
    s = Session(runner=runner, paths=paths, config=cfg, sleep=lambda _s: None)
    sp = s.add_speaker("AA:BB:CC:DD:EE:09", connect=False)
    assert sp.position == "speaker5"
    custom = s.add_speaker("AA:BB:CC:DD:EE:08", position="Back Porch", connect=False)
    assert custom.position == "back_porch" and s.config.find("back porch") is custom


def test_connect_speakers_fills_in_names(runner, world, paths):
    world.two_speakers()
    cfg = make_config()
    for sp in cfg.speakers:
        sp.name = ""
    s = Session(runner=runner, paths=paths, config=cfg, sleep=lambda _s: None)
    s.connect_speakers()
    assert [sp.name for sp in cfg.speakers] == ["Kitchen", "Living Room"]


def test_connect_speakers_without_config(runner, world, paths):
    s = Session(runner=runner, paths=paths, config=make_config(speakers=[]), sleep=lambda _s: None)
    with pytest.raises(ConfigError):
        s.connect_speakers()


def test_engine_state_roundtrip(tmp_path):
    st = EngineState(engine="pulse", sink_name="soundscape", modules=[3])
    st.save(tmp_path / "s.json")
    loaded = EngineState.load(tmp_path / "s.json")
    assert loaded.engine == "pulse" and loaded.modules == [3]
    (tmp_path / "s.json").write_text("{bad")
    assert EngineState.load(tmp_path / "s.json") is None
    assert EngineState.load(tmp_path / "missing.json") is None


# ---------------------------------------------------------------- PulseAudio engine

def test_pulse_engine_up_down(pulse_session, pulse_world):
    state = pulse_session.up()
    assert state.engine == "pulse" and state.modules
    name, args = pulse_world.modules[state.modules[0]]
    assert name == "module-combine-sink"
    assert f"slaves={pulse_world.bt_sink_name(MAC_A)},{pulse_world.bt_sink_name(MAC_B)}" in args
    assert "resample_method=soxr-vhq" in args and "sink_name=soundscape" in args
    assert pulse_world.default_sink == "soundscape"
    pulse_session.down()
    assert not pulse_world.modules and "soundscape" not in pulse_world.sinks
    assert pulse_world.default_sink.startswith("alsa_output")


def test_pulse_engine_resampler_fallback(pulse_session, pulse_world):
    pulse_world.no_soxr = True
    state = pulse_session.up()
    assert "resample_method=speex-float-10" in pulse_world.modules[state.modules[0]][1]
    assert any("soxr-vhq unavailable" in m for m in pulse_session.logs)


def test_pulse_engine_offsets_become_port_latency_leads(pulse_session, pulse_world):
    pulse_session.up()
    pulse_session.set_offset("left", 15)  # left should play 15 ms later -> right gets a 15 ms lead
    assert pulse_world.port_offsets[("bluez_card.AA_BB_CC_DD_EE_02", "speaker-output")] == 15000
    assert pulse_world.port_offsets[("bluez_card.AA_BB_CC_DD_EE_01", "speaker-output")] == 0
    pulse_session.down()
    assert pulse_world.port_offsets[("bluez_card.AA_BB_CC_DD_EE_02", "speaker-output")] == 0


def test_pulse_engine_cleans_leftover_modules(pulse_session, pulse_world):
    pulse_world.modules[9] = ("module-combine-sink", "sink_name=soundscape slaves=a,b")
    pulse_world.add_sink("soundscape", "stale", {}, ports=())
    pulse_session.up()
    assert 9 not in pulse_world.modules


def test_pulse_engine_stereo_split_warns(pulse_session):
    pulse_session.config.stereo_split = True
    pulse_session.up()
    assert any("stereo split" in m for m in pulse_session.logs)


def test_auto_engine_choice_falls_back_to_pulse_without_pipewire_tools(runner, world, paths):
    world.two_speakers()
    runner.tools.discard("pw-dump")
    s = Session(runner=runner, paths=paths, config=make_config(), sleep=lambda _s: None)
    assert s.up().engine == "pulse"


def test_forced_engine_from_config(runner, world, paths):
    world.two_speakers()
    s = Session(runner=runner, paths=paths, config=make_config(engine="pulse"), sleep=lambda _s: None)
    assert s.up().engine == "pulse"
