"""macOS backend tests with a fake HAL and fake blueutil/system_profiler/afplay."""

from __future__ import annotations

import ctypes
import json
import struct
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

from soundscape import engine as engine_mod
from soundscape.bluez import Device
from soundscape.config import Config, Speaker
from soundscape.errors import AudioError, BluetoothError
from soundscape.mac import coreaudio as ca
from soundscape.mac.audio import MacAudio, device_to_sink
from soundscape.mac.bluetooth import MacBluetooth, parse_blueutil_json, parse_system_profiler
from soundscape.mac.engine import AggregateEngine, RelayEngine, choose_mac_engine
from soundscape.mac.ioproc import ControlServer, Mixer, Output, build_parser, short_socket_path
from soundscape.paths import Paths
from soundscape.session import Session
from tests.conftest import MAC_A, MAC_B, FakeProcess, FakeRunner

UID_A = "AA-BB-CC-DD-EE-01:output"
UID_B = "AA-BB-CC-DD-EE-02:output"
UID_BUILTIN = "BuiltInSpeakerDevice"
UID_MIC = "BuiltInMicrophoneDevice"
UID_BH = "BlackHole2ch_UID"

SCOPE_OUT, SCOPE_IN, SCOPE_GLOBAL = ca.kAudioObjectPropertyScopeOutput, ca.kAudioObjectPropertyScopeInput, ca.kAudioObjectPropertyScopeGlobal


def asbd(channels: int, rate: float = 48000.0) -> bytes:
    d = ca.AudioStreamBasicDescription(rate, ca.kAudioFormatLinearPCM, ca.kAudioFormatFlagIsFloat, 4 * channels, 1,
                                       4 * channels, channels, 32, 0)
    return bytes(d)


class FakeHAL:
    """Property store keyed by (object, selector, scope, element). Streams are objects too."""

    def __init__(self):
        self.props: Dict[Tuple[int, int, int, int], bytes] = {}
        self.strings: Dict[Tuple[int, int], str] = {}
        self.devices: List[int] = []
        self.next_id = 100
        self.aggregates: Dict[int, dict] = {}
        self.default_output = 0
        self.default_input = 0
        self.destroyed: List[int] = []
        self.set_calls: List[tuple] = []
        self.lib = None

    # ---- construction helpers
    def add_device(self, uid: str, name: str, transport: str, out_ch=2, in_ch=0, rate=48000.0, volume=True, mute=False):
        did = self.next_id
        self.next_id += 1
        self.devices.append(did)
        self.strings[(did, ca.kAudioDevicePropertyDeviceUID)] = uid
        self.strings[(did, ca.kAudioObjectPropertyName)] = name
        code = {v: k for k, v in ca.TRANSPORT_NAMES.items()}[transport]
        self.props[(did, ca.kAudioDevicePropertyTransportType, SCOPE_GLOBAL, 0)] = struct.pack("I", code)
        self.props[(did, ca.kAudioDevicePropertyNominalSampleRate, SCOPE_GLOBAL, 0)] = struct.pack("d", rate)
        for scope, ch in ((SCOPE_OUT, out_ch), (SCOPE_IN, in_ch)):
            if ch:
                sid = self.next_id
                self.next_id += 1
                self.props[(did, ca.kAudioDevicePropertyStreams, scope, 0)] = struct.pack("I", sid)
                self.props[(sid, ca.kAudioStreamPropertyVirtualFormat, SCOPE_GLOBAL, 0)] = asbd(ch, rate)
        if volume and out_ch:
            self.props[(did, ca.kAudioHardwareServiceDeviceProperty_VirtualMainVolume, SCOPE_OUT, 0)] = struct.pack("f", 1.0)
        if mute and out_ch:
            self.props[(did, ca.kAudioDevicePropertyMute, SCOPE_OUT, 0)] = struct.pack("I", 0)
        self.props[(did, ca.kAudioDevicePropertyLatency, SCOPE_OUT, 0)] = struct.pack("I", 256)
        return did

    def remove_device(self, did: int):
        self.devices.remove(did)

    # ---- HAL interface
    def has(self, obj, selector, scope=SCOPE_GLOBAL, element=0):
        return (obj, selector, scope, element) in self.props

    def get_raw(self, obj, selector, scope=SCOPE_GLOBAL, element=0):
        if obj == ca.kAudioObjectSystemObject:
            if selector == ca.kAudioHardwarePropertyDevices:
                return struct.pack(f"{len(self.devices)}I", *self.devices)
            if selector == ca.kAudioHardwarePropertyDefaultOutputDevice:
                return struct.pack("I", self.default_output)
            if selector == ca.kAudioHardwarePropertyDefaultInputDevice:
                return struct.pack("I", self.default_input)
        if selector == ca.kAudioAggregateDevicePropertyActiveSubDeviceList and obj in self.aggregates:
            subs = [self.id_for_uid(u) for u in self.aggregates[obj]["subs"]]
            return struct.pack(f"{len(subs)}I", *subs)
        if selector == ca.kAudioAggregateDevicePropertyFullSubDeviceList and obj in self.aggregates:
            return struct.pack("Q", 0xDEADBEEF)  # a CFArrayRef pointer, never a list of ids
        try:
            return self.props[(obj, selector, scope, element)]
        except KeyError:
            raise ca.HALError(f"no property {selector:#x} on {obj}") from None

    def set_raw(self, obj, selector, data, scope=SCOPE_GLOBAL, element=0):
        self.set_calls.append((obj, selector, scope, element, data))
        if obj == ca.kAudioObjectSystemObject and selector == ca.kAudioHardwarePropertyDefaultOutputDevice:
            self.default_output = struct.unpack("I", data)[0]
            return
        if obj == ca.kAudioObjectSystemObject and selector == ca.kAudioHardwarePropertyDefaultSystemOutputDevice:
            return
        if (obj, selector, scope, element) not in self.props and selector not in (ca.kAudioDevicePropertyBufferFrameSize,):
            raise ca.HALError("not settable")
        self.props[(obj, selector, scope, element)] = data

    def get_cfstring(self, obj, selector, scope=SCOPE_GLOBAL):
        try:
            return self.strings[(obj, selector)]
        except KeyError:
            raise ca.HALError("no string") from None

    def create_aggregate(self, description):
        did = self.add_device(description["uid"], description["name"], "aggregate", out_ch=0, volume=False)
        self.aggregates[did] = {"desc": description, "subs": [s["uid"] for s in description["subdevices"]]}
        return did

    def destroy_aggregate(self, did):
        self.destroyed.append(did)
        self.aggregates.pop(did)
        self.remove_device(did)

    def id_for_uid(self, uid):
        for did in self.devices:
            if self.strings.get((did, ca.kAudioDevicePropertyDeviceUID)) == uid:
                return did
        return 0


def make_hal(blackhole=False):
    hal = FakeHAL()
    builtin = hal.add_device(UID_BUILTIN, "MacBook Pro Speakers", "builtin")
    hal.add_device(UID_MIC, "MacBook Pro Microphone", "builtin", out_ch=0, in_ch=1, volume=False)
    hal.add_device(UID_A, "Kitchen", "bluetooth", mute=True)
    hal.add_device(UID_B, "Living Room", "bluetooth")
    if blackhole:
        hal.add_device(UID_BH, "BlackHole 2ch", "virtual", out_ch=2, in_ch=2)
    hal.default_output = builtin
    hal.default_input = hal.id_for_uid(UID_MIC)
    return hal


# ---------------------------------------------------------------- coreaudio

def test_device_listing_and_bluetooth_lookup():
    core = ca.CoreAudio(make_hal())
    devices = {d.uid: d for d in core.devices()}
    assert devices[UID_A].is_bluetooth and devices[UID_A].bluetooth_address == MAC_A
    assert devices[UID_A].output_channels == 2 and devices[UID_A].output_streams == 1
    assert devices[UID_MIC].input_channels == 1 and devices[UID_MIC].output_channels == 0
    assert core.bluetooth_output(MAC_B).name == "Living Room"
    assert core.bluetooth_output("AA:BB:CC:DD:EE:99") is None
    assert core.device_by_uid(UID_BUILTIN).transport == "builtin"
    assert core.latency_frames(devices[UID_A].id) == 256


def test_default_output_volume_mute():
    hal = make_hal()
    core = ca.CoreAudio(hal)
    a = core.device_by_uid(UID_A)
    core.set_default_output(a.id)
    assert core.default_output() == a.id
    assert core.set_volume(a.id, 0.4) and core.volume(a.id) == pytest.approx(0.4)
    assert core.mute(a.id) is False and core.set_mute(a.id, True) and core.mute(a.id) is True
    b = core.device_by_uid(UID_B)
    assert core.mute(b.id) is None and core.set_mute(b.id, True) is False
    mic = core.device_by_uid(UID_MIC)
    assert core.volume(mic.id) is None and core.set_volume(mic.id, 0.5) is False


def test_create_aggregate_description_and_cleanup():
    hal = make_hal()
    core = ca.CoreAudio(hal)
    agg = core.create_aggregate("soundscape", "Soundscape", [(UID_A, False), (UID_B, True)], master_uid=UID_A)
    desc = hal.aggregates[agg]["desc"]
    assert desc["uid"] == "com.soundscape.soundscape" and desc["master"] == UID_A and desc["private"] == 0
    assert desc["subdevices"] == [{"uid": UID_A, "drift": 0}, {"uid": UID_B, "drift": 1}]
    assert [d.uid for d in core.soundscape_aggregates()] == ["com.soundscape.soundscape"]
    assert core.aggregate_subdevices(agg) == [core.device_by_uid(UID_A).id, core.device_by_uid(UID_B).id]
    core.destroy_aggregate(agg)
    assert core.soundscape_aggregates() == [] and hal.destroyed == [agg]


def test_fourcc_and_transport_table():
    assert ca.fourcc("blue") == 0x626C7565
    assert ca.TRANSPORT_NAMES[ca.fourcc("grup")] == "aggregate"


# ---------------------------------------------------------------- bluetooth

PROFILER = json.dumps({"SPBluetoothDataType": [{
    "device_connected": [{"Kitchen": {"device_address": "AA:BB:CC:DD:EE:01", "device_minorType": "Speaker"}}],
    "device_not_connected": [{"Living Room": {"device_address": "AA:BB:CC:DD:EE:02", "device_minorType": "Speaker"}},
                             {"Magic Mouse": {"device_address": "11:22:33:44:55:66", "device_minorType": "Mouse"}}],
}]})
BLUEUTIL_PAIRED = json.dumps([
    {"address": "aa-bb-cc-dd-ee-01", "name": "Kitchen", "paired": True, "connected": True, "RSSI": -40},
    {"address": "aa-bb-cc-dd-ee-02", "name": "Living Room", "paired": True, "connected": False},
])


class BtWorld:
    def __init__(self, runner: FakeRunner, blueutil=True):
        self.connected = {MAC_A: True, MAC_B: False}
        self.paired = {MAC_A, MAC_B}
        self.fail_connect = set()
        runner.tools |= {"system_profiler", "afplay"}
        if blueutil:
            runner.tools.add("blueutil")
        runner.tools.discard("busctl")
        runner.on(("system_profiler",), lambda argv: PROFILER)
        runner.on(("blueutil",), self.blueutil)

    def blueutil(self, argv):
        a = argv[1:]
        if a[:1] == ["--paired"]:
            items = []
            for mac in sorted(self.paired):
                items.append({"address": mac.lower().replace(":", "-"), "name": {MAC_A: "Kitchen", MAC_B: "Living Room"}.get(mac, mac),
                              "paired": True, "connected": self.connected.get(mac, False)})
            return json.dumps(items)
        if a[:1] == ["--inquiry"]:
            return json.dumps([{"address": "aa-bb-cc-dd-ee-03", "name": "Bedroom", "connected": False}])
        if a[:1] == ["--connect"]:
            if a[1] in self.fail_connect:
                return (1, "", "Failed to connect")
            self.connected[a[1]] = True
            return ""
        if a[:1] == ["--disconnect"]:
            self.connected[a[1]] = False
            return ""
        if a[:1] == ["--is-connected"]:
            return "1" if self.connected.get(a[1]) else "0"
        if a[:1] == ["--pair"]:
            self.paired.add(a[1])
            return ""
        if a[:1] == ["--unpair"]:
            self.paired.discard(a[1])
            return ""
        if a[:1] == ["--power"]:
            return ""
        return (1, "", "unknown")


def test_parsers():
    info = parse_system_profiler(PROFILER)
    assert info[MAC_A]["connected"] and not info[MAC_B]["connected"] and info[MAC_A]["minor_type"] == "Speaker"
    assert parse_blueutil_json(BLUEUTIL_PAIRED)[0]["name"] == "Kitchen"
    assert parse_blueutil_json("") == []
    with pytest.raises(BluetoothError):
        parse_blueutil_json("{bad")
    assert parse_system_profiler("{bad") == {}


def test_mac_bluetooth_devices_merge_and_connect(runner):
    world = BtWorld(runner)
    bt = MacBluetooth(runner, sleep=lambda _s: None)
    devs = {d.address: d for d in bt.devices()}
    assert devs[MAC_A].connected and devs[MAC_A].is_audio_sink and not devs["11:22:33:44:55:66"].is_audio_sink
    assert devs[MAC_A].rssi is None  # blueutil paired listing in our fake has no RSSI
    dev = bt.ensure_connected(MAC_B)
    assert dev.connected and world.connected[MAC_B]
    found = bt.scan(1)
    assert any(d.address == "AA:BB:CC:DD:EE:03" and not d.paired for d in found)
    bt.remove(MAC_B)
    assert MAC_B not in world.paired
    world.fail_connect.add(MAC_A)
    world.connected[MAC_A] = False
    with pytest.raises(BluetoothError):
        bt.ensure_connected(MAC_A, retries=0)


def test_mac_bluetooth_without_blueutil(runner):
    BtWorld(runner, blueutil=False)
    bt = MacBluetooth(runner, sleep=lambda _s: None)
    assert bt.ensure_connected(MAC_A).connected  # already connected: fine
    with pytest.raises(BluetoothError) as exc:
        bt.ensure_connected(MAC_B)
    assert "Bluetooth menu" in exc.value.hint
    with pytest.raises(BluetoothError):
        bt.connect(MAC_B)
    log = []
    assert bt.scan(1, log=log.append) and "blueutil" in log[0]
    with pytest.raises(BluetoothError):
        bt.require_device("AA:BB:CC:DD:EE:09")


# ---------------------------------------------------------------- MacAudio

def test_mac_audio_sinks_volume_mute_and_play(runner):
    BtWorld(runner)
    hal = make_hal()
    audio = MacAudio(runner, coreaudio=ca.CoreAudio(hal))
    info = audio.info()
    assert info.flavour == "CoreAudio" and info.default_sink == UID_BUILTIN and info.default_source == UID_MIC
    sinks = {s.name: s for s in audio.sinks()}
    assert UID_MIC not in sinks and sinks[UID_A].bluetooth_address == MAC_A
    assert audio.bluetooth_sink(MAC_B).description == "Living Room"
    audio.set_volume(UID_A, 55)
    assert audio.get_volume(UID_A) == 55
    audio.set_mute(UID_A, True)
    assert audio.sink(UID_A).mute
    audio.set_volume(UID_B, 80)
    audio.set_mute(UID_B, True)  # no mute control: emulated with volume
    assert audio.get_volume(UID_B) == 0
    audio.set_mute(UID_B, False)
    assert audio.get_volume(UID_B) == 80
    audio.group = [UID_A, UID_B]
    audio.set_volume("com.soundscape.soundscape", 30)
    assert audio.get_volume(UID_A) == 30 and audio.get_volume(UID_B) == 30
    hal_bh = make_hal(blackhole=True)
    audio_bh = MacAudio(runner, coreaudio=ca.CoreAudio(hal_bh))
    audio_bh.group = [UID_A, UID_B]
    audio_bh.set_volume(UID_BH, 45)  # BlackHole is a plain device, not a fan-out
    assert audio_bh.get_volume(UID_BH) == 45 and audio_bh.get_volume(UID_A) == 100
    with pytest.raises(AudioError):
        audio.set_volume("nope", 10)
    # playing to a specific device switches the default output and back
    audio.play_file("/tmp/x.wav", UID_B)
    assert [c for c in runner.calls if c[0] == "afplay"] == [["afplay", "/tmp/x.wav"]]
    assert hal.default_output == hal.id_for_uid(UID_BUILTIN)
    assert audio.blackhole() is None


def test_mac_audio_record_decimates_to_mono(runner):
    hal = make_hal()
    frames = 4800
    raw = struct.pack(f"<{frames}f", *([0.5] * frames))
    audio = MacAudio(runner, coreaudio=ca.CoreAudio(hal), recorder=lambda did, secs: raw)
    samples, rate = audio.record(0.1)
    assert rate == 48000 and len(samples) == frames and samples[0] == pytest.approx(0.5)
    with pytest.raises(AudioError):
        audio.record(0.1, source="missing")
    with pytest.raises(AudioError):
        audio.record(0.1, source=UID_A)  # no inputs


# ---------------------------------------------------------------- engines

def mac_session(runner, hal, tmp_path, monkeypatch, cfg=None, pids=None):
    monkeypatch.setenv("SOUNDSCAPE_PLATFORM", "mac")
    world = BtWorld(runner)
    world.connected[MAC_B] = True
    audio = MacAudio(runner, coreaudio=ca.CoreAudio(hal))
    bt = MacBluetooth(runner, sleep=lambda _s: None)
    cfg = cfg or Config(speakers=[Speaker(MAC_A, "Kitchen", "left"), Speaker(MAC_B, "Living Room", "right")],
                        connect_timeout_s=1, sink_timeout_s=1)
    logs: List[str] = []
    s = Session(runner=runner, paths=Paths.under(tmp_path), log=logs.append, config=cfg, bluez=bt, pulse=audio,
                sleep=lambda _s: None)
    s.logs = logs  # type: ignore[attr-defined]
    live = pids if pids is not None else {}
    monkeypatch.setattr(engine_mod, "pid_is_ours", lambda pid, marker: pid in live)
    monkeypatch.setattr(engine_mod, "kill_pid", lambda pid, marker, grace=3.0: bool(live.pop(pid, None)))
    return s, world, live


def test_aggregate_engine_up_down(runner, tmp_path, monkeypatch):
    hal = make_hal()
    s, world, _ = mac_session(runner, hal, tmp_path, monkeypatch)
    assert s.platform == "mac"
    state = s.up()
    assert state.engine == "aggregate" and state.sink_name == "com.soundscape.soundscape"
    agg = hal.aggregates[hal.id_for_uid(state.sink_name)]["desc"]
    assert agg["master"] == UID_A and agg["subdevices"][1] == {"uid": UID_B, "drift": 1}
    assert hal.default_output == hal.id_for_uid(state.sink_name)
    st = s.status()
    assert st["up"] and st["engine"] == "aggregate" and st["platform"] == "mac"
    assert st["speakers"][0]["sink"] == UID_A and st["speakers"][0]["connected"]
    with pytest.raises(AudioError) as exc:
        s.set_offset("right", 5)
    assert "BlackHole" in exc.value.hint
    assert s.set_volume("all", 40) == state.sink_name
    assert s.pulse.get_volume(UID_A) == 40 and s.pulse.get_volume(UID_B) == 40
    s.down()
    assert hal.default_output == hal.id_for_uid(UID_BUILTIN) and not hal.aggregates


def test_aggregate_engine_sweeps_leftovers(runner, tmp_path, monkeypatch):
    hal = make_hal()
    stale = hal.create_aggregate({"uid": "com.soundscape.soundscape", "name": "old", "subdevices": [{"uid": UID_A, "drift": 0}]})
    s, _, _ = mac_session(runner, hal, tmp_path, monkeypatch)
    s.up()
    assert stale in hal.destroyed and len(hal.aggregates) == 1


def test_relay_engine_spawns_process_and_controls_it(runner, tmp_path, monkeypatch):
    hal = make_hal(blackhole=True)
    pids: Dict[int, FakeProcess] = {}
    s, world, live = mac_session(runner, hal, tmp_path, monkeypatch, pids=pids)
    sent: List[str] = []

    def fake_send(path, command, timeout=3.0):
        sent.append(command)
        if command == "ping":
            return "pong"
        if command.startswith("delay"):
            return "ok 12.000 576"
        return "bye"

    monkeypatch.setattr("soundscape.mac.ioproc.send_command", fake_send)

    def spawn(argv, kwargs):
        proc = FakeProcess(argv, kwargs)
        live[proc.pid] = proc
        sock = argv[argv.index("--socket") + 1]
        Path(sock).parent.mkdir(parents=True, exist_ok=True)
        Path(sock).write_text("")
        return proc

    runner.tools.add(__import__("sys").executable)
    runner.on_popen(__import__("sys").executable, spawn)
    state = s.up()
    assert state.engine == "relay" and state.sink_name == UID_BH and state.with_delays
    argv = [p for p in runner.procs if "-m" in p.argv][0].argv
    assert "soundscape.mac.ioproc" in argv and f"left:{UID_A}:0.000" in argv and f"right:{UID_B}:0.000" in argv
    assert hal.default_output == hal.id_for_uid(UID_BH)
    s.set_offset("right", 12)
    assert any(c == f"delay {UID_B} 12.000" for c in sent)
    st = s.status()
    assert st["up"] and st["master_label"] == "Mac volume (BlackHole)"
    # master volume is the Mac's volume (BlackHole); speaker sliders stay independent trims
    s.pulse.set_volume(UID_A, 70)
    assert s.set_volume("all", 100) == UID_BH
    assert s.pulse.get_volume(UID_BH) == 100 and s.pulse.get_volume(UID_A) == 70
    s.down()
    assert "quit" in sent and not live
    assert hal.default_output == hal.id_for_uid(UID_BUILTIN)


def test_relay_requires_blackhole_and_choose(runner, tmp_path, monkeypatch):
    hal = make_hal()
    audio = MacAudio(runner, coreaudio=ca.CoreAudio(hal))
    cfg = Config(engine="relay")
    eng = RelayEngine(runner, audio, Paths.under(tmp_path), lambda _m: None)
    with pytest.raises(AudioError) as exc:
        eng.build([], cfg)
    assert "BlackHole" in exc.value.message
    assert isinstance(choose_mac_engine(Config(), runner, audio, Paths.under(tmp_path), lambda _m: None), AggregateEngine)
    audio_bh = MacAudio(runner, coreaudio=ca.CoreAudio(make_hal(blackhole=True)))
    assert isinstance(choose_mac_engine(Config(), runner, audio_bh, Paths.under(tmp_path), lambda _m: None), RelayEngine)
    assert isinstance(choose_mac_engine(Config(engine="aggregate"), runner, audio_bh, Paths.under(tmp_path), lambda _m: None), AggregateEngine)


# ---------------------------------------------------------------- relay mixer

def frames_bytes(values: List[Tuple[float, float]]) -> bytes:
    flat = [v for pair in values for v in pair]
    return struct.pack(f"<{len(flat)}f", *flat)


def test_mixer_delays_and_zero_pads():
    m = Mixer(rate=1000, outputs=[Output(UID_A, "left"), Output(UID_B, "right")])
    m.set_delay(UID_B, 3)  # 3 ms = 3 frames
    m.push(frames_bytes([(1, 1), (2, 2), (3, 3), (4, 4)]))
    assert struct.unpack("<8f", m.render_output(m.outputs[0], 4)) == (1, 1, 2, 2, 3, 3, 4, 4)
    delayed = struct.unpack("<8f", m.render_output(m.outputs[1], 4))
    assert delayed == (0, 0, 0, 0, 0, 0, 1, 1)
    m.push(frames_bytes([(5, 5), (6, 6), (7, 7), (8, 8)]))
    assert struct.unpack("<8f", m.render_output(m.outputs[1], 4)) == (2, 2, 3, 3, 4, 4, 5, 5)
    assert m.status()["outputs"][1]["delay_frames"] == 3
    with pytest.raises(KeyError):
        m.set_delay("ghost", 1)
    assert m.set_delay("left", 5000).delay_ms == 1000.0  # clamped to max


def test_mixer_stereo_split_and_channel_adapt():
    m = Mixer(rate=1000, outputs=[Output(UID_A, "left"), Output(UID_B, "right")], stereo_split=True)
    m.push(frames_bytes([(1, 2), (3, 4)]))
    assert struct.unpack("<4f", m.render_output(m.outputs[0], 2)) == (1, 1, 3, 3)
    assert struct.unpack("<4f", m.render_output(m.outputs[1], 2)) == (2, 2, 4, 4)
    mono_out = Output(UID_B, "right", channels=1)
    m2 = Mixer(rate=1000, outputs=[mono_out])
    m2.push(frames_bytes([(1, 2), (3, 4)]))
    assert struct.unpack("<2f", m2.render_output(mono_out, 2)) == (1, 3)


def test_mixer_process_writes_output_buffers():
    m = Mixer(rate=1000, outputs=[Output(UID_A, "left"), Output(UID_B, "right")], skip_outputs=1)
    m.set_delay("right", 1)
    src = ctypes.create_string_buffer(frames_bytes([(1, 1), (2, 2)]), 16)
    out_bh = ctypes.create_string_buffer(frames_bytes([(9, 9), (9, 9)]), 16)  # BlackHole's own output stream
    out_a = ctypes.create_string_buffer(16)
    out_b = ctypes.create_string_buffer(16)
    m.process([(2, 16, ctypes.addressof(src))],
              [(2, 16, ctypes.addressof(out_bh)), (2, 16, ctypes.addressof(out_a)), (2, 16, ctypes.addressof(out_b))])
    assert struct.unpack("<4f", out_bh.raw) == (0, 0, 0, 0)  # never fed back into the loopback
    assert struct.unpack("<4f", out_a.raw) == (1, 1, 2, 2)
    assert struct.unpack("<4f", out_b.raw) == (0, 0, 1, 1)
    m.process([], [(2, 16, ctypes.addressof(out_bh)), (2, 16, ctypes.addressof(out_a))])  # fewer buffers than outputs: no crash


def test_control_server_commands(tmp_path):
    m = Mixer(rate=48000, outputs=[Output(UID_A, "left"), Output(UID_B, "right")])
    quit_flag = {"q": False}
    srv = ControlServer(short_socket_path(str(tmp_path / "ctl.sock")), m, on_quit=lambda: quit_flag.__setitem__("q", True))
    try:
        assert srv.handle("ping") == "pong"
        assert srv.handle(f"delay {UID_B} 12.5").startswith("ok 12.500 600")
        assert srv.handle("delay nobody 1").startswith("err")
        assert srv.handle("stereo on") == "ok" and m.stereo_split
        assert json.loads(srv.handle("status"))["outputs"][1]["delay_ms"] == 12.5
        assert srv.handle("") .startswith("err") and srv.handle("bogus").startswith("err")
        assert srv.handle("quit") == "bye" and quit_flag["q"] and not srv.running
    finally:
        srv.sock.close()


def test_control_server_over_socket(tmp_path):
    import threading

    from soundscape.mac.ioproc import send_command
    m = Mixer(rate=48000, outputs=[Output(UID_A, "left")])
    path = short_socket_path(str(tmp_path / "ctl.sock"))
    srv = ControlServer(path, m, on_quit=lambda: None)
    t = threading.Thread(target=srv.serve, daemon=True)
    t.start()
    try:
        assert send_command(path, "ping") == "pong"
        assert send_command(path, f"delay {UID_A} 2").startswith("ok")
        assert send_command(path, "quit") == "bye"
    finally:
        t.join(timeout=3)


def test_short_socket_path():
    assert short_socket_path("/short/relay.sock") == "/short/relay.sock"
    long = "/private/var/folders/" + "x" * 120 + "/relay.sock"
    out = short_socket_path(long)
    assert out.startswith("/tmp/soundscape-") and len(out) < 60


def test_output_spec_parsing_handles_uid_colons():
    from soundscape.mac.ioproc import parse_output_spec
    out = parse_output_spec(f"left:{UID_A}:12.5")
    assert out.uid == UID_A and out.position == "left" and out.delay_ms == 12.5
    assert parse_output_spec("kitchen:BlackHole2ch_UID:0").uid == "BlackHole2ch_UID"
    with pytest.raises(ValueError):
        parse_output_spec("left::1")
    with pytest.raises(ValueError):
        parse_output_spec("left:uid:notanumber")


def test_control_server_delay_accepts_uids_with_spaces():
    m = Mixer(rate=1000, outputs=[Output("AppleUSBAudioEngine:Blue Yeti:1", "left")])
    srv = ControlServer.__new__(ControlServer)
    srv.mixer, srv.running, srv.on_quit = m, True, lambda: None
    assert srv.handle("delay AppleUSBAudioEngine:Blue Yeti:1 7").startswith("ok 7.000 7")
    assert srv.handle("delay left 3").startswith("ok 3.000 3")
    assert srv.handle("delay").startswith("err")


def test_mute_emulation_keeps_levels_through_solo_sequence(runner):
    """Calibration mutes/unmutes repeatedly; devices without a mute control must end at their original volume."""
    hal = make_hal()
    audio = MacAudio(runner, coreaudio=ca.CoreAudio(hal))
    audio.set_volume(UID_B, 80)  # Living Room has no mute control
    audio.set_mute(UID_B, False)  # unmuting something we never muted: no change
    assert audio.get_volume(UID_B) == 80
    audio.set_mute(UID_B, True)
    audio.set_mute(UID_B, True)  # second mute must not overwrite the saved level with 0
    assert audio.get_volume(UID_B) == 0
    audio.set_mute(UID_B, False)
    assert audio.get_volume(UID_B) == 80
    audio.set_mute(UID_B, False)
    assert audio.get_volume(UID_B) == 80


def test_ioproc_parser():
    args = build_parser().parse_args(["--socket", "/tmp/s", "--input", UID_BH, "--output", f"left:{UID_A}:0",
                                      "--output", f"right:{UID_B}:12.5", "--stereo"])
    assert args.stereo and len(args.output) == 2 and args.buffer == 1024


def test_device_to_sink_shape():
    d = ca.AudioDevice(id=5, uid=UID_A, name="Kitchen", transport="bluetooth", output_channels=2, input_channels=0,
                       sample_rate=44100.0)
    s = device_to_sink(d, 0.5, None)
    assert s.volume_percent == 50 and s.bluetooth_address == MAC_A and s.properties["device.api"] == "coreaudio"


def test_cli_doctor_on_mac(runner, tmp_path, monkeypatch, capsys):
    from soundscape import cli
    monkeypatch.setenv("SOUNDSCAPE_PLATFORM", "mac")
    BtWorld(runner, blueutil=False)
    hal = make_hal()
    monkeypatch.setattr("soundscape.mac.audio.CoreAudio", lambda: ca.CoreAudio(hal))
    assert cli.main(["--root", str(tmp_path), "doctor"], runner=runner) == 0
    out = capsys.readouterr().out
    assert "blueutil missing" in out and "BlackHole not installed" in out and "2 Bluetooth audio output" in out
    assert Device  # imported symbol used by type checkers
