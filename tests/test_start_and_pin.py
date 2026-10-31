"""`soundscape start`, the dashboard quit path, and microphone pinning."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

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
        assert session.state() is not None  # up before the dashboard opens

    monkeypatch.setattr("soundscape.remote.serve", fake_serve)
    assert cli.main(["--root", str(root), "start", "--no-open"], runner=runner) == 0
    assert served["watchdog_s"] == 10.0 and served["quit_allowed"] is True and served["open_browser"] is False
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
    assert struct  # noqa: keeps the import list honest
