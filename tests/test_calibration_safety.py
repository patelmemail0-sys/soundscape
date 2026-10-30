"""Calibration must never use a speaker's own microphone or apply an impossible measurement."""

from __future__ import annotations

import struct

import pytest

from soundscape import tones
from soundscape.errors import AudioError
from soundscape.mac import coreaudio as ca
from soundscape.mac.audio import MacAudio
from soundscape.pulse import PulseCtl
from soundscape.remote import RemoteApp
from soundscape.sync import RECORD_RATE, Calibrator
from tests.conftest import MAC_A, MAC_B, FakeProcess
from tests.test_mac import UID_A, UID_MIC, make_hal


def recording(latency_a: float, latency_b: float) -> bytes:
    template = tones.chirp(RECORD_RATE)
    rec = [0.0] * int(9 * RECORD_RATE)
    for at, gain in ((0.6 + 1.0 + latency_a, 0.5), (0.6 + 4.0 + latency_b, 0.45)):
        start = int(at * RECORD_RATE)
        for i, v in enumerate(template):
            rec[start + i] += gain * v
    return struct.pack(f"<{len(rec)}h", *[int(max(-1, min(1, v)) * 32767) for v in rec])


def install_mic(runner, data, seen):
    def factory(argv, kwargs):
        seen.append(argv)
        kwargs["stdout"].write(data)
        proc = FakeProcess(argv, kwargs)
        proc.returncode = 0
        return proc
    runner.on_popen("parec", factory)


def test_linux_picks_wired_mic_over_speaker_mic(runner, world):
    pc = PulseCtl(runner)
    names = [s["name"] for s in pc.sources()]
    assert "bluez_input.AA_BB_CC_DD_EE_01.0" in names and not any(n.endswith(".monitor") for n in names)
    assert pc.pick_microphone([MAC_A, MAC_B]) == world.default_source  # alsa_input wins
    bt = [s for s in pc.sources() if s["transport"] == "bluetooth"][0]
    assert bt["address"] == MAC_A


def test_calibration_records_from_the_picked_mic(session, world, runner):
    session.up()
    seen = []
    install_mic(runner, recording(0.180, 0.192), seen)
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    cal.auto()
    assert seen and seen[0][seen[0].index("--device") + 1] == world.default_source


def test_calibration_refuses_a_speakers_own_mic(session, world, runner):
    session.up()
    install_mic(runner, recording(0.180, 0.192), [])
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    with pytest.raises(AudioError) as exc:
        cal.measure(source="bluez_input.AA_BB_CC_DD_EE_01.0")
    assert "own microphones" in exc.value.message and "headset" in exc.value.hint


def test_implausible_measurement_is_rejected_and_not_applied(session, world, runner):
    session.up()
    install_mic(runner, recording(0.180, 0.180 + 0.41), [])
    cal = Calibrator(session, log=lambda _m: None, sleep=lambda _s: None)
    with pytest.raises(AudioError) as exc:
        cal.auto()
    assert "not plausible" in exc.value.message
    assert all(sp.offset_ms == 0 for sp in session.config.speakers)
    assert world.delays["soundscape_left_delay"] == 0.0


def test_reset_offsets_live_and_cli(session, world, runner, tmp_path, capsys):
    session.up()
    session.set_offset("right", 30)
    assert world.delays["soundscape_right_delay"] == pytest.approx(0.03)
    session.reset_offsets()
    assert world.delays["soundscape_right_delay"] == 0.0
    assert all(sp.offset_ms == 0 for sp in session.config.speakers)
    from soundscape import cli
    world.two_speakers() if not world.devices else None
    root = tmp_path / "root"
    assert cli.main(["--root", str(root), "add", MAC_A], runner=runner) == 0
    assert cli.main(["--root", str(root), "add", MAC_B], runner=runner) == 0
    assert cli.main(["--root", str(root), "offset", "left", "7"], runner=runner) == 0
    assert cli.main(["--root", str(root), "offset", "reset"], runner=runner) == 0
    assert "reset" in capsys.readouterr().out
    assert cli.main(["--root", str(root), "offset", "left"], runner=runner) == 2  # value required


def test_api_sources_and_reset(session, world):
    app = RemoteApp(session, log=lambda _m: None, synchronous_jobs=True)
    status, body, _ = app.handle("GET", "/api/sources", None)
    assert body["recommended"] == world.default_source
    speaker_mics = [s for s in body["sources"] if s["speaker_mic"]]
    assert speaker_mics and speaker_mics[0]["address"] == MAC_A
    session.up()
    app.handle("POST", "/api/offset", {"target": "right", "offset_ms": 9})
    status, body, _ = app.handle("POST", "/api/offset", {"target": "all", "offset_ms": 0})
    assert body["offsets"] == {"left": 0.0, "right": 0.0}


def test_mac_picks_builtin_mic_and_skips_speaker_mics(runner):
    hal = make_hal()
    # the HK/JBL style headset microphone that macOS makes the default input
    hal.add_device("AA-BB-CC-DD-EE-01:input", "Kitchen", "bluetooth", out_ch=0, in_ch=1, volume=False)
    hal.default_input = hal.id_for_uid("AA-BB-CC-DD-EE-01:input")
    audio = MacAudio(runner, coreaudio=ca.CoreAudio(hal))
    srcs = {s["name"]: s for s in audio.sources()}
    assert srcs["AA-BB-CC-DD-EE-01:input"]["address"] == MAC_A and srcs[UID_MIC]["transport"] == "builtin"
    assert audio.pick_microphone([MAC_A, MAC_B]) == UID_MIC
    assert audio.pick_microphone([]) == UID_MIC  # built-in still preferred over Bluetooth
    hal.remove_device(hal.id_for_uid(UID_MIC))
    assert audio.pick_microphone([MAC_A]) is None  # only the speaker's own mic is left: refuse
    assert UID_A  # keep the import meaningful for readers
