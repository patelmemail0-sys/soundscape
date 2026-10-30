import json

import pytest

from soundscape import cli
from soundscape.config import Config
from tests.conftest import MAC_A, MAC_B


def run(runner, root, *args):
    return cli.main(["--root", str(root), *args], runner=runner)


def test_full_cli_flow(runner, world, tmp_path, capsys):
    world.two_speakers()
    root = tmp_path / "root"
    assert run(runner, root, "doctor") == 0
    out = capsys.readouterr().out
    assert "everything looks good" in out or "ready with" in out

    assert run(runner, root, "scan", "--seconds", "1") == 0
    out = capsys.readouterr().out
    assert "Kitchen" in out and "Pixel" not in out

    assert run(runner, root, "add", MAC_A, "--position", "left") == 0
    assert run(runner, root, "add", MAC_B) == 0
    out = capsys.readouterr().out
    assert "added Living Room as right" in out and "soundscape up" in out
    cfg = Config.load(root / "config" / "config.json")
    assert [s.position for s in cfg.speakers] == ["left", "right"]

    assert run(runner, root, "up") == 0
    assert "all desktop audio" in capsys.readouterr().out
    assert world.default_sink == "soundscape"

    assert run(runner, root, "status", "--json") == 0
    st = json.loads(capsys.readouterr().out)
    assert st["up"] and st["engine"] == "pipewire"

    assert run(runner, root, "status") == 0
    out = capsys.readouterr().out
    assert "UP via pipewire" in out and "Kitchen" in out

    assert run(runner, root, "offset", "right", "8") == 0
    assert world.delays["soundscape_right_delay"] == pytest.approx(0.008)
    assert run(runner, root, "offset", "right", "-3", "--relative") == 0
    assert world.delays["soundscape_right_delay"] == pytest.approx(0.005)

    assert run(runner, root, "volume", "40") == 0
    assert world.sinks["soundscape"]["volume"] == 40
    assert run(runner, root, "volume", "left", "70") == 0
    assert world.sinks[world.bt_sink_name(MAC_A)]["volume"] == 70
    assert run(runner, root, "mute", "right") == 0
    assert world.sinks[world.bt_sink_name(MAC_B)]["mute"]
    assert run(runner, root, "mute", "right", "--off") == 0
    assert not world.sinks[world.bt_sink_name(MAC_B)]["mute"]

    assert run(runner, root, "test") == 0
    plays = [c for c in runner.calls if c[0] == "paplay"]
    assert {c[2] for c in plays} == {world.bt_sink_name(MAC_A), world.bt_sink_name(MAC_B)}

    assert run(runner, root, "config", "set", "stereo_split", "true") == 0
    assert "soundscape up" in capsys.readouterr().out  # reminder to apply
    assert Config.load(root / "config" / "config.json").stereo_split is True
    assert run(runner, root, "config", "show") == 0
    assert json.loads(capsys.readouterr().out)["stereo_split"] is True
    assert run(runner, root, "config", "path") == 0
    assert capsys.readouterr().out.strip().endswith("config.json")

    assert run(runner, root, "remove", "right") == 0
    assert run(runner, root, "down") == 0
    assert world.default_sink.startswith("alsa_output")
    assert run(runner, root, "down") == 0
    assert "nothing to take down" in capsys.readouterr().out


def test_errors_exit_2_with_hint(runner, world, tmp_path, capsys):
    root = tmp_path / "root"
    assert run(runner, root, "up") == 2
    err = capsys.readouterr().err
    assert "error:" in err and "hint:" in err
    assert run(runner, root, "offset", "left", "3") == 2
    assert run(runner, root, "config", "set", "engine", "jack") == 2
    assert run(runner, root, "config", "set", "nonsense", "1") == 2


def test_config_set_requires_key_and_value(runner, world, tmp_path):
    with pytest.raises(SystemExit):
        run(runner, tmp_path, "config", "set", "engine")


def test_up_flags_persist(runner, world, tmp_path):
    world.two_speakers()
    root = tmp_path / "root"
    run(runner, root, "add", MAC_A)
    run(runner, root, "add", MAC_B)
    assert run(runner, root, "up", "--engine", "pulse", "--stereo", "--no-default", "--no-compensate") == 0
    cfg = Config.load(root / "config" / "config.json")
    assert cfg.engine == "pulse" and cfg.stereo_split and not cfg.set_default_sink and not cfg.latency_compensate
    assert world.default_sink.startswith("alsa_output")


def test_doctor_reports_failures(runner, world, tmp_path, capsys):
    runner.tools.discard("bluetoothctl")
    assert run(runner, tmp_path, "doctor") == 1
    out = capsys.readouterr().out
    assert "bluetoothctl missing" in out and "fix:" in out


def test_play_brings_up_and_launches_player(runner, world, tmp_path):
    world.two_speakers()
    root = tmp_path / "root"
    run(runner, root, "add", MAC_A)
    run(runner, root, "add", MAC_B)
    assert run(runner, root, "play", "a.mp3", "b.flac", "--loop") == 0
    mpv = [c for c in runner.calls if c[0] == "mpv"][0]
    assert "--loop-playlist=inf" in mpv and mpv[-2:] == ["a.mp3", "b.flac"]
    proc = [p for p in runner.procs if p.argv[0] == "mpv"][0]
    assert proc.kwargs["env"]["PULSE_SINK"] == "soundscape"


def test_version_and_help(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "soundscape" in capsys.readouterr().out


def test_setup_wizard_noninteractive_path(runner, world, tmp_path, monkeypatch, capsys):
    world.two_speakers()
    root = tmp_path / "root"
    answers = iter(["", "1", "1", "n", "y", "n"])  # enter, pick left, pick right, no more speakers, keep L/R, no mic sync
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert run(runner, root, "setup", "--seconds", "1") == 0
    cfg = Config.load(root / "config" / "config.json")
    assert {s.position for s in cfg.speakers} == {"left", "right"}
    assert world.default_sink == "soundscape"
    assert "all set" in capsys.readouterr().out


def test_setup_swaps_positions_when_asked(runner, world, tmp_path, monkeypatch):
    world.two_speakers()
    root = tmp_path / "root"
    answers = iter(["", "1", "1", "n", "n", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert run(runner, root, "setup", "--seconds", "1") == 0
    cfg = Config.load(root / "config" / "config.json")
    left = [s for s in cfg.speakers if s.position == "left"][0]
    assert left.address == MAC_B


def test_setup_keeps_existing(runner, world, tmp_path, monkeypatch):
    world.two_speakers()
    root = tmp_path / "root"
    run(runner, root, "add", MAC_A)
    run(runner, root, "add", MAC_B)
    answers = iter(["y", "y", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert run(runner, root, "setup") == 0
    assert world.default_sink == "soundscape"


def test_setup_fails_fast_on_blocking_problem(runner, world, tmp_path, capsys):
    runner.tools.discard("pactl")
    assert run(runner, tmp_path, "setup") == 1
    assert "fix the problems" in capsys.readouterr().err


def test_print_devices_empty(capsys):
    cli.print_devices([], False)
    assert "pairing mode" in capsys.readouterr().out


def test_daemon_loop_brings_up_and_tears_down(runner, world, tmp_path, monkeypatch, capsys):
    import signal as _signal

    from soundscape import cli as _cli
    world.two_speakers()
    root = tmp_path / "root"
    run(runner, root, "add", MAC_A)
    run(runner, root, "add", MAC_B)
    handlers = {}
    monkeypatch.setattr(_signal, "signal", lambda num, fn: handlers.__setitem__(num, fn))
    ticks = {"n": 0}

    def fake_sleep(_s):
        ticks["n"] += 1
        if ticks["n"] >= 3:
            handlers[_signal.SIGTERM](_signal.SIGTERM, None)  # systemd stop

    monkeypatch.setattr(_cli.time, "sleep", fake_sleep)
    assert run(runner, root, "daemon", "--interval", "1") == 0
    assert "soundscape" not in world.sinks  # torn down on stop
    assert world.default_sink.startswith("alsa_output")
    out = capsys.readouterr().out
    assert "daemon" in out


def test_daemon_keep_leaves_output_in_place(runner, world, tmp_path, monkeypatch):
    import signal as _signal

    from soundscape import cli as _cli
    world.two_speakers()
    root = tmp_path / "root"
    run(runner, root, "add", MAC_A)
    run(runner, root, "add", MAC_B)
    handlers = {}
    monkeypatch.setattr(_signal, "signal", lambda num, fn: handlers.__setitem__(num, fn))
    monkeypatch.setattr(_cli.time, "sleep", lambda _s: handlers[_signal.SIGINT](_signal.SIGINT, None))
    assert run(runner, root, "daemon", "--keep") == 0
    assert "soundscape" in world.sinks


def test_daemon_backs_off_when_speakers_unavailable(runner, world, tmp_path, monkeypatch, capsys):
    import signal as _signal

    from soundscape import cli as _cli
    world.two_speakers()
    world.fail_connect.add(MAC_B)
    root = tmp_path / "root"
    run(runner, root, "add", MAC_A)
    run(runner, root, "add", MAC_B, "--no-connect")
    handlers = {}
    monkeypatch.setattr(_signal, "signal", lambda num, fn: handlers.__setitem__(num, fn))
    monkeypatch.setattr(_cli.time, "sleep", lambda _s: handlers[_signal.SIGTERM](_signal.SIGTERM, None))
    assert run(runner, root, "daemon") == 0
    assert "not ready" in capsys.readouterr().err


def test_setup_retries_when_speaker_cannot_connect(runner, world, tmp_path, monkeypatch, capsys):
    world.two_speakers()
    world.fail_connect.add(MAC_B)
    root = tmp_path / "root"
    # enter, pick left, pick right (fails) -> fix (clear failure) and retry, no more, keep L/R, no mic
    answers = iter(["", "1", "1", "retry", "n", "y", "n"])

    def fake_input(prompt=""):
        value = next(answers)
        if value == "retry":
            world.fail_connect.discard(MAC_B)
            return ""
        return value

    monkeypatch.setattr("builtins.input", fake_input)
    assert run(runner, root, "setup", "--seconds", "1") == 0
    assert "could not connect" in capsys.readouterr().err
    assert world.devices[MAC_B]["connected"]
