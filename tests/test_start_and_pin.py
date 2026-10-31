"""`soundscape start`, the dashboard quit path, and microphone pinning."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from soundscape import cli
from soundscape.config import Config
from soundscape.mac import coreaudio as ca
from soundscape.mac.ioproc import guard_microphone
from soundscape.remote import RemoteApp
from tests.conftest import MAC_A, MAC_B, FakeProcess
from tests.test_mac import UID_MIC, mac_session, make_hal

# -- microphone pinning -----------------------------------------------------------------


def test_linux_up_pins_default_source_to_wired_mic(session, world):
    world.two_speakers(connected=True)
    world.default_source = "bluez_input.AA_BB_CC_DD_EE_01.0"
    session.up()
    calls = [c for c in world.runner.calls if c[:2] == ["pactl", "set-default-source"]]
    assert calls and calls[-1][2] == "alsa_input.pci-0000_00_1f.3.analog-stereo"
    assert any("pinned to" in m for m in session.logs)
    st = session.status()
    assert st["microphone"]["pinned"] == "alsa_input.pci-0000_00_1f.3.analog-stereo"


def test_pin_can_be_disabled_and_custom(session, world):
    world.two_speakers(connected=True)
    session.config.pin_microphone = False
    assert session.pinned_microphone() is None and session.pin_microphone() is False
    session.config.pin_microphone = True
    session.config.microphone = "usb_mic"
    assert session.pinned_microphone() == "usb_mic"
    assert Config().set_option("pin_microphone", "off") is False


def test_mac_up_pins_builtin_and_relay_gets_mic_arg(runner, tmp_path, monkeypatch):
    hal = make_hal(blackhole=True)
    hal.add_device("AA-BB-CC-DD-EE-01:input", "Kitchen", "bluetooth", out_ch=0, in_ch=1, volume=False)
    hal.default_input = hal.id_for_uid("AA-BB-CC-DD-EE-01:input")
    s, world, live = mac_session(runner, hal, tmp_path, monkeypatch)
    monkeypatch.setattr("soundscape.mac.ioproc.send_command", lambda path, cmd, timeout=3.0: "pong" if cmd == "ping" else "bye")

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
    assert hal.default_input == hal.id_for_uid(UID_MIC)
    argv = [p for p in runner.procs if "-m" in p.argv][0].argv
    assert argv[argv.index("--mic") + 1] == UID_MIC
    # the system moves the input again (a speaker reconnects): ensure_up pins it back
    hal.default_input = hal.id_for_uid("AA-BB-CC-DD-EE-01:input")
    s.ensure_up()
    assert hal.default_input == hal.id_for_uid(UID_MIC)


def test_guard_microphone_resets_only_when_moved():
    hal = make_hal()
    core = ca.CoreAudio(hal)
    mic = hal.id_for_uid(UID_MIC)
    hal.default_input = mic
    assert guard_microphone(core, mic) is False
    hal.default_input = hal.id_for_uid("BuiltInSpeakerDevice")
    assert guard_microphone(core, mic) is True and hal.default_input == mic


def test_stereo_split_stays_off_by_default_and_is_announced(runner, tmp_path, monkeypatch):
    assert Config().stereo_split is False
    hal = make_hal(blackhole=True)
    s, world, live = mac_session(runner, hal, tmp_path, monkeypatch, cfg=None)
    s.config.stereo_split = True
    monkeypatch.setattr("soundscape.mac.ioproc.send_command", lambda path, cmd, timeout=3.0: "pong" if cmd == "ping" else "bye")

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
    assert any("stereo split is ON" in m for m in s.logs)


# -- start / quit ---------------------------------------------------------------------------

def test_start_brings_up_serves_and_takes_down(runner, world, tmp_path, monkeypatch, capsys):
    world.two_speakers()
    root = tmp_path / "root"
    assert cli.main(["--root", str(root), "add", MAC_A], runner=runner) == 0
    assert cli.main(["--root", str(root), "add", MAC_B], runner=runner) == 0
    served = {}

    def fake_serve(session, **kw):
        served.update(kw)
        assert session.state() is None  # the dashboard comes first ...
        if kw["startup_up"]:
            session.up()  # ... and `up` runs as the first background job
        assert session.state() is not None

    monkeypatch.setattr("soundscape.remote.serve", fake_serve)
    assert cli.main(["--root", str(root), "start", "--no-open"], runner=runner) == 0
    assert served["watchdog_s"] == 10.0 and served["quit_allowed"] is True and served["open_browser"] is False
    assert served["startup_up"] is True
    assert "soundscape" not in world.sinks  # taken down after serve returned
    assert "normal audio output is back" in capsys.readouterr().out


def test_start_without_speakers_still_serves(runner, world, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("soundscape.remote.serve", lambda session, **kw: None)
    assert cli.main(["--root", str(tmp_path), "start", "--no-open", "--keep"], runner=runner) == 0
    assert "no speakers configured yet" in capsys.readouterr().out


def test_quit_endpoint_and_watchdog(session, world):
    app = RemoteApp(session, log=lambda _m: None, synchronous_jobs=True)
    assert app.handle("POST", "/api/quit", {})[0] == 409  # not started with `start`
    assert app.handle("GET", "/api/status", None)[1]["can_quit"] is False
    calls = []
    app.on_quit = lambda: calls.append("quit")
    status, body, _ = app.handle("POST", "/api/quit", {})
    assert status == 200 and body["ok"]
    import time
    time.sleep(0.5)
    assert calls == ["quit"]
    # watchdog brings things up and re-pins
    app.watchdog_tick()
    assert "soundscape" in world.sinks
    world.devices[MAC_B]["connected"] = False
    world._drop_bt_sink(MAC_B)
    app.watchdog_tick()
    assert world.bt_sink_name(MAC_B) in world.sinks


def test_install_script_mentions_app_and_start():
    text = Path("install.sh").read_text()
    assert "Soundscape.app" in text and "soundscape start" in text and "LSUIElement" in text
    assert "CFBundleIconFile" in text and "Soundscape.icns" in text and "Icon=soundscape" in text
    assert Path("assets/Soundscape.icns").stat().st_size > 100_000 and Path("assets/soundscape.svg").exists()
    assert struct  # noqa: keeps the import list honest


# -- detached start (what the Soundscape app runs) ---------------------------------------

def test_server_alive_is_false_on_a_closed_port():
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    assert cli._server_alive("127.0.0.1", port, None) is False
    assert cli._dashboard_url("0.0.0.0", 8787, "t") == "http://127.0.0.1:8787/?token=t"


def test_launch_detached_spawns_background_process_and_opens_browser(runner, world, tmp_path, monkeypatch, capsys):
    spawned = {}
    opened = []

    class FakePopen:
        def __init__(self, argv, **kw):
            spawned["argv"] = argv
            spawned["kw"] = kw

    alive = iter([False, False, True])
    monkeypatch.setattr(cli, "_server_alive", lambda host, port, token: next(alive, True))
    monkeypatch.setattr("subprocess.Popen", FakePopen)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    assert cli.main(["--root", str(tmp_path), "start", "--detach", "--port", "8790", "--token", "abc"], runner=runner) == 0
    argv = spawned["argv"]
    assert argv[1:3] == ["-m", "soundscape"] and "start" in argv and "--no-open" in argv
    assert argv[argv.index("--root") + 1] == str(tmp_path) and argv[argv.index("--token") + 1] == "abc"
    assert spawned["kw"]["start_new_session"] is True
    assert opened == ["http://127.0.0.1:8790/?token=abc"]
    assert "running in the background" in capsys.readouterr().out


def test_launch_detached_when_already_running_only_opens_browser(runner, world, tmp_path, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli, "_server_alive", lambda host, port, token: True)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("must not spawn a second server"))
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    assert cli.main(["--root", str(tmp_path), "start", "--detach"], runner=runner) == 0
    assert opened == ["http://127.0.0.1:8787/"] and "already running" in capsys.readouterr().out


def test_launch_detached_reports_failure(runner, world, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_server_alive", lambda host, port, token: False)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: None)
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock, 100.0))
    assert cli.main(["--root", str(tmp_path), "start", "--detach", "--no-open"], runner=runner) == 1
    assert "did not start" in capsys.readouterr().err


def test_start_log_path(runner, world, tmp_path, monkeypatch):
    from soundscape.paths import Paths
    from soundscape.session import Session
    s = Session(runner=runner, paths=Paths.under(tmp_path), config=Config(), sleep=lambda _s: None)
    assert cli.start_log_path(s) == tmp_path / "state" / "start.log"
    monkeypatch.setattr(s, "platform", "mac")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert cli.start_log_path(s) == tmp_path / "Library" / "Logs" / "Soundscape.log"


def test_launch_detached_restarts_an_outdated_server(runner, world, tmp_path, monkeypatch, capsys):
    from soundscape import __version__
    spawned, opened, quits = {}, [], []
    alive = iter([True, True, False, True])  # running (old) -> still up -> gone after quit -> the new one answers
    monkeypatch.setattr(cli, "_server_alive", lambda host, port, token: next(alive, True))
    monkeypatch.setattr(cli, "_server_version", lambda host, port, token: "0.7.0")
    monkeypatch.setattr(cli, "_quit_server", lambda host, port, token: quits.append((host, port, token)) or True)
    monkeypatch.setattr("subprocess.Popen", lambda argv, **kw: spawned.setdefault("argv", argv))
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    assert cli.main(["--root", str(tmp_path), "start", "--detach"], runner=runner) == 0
    out = capsys.readouterr().out
    assert "0.7.0 is running but " + __version__ + " is installed" in out and quits == [("0.0.0.0", 8787, None)]
    assert "start" in spawned["argv"] and opened == ["http://127.0.0.1:8787/"]
    # same version: nothing is restarted
    monkeypatch.setattr(cli, "_server_alive", lambda host, port, token: True)
    monkeypatch.setattr(cli, "_server_version", lambda host, port, token: __version__)
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: pytest.fail("must not spawn"))
    assert cli.main(["--root", str(tmp_path), "start", "--detach", "--no-open"], runner=runner) == 0
    assert "already running" in capsys.readouterr().out
