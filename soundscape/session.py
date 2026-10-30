"""Orchestration: config + BlueZ + sound server + engine = one `up`/`down`/`status` surface."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from .bluez import BlueZ, Device
from .config import Config, Speaker
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
                 bluez: Optional[BlueZ] = None, pulse: Optional[PulseCtl] = None,
                 engine: Optional[Engine] = None, sleep: Callable[[float], None] = time.sleep):
        self.runner = runner or Runner()
        self.paths = (paths or Paths()).ensure()
        self.log = log
        self.config = config if config is not None else Config.load(self.paths.config_file)
        self.bluez = bluez or BlueZ(self.runner, sleep=sleep)
        self.pulse = pulse or PulseCtl(self.runner)
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

    def engine(self, state: Optional[EngineState] = None) -> Engine:
        if self._engine is not None:
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
    def up(self, rebuild: bool = True) -> EngineState:
        if len(self.config.speakers) < 2:
            raise ConfigError(f"{len(self.config.speakers)} speaker(s) configured; Soundscape needs at least two",
                              "run `soundscape setup` or `soundscape add <address> --position right`")
        self.config.normalize_offsets()
        existing = self.state()
        if existing:
            if not rebuild and self.engine(existing).alive(existing):
                self.log("already up")
                return existing
            self.down()
        self.log(f"sound server: {self.server().flavour} {self.server().server_version}".rstrip())
        bound = self.connect_speakers()
        engine = self.engine()
        self.log(f"building virtual output '{self.config.sink_name}' with the {engine.name} engine ...")
        state = engine.build(bound, self.config)
        for b in bound:
            if b.speaker.volume is not None:
                try:
                    self.pulse.set_volume(b.sink.name, b.speaker.volume)
                except AudioError as exc:
                    self.log(f"volume for {b.speaker.label} not applied: {exc.message}")
        self._save_state(state)
        self.save_config()
        self.log(f"ready: '{self.config.sink_name}' plays through {', '.join(b.speaker.label for b in bound)}")
        return state

    def down(self) -> bool:
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
        if state and self.engine(state).alive(state):
            missing = [o for o in state.outputs if self.pulse.sink(o.sink) is None]
            if not missing:
                return state
            self.log(f"lost {', '.join(o.label for o in missing)}; rebuilding")
        return self.up(rebuild=True)

    # -- live controls ---------------------------------------------------
    def set_offset(self, selector: str, offset_ms: float, relative: bool = False) -> Speaker:
        sp = self.config.require(selector)
        new = sp.offset_ms + offset_ms if relative else offset_ms
        sp.offset_ms = round(new, 3)
        self.config.normalize_offsets()
        state = self.state()
        if state:
            engine = self.engine(state)
            for s in self.config.speakers:
                if state.output_for(s.address):
                    engine.set_offset(state, s.address, s.offset_ms)
            self._save_state(state)
        self.save_config()
        return sp

    def apply_measured_gap(self, early: Speaker, late: Speaker, gap_ms: float) -> None:
        """``late`` was heard ``gap_ms`` after ``early``: delay ``early`` by that much."""
        early.offset_ms = round(early.offset_ms + gap_ms, 3)
        self.config.normalize_offsets()
        self.set_offset(early.address, early.offset_ms)

    def set_volume(self, selector: str, percent: int) -> str:
        if selector in ("all", "master", "both"):
            self.pulse.set_volume(self.config.sink_name, percent)
            return self.config.sink_name
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
            self.pulse.set_mute(self.config.sink_name, mute)
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
            out["up"] = any(s.name == state.sink_name for s in sinks)
            out["pid"] = state.pid
            out["stereo_split"] = state.stereo_split
            out["manual_delays"] = state.with_delays
            out["is_default"] = bool(info and info.default_sink == state.sink_name)
        master = next((s for s in sinks if s.name == self.config.sink_name), None)
        if master:
            out["volume"] = master.volume_percent
            out["muted"] = master.mute
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
            })
        return out

    # -- discovery helpers -------------------------------------------------
    def scan(self, seconds: float, audio_only: bool = True) -> List[Device]:
        devices = self.bluez.scan(seconds, log=self.log)
        if audio_only:
            devices = [d for d in devices if d.is_audio_sink]
        return sorted(devices, key=lambda d: (not d.paired, -(d.rssi or -999), d.label))

    def add_speaker(self, address: str, position: Optional[str] = None, name: str = "",
                    replace: bool = False, connect: bool = True) -> Speaker:
        taken = {s.position for s in self.config.speakers}
        if position is None:
            position = next((p for p in ("left", "right", "third", "fourth") if p not in taken), None)
            if position is None:
                raise ConfigError("all four positions are taken", "remove a speaker first")
        dev = self.bluez.device(address)
        if not name and dev:
            name = dev.label if dev.label != dev.address else ""
        sp = Speaker(address=address, name=name, position=position)
        self.config.add(sp, replace=replace)
        if connect:
            self.bluez.ensure_connected(sp.address, log=self.log)
        self.save_config()
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
