import struct

import pytest

from soundscape import tones
from soundscape.errors import AudioError, ConfigError
from soundscape.sync import RECORD_RATE, Calibrator
from tests.conftest import MAC_A, MAC_B, FakeProcess


def synthetic_recording(latency_a: float, latency_b: float, lead: float = 0.6, seconds: float = 8.0) -> bytes:
    template = tones.chirp(RECORD_RATE)
    rec = [0.0] * int(seconds * RECORD_RATE)
    for at, gain in ((lead + tones.CALIBRATION_FIRST_S + latency_a, 0.5),
                     (lead + tones.CALIBRATION_SECOND_S + latency_b, 0.45)):
        start = int(at * RECORD_RATE)
        for i, v in enumerate(template):
            rec[start + i] += gain * v
    return struct.pack(f"<{len(rec)}h", *[int(max(-1, min(1, v)) * 32767) for v in rec])


def install_mic(runner, data: bytes):
    def factory(argv, kwargs):
        kwargs["stdout"].write(data)
        return FakeProcess(argv, kwargs)
    runner.on_popen("parec", factory)


def test_measure_detects_late_speaker_and_applies(session, world, runner):
    session.up()
    install_mic(runner, synthetic_recording(0.180, 0.180 + 0.0238))
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    m = cal.auto()
    assert m.gap_ms == pytest.approx(23.8, abs=0.3)
    assert m.applied_to.address == MAC_A  # kitchen was early, so it gets delayed
    assert world.delays["soundscape_left_delay"] == pytest.approx(0.0238, abs=0.0003)
    assert world.delays["soundscape_right_delay"] == 0.0
    # mutes were toggled and restored
    mute_calls = [c for c in runner.calls if c[:2] == ["pactl", "set-sink-mute"]]
    assert mute_calls[0][2] == world.bt_sink_name(MAC_B) and mute_calls[0][3] == "1"
    assert not world.sinks[world.bt_sink_name(MAC_A)]["mute"] and not world.sinks[world.bt_sink_name(MAC_B)]["mute"]
    play = [c for c in runner.calls if c[0] == "paplay"][0]
    assert play[1:3] == ["--device", "soundscape"]


def test_measure_other_direction_and_no_apply(session, world, runner):
    session.up()
    install_mic(runner, synthetic_recording(0.200, 0.200 - 0.011))
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    m = cal.auto(apply=False)
    assert m.gap_ms == pytest.approx(-11.0, abs=0.3) and m.applied_to is None
    assert world.delays["soundscape_right_delay"] == 0.0
    m2 = cal.auto()
    assert m2.applied_to.address == MAC_B
    assert world.delays["soundscape_right_delay"] == pytest.approx(0.011, abs=0.0003)


def test_low_confidence_is_rejected(session, world, runner):
    session.up()
    import random
    rnd = random.Random(3)
    noise = struct.pack(f"<{8 * RECORD_RATE}h", *[int(rnd.uniform(-0.3, 0.3) * 32767) for _ in range(8 * RECORD_RATE)])
    install_mic(runner, noise)
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    with pytest.raises(AudioError) as exc:
        cal.auto()
    assert "confidence" in exc.value.message


def test_requires_up_and_two_speakers(session, runner):
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    with pytest.raises(AudioError) as exc:
        cal.measure()
    assert "soundscape up" in exc.value.hint
    session.config.speakers = session.config.speakers[:1]
    with pytest.raises(ConfigError):
        cal.measure()


def test_short_recording_and_bad_source(session, world, runner):
    session.up()
    install_mic(runner, b"\x00" * 100)
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    with pytest.raises(AudioError) as exc:
        cal.measure()
    assert "too short" in exc.value.message
    with pytest.raises(AudioError) as exc:
        cal.measure(source="soundscape.monitor")
    assert "loopback" in exc.value.message
    world.default_source = ""
    with pytest.raises(AudioError) as exc:
        cal.measure()
    assert "--source" in exc.value.hint


def test_nudge_logic_moves_negative_delay_to_other_speaker(session, world):
    session.up()
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    a, b = session.config.speakers
    cal._nudge(b, 5, a, b)
    assert (a.offset_ms, b.offset_ms) == (0, 5)
    cal._nudge(b, -8, a, b)
    assert (a.offset_ms, b.offset_ms) == (3, 0)
    assert world.delays["soundscape_left_delay"] == pytest.approx(0.003)


def test_by_ear_needs_tty(session, monkeypatch):
    session.up()
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": staticmethod(lambda: False)})())
    with pytest.raises(AudioError):
        cal.by_ear()
