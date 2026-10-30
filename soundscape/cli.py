"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import __version__, tones
from .config import ENGINES, Config, Speaker
from .doctor import FAIL, run_checks, summarize
from .errors import AudioError, ConfigError, SoundscapeError
from .paths import Paths
from .runner import Runner
from .session import Session

Log = Callable[[str], None]


def say(msg: str) -> None:
    print(msg, flush=True)


def err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# -- shared actions ------------------------------------------------------------

def play_position_tones(session: Session, only: Optional[str] = None, log: Log = say) -> None:
    """Play a distinct tone directly to each speaker so the user can tell left from right."""
    workdir = Path(tempfile.mkdtemp(prefix="soundscape-"))
    ordered = session.config.ordered()
    slots = tones.tone_slots([sp.position for sp in ordered])
    speakers = ordered if not only else [session.config.require(only)]
    for sp in speakers:
        sink = session.pulse.bluetooth_sink(sp.address)
        if sink is None:
            log(f"{sp.position}: {sp.label} has no audio sink (not connected?)")
            continue
        path = tones.position_tone(workdir / f"{sp.position}.wav", sp.position, slot=slots[sp.position])
        log(f"{sp.position}: playing tone on {sp.label} ...")
        session.pulse.play_file(path, sink.name, wait=True)


def print_status(status: dict) -> None:
    server = status.get("server", {})
    if "error" in server:
        say(f"sound server: unavailable ({server['error']})")
    else:
        say(f"sound server: {server.get('name', '?')} {server.get('version', '')}".rstrip())
    if status["up"]:
        extras = []
        if status.get("is_default"):
            extras.append("default output")
        if status.get("stereo_split"):
            extras.append("stereo split")
        if status.get("manual_delays") is False:
            extras.append("no manual delays")
        suffix = f" ({', '.join(extras)})" if extras else ""
        vol = f", volume {status['volume']}%" if status.get("volume") is not None else ""
        say(f"virtual output '{status['sink']}': UP via {status['engine']}{vol}{suffix}")
    else:
        say(f"virtual output '{status['sink']}': down")
    if status.get("bluetooth_error"):
        say(f"bluetooth: {status['bluetooth_error']}")
    if not status["speakers"]:
        say("speakers: none configured (run `soundscape setup`)")
    for sp in status["speakers"]:
        link = "connected" if sp["connected"] else ("paired, not connected" if sp["paired"] else "not paired")
        sink = sp["sink"] or "no sink"
        vol = f"{sp['volume']}%" if sp.get("volume") is not None else "-"
        mode = "  HEADSET MODE (music muted)" if sp.get("mode") == "headset" else ""
        say(f"  {sp['position']:<6} {sp['name']:<28} {sp['address']}  {link:<22} {sink}  vol {vol}  delay +{sp['offset_ms']:.1f} ms{mode}")
    if status.get("default_input_is_speaker"):
        say("warning: the system microphone is one of the speakers; recording from it silences that speaker. "
            "`soundscape up` moves it to the built-in mic.")
    detail = status.get("engine_detail")
    if detail and isinstance(detail, dict) and detail.get("buffer_map") is not None:
        written = ", ".join(f"{o['position']}={o.get('frames_written', 0)}" for o in detail.get("outputs", []))
        say(f"relay: buffers {detail['buffer_map']}  frames written {written}")


def print_devices(devices, show_all: bool) -> None:
    if not devices:
        say("no devices found. Put the speaker in pairing mode (usually hold the Bluetooth button) and scan again.")
        return
    say(f"{'#':>2}  {'address':<17}  {'name':<30} {'signal':>6}  state")
    for i, d in enumerate(devices, 1):
        state = "connected" if d.connected else ("paired" if d.paired else "new")
        kind = "" if d.is_audio_sink or not show_all else "  (not an audio device)"
        rssi = f"{d.rssi} dBm" if d.rssi is not None else ""
        say(f"{i:>2}  {d.address:<17}  {d.label[:30]:<30} {rssi:>8}  {state}{kind}")


# -- commands ------------------------------------------------------------------

def cmd_doctor(session: Session, args: argparse.Namespace) -> int:
    checks = run_checks(session.runner, session.bluez, session.pulse)
    for c in checks:
        line = f" {c.symbol} {c.title}"
        if c.detail:
            line += f"  - {c.detail}"
        say(line)
        if c.fix and c.status != "ok":
            say(f"      fix: {c.fix}")
    say(summarize(checks))
    return 1 if any(c.status == FAIL for c in checks) else 0


def cmd_scan(session: Session, args: argparse.Namespace) -> int:
    devices = session.scan(args.seconds, audio_only=not args.all)
    if args.json:
        say(json.dumps([d.__dict__ for d in devices], indent=2))
    else:
        print_devices(devices, args.all)
        if devices:
            say("\nnext: soundscape add <address> --position left   (then another with --position right)")
    return 0


def cmd_add(session: Session, args: argparse.Namespace) -> int:
    sp = session.add_speaker(args.address, position=args.position, name=args.name or "",
                             replace=args.replace, connect=not args.no_connect)
    say(f"added {sp.label} as {sp.position}")
    n = len(session.config.speakers)
    if n >= 2:
        say(f"you have {n} speakers; run `soundscape up` to join them")
    return 0


def cmd_remove(session: Session, args: argparse.Namespace) -> int:
    sp = session.remove_speaker(args.speaker, forget=args.forget)
    say(f"removed {sp.label} ({sp.position})")
    return 0


def cmd_up(session: Session, args: argparse.Namespace) -> int:
    cfg = session.config
    if args.engine:
        cfg.engine = args.engine
    if args.stereo is not None:
        cfg.stereo_split = args.stereo
    if args.no_default:
        cfg.set_default_sink = False
    if args.no_compensate:
        cfg.latency_compensate = False
    state = session.up()
    if cfg.set_default_sink:
        say("all desktop audio now plays through both speakers")
    else:
        say(f"select the output named '{cfg.description}' in your app or sound settings")
    if state.engine == "aggregate":
        say("tip: install BlackHole (brew install blackhole-2ch) to unlock sync trims and stereo split")
    elif state.engine in ("pulse", "relay"):
        say("tip: run `soundscape sync --auto` once with a microphone to align the speakers")
    return 0


def cmd_down(session: Session, args: argparse.Namespace) -> int:
    if not session.down():
        say("nothing to take down")
    return 0


def cmd_status(session: Session, args: argparse.Namespace) -> int:
    status = session.status()
    if args.json:
        say(json.dumps(status, indent=2))
    else:
        print_status(status)
    return 0


def cmd_volume(session: Session, args: argparse.Namespace) -> int:
    target, percent = args.target, args.percent
    if percent is None:  # `soundscape volume 40` means everything
        if not target.lstrip("-").isdigit():
            raise ConfigError(f"volume needs a number, got {target!r}", "examples: `volume 40`, `volume left 70`")
        percent, target = int(target), "all"
    if not 0 <= int(percent) <= 150:
        raise ConfigError("volume must be between 0 and 150 percent")
    sink = session.set_volume(target, int(percent))
    say(f"{sink}: {int(percent)}%")
    return 0


def cmd_mute(session: Session, args: argparse.Namespace) -> int:
    session.set_mute(args.target, not args.off)
    say(f"{args.target}: {'muted' if not args.off else 'unmuted'}")
    return 0


def cmd_offset(session: Session, args: argparse.Namespace) -> int:
    if args.speaker in ("reset", "all") and (args.ms is None or args.ms == 0):
        session.reset_offsets()
        say("all trims reset to 0 ms")
        return 0
    if args.ms is None:
        raise ConfigError("offset needs a value in ms", "example: `soundscape offset right 12.5`, or `offset reset`")
    sp = session.set_offset(args.speaker, args.ms, relative=args.relative)
    say("delays now: " + ", ".join(f"{s.position}={s.label} +{s.offset_ms:.1f} ms" for s in session.config.speakers))
    if not session.state():
        say(f"(saved for the next `soundscape up`; {sp.label} is not playing right now)")
    return 0


def cmd_sync(session: Session, args: argparse.Namespace) -> int:
    from .sync import Calibrator
    cal = Calibrator(session, log=say)
    if args.auto:
        m = cal.auto(source=args.source, apply=not args.no_apply, passes=args.passes)
        if m.applied_to is None:
            say("speakers are already within 0.5 ms; nothing changed" if abs(m.gap_ms) < 0.5 else "measured only; not applied")
        say("done. Re-run after moving a speaker or changing codec; offsets are remembered.")
    else:
        cal.by_ear(step_ms=args.step, fine_ms=args.fine)
    return 0


def cmd_test(session: Session, args: argparse.Namespace) -> int:
    play_position_tones(session, only=args.speaker)
    return 0


def cmd_play(session: Session, args: argparse.Namespace) -> int:
    from .player import play
    state = session.state()
    if not state or not session.engine(state).alive(state):
        say("Soundscape is not up; bringing it up first")
        session.up()
    return play(session.runner, args.files, session.config.sink_name, player=args.player,
                loop=args.loop, shuffle=args.shuffle, volume=args.volume)


def cmd_ui(session: Session, args: argparse.Namespace) -> int:
    from .remote import serve
    serve(session, host=args.host, port=args.port, log=say, token=args.token,
          open_browser=not args.no_open and args.command == "ui")
    return 0


def cmd_daemon(session: Session, args: argparse.Namespace) -> int:
    stop = {"flag": False}

    def _stop(signum, frame):  # noqa: ARG001
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    say(f"soundscape daemon: keeping both speakers joined (check every {args.interval:.0f}s)")
    backoff = args.interval
    while not stop["flag"]:
        try:
            session.ensure_up()
            backoff = args.interval
        except SoundscapeError as exc:
            err(f"not ready: {exc.message}")
            backoff = min(backoff * 1.5, 120.0)
        deadline = time.monotonic() + backoff
        while not stop["flag"] and time.monotonic() < deadline:
            time.sleep(0.5)
    if args.keep:
        say("daemon stopping; leaving the virtual output in place")
    else:
        session.down()
    return 0


def cmd_config(session: Session, args: argparse.Namespace) -> int:
    cfg = session.config
    if args.action == "path":
        say(str(session.paths.config_file))
        return 0
    if args.action == "show":
        say(json.dumps(cfg.to_dict(), indent=2))
        return 0
    if args.action == "set":
        value = cfg.set_option(args.key, args.value)
        session.save_config()
        say(f"{args.key} = {value!r}")
        if session.state():
            say("run `soundscape up` to apply")
        return 0
    raise ConfigError(f"unknown config action {args.action}")


def cmd_setup(session: Session, args: argparse.Namespace) -> int:
    """Interactive first-run wizard."""
    say("Soundscape setup\n")
    checks = run_checks(session.runner, session.bluez, session.pulse)
    for c in checks:
        if c.status != "ok":
            say(f" {c.symbol} {c.title}" + (f"  - {c.detail}" if c.detail else ""))
            if c.fix:
                say(f"      fix: {c.fix}")
    if any(c.status == FAIL for c in checks):
        err("fix the problems above, then run `soundscape setup` again")
        return 1
    say(" + environment looks fine\n")

    if session.config.speakers and not args.fresh:
        say("configured speakers: " + ", ".join(f"{s.position}={s.label}" for s in session.config.speakers))
        if _ask("keep them and continue? [Y/n] ", default="y") == "y":
            return _finish_setup(session, args)
        session.config.speakers = []

    input("Put ALL your speakers in pairing mode (hold their Bluetooth button until it blinks), then press Enter.")
    chosen: List[Speaker] = []
    index = 0
    while True:
        position = session.config.next_position()
        devices = session.scan(args.seconds, audio_only=True)
        devices = [d for d in devices if d.address not in {c.address for c in chosen}]
        if not devices and index < 2:
            say("no audio devices found; scanning again for longer ...")
            devices = session.scan(args.seconds * 2, audio_only=True)
            devices = [d for d in devices if d.address not in {c.address for c in chosen}]
        if not devices:
            if index >= 2:
                say("no more speakers found.")
                break
            err("still nothing. Make sure the speakers are in pairing mode and not connected to a phone.")
            return 1
        if position in ("left", "right"):
            say(f"\nwhich speaker goes on the {position.upper()}?")
        else:
            say(f"\nwhich speaker is next? (it will be called '{position}' unless you name it)")
        print_devices(devices, False)
        pick = _pick(len(devices))
        if pick is None:
            if index >= 2:
                break
            return 1
        dev = devices[pick - 1]
        if position not in ("left", "right"):
            custom = input(f"name for this speaker's spot [{position}]: ").strip()
            if custom:
                position = custom
        sp = _add_with_retry(session, dev.address, position)
        if sp is None:
            return 1
        chosen.append(sp)
        index += 1
        say(f" + {sp.label} connected as {sp.position}\n")
        if index >= 2 and _ask("add another speaker? [y/N] ", default="n") != "y":
            break
    return _finish_setup(session, args)


def _add_with_retry(session: Session, address: str, position: str) -> Optional[Speaker]:
    """Pair/connect; when that cannot be done from here (no blueutil on macOS, speaker off),
    let the user fix it and try again instead of aborting the wizard."""
    from .errors import BluetoothError
    for attempt in range(3):
        try:
            return session.add_speaker(address, position=position, replace=True, connect=True)
        except BluetoothError as exc:
            err(f"could not connect: {exc.message}")
            if exc.hint:
                err(f"  hint: {exc.hint}")
            if attempt == 2 or _ask("fix that and press Enter to retry, or q to quit: ", default="") == "q":
                return None
    return None


def _finish_setup(session: Session, args: argparse.Namespace) -> int:
    say("joining the speakers ...")
    session.up()
    say("")
    if session.runner.which("paplay") or session.runner.which("afplay"):
        say("playing a low tone on LEFT, then a higher tone on RIGHT")
        play_position_tones(session)
        if _ask("did the low tone come from the speaker you want on the left? [Y/n] ", default="y") == "n":
            for sp in session.config.speakers:
                if sp.position in ("left", "right"):
                    sp.position = "right" if sp.position == "left" else "left"
            session.config.sort()
            session.save_config()
            say("swapped left and right")
            session.up()
    can_record = session.runner.which("parec") or session.platform == "mac"
    if can_record and _ask("\nalign the speakers now using this computer's microphone? [y/N] ", default="n") == "y":
        from .sync import Calibrator
        try:
            Calibrator(session, log=say).auto(passes=1)
        except SoundscapeError as exc:
            err(f"calibration skipped: {exc.message}" + (f"\n  hint: {exc.hint}" if exc.hint else ""))
    say("\nall set. Useful next steps:")
    say("  soundscape add <address>    add another speaker any time")
    say("  soundscape status           what is connected and playing")
    say("  soundscape play song.mp3    play a file through both speakers")
    say("  soundscape sync             fine-tune alignment by ear   (or: sync --auto)")
    say("  soundscape ui               dashboard in your browser: visualize and control it all")
    say("  soundscape down             restore your normal audio output")
    return 0


def _ask(prompt: str, default: str) -> str:
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return default
    return answer[:1] or default


def _pick(count: int) -> Optional[int]:
    while True:
        try:
            raw = input(f"number [1-{count}] (or q): ").strip().lower()
        except EOFError:
            return None
        if raw == "q":
            return None
        if raw.isdigit() and 1 <= int(raw) <= count:
            return int(raw)


# -- parser --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="soundscape",
        description="Play one audio source through two Bluetooth speakers at once, in sync.",
        epilog="First time? Run `soundscape setup`.")
    p.add_argument("--version", action="version", version=f"soundscape {__version__}")
    p.add_argument("-v", "--verbose", action="store_true", help="show what is happening behind the scenes")
    p.add_argument("--root", metavar="DIR", help="keep config and state under DIR (default: XDG dirs)")
    p.add_argument("--adapter", metavar="hciN", help="Bluetooth adapter to use (default: first)")
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = True

    sub.add_parser("setup", help="guided first-run: scan, pair, join, test").add_argument(
        "--seconds", type=float, default=12, help="scan duration")
    sub.choices["setup"].add_argument("--fresh", action="store_true", help="ignore already configured speakers")
    sub.add_parser("doctor", help="check Bluetooth, sound server and tools")

    s = sub.add_parser("scan", help="discover nearby speakers")
    s.add_argument("--seconds", type=float, default=10)
    s.add_argument("--all", action="store_true", help="include non-audio devices")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("add", help="pair/connect a speaker and remember it (any number of speakers)")
    s.add_argument("address", help="Bluetooth address AA:BB:CC:DD:EE:FF")
    s.add_argument("--position", metavar="NAME",
                   help="left, right, or any name such as kitchen (default: left, right, speaker3, ...)")
    s.add_argument("--name", help="friendly name")
    s.add_argument("--replace", action="store_true")
    s.add_argument("--no-connect", action="store_true", help="only save; do not pair/connect now")

    s = sub.add_parser("remove", help="forget a configured speaker")
    s.add_argument("speaker", help="position (left, right, kitchen...), address or name")
    s.add_argument("--forget", action="store_true", help="also unpair it in BlueZ")

    s = sub.add_parser("up", help="connect both speakers and create the joint output")
    s.add_argument("--engine", choices=ENGINES)
    s.add_argument("--stereo", dest="stereo", action="store_true", default=None,
                   help="'left' plays the L channel, 'right' the R channel; other speakers get both")
    s.add_argument("--no-stereo", dest="stereo", action="store_false", help="both speakers play full stereo")
    s.add_argument("--no-default", action="store_true", help="do not make it the default output")
    s.add_argument("--no-compensate", action="store_true", help="disable automatic latency compensation")

    sub.add_parser("down", help="remove the joint output and restore the previous default")
    sub.add_parser("status", help="show speakers, sinks and sync state").add_argument("--json", action="store_true")

    s = sub.add_parser("volume", help="set volume: `volume 40` (both) or `volume left 70`")
    s.add_argument("target", help="all | left | right | address | name | percent")
    s.add_argument("percent", nargs="?", type=int)

    s = sub.add_parser("mute", help="mute a speaker or everything")
    s.add_argument("target", nargs="?", default="all")
    s.add_argument("--off", action="store_true", help="unmute instead")

    s = sub.add_parser("offset", help="delay one speaker by N ms (live, remembered); `offset reset` zeroes all")
    s.add_argument("speaker", help="left, right, address, name, or 'reset'")
    s.add_argument("ms", type=float, nargs="?")
    s.add_argument("--relative", action="store_true", help="add to the current delay instead of replacing it")

    s = sub.add_parser("sync", help="align the two speakers (by ear, or --auto with a microphone)")
    s.add_argument("--auto", action="store_true", help="measure with the microphone and apply")
    s.add_argument("--source", help="microphone source name (see `pactl list short sources`)")
    s.add_argument("--passes", type=int, default=1, help="measure several times (auto mode)")
    s.add_argument("--no-apply", action="store_true", help="measure only")
    s.add_argument("--step", type=float, default=5.0, help="coarse step in ms (by-ear mode)")
    s.add_argument("--fine", type=float, default=1.0, help="fine step in ms (by-ear mode)")

    s = sub.add_parser("test", help="play a tone on each speaker in turn")
    s.add_argument("speaker", nargs="?", help="only this speaker")

    s = sub.add_parser("play", help="play files or URLs through both speakers")
    s.add_argument("files", nargs="+")
    s.add_argument("--loop", action="store_true")
    s.add_argument("--shuffle", action="store_true")
    s.add_argument("--player", choices=("mpv", "ffplay", "paplay"))
    s.add_argument("--volume", type=int, help="player volume percent (mpv only)")

    for name, help_text in (("ui", "open the web dashboard: visualize and control everything"),
                            ("remote", "same dashboard, for your phone (does not open a browser)")):
        s = sub.add_parser(name, help=help_text)
        s.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 for LAN access)")
        s.add_argument("--port", type=int, default=8787)
        s.add_argument("--token", help="require this token (open the page with ?token=...)")
        s.add_argument("--no-open", action="store_true", help="do not open a browser")

    s = sub.add_parser("daemon", help="keep the speakers joined; reconnect when they drop (for systemd)")
    s.add_argument("--interval", type=float, default=10.0, help="seconds between checks")
    s.add_argument("--keep", action="store_true", help="leave the output in place when the daemon stops")

    s = sub.add_parser("config", help="show or change settings")
    s.add_argument("action", choices=("show", "path", "set"))
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    return p


COMMANDS = {
    "setup": cmd_setup, "doctor": cmd_doctor, "scan": cmd_scan, "add": cmd_add, "remove": cmd_remove,
    "up": cmd_up, "down": cmd_down, "status": cmd_status, "volume": cmd_volume, "mute": cmd_mute,
    "offset": cmd_offset, "sync": cmd_sync, "test": cmd_test, "play": cmd_play, "remote": cmd_ui, "ui": cmd_ui,
    "daemon": cmd_daemon, "config": cmd_config,
}


def make_session(args: argparse.Namespace, runner: Optional[Runner] = None) -> Session:
    from . import platform as plat
    runner = runner or Runner()
    paths = Paths.under(Path(args.root)) if args.root else Paths()
    log = say if args.verbose or args.command in ("setup", "up", "down", "daemon", "add", "scan") else (lambda _m: None)
    bluez = None
    if plat.current() != plat.MAC and args.adapter:
        from .bluez import BlueZ
        bluez = BlueZ(runner, adapter=args.adapter)
    return Session(runner=runner, paths=paths, log=log, bluez=bluez)


def main(argv: Optional[Sequence[str]] = None, runner: Optional[Runner] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config" and args.action == "set" and (not args.key or args.value is None):
        parser.error("config set needs KEY and VALUE")
    try:
        session = make_session(args, runner)
        return COMMANDS[args.command](session, args)
    except SoundscapeError as exc:
        err(f"error: {exc.message}")
        if exc.hint:
            err(f"  hint: {exc.hint}")
        return 2
    except KeyboardInterrupt:
        err("interrupted")
        return 130


__all__ = ["main", "build_parser", "play_position_tones", "Config", "AudioError"]
