"""Engines turn N Bluetooth sinks into one virtual output.

* :class:`PipeWireEngine` - native PipeWire combine-stream with per-speaker delay filters.
* :class:`PulseEngine`    - PulseAudio ``module-combine-sink`` driven through ``pactl``
                            (also the fallback on PipeWire systems without the ``pipewire`` binary).
"""

from __future__ import annotations

import json
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import Config, Speaker
from .errors import AudioError
from .paths import Paths
from .pipewire import EnginePlan, OutputPlan, PipeWireCtl, render_config
from .pulse import PulseCtl, Sink
from .runner import Runner

Log = Callable[[str], None]


@dataclass
class Bound:
    """A configured speaker matched to its live sink."""
    speaker: Speaker
    sink: Sink


@dataclass
class OutputState:
    address: str
    position: str
    label: str
    sink: str
    offset_ms: float = 0.0
    delay_node: Optional[str] = None


@dataclass
class EngineState:
    engine: str
    sink_name: str
    outputs: List[OutputState] = field(default_factory=list)
    pid: Optional[int] = None
    conf: Optional[str] = None
    modules: List[int] = field(default_factory=list)
    previous_default_sink: str = ""
    with_delays: bool = False
    stereo_split: bool = False
    created_at: float = field(default_factory=time.time)

    def output_for(self, address: str) -> Optional[OutputState]:
        for o in self.outputs:
            if o.address == address:
                return o
        return None

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @classmethod
    def load(cls, path: Path) -> Optional[EngineState]:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
        outputs = [OutputState(**o) for o in data.pop("outputs", [])]
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(outputs=outputs, **{k: v for k, v in data.items() if k in known})


def pid_is_ours(pid: int, marker: str) -> bool:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    return marker.encode() in cmdline


def find_pids(marker: str) -> List[int]:
    """PIDs of processes whose command line mentions ``marker`` (stray engines after a crash)."""
    found: List[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for entry in entries:
        if entry.isdigit() and int(entry) != os.getpid() and pid_is_ours(int(entry), marker):
            found.append(int(entry))
    return found


def kill_pid(pid: int, marker: str, grace: float = 3.0) -> bool:
    if not pid_is_ours(pid, marker):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not pid_is_ours(pid, marker):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return True


class Engine:
    name = "base"

    def __init__(self, runner: Runner, pulse: Any, paths: Paths, log: Log):
        self.runner, self.paths, self.log = runner, paths, log
        self.pulse: Any = pulse  # PulseCtl on Linux, MacAudio on macOS (same method names)

    def build(self, bound: List[Bound], cfg: Config) -> EngineState:  # pragma: no cover - abstract
        raise NotImplementedError

    def teardown(self, state: EngineState) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def set_offset(self, state: EngineState, address: str, offset_ms: float) -> None:  # pragma: no cover
        raise NotImplementedError

    def alive(self, state: EngineState) -> bool:
        return self.pulse.sink(state.sink_name) is not None

    # shared helpers
    def _wait_for_sink(self, name: str, timeout: float, poll: float = 0.25) -> Sink:
        deadline = time.monotonic() + timeout
        while True:
            sink = self.pulse.sink(name)
            if sink:
                return sink
            if time.monotonic() >= deadline:
                raise AudioError(f"virtual sink {name!r} did not appear within {timeout:.0f}s")
            time.sleep(poll)

    def _route_default(self, state: EngineState, cfg: Config) -> None:
        if not cfg.set_default_sink:
            return
        info = self.pulse.info()
        if info.default_sink and info.default_sink != state.sink_name:
            state.previous_default_sink = info.default_sink
        self.pulse.set_default_sink(state.sink_name)
        moved = self.pulse.move_all_inputs(state.sink_name)
        if moved:
            self.log(f"moved {moved} playing stream(s) to {state.sink_name}")

    def _restore_default(self, state: EngineState) -> None:
        if state.previous_default_sink and self.pulse.sink(state.previous_default_sink):
            try:
                self.pulse.set_default_sink(state.previous_default_sink)
            except AudioError as exc:
                self.log(f"could not restore default sink: {exc.message}")


class PipeWireEngine(Engine):
    name = "pipewire"
    MARKER = "soundscape-engine.conf"

    def __init__(self, runner: Runner, pulse: PulseCtl, paths: Paths, log: Log, pw: Optional[PipeWireCtl] = None):
        super().__init__(runner, pulse, paths, log)
        self.pw = pw or PipeWireCtl(runner)

    def _plan(self, bound: List[Bound], cfg: Config, with_delays: bool) -> EnginePlan:
        outputs = [OutputPlan(b.speaker.position, b.speaker.label, b.sink.name, b.speaker.offset_ms) for b in bound]
        return EnginePlan(outputs=outputs, sink_name=cfg.sink_name, description=cfg.description,
                          latency_compensate=cfg.latency_compensate, resample_quality=cfg.resample_quality,
                          stereo_split=cfg.stereo_split, with_delays=with_delays)

    def _spawn(self, plan: EnginePlan, cfg: Config) -> int:
        self.paths.ensure()
        conf = self.paths.engine_conf
        conf.write_text(render_config(plan))
        env = dict(os.environ)
        env["PIPEWIRE_CONFIG_DIR"] = str(conf.parent)
        env.pop("PIPEWIRE_CONFIG_NAME", None)
        log = self.runner.open_log(str(self.paths.engine_log))
        proc = self.runner.popen(["pipewire", "-c", conf.name], stdout=log, stderr=log, env=env,
                                 start_new_session=True)
        try:
            self._wait_for_sink(cfg.sink_name, cfg.sink_timeout_s)
        except AudioError:
            if proc.poll() is None:
                proc.terminate()
            raise
        if proc.poll() is not None:
            raise AudioError("the engine process exited right after start", f"see {self.paths.engine_log}")
        return proc.pid

    def build(self, bound: List[Bound], cfg: Config) -> EngineState:
        self.runner.require("pipewire")
        for pid in find_pids(self.MARKER):
            if kill_pid(pid, self.MARKER):
                self.log(f"stopped a stray engine process (pid {pid})")
        with_delays = True
        try:
            pid = self._spawn(self._plan(bound, cfg, True), cfg)
        except AudioError as exc:
            self.log(f"engine with delay filters failed ({exc.message}); retrying without manual delays")
            with_delays = False
            pid = self._spawn(self._plan(bound, cfg, False), cfg)
        outputs = []
        for b in bound:
            outputs.append(OutputState(
                address=b.speaker.address, position=b.speaker.position, label=b.speaker.label,
                sink=b.sink.name, offset_ms=b.speaker.offset_ms,
                delay_node=f"soundscape_{b.speaker.position}_delay" if with_delays else None))
        state = EngineState(engine=self.name, sink_name=cfg.sink_name, outputs=outputs, pid=pid,
                            conf=str(self.paths.engine_conf), with_delays=with_delays,
                            stereo_split=cfg.stereo_split)
        self._route_default(state, cfg)
        return state

    def teardown(self, state: EngineState) -> None:
        if state.pid:
            if kill_pid(state.pid, self.MARKER):
                self.log("stopped engine process")
        self._restore_default(state)
        try:
            self.paths.engine_conf.unlink()
        except OSError:
            pass

    def alive(self, state: EngineState) -> bool:
        return bool(state.pid) and pid_is_ours(state.pid or -1, self.MARKER) and super().alive(state)

    def set_offset(self, state: EngineState, address: str, offset_ms: float) -> None:
        out = state.output_for(address)
        if out is None:
            raise AudioError(f"{address} is not part of the running session")
        if not out.delay_node:
            raise AudioError("this engine run has no delay filters (older PipeWire?)",
                             "manual offsets need PipeWire >= 0.3.65 with the filter-chain delay plugin")
        self.pw.set_delay(out.delay_node, offset_ms / 1000.0)
        out.offset_ms = offset_ms


class PulseEngine(Engine):
    name = "pulse"
    RESAMPLERS = ("soxr-vhq", "speex-float-10", "speex-float-5", None)

    def _load_combine(self, bound: List[Bound], cfg: Config) -> int:
        slaves = ",".join(b.sink.name for b in bound)
        methods = [cfg.pulse_resample_method] + [m for m in self.RESAMPLERS if m != cfg.pulse_resample_method]
        last: Optional[AudioError] = None
        for method in methods:
            try:
                return self.pulse.load_module(
                    "module-combine-sink", sink_name=cfg.sink_name, slaves=slaves,
                    resample_method=method, adjust_time=5,
                    sink_properties=f'device.description="{cfg.description}"')
            except AudioError as exc:
                last = exc
                if method is not None:
                    self.log(f"resampler {method} unavailable, trying the next one")
        assert last is not None
        raise last

    def _unload_leftovers(self, sink_name: str) -> None:
        for mod in self.pulse.modules():
            if mod.name == "module-combine-sink" and f"sink_name={sink_name}" in mod.args:
                if self.pulse.unload_module(mod.index):
                    self.log(f"removed a leftover combine sink (module {mod.index})")

    def build(self, bound: List[Bound], cfg: Config) -> EngineState:
        if cfg.stereo_split:
            self.log("stereo split is only available with the PipeWire engine; playing full stereo to both")
        self._unload_leftovers(cfg.sink_name)
        index = self._load_combine(bound, cfg)
        state = EngineState(engine=self.name, sink_name=cfg.sink_name, modules=[index])
        self._wait_for_sink(cfg.sink_name, cfg.sink_timeout_s)
        for b in bound:
            state.outputs.append(OutputState(address=b.speaker.address, position=b.speaker.position,
                                             label=b.speaker.label, sink=b.sink.name,
                                             offset_ms=b.speaker.offset_ms))
        self._apply_port_offsets(state)
        self._route_default(state, cfg)
        return state

    def _apply_port_offsets(self, state: EngineState) -> None:
        """PulseAudio has no per-output delay. ``set-port-latency-offset`` tells the server a
        port has hidden downstream latency, so the combine sink feeds it *earlier*. Delaying
        speaker A by X therefore means giving every other speaker a +X offset."""
        most = max((o.offset_ms for o in state.outputs), default=0.0)
        for o in state.outputs:
            lead_us = int(round((most - o.offset_ms) * 1000))
            card = self.pulse.card_for_mac(o.address)
            sink = self.pulse.sink(o.sink)
            port = (sink.active_port if sink and sink.active_port else None) or (card.ports[0] if card and card.ports else None)
            if not card or not port:
                if lead_us:
                    self.log(f"cannot find card/port for {o.label}; sync offset not applied")
                continue
            try:
                self.pulse.set_port_latency_offset(card.name, port, lead_us)
            except AudioError as exc:
                self.log(f"port latency offset for {o.label} failed: {exc.message}")

    def set_offset(self, state: EngineState, address: str, offset_ms: float) -> None:
        out = state.output_for(address)
        if out is None:
            raise AudioError(f"{address} is not part of the running session")
        out.offset_ms = offset_ms
        self._apply_port_offsets(state)

    def teardown(self, state: EngineState) -> None:
        for o in state.outputs:
            card = self.pulse.card_for_mac(o.address)
            sink = self.pulse.sink(o.sink)
            port = (sink.active_port if sink else None) or (card.ports[0] if card and card.ports else None)
            if card and port:
                try:
                    self.pulse.set_port_latency_offset(card.name, port, 0)
                except AudioError:
                    pass
        for index in sorted(state.modules, reverse=True):
            self.pulse.unload_module(index)
        self._unload_leftovers(state.sink_name)
        self._restore_default(state)


def choose_engine(cfg: Config, runner: Runner, pulse: PulseCtl, paths: Paths, log: Log,
                  is_pipewire: Optional[bool] = None) -> Engine:
    choice = cfg.engine
    if choice == "auto":
        if is_pipewire is None:
            is_pipewire = pulse.info().is_pipewire
        choice = "pipewire" if (is_pipewire and runner.which("pipewire") and runner.which("pw-dump")) else "pulse"
    if choice == "pipewire":
        return PipeWireEngine(runner, pulse, paths, log)
    return PulseEngine(runner, pulse, paths, log)


ENGINES: Dict[str, type] = {"pipewire": PipeWireEngine, "pulse": PulseEngine}
