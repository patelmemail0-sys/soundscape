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
from typing import Callable, List

from .. import engine as base
from ..config import Config
from ..engine import Bound, Engine, EngineState, OutputState
from ..errors import AudioError
from ..paths import Paths
from ..runner import Runner
from .audio import BLACKHOLE_HINT, MacAudio
from .coreaudio import AGGREGATE_UID_PREFIX

Log = Callable[[str], None]


class AggregateEngine(Engine):
    name = "aggregate"

    def __init__(self, runner: Runner, audio: MacAudio, paths: Paths, log: Log):
        super().__init__(runner, audio, paths, log)
        self.audio = audio

    def _uid(self, cfg: Config) -> str:
        return f"{AGGREGATE_UID_PREFIX}{cfg.sink_name}"

    def _cleanup(self, uid: str) -> None:
        for d in self.audio.ca.soundscape_aggregates():
            if d.uid == uid:
                try:
                    self.audio.ca.destroy_aggregate(d.id)
                    self.log("removed a leftover Soundscape aggregate device")
                except AudioError as exc:
                    self.log(f"could not remove old aggregate: {exc.message}")

    def build(self, bound: List[Bound], cfg: Config) -> EngineState:
        uid = self._uid(cfg)
        self._cleanup(uid)
        master = bound[0].sink.name
        subdevices = [(b.sink.name, b.sink.name != master) for b in bound]
        agg_id = self.audio.ca.create_aggregate(uid, cfg.description, subdevices, master_uid=master, private=False)
        deadline = time.monotonic() + cfg.sink_timeout_s
        while self.audio.ca.device_by_uid(uid) is None:
            if time.monotonic() > deadline:
                raise AudioError("the aggregate device did not appear", "open Audio MIDI Setup and check for errors")
            time.sleep(0.2)
        state = EngineState(engine=self.name, sink_name=uid, device_id=agg_id)
        for b in bound:
            state.outputs.append(OutputState(address=b.speaker.address, position=b.speaker.position,
                                             label=b.speaker.label, sink=b.sink.name, offset_ms=b.speaker.offset_ms,
                                             sink_index=b.sink.index))
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
        for pid in base.find_pids(self.MARKER):
            if base.kill_pid(pid, self.MARKER):
                self.log(f"stopped a stray relay process (pid {pid})")
        self.paths.ensure()
        argv = [sys.executable, "-m", "soundscape.mac.ioproc", "--socket", self.socket_path,
                "--input", blackhole.uid, "--name", cfg.description,
                "--uid", f"{AGGREGATE_UID_PREFIX}{cfg.sink_name}.relay"]
        for b in bound:
            argv += ["--output", f"{b.speaker.position}:{b.sink.name}:{b.speaker.offset_ms:.3f}"]
        if cfg.stereo_split:
            argv.append("--stereo")
        buffer = os.environ.get("SOUNDSCAPE_RELAY_BUFFER")
        if buffer and buffer.isdigit():
            argv += ["--buffer", buffer]
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
