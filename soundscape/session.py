"""Orchestration: config + BlueZ + sound server + engine = one `up`/`down`/`status` surface."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from . import platform as plat
from .bluez import BlueZ, Device
from .config import Config, Speaker, parse_distance
from .engine import ENGINES, Bound, Engine, EngineState, choose_engine
from .errors import AudioError, BluetoothError, ConfigError, SoundscapeError
from .paths import Paths
from .pulse import PulseCtl, ServerInfo, Sink
from .runner import Runner

Log = Callable[[str], None]


def _quiet(_: str) -> None:
    return None


class Session:
    def __init__(self, runner: Optional[Runner] = None, paths: Optional[Paths] = None,
                 log: Log = _quiet, config: Optional[Config] = None,
                 bluez: Any = None, pulse: Any = None,
                 engine: Optional[Engine] = None, sleep: Callable[[float], None] = time.sleep):
        self.runner = runner or Runner()
        self.paths = (paths or Paths()).ensure()
        self.log = log
        self.config = config if config is not None else Config.load(self.paths.config_file)
        self.platform = plat.current()
        if bluez is None or pulse is None:
            if self.platform == plat.MAC:
                from .mac.audio import MacAudio
                from .mac.bluetooth import MacBluetooth
                bluez = bluez or MacBluetooth(self.runner, sleep=sleep)
                pulse = pulse or MacAudio(self.runner)
            else:
                bluez = bluez or BlueZ(self.runner, sleep=sleep)
                pulse = pulse or PulseCtl(self.runner)
        self.bluez: Any = bluez  # Bluetooth backend (BlueZ on Linux, MacBluetooth on macOS)
        self.pulse: Any = pulse  # audio backend (PulseCtl on Linux, MacAudio on macOS)
        self._engine = engine
        self._sleep = sleep
        self._server: Optional[ServerInfo] = None

    # -- persistence -----------------------------------------------------
    def save_config(self) -> None:
        self.config.save(self.paths.config_file)

    def state(self) -> Optional[EngineState]:
        return EngineState.load(self.paths.state_file)

    def _save_state(self, state: Optional[EngineState]) -> None:
        if state is None:
            try:
                self.paths.state_file.unlink()
            except OSError:
                pass
        else:
            state.save(self.paths.state_file)

    # -- environment -----------------------------------------------------
    def server(self, refresh: bool = False) -> ServerInfo:
        if self._server is None or refresh:
            self._server = self.pulse.info()
        return self._server

    @property
    def bt(self):
        return self.bluez

    @property
    def audio(self):
        return self.pulse

    def engine(self, state: Optional[EngineState] = None) -> Engine:
        if self._engine is not None:
            return self._engine
        if self.platform == plat.MAC:
            from .mac.engine import MAC_ENGINES, choose_mac_engine
            if state is not None and state.engine in MAC_ENGINES:
                self._engine = MAC_ENGINES[state.engine](self.runner, self.pulse, self.paths, self.log)
            else:
                self._engine = choose_mac_engine(self.config, self.runner, self.pulse, self.paths, self.log)
            return self._engine
        if state is not None and state.engine in ENGINES:
            self._engine = ENGINES[state.engine](self.runner, self.pulse, self.paths, self.log)
        else:
            self._engine = choose_engine(self.config, self.runner, self.pulse, self.paths, self.log,
                                         is_pipewire=self.server().is_pipewire)
        return self._engine

    # -- speakers --------------------------------------------------------
    def connect_speakers(self, speakers: Optional[List[Speaker]] = None) -> List[Bound]:
        speakers = speakers if speakers is not None else self.config.speakers
        if not speakers:
            raise ConfigError("no speakers configured", "run `soundscape setup` or `soundscape add <address>`")
        bound: List[Bound] = []
        for sp in speakers:
            self.bluez.ensure_connected(sp.address, log=self.log, connect_timeout=self.config.connect_timeout_s)
            sink = self._wait_for_bt_sink(sp)
            if sp.name == "" :
                dev = self.bluez.device(sp.address)
                if dev and dev.label != sp.address:
                    sp.name = dev.label
            bound.append(Bound(sp, sink))
            self.log(f"{sp.position}: {sp.label} -> {sink.name}")
        return bound

    def _wait_for_bt_sink(self, sp: Speaker) -> Sink:
        deadline = time.monotonic() + self.config.sink_timeout_s
        while True:
            sink = self.pulse.bluetooth_sink(sp.address)
            if sink:
                return sink
            if time.monotonic() >= deadline:
                raise AudioError(
                    f"{sp.label} is connected but no audio sink appeared within {self.config.sink_timeout_s:.0f}s",
                    "the speaker may be in headset (HFP) mode or the A2DP profile is missing. "
                    "Check `pactl list cards` and switch the card profile to a2dp-sink.")
            self._sleep(0.5)

    # -- lifecycle -------------------------------------------------------
    def _lock(self):
        """One lifecycle command at a time per state dir (two concurrent `up`s would spawn two engines)."""
        import fcntl
        self.paths.ensure()
        fh = open(self.paths.state_dir / "lock", "w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise ConfigError("another soundscape command is already changing the setup",
                              "wait for it to finish (or stop the daemon) and try again") from None
        return fh

    def up(self, rebuild: bool = True) -> EngineState:
        lock = self._lock()
        try:
            return self._up(rebuild)
        finally:
            lock.close()

    def _up(self, rebuild: bool = True) -> EngineState:
        if len(self.config.speakers) < 2:
            raise ConfigError(f"{len(self.config.speakers)} speaker(s) configured; Soundscape needs at least two",
                              "run `soundscape setup` or `soundscape add <address> --position right`")
        self.config.normalize_offsets()
        existing = self.state()
        if existing:
            if not rebuild and self.engine(existing).alive(existing):
                self.log("already up")
                return existing
            self._down()
        self.log(f"sound server: {self.server().flavour} {self.server().server_version}".rstrip())
        bound = self.connect_speakers()
        bound = self._protect_a2dp(bound)
        engine = self.engine()
        self.log(f"building virtual output '{self.config.sink_name}' with the {engine.name} engine ...")
        state = engine.build(bound, self.config)
        self._apply_levels(bound)
        self._save_state(state)
        self.save_config()
        self.log(f"ready: '{self.config.sink_name}' plays through {', '.join(b.speaker.label for b in bound)}")
        return state

    HEADSET_RECOVERY_S = 6.0

    def pinned_microphone(self) -> Optional[str]:
        """The input device the system microphone should stay on (None = pinning off/unavailable)."""
        if not self.config.pin_microphone:
            return None
        if self.config.microphone and self.config.microphone != "builtin":
            return self.config.microphone
        builtin = getattr(self.pulse, "builtin_microphone", None)
        mic = builtin() if builtin else None
        if mic is None:
            picker = getattr(self.pulse, "pick_microphone", None)
            mic = picker([sp.address for sp in self.config.speakers]) if picker else None
        return mic

    def pin_microphone(self, quiet: bool = False) -> bool:
        """Force the system default input onto the pinned microphone. Returns True if it changed it.

        macOS moves the default input to a Bluetooth device whenever one connects; anything then
        recording (browser, Siri, a call) flips that speaker into headset mode."""
        mic = self.pinned_microphone()
        if not mic or not hasattr(self.pulse, "set_default_source"):
            return False
        try:
            current = self.pulse.default_source()
        except SoundscapeError:
            return False
        if current == mic:
            return False
        try:
            self.pulse.set_default_source(mic)
        except SoundscapeError as exc:
            if not quiet:
                self.log(f"could not pin the microphone: {exc.message}")
            return False
        if not quiet:
            self.log(f"system microphone pinned to {mic} (was {current or 'unset'}) so no speaker drops into headset mode")
        return True

    def _protect_a2dp(self, bound: List[Bound]) -> List[Bound]:
        """Pin the microphone away from the speakers, then give speakers that are in headset mode
        a few seconds to come back to music mode (and reconnect them if they do not)."""
        macs = {b.speaker.address for b in bound}
        if not self.pin_microphone():
            get_addr = getattr(self.pulse, "default_source_address", None)
            try:
                addr = get_addr() if get_addr else None
            except SoundscapeError:
                addr = None
            if addr and addr.upper() in macs:
                self.log("warning: the system microphone is one of the speakers; recording from it will silence that "
                         "speaker. Pick the built-in mic in System Settings > Sound > Input")
        if not any(b.sink.headset_mode for b in bound):
            return bound
        deadline = time.monotonic() + self.HEADSET_RECOVERY_S
        while True:
            refreshed = []
            for b in bound:
                sink = self.pulse.bluetooth_sink(b.speaker.address) or b.sink
                refreshed.append(Bound(b.speaker, sink))
            stuck = [b for b in refreshed if b.sink.headset_mode]
            if not stuck:
                self.log("all speakers back in music (A2DP) mode")
                return refreshed
            if time.monotonic() >= deadline:
                break
            self._sleep(0.5)
        # still stuck: a Bluetooth reconnect resets the link to the music (A2DP) profile
        result = []
        for b in refreshed:
            if not b.sink.headset_mode:
                result.append(b)
                continue
            self.log(f"{b.speaker.label} is stuck in headset mode ({b.sink.sample_rate or '?'} Hz); reconnecting it "
                     "to get music (A2DP) quality back")
            try:
                sink = self._reconnect(b.speaker)
            except SoundscapeError as exc:
                self.log(f"reconnect of {b.speaker.label} failed: {exc.message}")
                sink = b.sink
            if sink.headset_mode:
                self.log(f"warning: {b.speaker.label} is still in headset mode. Something is recording from its "
                         "microphone: check System Settings > Sound > Input and apps with microphone access "
                         "(browser, FaceTime, Zoom, Dictation)")
            result.append(Bound(b.speaker, sink))
        return result

    def _reconnect(self, sp: Speaker, settle_s: float = 2.0) -> Sink:
        """Drop and re-establish the Bluetooth link, then wait for the speaker's sink."""
        self.bluez.disconnect(sp.address)
        self._sleep(settle_s)
        self.bluez.ensure_connected(sp.address, log=self.log, connect_timeout=self.config.connect_timeout_s)
        sink = self._wait_for_bt_sink(sp)
        if sink.headset_mode:  # the profile may switch a moment after the device appears
            deadline = time.monotonic() + 5.0
            while sink.headset_mode and time.monotonic() < deadline:
                self._sleep(0.5)
                sink = self.pulse.bluetooth_sink(sp.address) or sink
        return sink

    def reconnect_speaker(self, selector: str) -> Speaker:
        """User-triggered: reconnect one speaker and rebuild if Soundscape is up."""
        sp = self.config.require(selector)
        self._reconnect(sp)
        if self.state():
            self.up()
        return sp

    def _apply_levels(self, bound: List[Bound]) -> None:
        """Soundscape never limits a speaker: every speaker runs at its own 100% (its physical
        maximum over Bluetooth absolute volume) unless the user trimmed it deliberately."""
        raised = []
        for b in bound:
            target = b.speaker.volume if b.speaker.volume is not None else 100
            try:
                before = self.pulse.get_volume(b.sink.name)
                self.pulse.set_volume(b.sink.name, target)
                if before is not None and before < target:
                    raised.append(f"{b.speaker.label} {before}% -> {target}%")
            except AudioError as exc:
                self.log(f"volume for {b.speaker.label} not applied: {exc.message}")
        if raised:
            self.log("speakers set to full level: " + ", ".join(raised) +
                     " (lower them with their sliders or their own buttons)")

    def down(self) -> bool:
        lock = self._lock()
        try:
            return self._down()
        finally:
            lock.close()

    def _down(self) -> bool:
        state = self.state()
        if state is None:
            return False
        try:
            self.engine(state).teardown(state)
        finally:
            self._save_state(None)
            self._engine = None
        self.log("virtual output removed")
        return True

    def ensure_up(self) -> EngineState:
        """Used by the daemon: reconnect and rebuild only when something is actually wrong."""
        state = self.state()
        if state:
            self.pin_microphone()
            problems = self.engine(state).missing(state)
            if not problems:
                left_out = self._connected_but_not_in(state)
                if not left_out:
                    return state
                self.log(f"{', '.join(left_out)} connected but not in the output; rebuilding")
            else:
                self.log(f"lost {', '.join(problems)}; rebuilding")
        return self.up(rebuild=True)

    def _connected_but_not_in(self, state: EngineState) -> List[str]:
        """Configured speakers that are connected over Bluetooth but missing from the running output
        (added after the output was built)."""
        missing = [sp for sp in self.config.speakers if state.output_for(sp.address) is None]
        if not missing:
            return []
        try:
            devices = {d.address: d for d in self.bluez.devices()}
        except SoundscapeError:
            return []
        return [sp.label for sp in missing if devices.get(sp.address) and devices[sp.address].connected]

    # -- live controls ---------------------------------------------------
    MAX_OFFSET_MS = 1000.0

    def set_offset(self, selector: str, offset_ms: float, relative: bool = False) -> Speaker:
        sp = self.config.require(selector)
        if offset_ms != offset_ms or abs(offset_ms) > self.MAX_OFFSET_MS:  # NaN or absurd
            raise ConfigError(f"offset {offset_ms} ms is out of range",
                              f"use a value between -{self.MAX_OFFSET_MS:.0f} and {self.MAX_OFFSET_MS:.0f} ms")
        previous = [s.offset_ms for s in self.config.speakers]
        new = sp.offset_ms + offset_ms if relative else offset_ms
        sp.offset_ms = round(new, 3)
        self.config.normalize_offsets()
        if any(s.offset_ms > self.MAX_OFFSET_MS for s in self.config.speakers):
            for s, old in zip(self.config.speakers, previous):
                s.offset_ms = old
            raise ConfigError("that trim would push another speaker past the 1000 ms maximum",
                              "reset with `soundscape offset <speaker> 0` and try smaller steps")
        state = self.state()
        if state:
            engine = self.engine(state)
            for s in self.config.speakers:
                if state.output_for(s.address):
                    engine.set_offset(state, s.address, s.offset_ms)
            self._save_state(state)
        self.save_config()
        return sp

    def set_distance(self, selector: str, distance: object) -> Speaker:
        """Remember how far ``selector`` stands from the calibration microphone (the laptop), so
        ``sync --auto`` can take the sound's travel time out of what the microphone hears."""
        sp = self.config.require(selector)
        sp.distance_m = parse_distance(distance)
        self.save_config()
        return sp

    def reset_offsets(self) -> None:
        """Zero every speaker's trim, live if the engine is up."""
        for sp in self.config.speakers:
            sp.offset_ms = 0.0
        state = self.state()
        if state:
            engine = self.engine(state)
            for sp in self.config.speakers:
                if state.output_for(sp.address):
                    try:
                        engine.set_offset(state, sp.address, 0.0)
                    except AudioError as exc:
                        self.log(f"could not reset {sp.label}: {exc.message}")
            self._save_state(state)
        self.save_config()

    def apply_measured_gap(self, early: Speaker, late: Speaker, gap_ms: float) -> None:
        """``late`` was heard ``gap_ms`` after ``early``: delay ``early`` by that much."""
        early.offset_ms = round(early.offset_ms + gap_ms, 3)
        self.config.normalize_offsets()
        self.set_offset(early.address, early.offset_ms)

    def _master_sink(self) -> str:
        state = self.state()
        if hasattr(self.pulse, "group"):
            self.pulse.group = [o.sink for o in state.outputs] if state else []
        return state.sink_name if state else self.config.sink_name

    def set_volume(self, selector: str, percent: int) -> str:
        if selector in ("all", "master", "both"):
            master = self._master_sink()
            self.pulse.set_volume(master, percent)
            return master
        sp = self.config.require(selector)
        sp.volume = percent
        sink = self.pulse.bluetooth_sink(sp.address)
        if sink is None:
            raise AudioError(f"{sp.label} has no audio sink right now", "is it connected?")
        self.pulse.set_volume(sink.name, percent)
        self.save_config()
        return sink.name

    def set_mute(self, selector: str, mute: bool) -> None:
        if selector in ("all", "master", "both"):
            self.pulse.set_mute(self._master_sink(), mute)
            return
        sp = self.config.require(selector)
        sink = self.pulse.bluetooth_sink(sp.address)
        if sink is None:
            raise AudioError(f"{sp.label} has no audio sink right now")
        self.pulse.set_mute(sink.name, mute)

    # -- reporting -------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"up": False, "engine": None, "sink": self.config.sink_name, "speakers": []}
        state = self.state()
        try:
            info = self.server(refresh=True)
            out["server"] = {"name": info.server_name, "version": info.server_version,
                             "default_sink": info.default_sink}
        except SoundscapeError as exc:
            out["server"] = {"error": exc.message}
            info = None
        sinks: List[Sink] = []
        if info is not None:
            try:
                sinks = self.pulse.sinks()
            except SoundscapeError:
                sinks = []
        devices: Dict[str, Device] = {}
        try:
            devices = {d.address: d for d in self.bluez.devices()}
        except SoundscapeError as exc:
            out["bluetooth_error"] = exc.message
        if state:
            out["engine"] = state.engine
            try:
                out["up"] = self.engine(state).alive(state)
            except SoundscapeError:
                out["up"] = any(s.name == state.sink_name for s in sinks)
            out["pid"] = state.pid
            out["stereo_split"] = state.stereo_split
            out["manual_delays"] = state.with_delays
            out["is_default"] = bool(info and info.default_sink == state.sink_name)
        master_name = state.sink_name if state else self.config.sink_name
        master = next((s for s in sinks if s.name == master_name), None)
        if master:
            out["volume"] = master.volume_percent
            out["muted"] = master.mute
        out["platform"] = self.platform
        if state and state.engine == "relay":
            out["master_label"] = "Mac volume (BlackHole)"
        elif state and state.engine == "aggregate":
            out["master_label"] = "All speakers"
        else:
            out["master_label"] = "Master"
        for sp in self.config.speakers:
            dev = devices.get(sp.address)
            sink = next((s for s in sinks if s.bluetooth_address == sp.address), None)
            out["speakers"].append({
                "position": sp.position, "name": sp.label, "address": sp.address,
                "paired": bool(dev and dev.paired), "connected": bool(dev and dev.connected),
                "sink": sink.name if sink else None,
                "volume": sink.volume_percent if sink else None,
                "muted": sink.mute if sink else None,
                "offset_ms": sp.offset_ms,
                "distance_m": sp.distance_m,
                "sample_rate": sink.sample_rate if sink else None,
                "mode": ("headset" if sink.headset_mode else "music") if sink else None,
            })
        get_addr = getattr(self.pulse, "default_source_address", None)
        if get_addr:
            try:
                addr = get_addr()
                out["default_input_is_speaker"] = bool(addr and addr.upper() in {sp.address for sp in self.config.speakers})
            except SoundscapeError:
                pass
        try:
            pinned = self.pinned_microphone()
            current = self.pulse.default_source()
            out["microphone"] = {"pinned": pinned, "current": current,
                                 "ok": bool(pinned) and current == pinned if pinned else None}
        except SoundscapeError:
            out["microphone"] = None
        if state and hasattr(self.engine(state), "describe"):
            try:
                out["engine_detail"] = self.engine(state).describe(state)  # type: ignore[attr-defined]
            except SoundscapeError:
                out["engine_detail"] = None
        return out

    # -- discovery helpers -------------------------------------------------
    def scan(self, seconds: float, audio_only: bool = True) -> List[Device]:
        devices = self.bluez.scan(seconds, log=self.log)
        if audio_only:
            devices = [d for d in devices if d.is_audio_sink]
        return sorted(devices, key=lambda d: (not d.paired, -(d.rssi or -999), d.label))

    def add_speaker(self, address: str, position: Optional[str] = None, name: str = "",
                    replace: bool = False, connect: bool = True, distance: object = None,
                    join: bool = True) -> Speaker:
        if position is None:
            position = self.config.next_position()
        dev = self.bluez.device(address)
        if not name and dev:
            name = dev.label if dev.label != dev.address else ""
        sp = Speaker(address=address, name=name, position=position, distance_m=parse_distance(distance))
        self.config.add(sp, replace=replace)
        if connect:
            self.bluez.ensure_connected(sp.address, log=self.log)
        self.save_config()
        if join and connect and self.state():
            # the running output was built for the old set: rebuild it so the new speaker plays at once
            self.log(f"rebuilding the output to include {sp.label}")
            self.up(rebuild=True)
        return sp

    def remove_speaker(self, selector: str, forget: bool = False) -> Speaker:
        sp = self.config.remove(selector)
        self.save_config()
        if forget:
            try:
                self.bluez.remove(sp.address)
            except BluetoothError as exc:
                self.log(exc.message)
        return sp
