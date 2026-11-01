"""Three (and more) speakers end to end: engines, calibration, mixer, tones."""

from __future__ import annotations

import struct

import pytest

from soundscape import tones
from soundscape.config import Config, Speaker
from soundscape.mac.ioproc import Mixer, Output
from soundscape.pipewire import EnginePlan, OutputPlan, render_config
from soundscape.session import Session
from soundscape.sync import RECORD_RATE, Calibrator
from tests.conftest import MAC_A, MAC_B, FakeProcess, World

MAC_C = "AA:BB:CC:DD:EE:03"
MAC_D = "AA:BB:CC:DD:EE:04"


def three_config() -> Config:
    return Config(speakers=[Speaker(MAC_A, "Kitchen", "left"), Speaker(MAC_B, "Living Room", "right"),
                            Speaker(MAC_C, "Patio", "patio")], connect_timeout_s=1.0, sink_timeout_s=0.4)


@pytest.fixture
def trio(runner, world, paths):
    world.two_speakers()
    world.add_device(MAC_C, "Patio", paired=True, trusted=True)
    logs = []
    s = Session(runner=runner, paths=paths, log=logs.append, config=three_config(), sleep=lambda _s: None)
    s.logs = logs  # type: ignore[attr-defined]
    return s


def test_three_speakers_pipewire(trio, world):
    state = trio.up()
    assert [o.position for o in state.outputs] == ["left", "right", "patio"]
    conf = trio.paths.engine_conf.read_text()
    assert conf.count("libpipewire-module-filter-chain") == 3
    assert 'node.name = "soundscape_patio_delay"' in conf
    assert set(world.delays) == {"soundscape_left_delay", "soundscape_right_delay", "soundscape_patio_delay"}
    trio.set_offset("patio", 7)
    assert world.delays["soundscape_patio_delay"] == pytest.approx(0.007)
    trio.set_offset("left", -3)  # others get +3
    assert world.delays["soundscape_left_delay"] == 0.0
    assert world.delays["soundscape_right_delay"] == pytest.approx(0.003)
    assert world.delays["soundscape_patio_delay"] == pytest.approx(0.010)
    st = trio.status()
    assert len(st["speakers"]) == 3 and st["speakers"][2]["position"] == "patio"
    trio.down()
    assert "soundscape" not in world.sinks


def test_three_speakers_pulse(runner, pulse_world, paths):
    pulse_world.two_speakers()
    pulse_world.add_device(MAC_C, "Patio", paired=True, trusted=True)
    s = Session(runner=runner, paths=paths, config=three_config(), sleep=lambda _s: None)
    state = s.up()
    args = pulse_world.modules[state.modules[0]][1]
    assert args.count("bluez_sink") == 3
    s.set_offset("patio", 10)  # patio later -> the other two lead by 10 ms
    assert pulse_world.port_offsets[("bluez_card.AA_BB_CC_DD_EE_01", "speaker-output")] == 10000
    assert pulse_world.port_offsets[("bluez_card.AA_BB_CC_DD_EE_02", "speaker-output")] == 10000
    assert pulse_world.port_offsets[("bluez_card.AA_BB_CC_DD_EE_03", "speaker-output")] == 0


def test_stereo_split_with_three_outputs_only_splits_left_right():
    outs = [OutputPlan("left", "A", "sa"), OutputPlan("right", "B", "sb"), OutputPlan("patio", "C", "sc")]
    text = render_config(EnginePlan(outs, stereo_split=True))
    assert text.count("combine.audio.position") == 2
    assert 'node.name = "soundscape_patio_delay" } ]\n            actions = { create-stream = {  } }' in text


def synthetic(lat: list, lead: float = 0.6) -> bytes:
    """One chirp per speaker at calibration_times()[k] + lead + lat[k]."""
    count = len(lat)
    template = tones.chirp(RECORD_RATE)
    total = int((lead + tones.calibration_length(count) + 1.0) * RECORD_RATE)
    rec = [0.0] * total
    for at, latency in zip(tones.calibration_times(count), lat):
        start = int((lead + at + latency) * RECORD_RATE)
        for i, v in enumerate(template):
            rec[start + i] += 0.5 * v
    return struct.pack(f"<{len(rec)}h", *[int(max(-1, min(1, v)) * 32767) for v in rec])


def install_mic(runner, data: bytes):
    def factory(argv, kwargs):
        kwargs["stdout"].write(data)
        proc = FakeProcess(argv, kwargs)
        proc.returncode = 0
        return proc
    runner.on_popen("parec", factory)


def test_calibration_track_for_three():
    path = tones.calibration_track(__import__("pathlib").Path("/tmp/soundscape-test-cal3.wav"), rate=8000, count=3)
    mono, rate = tones.read_wav_mono(path)
    assert len(mono) == int(tones.calibration_length(3) * rate) == 9 * rate
    assert tones.calibration_times(3) == [1.0, 4.0, 7.0] and tones.calibration_switches(3) == [2.5, 5.5]
    energy = lambda a, b: sum(v * v for v in mono[int(a * rate):int(b * rate)])  # noqa: E731
    assert energy(7.0, 7.06) > 1.0 and energy(5.0, 6.9) == 0


def test_auto_sync_three_speakers(trio, world, runner):
    trio.up()
    # kitchen 180 ms, living room 192 ms (+12), patio 175 ms (-5): everyone waits for the living room
    install_mic(runner, synthetic([0.180, 0.192, 0.175]))
    cal = Calibrator(trio, log=lambda _m: None, sleep=lambda _s: None)
    m = cal.auto()
    assert m.lag_ms[0] == 0.0
    assert m.lag_ms[1] == pytest.approx(12.0, abs=0.3) and m.lag_ms[2] == pytest.approx(-5.0, abs=0.3)
    assert m.spread_ms == pytest.approx(17.0, abs=0.5)
    offsets = {sp.position: sp.offset_ms for sp in trio.config.speakers}
    assert offsets["right"] == 0.0
    assert offsets["left"] == pytest.approx(12.0, abs=0.3) and offsets["patio"] == pytest.approx(17.0, abs=0.5)
    assert world.delays["soundscape_patio_delay"] == pytest.approx(0.017, abs=0.0005)
    # mute sequence: speaker 0 solo, then 1, then 2, then all unmuted
    mutes = [c[2:] for c in runner.calls if c[:2] == ["pactl", "set-sink-mute"]]
    assert mutes[0:3] == [[world.bt_sink_name(MAC_A), "0"], [world.bt_sink_name(MAC_B), "1"], [world.bt_sink_name(MAC_C), "1"]]
    assert mutes[-3:] == [[world.bt_sink_name(MAC_A), "0"], [world.bt_sink_name(MAC_B), "0"], [world.bt_sink_name(MAC_C), "0"]]


def test_nudge_with_three(trio, world):
    trio.up()
    cal = Calibrator(trio, log=lambda _m: None, sleep=lambda _s: None)
    a, b, c = trio.config.ordered()
    cal._nudge(c, 4, [a, b, c])
    assert (a.offset_ms, b.offset_ms, c.offset_ms) == (0, 0, 4)
    cal._nudge(a, -6, [a, b, c])
    assert (a.offset_ms, b.offset_ms, c.offset_ms) == (0, 6, 10)
    assert world.delays["soundscape_right_delay"] == pytest.approx(0.006)


def test_mixer_three_outputs_independent_delays():
    m = Mixer(rate=1000, outputs=[Output("a", "left"), Output("b", "right"), Output("c", "patio")], stereo_split=True)
    m.set_delay("c", 2)
    m.push(struct.pack("<8f", 1, 10, 2, 20, 3, 30, 4, 40))
    assert struct.unpack("<8f", m.render_output(m.outputs[0], 4)) == (1, 1, 2, 2, 3, 3, 4, 4)       # left channel
    assert struct.unpack("<8f", m.render_output(m.outputs[1], 4)) == (10, 10, 20, 20, 30, 30, 40, 40)  # right channel
    assert struct.unpack("<8f", m.render_output(m.outputs[2], 4)) == (0, 0, 0, 0, 1, 10, 2, 20)      # full stereo, 2 frames late


def test_tones_rise_with_position_index(tmp_path):
    files = [tones.position_tone(tmp_path / f"{i}.wav", pos, rate=8000, index=i)
             for i, pos in enumerate(("left", "right", "patio", "garage"))]
    import wave
    lengths = {wave.open(str(f)).getnframes() for f in files}
    assert len(lengths) == 1  # same rhythm, different pitch
    samples = [tones.read_wav_mono(f)[0] for f in files]
    assert samples[2] != samples[3] and samples[0] != samples[1]


def test_four_speakers_up(runner, world, paths):
    world.two_speakers()
    world.add_device(MAC_C, "Patio", paired=True, trusted=True)
    world.add_device(MAC_D, "Garage", paired=True, trusted=True)
    cfg = three_config()
    cfg.add(Speaker(MAC_D, "Garage", "garage"))
    s = Session(runner=runner, paths=paths, config=cfg, sleep=lambda _s: None)
    state = s.up()
    assert len(state.outputs) == 4 and s.paths.engine_conf.read_text().count("filter-chain") == 4
    assert [sp["name"] for sp in s.status()["speakers"]] == ["Kitchen", "Living Room", "Garage", "Patio"]
    assert World  # fixture class referenced for readers


def test_adding_a_speaker_while_up_joins_it_at_once(runner, world, paths):
    from soundscape.session import Session
    from tests.conftest import make_config
    world.two_speakers()
    world.add_device(MAC_C, "Patio", paired=True, trusted=True)
    s = Session(runner=runner, paths=paths, config=make_config(), sleep=lambda _s: None)
    s.up()
    assert s.state().output_for(MAC_C) is None
    logs = []
    s.log = logs.append
    s.add_speaker(MAC_C, position="patio")
    assert s.state().output_for(MAC_C) is not None  # rebuilt: the third speaker plays without pressing Rebuild
    assert any("rebuilding the output to include Patio" in line for line in logs)


def test_watchdog_rebuilds_when_a_configured_speaker_is_left_out(runner, world, paths):
    from soundscape.config import Speaker
    from soundscape.session import Session
    from tests.conftest import make_config
    world.two_speakers()
    world.add_device(MAC_C, "Patio", paired=True, trusted=True, connected=True)
    s = Session(runner=runner, paths=paths, config=make_config(), sleep=lambda _s: None)
    s.up()
    s.config.add(Speaker(MAC_C, "Patio", "patio"))  # added behind the engine's back (e.g. by the CLI on another shell)
    s.save_config()
    logs = []
    s.log = logs.append
    s.ensure_up()
    assert s.state().output_for(MAC_C) is not None
    assert any("connected but not in the output; rebuilding" in line for line in logs)
    s.ensure_up()
    assert sum("rebuilding" in line for line in logs) == 1  # settled: no rebuild loop
