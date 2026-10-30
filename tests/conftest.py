"""Fakes for every external tool Soundscape touches, plus a stateful World that emulates
BlueZ + a sound server well enough to run the real orchestration code end to end."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

from soundscape import engine as engine_mod
from soundscape.config import Config, Speaker, mac_underscored
from soundscape.paths import Paths
from soundscape.runner import Result, Runner
from soundscape.session import Session

DEFAULT_TOOLS = {"busctl", "bluetoothctl", "pactl", "paplay", "parec", "pipewire", "pw-dump", "pw-cli", "mpv"}

MAC_A = "AA:BB:CC:DD:EE:01"
MAC_B = "AA:BB:CC:DD:EE:02"
MAC_PHONE = "11:22:33:44:55:66"


class FakeProcess:
    _next_pid = 40000

    def __init__(self, argv: List[str], kwargs: Dict[str, Any], on_start=None, on_stop=None):
        self.argv = list(argv)
        self.kwargs = kwargs
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1
        self.stdin = io.StringIO()
        self.returncode: Optional[int] = None
        self.on_stop = on_stop
        if on_start:
            on_start(self)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self._finish(0)
        return self.returncode

    def terminate(self):
        self._finish(-15)

    def kill(self):
        self._finish(-9)

    def _finish(self, code: int):
        if self.returncode is None:
            self.returncode = code
            if self.on_stop:
                self.on_stop(self)


class FakeRunner(Runner):
    def __init__(self, tools=None):
        self.tools = set(DEFAULT_TOOLS if tools is None else tools)
        self.calls: List[List[str]] = []
        self.procs: List[FakeProcess] = []
        self.handlers: List[Tuple[Callable[[List[str]], bool], Any]] = []
        self.popen_handlers: Dict[str, Callable[[List[str], Dict[str, Any]], FakeProcess]] = {}
        self.logs: List[io.BytesIO] = []

    def which(self, name: str):
        return f"/usr/bin/{name}" if name in self.tools else None

    def on(self, prefix, response):
        """prefix: tuple of leading argv items (use '*' as a wildcard for one position)."""
        prefix = tuple(prefix)

        def pred(argv: List[str]) -> bool:
            if len(argv) < len(prefix):
                return False
            return all(p == "*" or p == a for p, a in zip(prefix, argv))

        self.handlers.insert(0, (pred, response))

    def on_popen(self, name: str, factory):
        self.popen_handlers[name] = factory

    def run(self, argv, *, timeout=30, input=None, env=None) -> Result:
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] not in self.tools:
            from soundscape.errors import ToolMissing
            raise ToolMissing(argv[0])
        for pred, resp in self.handlers:
            if pred(argv):
                out = resp(argv) if callable(resp) else resp
                if isinstance(out, Result):
                    return out
                if isinstance(out, tuple):
                    code, stdout = out[0], out[1]
                    stderr = out[2] if len(out) > 2 else ""
                    return Result(tuple(argv), code, stdout, stderr)
                return Result(tuple(argv), 0, str(out), "")
        return Result(tuple(argv), 1, "", f"unhandled: {' '.join(argv)}")

    def popen(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] not in self.tools:
            from soundscape.errors import ToolMissing
            raise ToolMissing(argv[0])
        factory = self.popen_handlers.get(argv[0])
        proc = factory(argv, kwargs) if factory else FakeProcess(argv, kwargs)
        self.procs.append(proc)
        return proc

    def open_log(self, path: str):
        buf = io.BytesIO()
        self.logs.append(buf)
        return buf

    def calls_with(self, *prefix) -> List[List[str]]:
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


def variant(type_: str, data: Any) -> Dict[str, Any]:
    return {"type": type_, "data": data}


class World:
    """Stateful emulation of bluetoothd + pipewire-pulse (or PulseAudio)."""

    def __init__(self, runner: FakeRunner, pipewire: bool = True):
        self.runner = runner
        self.pipewire = pipewire
        self.adapter_powered = True
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.sinks: Dict[str, Dict[str, Any]] = {}
        self.cards: Dict[str, Dict[str, Any]] = {}
        self.modules: Dict[int, Tuple[str, str]] = {}
        self.sink_inputs: Dict[int, int] = {}
        self.default_sink = "alsa_output.pci-0000_00_1f.3.analog-stereo"
        self.default_source = "alsa_input.pci-0000_00_1f.3.analog-stereo"
        self.pw_nodes: Dict[str, int] = {}
        self.delays: Dict[str, float] = {}
        self.delay_targets: Dict[str, str] = {}  # delay node -> speaker sink it feeds
        self.port_offsets: Dict[Tuple[str, str], int] = {}
        self.live_pids: Dict[int, FakeProcess] = {}
        self.fail_connect: set = set()
        self.fail_pair: set = set()
        self.no_sink_for: set = set()
        self.engine_breaks_with_delays = False
        self.next_index = 100
        self.add_sink(self.default_sink, "Built-in Audio", {})
        self.add_sink(self.default_source, "Built-in Mic", {})  # as sink for simplicity of lookups
        self._install()

    # -- state helpers ---------------------------------------------------
    def _idx(self) -> int:
        self.next_index += 1
        return self.next_index

    def add_device(self, mac: str, name: str, paired=False, connected=False, trusted=False, audio=True, rssi=-55):
        self.devices[mac] = {"name": name, "paired": paired, "connected": connected, "trusted": trusted,
                             "audio": audio, "rssi": rssi}
        if connected and audio:
            self._ensure_bt_sink(mac)

    def bt_sink_name(self, mac: str) -> str:
        key = mac_underscored(mac)
        return f"bluez_output.{key}.1" if self.pipewire else f"bluez_sink.{key}.a2dp_sink"

    def add_sink(self, name: str, description: str, props: Dict[str, str], ports=("speaker-output",)):
        self.sinks[name] = {"index": self._idx(), "description": description, "volume": 100, "mute": False,
                            "props": props, "ports": list(ports), "active_port": ports[0] if ports else None}
        return self.sinks[name]

    def _ensure_bt_sink(self, mac: str):
        if mac in self.no_sink_for:
            return
        name = self.bt_sink_name(mac)
        if name not in self.sinks:
            props = {"api.bluez5.address": mac, "device.api": "bluez5"} if self.pipewire else {"device.string": mac, "device.api": "bluez"}
            self.add_sink(name, self.devices[mac]["name"], props)
            self.cards[f"bluez_card.{mac_underscored(mac)}"] = {"index": self._idx(), "ports": ["speaker-output"],
                                                                "props": {"api.bluez5.address": mac}}

    def _drop_bt_sink(self, mac: str):
        self.sinks.pop(self.bt_sink_name(mac), None)
        self.cards.pop(f"bluez_card.{mac_underscored(mac)}", None)
        # node.dont-reconnect: the delay filter targeting this sink is destroyed too
        for node in list(self.pw_nodes):
            if node.endswith("_delay") and self.delay_targets.get(node) == self.bt_sink_name(mac):
                self.pw_nodes.pop(node, None)
                self.delays.pop(node, None)
                self.pw_nodes.pop(f"output.soundscape_{node}", None)

    # -- busctl ------------------------------------------------------------
    def managed_objects(self) -> str:
        objs: Dict[str, Any] = {
            "/org/bluez": {"org.bluez.AgentManager1": {}},
            "/org/bluez/hci0": {"org.bluez.Adapter1": {
                "Address": variant("s", "00:1A:7D:DA:71:13"), "Alias": variant("s", "laptop"),
                "Powered": variant("b", self.adapter_powered), "Discovering": variant("b", False)}},
        }
        for mac, d in self.devices.items():
            uuids = ["0000110b-0000-1000-8000-00805f9b34fb", "0000110e-0000-1000-8000-00805f9b34fb"] if d["audio"] else \
                ["0000110a-0000-1000-8000-00805f9b34fb"]
            props = {"Address": variant("s", mac), "Name": variant("s", d["name"]), "Alias": variant("s", d["name"]),
                     "Paired": variant("b", d["paired"]), "Trusted": variant("b", d["trusted"]),
                     "Connected": variant("b", d["connected"]), "UUIDs": variant("as", uuids),
                     "Adapter": variant("o", "/org/bluez/hci0"), "RSSI": variant("n", d["rssi"])}
            if d["audio"]:
                props["Icon"] = variant("s", "audio-card")
            objs[f"/org/bluez/hci0/dev_{mac_underscored(mac)}"] = {"org.bluez.Device1": props}
        return json.dumps({"type": "a{oa{sa{sv}}}", "data": [objs]})

    def _mac_from_path(self, path: str) -> str:
        return path.rsplit("dev_", 1)[1].replace("_", ":")

    def _busctl(self, argv: List[str]):
        if "GetManagedObjects" in argv:
            return self.managed_objects()
        if argv[1:3] == ["--system", "set-property"]:
            path, iface, prop, _t, value = argv[4:9]
            if prop == "Powered":
                self.adapter_powered = value == "true"
                return ""
            if prop == "Trusted":
                self.devices[self._mac_from_path(path)]["trusted"] = value == "true"
                return ""
        if "call" in argv:
            i = argv.index("call")
            path = argv[i + 2]
            method = argv[i + 4]
            if method == "RemoveDevice":
                mac = self._mac_from_path(argv[i + 6])
                self.devices.pop(mac, None)
                self._drop_bt_sink(mac)
                return ""
            mac = self._mac_from_path(path)
            dev = self.devices.get(mac)
            if dev is None:
                return (1, "", "Call failed: Method \"Connect\" with signature \"\" on interface \"org.bluez.Device1\" doesn't exist")
            if method == "Pair":
                if mac in self.fail_pair:
                    return (1, "", "Call failed: Authentication Failed")
                dev["paired"] = True
                return ""
            if method == "Connect":
                if mac in self.fail_connect:
                    return (1, "", "Call failed: br-connection-page-timeout")
                dev["connected"] = True
                if dev["audio"]:
                    self._ensure_bt_sink(mac)
                return ""
            if method == "Disconnect":
                dev["connected"] = False
                self._drop_bt_sink(mac)
                return ""
        return (1, "", f"fake busctl: unhandled {argv}")

    # -- pactl -------------------------------------------------------------
    def _sinks_json(self) -> str:
        out = []
        for name, s in self.sinks.items():
            out.append({"index": s["index"], "state": "RUNNING", "name": name, "description": s["description"],
                        "driver": "PipeWire" if self.pipewire else "module-bluez5-device.c",
                        "sample_specification": "s16le 2ch 48000Hz", "owner_module": 4294967295, "mute": s["mute"],
                        "volume": {"front-left": {"value": 65536, "value_percent": f"{s['volume']}%", "db": "0.00 dB"},
                                   "front-right": {"value": 65536, "value_percent": f"{s['volume']}%", "db": "0.00 dB"}},
                        "monitor_source": f"{name}.monitor", "properties": s["props"],
                        "ports": [{"name": p, "description": p, "type": "Speaker"} for p in s["ports"]],
                        "active_port": s["active_port"], "formats": ["pcm"]})
        return json.dumps(out)

    def _cards_json(self) -> str:
        return json.dumps([{"index": c["index"], "name": name, "driver": "PipeWire", "properties": c["props"],
                            "active_profile": "a2dp-sink",
                            "ports": {p: {"description": p, "type": "Speaker", "latency_offset": 0} for p in c["ports"]}}
                           for name, c in self.cards.items()])

    def _sink_by_index(self, index: int) -> Optional[str]:
        for name, s in self.sinks.items():
            if s["index"] == index:
                return name
        return None

    def _pactl(self, argv: List[str]):
        a = argv[1:]
        if a == ["info"]:
            server = "PulseAudio (on PipeWire 1.0.5)" if self.pipewire else "pulseaudio"
            return (f"Server String: /run/user/1000/pulse/native\nServer Name: {server}\n"
                    f"Server Version: 15.0.0\nDefault Sink: {self.default_sink}\nDefault Source: {self.default_source}\n")
        if a[:3] == ["-f", "json", "list"]:
            kind = a[3]
            if kind == "sinks":
                return self._sinks_json()
            if kind == "cards":
                return self._cards_json()
        if a[:3] == ["list", "short", "modules"]:
            return "".join(f"{i}\t{n}\t{args}\n" for i, (n, args) in self.modules.items())
        if a[:3] == ["list", "short", "sink-inputs"]:
            return "".join(f"{i}\t{s}\tclient\tprotocol-native.c\ts16le 2ch 44100Hz\n" for i, s in self.sink_inputs.items())
        if a[0] == "load-module":
            name = a[1]
            args = " ".join(a[2:])
            if name == "module-combine-sink":
                m = re.search(r"resample_method=(\S+)", args)
                if m and m.group(1) == "soxr-vhq" and getattr(self, "no_soxr", False):
                    return (1, "", "Failure: Module initialization failed")
                sink_name = re.search(r"sink_name=(\S+)", args).group(1)
                self.add_sink(sink_name, "Soundscape", {"device.class": "filter"}, ports=())
                self.sinks[sink_name]["props"]["module"] = str(self._idx())
            idx = self._idx()
            self.modules[idx] = (name, args)
            return f"{idx}\n"
        if a[0] == "unload-module":
            idx = int(a[1])
            mod = self.modules.pop(idx, None)
            if mod and mod[0] == "module-combine-sink":
                sink_name = re.search(r"sink_name=(\S+)", mod[1]).group(1)
                self.sinks.pop(sink_name, None)
            return "" if mod else (1, "", "Failure: No such entity")
        if a[0] == "set-default-sink":
            if a[1] not in self.sinks:
                return (1, "", "Failure: No such entity")
            self.default_sink = a[1]
            return ""
        if a[0] == "move-sink-input":
            self.sink_inputs[int(a[1])] = self.sinks[a[2]]["index"]
            return ""
        if a[0] == "set-sink-volume":
            if a[1] not in self.sinks:
                return (1, "", "Failure: No such entity")
            self.sinks[a[1]]["volume"] = int(a[2].rstrip("%"))
            return ""
        if a[0] == "set-sink-mute":
            if a[1] not in self.sinks:
                return (1, "", "Failure: No such entity")
            self.sinks[a[1]]["mute"] = a[2] in ("1", "true", "yes")
            return ""
        if a[0] == "set-port-latency-offset":
            self.port_offsets[(a[1], a[2])] = int(a[3])
            return ""
        return (1, "", f"fake pactl: unhandled {a}")

    # -- pipewire tools ------------------------------------------------------
    def _pw_dump(self, argv):
        return json.dumps([{"id": nid, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": name}}}
                           for name, nid in self.pw_nodes.items()] +
                          [{"id": 1, "type": "PipeWire:Interface:Core", "info": {}}])

    def _pw_cli(self, argv):
        if argv[1] == "set-param":
            nid = int(argv[2])
            name = next((n for n, i in self.pw_nodes.items() if i == nid), None)
            m = re.search(r'"[^"]+"\s+([0-9.]+)', argv[4])
            if name is None or not m:
                return (1, "", "Error: no global")
            self.delays[name] = float(m.group(1))
            return ""
        return (1, "", "fake pw-cli: unhandled")

    def _spawn_engine(self, argv: List[str], kwargs: Dict[str, Any]) -> FakeProcess:
        conf_dir = Path(kwargs["env"]["PIPEWIRE_CONFIG_DIR"])
        text = (conf_dir / argv[2]).read_text()
        has_delays = "libpipewire-module-filter-chain" in text
        sink_name = re.search(r'combine.mode = sink\s+node.name = "([^"]+)"', text).group(1)
        delay_nodes = re.findall(r'capture.props = \{\s+node.name = "([^"]+)"', text)
        initial = re.findall(r'control = \{ "Delay \(s\)" = ([0-9.]+) \}', text)
        targets = re.findall(r'target.object = "([^"]+)"', text)

        def on_start(proc: FakeProcess):
            if has_delays and self.engine_breaks_with_delays:
                proc.returncode = 1
                return
            self.live_pids[proc.pid] = proc
            self.add_sink(sink_name, "Soundscape (both speakers)", {"node.name": sink_name}, ports=())
            self.pw_nodes[sink_name] = self._idx()
            for node, value, target in zip(delay_nodes, initial, targets):
                self.pw_nodes[node] = self._idx()
                self.delays[node] = float(value)
                self.delay_targets[node] = target
                self.pw_nodes[f"output.{sink_name}_{node}"] = self._idx()

        def on_stop(proc: FakeProcess):
            self.live_pids.pop(proc.pid, None)
            self.sinks.pop(sink_name, None)
            self.pw_nodes.pop(sink_name, None)
            for node in delay_nodes:
                self.pw_nodes.pop(node, None)
                self.delays.pop(node, None)
                self.delay_targets.pop(node, None)
                self.pw_nodes.pop(f"output.{sink_name}_{node}", None)

        return FakeProcess(argv, kwargs, on_start=on_start, on_stop=on_stop)

    def _install(self):
        r = self.runner
        r.on(("busctl",), self._busctl)
        r.on(("pactl",), self._pactl)
        r.on(("pw-dump",), self._pw_dump)
        r.on(("pw-cli",), self._pw_cli)
        r.on(("pipewire", "--version"), "pipewire\nCompiled with libpipewire 1.0.5\nLinked with libpipewire 1.0.5\n")
        r.on(("playerctl",), "")
        r.on_popen("pipewire", self._spawn_engine)

    # -- test conveniences -----------------------------------------------------
    def pid_is_ours(self, pid: int, marker: str) -> bool:
        return pid in self.live_pids

    def kill_pid(self, pid: int, marker: str, grace: float = 3.0) -> bool:
        proc = self.live_pids.get(pid)
        if not proc:
            return False
        proc.terminate()
        return True

    def two_speakers(self, connected=False, paired=True):
        self.add_device(MAC_A, "Kitchen", paired=paired, trusted=paired, connected=connected)
        self.add_device(MAC_B, "Living Room", paired=paired, trusted=paired, connected=connected)
        self.add_device(MAC_PHONE, "Pixel", paired=True, audio=False)


@pytest.fixture(autouse=True)
def _linux_platform(monkeypatch):
    """Tests emulate a Linux box unless they explicitly opt into the macOS backend."""
    monkeypatch.setenv("SOUNDSCAPE_PLATFORM", "linux")


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def world(runner, monkeypatch):
    w = World(runner, pipewire=True)
    monkeypatch.setattr(engine_mod, "pid_is_ours", w.pid_is_ours)
    monkeypatch.setattr(engine_mod, "kill_pid", w.kill_pid)
    return w


@pytest.fixture
def pulse_world(runner, monkeypatch):
    runner.tools -= {"pipewire", "pw-dump", "pw-cli"}
    w = World(runner, pipewire=False)
    monkeypatch.setattr(engine_mod, "pid_is_ours", w.pid_is_ours)
    monkeypatch.setattr(engine_mod, "kill_pid", w.kill_pid)
    return w


@pytest.fixture
def paths(tmp_path):
    return Paths.under(tmp_path)


def make_config(**overrides) -> Config:
    cfg = Config(speakers=[Speaker(MAC_A, "Kitchen", "left"), Speaker(MAC_B, "Living Room", "right")],
                 connect_timeout_s=1.0, sink_timeout_s=0.4)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


@pytest.fixture
def session(runner, world, paths):
    world.two_speakers()
    logs: List[str] = []
    s = Session(runner=runner, paths=paths, log=logs.append, config=make_config(), sleep=lambda _s: None)
    s.logs = logs  # type: ignore[attr-defined]
    return s


@pytest.fixture
def pulse_session(runner, pulse_world, paths):
    pulse_world.two_speakers()
    logs: List[str] = []
    s = Session(runner=runner, paths=paths, log=logs.append, config=make_config(), sleep=lambda _s: None)
    s.logs = logs  # type: ignore[attr-defined]
    return s
