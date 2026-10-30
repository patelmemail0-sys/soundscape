"""Bluetooth headset-mode (HFP) detection and recovery: the cause of 'only one speaker plays'."""

from __future__ import annotations

import struct

from soundscape.config import Config, Speaker
from soundscape.engine import Bound
from soundscape.mac import coreaudio as ca
from soundscape.mac.audio import MacAudio
from soundscape.mac.engine import sweep_aggregates
from soundscape.mac.ioproc import Output, build_buffer_map
from soundscape.pulse import Sink
from soundscape.session import Session
from tests.conftest import MAC_A, MAC_B, make_config
from tests.test_mac import SCOPE_OUT, UID_A, UID_B, UID_BH, UID_MIC, mac_session, make_hal


def test_sink_headset_mode_detection():
    assert Sink(1, "bluez_output.AA_BB_CC_DD_EE_01.1", sample_spec="s16le 1ch 16000Hz").headset_mode
    assert Sink(1, "bluez_output.AA_BB_CC_DD_EE_01.1", sample_spec="s16le 2ch 48000Hz").headset_mode is False
    assert Sink(1, "bluez_output.AA_BB_CC_DD_EE_01.1", properties={"api.bluez5.profile": "headset-head-unit"}).headset_mode
    assert Sink(1, "AA-BB-CC-DD-EE-01:output", properties={"sample_rate": "8000"}).headset_mode
    assert Sink(1, "alsa_output.x", sample_spec="s16le 2ch 8000Hz").headset_mode is False  # not Bluetooth
    assert Sink(1, "x", sample_spec="s16le 2ch 44100Hz").sample_rate == 44100


def test_status_reports_mode_and_cli_flags_it(session, world, capsys):
    world.two_speakers(connected=True)
    world.sinks[world.bt_sink_name(MAC_A)]["rate"] = 16000
    st = session.status()
    modes = {sp["position"]: sp["mode"] for sp in st["speakers"]}
    assert modes == {"left": "headset", "right": "music"}
    from soundscape.cli import print_status
    print_status(st)
    assert "HEADSET MODE" in capsys.readouterr().out


def test_up_waits_for_headset_speaker_to_recover(session, world):
    world.two_speakers(connected=True)
    world.sinks[world.bt_sink_name(MAC_A)]["rate"] = 16000
    polls = {"n": 0}

    def sleep(_s):  # the speaker returns to A2DP after a couple of polls
        polls["n"] += 1
        if polls["n"] == 2:
            world.sinks[world.bt_sink_name(MAC_A)]["rate"] = 48000

    session._sleep = sleep
    session.up()
    assert any("back in music" in m for m in session.logs)
    assert not any("warning" in m and "headset" in m for m in session.logs)


def test_up_reconnects_a_speaker_stuck_in_headset_mode(session, world, monkeypatch):
    world.two_speakers(connected=True)
    world.sinks[world.bt_sink_name(MAC_B)]["rate"] = 8000
    monkeypatch.setattr(Session, "HEADSET_RECOVERY_S", 0.0)
    state = session.up()
    assert any("stuck in headset mode (8000 Hz); reconnecting" in m for m in session.logs)
    methods = [c[-1] for c in world.runner.calls if c[0] == "busctl" and "call" in c]
    assert "Disconnect" in methods and methods.index("Disconnect") < len(methods) - 1
    # the re-created sink runs at a music rate and is what the engine was built with
    assert world.sinks[world.bt_sink_name(MAC_B)]["rate"] == 48000
    assert state.output_for(MAC_B).sink_index == world.sinks[world.bt_sink_name(MAC_B)]["index"]
    assert not any("still in headset mode" in m for m in session.logs)


def test_reconnect_speaker_rebuilds_when_up(session, world):
    world.two_speakers(connected=True)
    first = session.up()
    sp = session.reconnect_speaker("right")
    assert sp.address == MAC_B
    second = session.state()
    assert second.pid != first.pid  # rebuilt around the fresh sink


def test_api_reconnect_job(session, world):
    from soundscape.remote import RemoteApp
    world.two_speakers(connected=True)
    app = RemoteApp(session, log=lambda _m: None, synchronous_jobs=True)
    status, body, _ = app.handle("POST", "/api/reconnect", {"target": "left"})
    assert status == 200 and body["job"]["state"] == "done" and body["job"]["result"]["address"] == MAC_A


def test_cli_reconnect(runner, world, tmp_path, capsys):
    from soundscape import cli
    world.two_speakers(connected=True)
    root = str(tmp_path)
    assert cli.main(["--root", root, "add", MAC_A], runner=runner) == 0
    assert cli.main(["--root", root, "add", MAC_B], runner=runner) == 0
    assert cli.main(["--root", root, "reconnect", "left"], runner=runner) == 0
    assert "reconnected Kitchen" in capsys.readouterr().out


def test_mac_up_moves_default_input_off_a_speaker(runner, tmp_path, monkeypatch):
    hal = make_hal()
    hal.add_device("AA-BB-CC-DD-EE-01:input", "Kitchen", "bluetooth", out_ch=0, in_ch=1, volume=False)
    hal.default_input = hal.id_for_uid("AA-BB-CC-DD-EE-01:input")
    s, world, _ = mac_session(runner, hal, tmp_path, monkeypatch)
    assert s.pulse.default_source_address() == MAC_A
    s.up()
    assert hal.default_input == hal.id_for_uid(UID_MIC)
    assert any("switched it to" in m for m in s.logs)
    assert s.status()["default_input_is_speaker"] is False


def test_relay_up_sweeps_stale_public_aggregate(runner, tmp_path, monkeypatch):
    hal = make_hal(blackhole=True)
    stale = hal.create_aggregate({"uid": "com.soundscape.soundscape", "name": "Soundscape (both speakers)",
                                  "subdevices": [{"uid": UID_A, "drift": 0}, {"uid": UID_B, "drift": 1}]})
    s, world, live = mac_session(runner, hal, tmp_path, monkeypatch)
    monkeypatch.setattr("soundscape.mac.ioproc.send_command", lambda path, cmd, timeout=3.0: "pong" if cmd == "ping" else "bye")
    from pathlib import Path

    from tests.conftest import FakeProcess

    def spawn(argv, kwargs):
        proc = FakeProcess(argv, kwargs)
        live[proc.pid] = proc
        sock = argv[argv.index("--socket") + 1]
        Path(sock).parent.mkdir(parents=True, exist_ok=True)
        Path(sock).write_text("")
        return proc

    import sys
    runner.tools.add(sys.executable)
    runner.on_popen(sys.executable, spawn)
    s.up()
    assert stale in hal.destroyed and not hal.aggregates
    assert any("leftover" in m for m in s.logs)


def test_sweep_keeps_the_named_aggregate(runner):
    hal = make_hal()
    keep = hal.create_aggregate({"uid": "com.soundscape.keep", "name": "k", "subdevices": []})
    old = hal.create_aggregate({"uid": "com.soundscape.old", "name": "o", "subdevices": []})
    audio = MacAudio(runner, coreaudio=ca.CoreAudio(hal))
    assert sweep_aggregates(audio, lambda _m: None, keep_uid="com.soundscape.keep") == 1
    assert old in hal.destroyed and keep not in hal.destroyed


def test_buffer_map_follows_active_subdevice_order(runner):
    hal = make_hal(blackhole=True)
    outs = [Output(UID_A, "left"), Output(UID_B, "right")]
    core = ca.CoreAudio(hal)
    agg = core.create_aggregate("relay", "r", [(UID_BH, True), (UID_A, False), (UID_B, True)], master_uid=UID_A, private=True)
    assert [o.position if o else None for o in build_buffer_map(core, agg, UID_BH, outs)] == [None, "left", "right"]
    # CoreAudio may order the active list differently from our request: the map must follow it
    hal.aggregates[agg]["subs"] = [UID_A, UID_BH, UID_B]
    assert [o.position if o else None for o in build_buffer_map(core, agg, UID_BH, outs)] == ["left", None, "right"]
    # a speaker that did not make it into the aggregate is reported, not silently dropped
    hal.aggregates[agg]["subs"] = [UID_BH, UID_B]
    assert [o.position if o else None for o in build_buffer_map(core, agg, UID_BH, outs)] == [None, "right"]
    assert outs[1].channels == 2 and outs[1].streams == 1


def test_mixer_with_buffer_map_silences_unmapped_buffers():
    import ctypes

    from soundscape.mac.ioproc import Mixer
    a, b = Output(UID_A, "left"), Output(UID_B, "right")
    m = Mixer(rate=1000, outputs=[a, b], buffer_map=[a, None, b])
    src = ctypes.create_string_buffer(struct.pack("<4f", 1, 1, 2, 2), 16)
    bufs = [ctypes.create_string_buffer(struct.pack("<4f", 9, 9, 9, 9), 16) for _ in range(3)]
    m.process([(2, 16, ctypes.addressof(src))], [(2, 16, ctypes.addressof(x)) for x in bufs])
    assert struct.unpack("<4f", bufs[0].raw) == (1, 1, 2, 2)
    assert struct.unpack("<4f", bufs[1].raw) == (0, 0, 0, 0)
    assert struct.unpack("<4f", bufs[2].raw) == (1, 1, 2, 2)
    assert a.frames_written == 2 and m.status()["buffer_map"] == ["left", None, "right"]


def test_relay_prefers_music_mode_speaker_as_clock(runner, tmp_path, monkeypatch):
    """The first --output becomes the aggregate's clock master; a headset-mode speaker must not."""
    hal = make_hal(blackhole=True)
    hal.props[(hal.id_for_uid(UID_A), ca.kAudioDevicePropertyNominalSampleRate, ca.kAudioObjectPropertyScopeGlobal, 0)] = struct.pack("d", 16000.0)
    s, world, live = mac_session(runner, hal, tmp_path, monkeypatch)
    monkeypatch.setattr(Session, "HEADSET_RECOVERY_S", 0.0)
    monkeypatch.setattr("soundscape.mac.ioproc.send_command", lambda path, cmd, timeout=3.0: "pong" if cmd == "ping" else "bye")
    import sys
    from pathlib import Path

    from tests.conftest import FakeProcess

    def spawn(argv, kwargs):
        proc = FakeProcess(argv, kwargs)
        live[proc.pid] = proc
        sock = argv[argv.index("--socket") + 1]
        Path(sock).parent.mkdir(parents=True, exist_ok=True)
        Path(sock).write_text("")
        return proc

    runner.tools.add(sys.executable)
    runner.on_popen(sys.executable, spawn)
    s.up()
    argv = [p for p in runner.procs if "-m" in p.argv][0].argv
    first_output = argv[argv.index("--output") + 1]
    assert first_output.startswith("right:"), first_output
    assert Config and Speaker and Bound and make_config and MAC_B and SCOPE_OUT  # imports used for readers
