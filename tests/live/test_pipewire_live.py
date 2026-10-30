"""Runs the real PipeWire engine against a live PipeWire + WirePlumber + pipewire-pulse stack.

Needs no Bluetooth: two null sinks named like BlueZ sinks stand in for the speakers.
Skipped unless SOUNDSCAPE_LIVE=1 (see .github/workflows/ci.yml for the stack setup).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from soundscape import tones
from soundscape.config import Config, Speaker
from soundscape.engine import Bound, PipeWireEngine
from soundscape.paths import Paths
from soundscape.pipewire import PipeWireCtl
from soundscape.pulse import PulseCtl
from soundscape.runner import Runner

pytestmark = pytest.mark.skipif(os.environ.get("SOUNDSCAPE_LIVE") != "1", reason="set SOUNDSCAPE_LIVE=1 with a running PipeWire stack")

MAC_A, MAC_B = "AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"


def pw_dump():
    return json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, check=True).stdout)


@pytest.fixture(scope="module")
def stack():
    runner = Runner()
    pulse = PulseCtl(runner)
    info = pulse.info()
    assert info.is_pipewire, f"live test needs pipewire-pulse, got {info.server_name}"
    mods = []
    for mac, desc in ((MAC_A, "Kitchen"), (MAC_B, "LivingRoom")):
        if pulse.bluetooth_sink(mac) is None:
            key = mac.replace(":", "_")
            mods.append(pulse.load_module("module-null-sink", sink_name=f"bluez_output.{key}.1",
                                          sink_properties=f"device.description={desc}"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not (pulse.bluetooth_sink(MAC_A) and pulse.bluetooth_sink(MAC_B)):
        time.sleep(0.2)
    yield runner, pulse
    for m in mods:
        pulse.unload_module(m)


def test_engine_end_to_end(stack, tmp_path):
    runner, pulse = stack
    pw = PipeWireCtl(runner)
    a = Speaker(MAC_A, "Kitchen", "left", offset_ms=0.0)
    b = Speaker(MAC_B, "LivingRoom", "right", offset_ms=12.5)
    bound = [Bound(a, pulse.bluetooth_sink(MAC_A)), Bound(b, pulse.bluetooth_sink(MAC_B))]
    cfg = Config(speakers=[a, b], stereo_split=True, sink_timeout_s=10)
    paths = Paths.under(tmp_path).ensure()
    logs = []
    eng = PipeWireEngine(runner, pulse, paths, logs.append)
    previous_default = pulse.info().default_sink

    state = eng.build(bound, cfg)
    try:
        assert state.with_delays, logs
        assert pulse.info().default_sink == "soundscape"
        names = pw.node_names()
        for node in ("soundscape", "soundscape_left_delay", "soundscape_right_delay"):
            assert node in names
        assert pw.node_id("soundscape_left_delay") is not None

        # combine streams carry the configured resampler quality
        streams = [o for o in pw_dump() if o.get("type") == "PipeWire:Interface:Node"
                   and o["info"]["props"].get("node.name", "").startswith("output.soundscape")]
        assert len(streams) == 2
        assert all(s["info"]["props"].get("resample.quality") == 10 for s in streams)

        # links: delay outputs feed the speaker sinks, combine streams feed the delay sinks
        objs = pw_dump()
        nodes = {o["id"]: o["info"]["props"].get("node.name") for o in objs if o.get("type") == "PipeWire:Interface:Node"}
        links = {(nodes.get(o["info"]["output-node-id"]), nodes.get(o["info"]["input-node-id"]))
                 for o in objs if o.get("type") == "PipeWire:Interface:Link"}
        assert ("soundscape_left_delay.output", pulse.bluetooth_sink(MAC_A).name) in links
        assert ("soundscape_right_delay.output", pulse.bluetooth_sink(MAC_B).name) in links
        assert ("output.soundscape_soundscape_left_delay", "soundscape_left_delay") in links

        # live delay change is applied to the filter node
        eng.set_offset(state, MAC_B, 20.0)
        right = next(o for o in pw_dump() if o.get("type") == "PipeWire:Interface:Node"
                     and o["info"]["props"].get("node.name") == "soundscape_right_delay")
        controls = [p["params"] for p in right["info"]["params"]["Props"]
                    if "params" in p and "delay:Delay (s)" in p["params"]]
        assert controls, right["info"]["params"]["Props"]
        value = controls[0][controls[0].index("delay:Delay (s)") + 1]
        assert value == pytest.approx(0.02)

        # audio actually flows through the whole chain
        wav = tones.position_tone(tmp_path / "tone.wav", "left")
        subprocess.run(["paplay", "--device", "soundscape", str(wav)], check=True, timeout=20)
        states = {s.name: s.state for s in pulse.sinks()}
        assert states["soundscape"] in ("IDLE", "RUNNING")
        assert states[pulse.bluetooth_sink(MAC_A).name] in ("IDLE", "RUNNING")
    finally:
        eng.teardown(state)

    time.sleep(0.5)
    assert "soundscape" not in {s.name for s in pulse.sinks()}
    assert pulse.info().default_sink == previous_default
    assert not Path(state.conf).exists()
