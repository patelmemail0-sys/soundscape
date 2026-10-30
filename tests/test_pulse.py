import json

import pytest

from soundscape.errors import AudioError
from soundscape.pulse import (
    PulseCtl,
    Sink,
    parse_cards_text,
    parse_info,
    parse_short_modules,
    parse_short_sink_inputs,
    parse_sinks_json,
    parse_sinks_text,
)
from tests.conftest import MAC_A, FakeRunner, World

TEXT_SINKS = """Sink #45
\tState: SUSPENDED
\tName: bluez_sink.AA_BB_CC_DD_EE_01.a2dp_sink
\tDescription: Kitchen
\tDriver: module-bluez5-device.c
\tSample Specification: s16le 2ch 44100Hz
\tChannel Map: front-left,front-right
\tOwner Module: 29
\tMute: no
\tVolume: front-left: 39321 /  60% / -13.31 dB,   front-right: 39321 /  60% / -13.31 dB
\t        balance 0.00
\tBase Volume: 65536 / 100% / 0.00 dB
\tMonitor Source: bluez_sink.AA_BB_CC_DD_EE_01.a2dp_sink.monitor
\tLatency: 0 usec, configured 0 usec
\tFlags: HARDWARE DECIBEL_VOLUME LATENCY
\tProperties:
\t\tbluetooth.protocol = "a2dp_sink"
\t\tdevice.description = "Kitchen"
\t\tdevice.string = "AA:BB:CC:DD:EE:01"
\t\tdevice.api = "bluez"
\tPorts:
\t\tspeaker-output: Speaker (type: Speaker, priority: 0, available)
\tActive Port: speaker-output
\tFormats:
\t\tpcm

Sink #0
\tState: RUNNING
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo
\tDescription: Built-in Audio Analog Stereo
\tDriver: module-alsa-card.c
\tSample Specification: s16le 2ch 44100Hz
\tOwner Module: 7
\tMute: yes
\tVolume: front-left: 65536 / 100% / 0.00 dB,   front-right: 65536 / 100% / 0.00 dB
\tProperties:
\t\talsa.card = "0"
\tPorts:
\t\tanalog-output-speaker: Speakers (type: Speaker, priority: 10000, availability unknown)
\tActive Port: analog-output-speaker
"""

TEXT_CARDS = """Card #44
\tName: bluez_card.AA_BB_CC_DD_EE_01
\tDriver: module-bluez5-device.c
\tOwner Module: 29
\tProperties:
\t\tdevice.string = "AA:BB:CC:DD:EE:01"
\t\tdevice.api = "bluez"
\tProfiles:
\t\ta2dp_sink: High Fidelity Playback (A2DP Sink) (sinks: 1, sources: 0, priority: 40, available: yes)
\t\toff: Off (sinks: 0, sources: 0, priority: 0, available: yes)
\tActive Profile: a2dp_sink
\tPorts:
\t\tspeaker-output: Speaker (type: Speaker, priority: 0, latency offset: 0 usec, available)
\t\t\tPart of profile(s): a2dp_sink
"""


def test_parse_info_pipewire_and_pulse():
    info = parse_info("Server Name: PulseAudio (on PipeWire 1.0.5)\nServer Version: 15.0.0\nDefault Sink: x\nDefault Source: mic\n")
    assert info.is_pipewire and info.flavour == "PipeWire" and info.default_sink == "x" and info.default_source == "mic"
    assert not parse_info("Server Name: pulseaudio\n").is_pipewire


def test_parse_sinks_text():
    sinks = parse_sinks_text(TEXT_SINKS)
    assert len(sinks) == 2
    bt = sinks[0]
    assert bt.name == "bluez_sink.AA_BB_CC_DD_EE_01.a2dp_sink"
    assert bt.volume_percent == 60 and not bt.mute and bt.owner_module == 29
    assert bt.properties["device.string"] == "AA:BB:CC:DD:EE:01"
    assert bt.ports == ["speaker-output"] and bt.active_port == "speaker-output"
    assert bt.bluetooth_address == MAC_A
    assert sinks[1].mute and sinks[1].bluetooth_address is None


def test_parse_cards_text():
    cards = parse_cards_text(TEXT_CARDS)
    assert cards[0].name == "bluez_card.AA_BB_CC_DD_EE_01"
    assert cards[0].ports == ["speaker-output"] and cards[0].active_profile == "a2dp_sink"


def test_parse_sinks_json_volume_average_and_owner():
    data = [{"index": 3, "name": "s", "volume": {"l": {"value_percent": "40%"}, "r": {"value_percent": "60%"}},
             "owner_module": 4294967295, "properties": {"api.bluez5.address": "aa:bb:cc:dd:ee:01"}, "ports": []}]
    sink = parse_sinks_json(json.dumps(data))[0]
    assert sink.volume_percent == 50 and sink.owner_module is None
    assert sink.bluetooth_address == MAC_A


def test_bluetooth_address_from_pipewire_name():
    assert Sink(1, "bluez_output.AA_BB_CC_DD_EE_01.1").bluetooth_address == MAC_A
    assert Sink(1, "alsa_output.x").bluetooth_address is None


def test_parse_short_lists():
    mods = parse_short_modules("1\tmodule-combine-sink\tsink_name=soundscape slaves=a,b\n2\tmodule-null-sink\t\n")
    assert mods[0].index == 1 and "slaves" in mods[0].args and mods[1].args == ""
    inputs = parse_short_sink_inputs("12\t45\t9\tprotocol-native.c\ts16le 2ch 44100Hz\n")
    assert inputs[0].index == 12 and inputs[0].sink == 45


def test_json_fallback_to_text(runner):
    runner.on(("pactl", "-f", "json"), (1, "", "Unknown format"))
    runner.on(("pactl", "list", "sinks"), TEXT_SINKS)
    runner.on(("pactl", "list", "cards"), TEXT_CARDS)
    pc = PulseCtl(runner)
    assert pc.bluetooth_sink(MAC_A).description == "Kitchen"
    assert pc.card_for_mac(MAC_A).name == "bluez_card.AA_BB_CC_DD_EE_01"
    # second call must not retry json
    runner.calls.clear()
    pc.sinks()
    assert all(c[:3] != ["pactl", "-f", "json"] for c in runner.calls)


def test_world_backed_operations(runner):
    world = World(runner)
    world.two_speakers(connected=True)
    pc = PulseCtl(runner)
    sink = pc.bluetooth_sink(MAC_A)
    assert sink is not None and sink.description == "Kitchen"
    idx = pc.load_module("module-combine-sink", sink_name="soundscape", slaves=f"{sink.name},x", resample_method=None)
    assert idx in world.modules and "resample_method" not in world.modules[idx][1]
    pc.set_default_sink("soundscape")
    assert world.default_sink == "soundscape"
    world.sink_inputs[7] = world.sinks[world.default_source]["index"]
    assert pc.move_all_inputs("soundscape") == 1
    pc.set_volume("soundscape", 250)
    assert world.sinks["soundscape"]["volume"] == 150
    pc.set_mute("soundscape", True)
    assert world.sinks["soundscape"]["mute"]
    pc.set_port_latency_offset("bluez_card.AA_BB_CC_DD_EE_01", "speaker-output", 12000)
    assert world.port_offsets[("bluez_card.AA_BB_CC_DD_EE_01", "speaker-output")] == 12000
    assert pc.unload_module(idx) and not pc.unload_module(idx)
    assert pc.get_volume("nope") is None


def test_errors_are_wrapped(runner):
    runner.on(("pactl", "info"), (1, "", "Connection failure: Connection refused"))
    with pytest.raises(AudioError) as exc:
        PulseCtl(runner).info()
    assert "pipewire-pulse" in exc.value.hint
    runner.on(("pactl", "load-module"), "not-a-number\n")
    with pytest.raises(AudioError):
        PulseCtl(runner).load_module("module-null-sink")
    runner.on(("pactl", "load-module"), (1, "", "Failure: Module initialization failed"))
    with pytest.raises(AudioError):
        PulseCtl(runner).load_module("module-null-sink")


def test_missing_pactl():
    runner = FakeRunner(tools=set())
    from soundscape.errors import ToolMissing
    with pytest.raises(ToolMissing):
        PulseCtl(runner).info()
