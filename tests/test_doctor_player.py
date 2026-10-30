import pytest

from soundscape.doctor import FAIL, OK, WARN, run_checks, summarize
from soundscape.errors import ToolMissing
from soundscape.player import build_command, pick_player, play
from tests.conftest import FakeRunner


def titles(checks, status=None):
    return [c.title for c in checks if status is None or c.status == status]


def test_doctor_all_good(runner, world):
    world.two_speakers()
    checks = run_checks(runner)
    assert not titles(checks, FAIL)
    assert any("PipeWire native engine" in t for t in titles(checks, OK))
    assert summarize(checks) == "everything looks good"


def test_doctor_warns_without_paired_speakers_or_tools(runner, world):
    runner.tools -= {"paplay", "mpv", "pw-cli"}
    checks = run_checks(runner)
    warns = titles(checks, WARN)
    assert "paplay missing" in warns and "no media player found" in warns
    assert any("pipewire CLI tools missing" in t for t in warns)
    assert any("0 paired audio device" in t for t in warns)
    assert "warning" in summarize(checks)


def test_doctor_old_pipewire(runner, world):
    runner.on(("pipewire", "--version"), "Compiled with libpipewire 0.3.48\n")
    checks = run_checks(runner)
    assert any("is old" in t for t in titles(checks, WARN))


def test_doctor_pulseaudio_without_bluetooth_module(runner, pulse_world):
    checks = run_checks(runner)
    assert "PulseAudio has no Bluetooth module" in titles(checks, FAIL)
    pulse_world.modules[1] = ("module-bluetooth-discover", "")
    checks = run_checks(runner)
    assert "pulseaudio-module-bluetooth loaded" in titles(checks, OK)


def test_doctor_no_server_and_no_bluetooth(runner):
    runner.on(("pactl",), (1, "", "Connection failure: Connection refused"))
    runner.on(("busctl",), (1, "", "Failed to connect to bus"))
    checks = run_checks(runner)
    fails = titles(checks, FAIL)
    assert "no sound server" in fails and "bluetoothd unreachable" in fails
    assert "blocking" in summarize(checks)


def test_doctor_adapter_off(runner, world):
    world.adapter_powered = False
    checks = run_checks(runner)
    assert any(t.endswith("off") for t in titles(checks, WARN))


def test_pick_player_and_commands():
    runner = FakeRunner(tools={"ffplay"})
    assert pick_player(runner) == "ffplay"
    assert pick_player(FakeRunner(tools={"paplay"})) == "paplay"
    with pytest.raises(ToolMissing):
        pick_player(FakeRunner(tools=set()))
    with pytest.raises(ToolMissing):
        pick_player(runner, "mpv")
    assert build_command("mpv", ["a"], "s", loop=True, volume=50)[-3:] == ["--loop-playlist=inf", "--volume=50", "a"]
    assert build_command("ffplay", ["a"], "s", loop=True) == ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", "-loop", "0", "a"]
    assert build_command("paplay", ["a"], "s") == ["paplay", "--device", "s", "a"]
    with pytest.raises(ValueError):
        build_command("ffplay", ["a", "b"], "s")
    with pytest.raises(ValueError):
        build_command("paplay", ["a", "b"], "s")
    with pytest.raises(ValueError):
        build_command("vlc", ["a"], "s")


def test_play_sequential_fallback_and_shuffle():
    runner = FakeRunner(tools={"ffplay"})
    assert play(runner, ["a", "b", "c"], "soundscape", shuffle=True) == 0
    launched = [c[-1] for c in runner.calls if c[0] == "ffplay"]
    assert sorted(launched) == ["a", "b", "c"]
    runner = FakeRunner(tools={"mpv"})
    assert play(runner, ["a"], "soundscape") == 0
