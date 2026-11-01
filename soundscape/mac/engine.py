"""macOS engines.

* :class:`AggregateEngine` - a CoreAudio aggregate device of the speakers, drift-corrected,
  set as the default output. Built in, nothing to install. No per-speaker delay.
* :class:`RelayEngine` - BlackHole becomes the default output; a relay process reads it and
  writes to each speaker through a private aggregate with sample-accurate delays. This is the
  engine that makes ``sync --auto`` able to *apply* what it measures.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable, List, Optional

from .. import engine as base
from ..config import Config
from ..engine import Bound, Engine, EngineState, OutputState
from ..errors import AudioError
from ..paths import Paths
from ..runner import Runner
from .audio import BLACKHOLE_HINT, MacAudio
from .coreaudio import AGGREGATE_UID_PREFIX

Log = Callable[[str], None]


def sweep_aggregates(audio: MacAudio, log: Log, keep_uid: Optional[str] = None) -> int:
    """Destroy public Soundscape aggregate devices left behind by earlier runs. They show up in
    the Sound menu as 'Soundscape (both speakers)' and confuse users and CoreAudio alike."""
    removed = 0
    for d in audio.ca.soundscape_aggregates():
        if keep_uid and d.uid == keep_uid:
            continue
        try:
            audio.ca.destroy_aggregate(d.id)
            removed += 1
        except AudioError as exc:
            log(f"could not remove old aggregate {d.name}: {exc.message}")
    if removed:
        log(f"removed {removed} leftover Soundscape aggregate device(s)")
    return removed


class AggregateEngine(Engine):
    name = "aggregate"

    def __init__(self, runner: Runner, audio: MacAudio, paths: Paths, log: Log):
        super().__init__(runner, audio, paths, log)
        self.audio = audio

    def _uid(self, cfg: Config) -> str:
        return f"{AGGREGATE_UID_PREFIX}{cfg.sink_name}"

    def _cleanup(self, uid: str) -> None:
        sweep_aggregates(self.audio, self.log)

    def build(self, bound: List[Bound], cfg: Config) -> EngineState:
        uid = self._uid(cfg)
        self._cleanup(uid)
        master = bound[0].sink.name
        subdevices = [(b.sink.name, b.sink.name != master) for b in bound]
        rate = self.audio.ca.align_sample_rates([b.sink.index for b in bound])
        if rate:
            self.log(f"speakers aligned at {int(rate)} Hz")
        else:
            self.log("warning: the speakers share no common sample rate; one of them may stay silent")
        # stacked = Multi-Output Device: every speaker gets the same stereo signal
        agg_id = self.audio.ca.create_aggregate(uid, cfg.description, subdevices, master_uid=master, private=False,
                                                stacked=True)
        deadline = time.monotonic() + cfg.sink_timeout_s
        while self.audio.ca.device_by_uid(uid) is None:
            if time.monotonic() > deadline:
                raise AudioError("the aggregate device did not appear", "open Audio MIDI Setup and check for errors")
            time.sleep(0.2)
        state = EngineState(engine=self.name, sink_name=uid, device_id=agg_id)
        active = set()
        for sub in self.audio.ca.aggregate_subdevices(agg_id):
            try:
                active.add(self.audio.ca.device(sub).uid)
            except AudioError:
                continue
        for b in bound:
            if active and b.sink.name not in active:
                self.log(f"warning: {b.speaker.label} is not active inside the aggregate device "
                         "(sample rate mismatch or device busy); it will stay silent")
        for b in bound:
            state.outputs.append(OutputState(address=b.speaker.address, position=b.speaker.position,
                                             label=b.speaker.label, sink=b.sink.name, offset_ms=b.speaker.offset_ms,
                                             sink_index=b.sink.index))
        if self.audio.blackhole() is not None and cfg.engine != "auto":
            self.log("note: BlackHole is installed but the engine setting forces 'aggregate'. Set the engine to "
                     "'auto' (Settings in the dashboard, or `soundscape config set engine auto`) to get sync trims "
                     "and stereo split through the relay engine")
        if any(b.speaker.offset_ms for b in bound):
            self.log("sync trims are ignored by the aggregate engine; " + BLACKHOLE_HINT)
        if cfg.stereo_split:
            self.log("stereo split needs the relay engine (BlackHole); both speakers get full stereo")
        self.audio.group = [b.sink.name for b in bound]
        self._route_default(state, cfg)
        return state

    def teardown(self, state: EngineState) -> None:
        self._restore_default(state)
        d = self.audio.ca.device_by_uid(state.sink_name)
        if d is not None:
            try:
                self.audio.ca.destroy_aggregate(d.id)
            except AudioError as exc:
                self.log(f"could not destroy aggregate: {exc.message}")

    def alive(self, state: EngineState) -> bool:
        return self.audio.ca.device_by_uid(state.sink_name) is not None

    def set_offset(self, state: EngineState, address: str, offset_ms: float) -> None:
        raise AudioError("the aggregate engine cannot delay one speaker", BLACKHOLE_HINT + "; then `soundscape up` again")


def relay_block_size(cfg: Config, outputs: int) -> int:
    """Frames per mixer block: the SOUNDSCAPE_RELAY_BUFFER variable, else the relay_buffer setting,
    else 1024 for two speakers and 2048 for three or more (bigger blocks give the Python mixer the
    headroom several Bluetooth streams need; every speaker is delayed equally, so music is unaffected)."""
    env = os.environ.get("SOUNDSCAPE_RELAY_BUFFER")
    if env and env.isdigit():
        return int(env)
    return int(cfg.relay_buffer) or (2048 if outputs >= 3 else 1024)


class RelayEngine(Engine):
    name = "relay"
    MARKER = "soundscape.mac.ioproc"

    def __init__(self, runner: Runner, audio: MacAudio, paths: Paths, log: Log):
        super().__init__(runner, audio, paths, log)
        self.audio = audio

    @property
    def socket_path(self) -> str:
        from .ioproc import short_socket_path
        return short_socket_path(str(self.paths.state_dir / "relay.sock"))

    def _send(self, command: str) -> str:
        from .ioproc import send_command
        try:
            return send_command(self.socket_path, command)
        except OSError as exc:
            raise AudioError(f"relay engine is not answering: {exc}", "run `soundscape up`") from exc

    def build(self, bound: List[Bound], cfg: Config) -> EngineState:
        blackhole = self.audio.blackhole()
        if blackhole is None:
            raise AudioError("BlackHole is not installed", BLACKHOLE_HINT)
        sweep_aggregates(self.audio, self.log)
        for pid in base.find_pids(self.MARKER):
            if base.kill_pid(pid, self.MARKER):
                self.log(f"stopped a stray relay process (pid {pid})")
        self.paths.ensure()
        argv = [sys.executable, "-m", "soundscape.mac.ioproc", "--socket", self.socket_path,
                "--input", blackhole.uid, "--name", cfg.description,
                "--uid", f"{AGGREGATE_UID_PREFIX}{cfg.sink_name}.relay"]
        if cfg.stereo_split:
            self.log("note: stereo split is ON: the left speaker plays only the left channel and the right speaker "
                     "only the right. Turn it off (`soundscape config set stereo_split false`) for full audio everywhere")
        ordered = sorted(bound, key=lambda b: b.sink.headset_mode)  # a music-mode speaker drives the clock
        for b in ordered:
            argv += ["--output", f"{b.speaker.position}:{b.sink.name}:{b.speaker.offset_ms:.3f}"]
        if cfg.stereo_split:
            argv.append("--stereo")
        block = relay_block_size(cfg, len(bound))
        argv += ["--buffer", str(block)]
        self.log(f"relay block: {block} frames ({block / 48:.0f} ms at 48 kHz)")
        if cfg.pin_microphone:
            mic = cfg.microphone if cfg.microphone and cfg.microphone != "builtin" else self.audio.builtin_microphone()
            if mic:
                argv += ["--mic", mic]
        log = self.runner.open_log(str(self.paths.engine_log))
        proc = self.runner.popen(argv, stdout=log, stderr=log, start_new_session=True,
                                 env=dict(os.environ, PYTHONUNBUFFERED="1"))
        deadline = time.monotonic() + cfg.sink_timeout_s
        ready = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AudioError("the relay engine exited during start-up", f"see {self.paths.engine_log}")
            if os.path.exists(self.socket_path):
                try:
                    if self._send("ping") == "pong":
                        ready = True
                        break
                except AudioError:
                    pass
            time.sleep(0.2)
        if not ready:
            try:
                proc.terminate()
            except OSError:
                pass
            raise AudioError("the relay engine did not come up in time", f"see {self.paths.engine_log}")
        state = EngineState(engine=self.name, sink_name=blackhole.uid, pid=proc.pid, conf=self.socket_path,
                            with_delays=True, stereo_split=cfg.stereo_split)
        for b in bound:
            state.outputs.append(OutputState(address=b.speaker.address, position=b.speaker.position,
                                             label=b.speaker.label, sink=b.sink.name, offset_ms=b.speaker.offset_ms,
                                             delay_node=b.speaker.position, sink_index=b.sink.index))
        self.audio.group = [b.sink.name for b in bound]
        self._route_default(state, cfg)
        level = self.audio.ca.volume(blackhole.id)
        if level is not None and level < 0.999:
            # the relay runs at unity gain: BlackHole (the Mac's volume) feeds every speaker unchanged
            if self.audio.ca.set_volume(blackhole.id, 1.0):
                self.log(f"Mac volume (BlackHole) raised from {int(round(level * 100))}% to 100% so no level is lost; "
                         "use the speaker sliders or the speakers' own buttons to turn down")
            else:
                self.log(f"the Mac's volume (BlackHole) is at {int(round(level * 100))}%; raise it with the volume keys "
                         "for full level")
        return state

    def teardown(self, state: EngineState) -> None:
        self._restore_default(state)
        try:
            self._send("quit")
        except AudioError:
            pass
        if state.pid:
            time.sleep(0.3)
            base.kill_pid(state.pid, self.MARKER)
        try:
            os.unlink(self.socket_path)
        except OSError:
            pass

    def alive(self, state: EngineState) -> bool:
        if not state.pid or not base.pid_is_ours(state.pid, self.MARKER):
            return False
        try:
            return self._send("ping") == "pong"
        except AudioError:
            return False

    def mute_output(self, state: EngineState, address: str, muted: bool) -> None:
        out = state.output_for(address)
        if out is None:
            raise AudioError(f"{address} is not part of the running session")
        reply = self._send(f"mute {out.sink} {1 if muted else 0}")
        if not reply.startswith("ok"):
            raise AudioError(f"relay engine refused the mute: {reply}")

    def describe(self, state: EngineState) -> Optional[dict]:
        """Live layout from the relay process (which buffer feeds which speaker, frames written)."""
        import json
        try:
            reply = self._send("status")
            return json.loads(reply)
        except (AudioError, ValueError):
            return None

    def set_offset(self, state: EngineState, address: str, offset_ms: float) -> None:
        out = state.output_for(address)
        if out is None:
            raise AudioError(f"{address} is not part of the running session")
        reply = self._send(f"delay {out.sink} {offset_ms:.3f}")
        if not reply.startswith("ok"):
            raise AudioError(f"relay engine refused the delay: {reply}")
        out.offset_ms = offset_ms


def choose_mac_engine(cfg: Config, runner: Runner, audio: MacAudio, paths: Paths, log: Log) -> Engine:
    choice = cfg.engine
    if choice == "auto":
        choice = "relay" if audio.blackhole() is not None else "aggregate"
    if choice == "relay":
        return RelayEngine(runner, audio, paths, log)
    return AggregateEngine(runner, audio, paths, log)


MAC_ENGINES = {"aggregate": AggregateEngine, "relay": RelayEngine}
