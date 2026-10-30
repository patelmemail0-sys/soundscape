import json

import pytest

from soundscape.bluez import BlueZ, Device, explain, parse_managed_objects, unwrap
from soundscape.errors import BluetoothError, ToolMissing
from soundscape.runner import Result
from tests.conftest import MAC_A, MAC_B, MAC_PHONE, FakeRunner, World


def test_unwrap_strips_variants_recursively():
    raw = {"type": "a{sv}", "data": {"Name": {"type": "s", "data": "JBL"},
                                     "UUIDs": {"type": "as", "data": ["x"]},
                                     "Nested": {"type": "v", "data": {"type": "b", "data": True}}}}
    assert unwrap(raw) == {"Name": "JBL", "UUIDs": ["x"], "Nested": True}


def test_parse_managed_objects_handles_busctl_shape(runner):
    world = World(runner)
    world.add_device(MAC_A, "Kitchen", audio=True)
    objs = parse_managed_objects(json.loads(world.managed_objects()))
    assert "/org/bluez/hci0" in objs
    dev = objs[f"/org/bluez/hci0/dev_{MAC_A.replace(':', '_')}"]["org.bluez.Device1"]
    assert dev["Name"] == "Kitchen" and dev["Paired"] is False


def test_devices_and_audio_filter(runner):
    world = World(runner)
    world.two_speakers()
    bz = BlueZ(runner, sleep=lambda _s: None)
    devs = {d.address: d for d in bz.devices()}
    assert devs[MAC_A].is_audio_sink and devs[MAC_B].is_audio_sink
    assert not devs[MAC_PHONE].is_audio_sink
    assert devs[MAC_PHONE].paired
    assert bz.adapter().hci == "hci0" and bz.adapter().powered


def test_scan_drives_bluetoothctl_and_returns_devices(runner):
    world = World(runner)
    world.two_speakers()
    bz = BlueZ(runner, sleep=lambda _s: None)
    devs = bz.scan(seconds=3)
    proc = [p for p in runner.procs if p.argv == ["bluetoothctl"]][0]
    assert "scan on" in proc.stdin.getvalue() and "scan off" in proc.stdin.getvalue()
    assert proc.returncode is not None
    assert {d.address for d in devs} >= {MAC_A, MAC_B}


def test_ensure_connected_pairs_trusts_connects(runner):
    world = World(runner)
    world.add_device(MAC_A, "Kitchen", paired=False)
    bz = BlueZ(runner, sleep=lambda _s: None)
    log = []
    dev = bz.ensure_connected(MAC_A, log=log.append)
    assert dev.connected and world.devices[MAC_A]["trusted"] and world.devices[MAC_A]["paired"]
    methods = [c[-1] for c in runner.calls if c[0] == "busctl" and "call" in c]
    assert "Pair" in methods and "Connect" in methods
    assert any("pairing" in m for m in log)


def test_ensure_connected_is_idempotent_when_connected(runner):
    world = World(runner)
    world.add_device(MAC_A, "Kitchen", paired=True, trusted=True, connected=True)
    bz = BlueZ(runner, sleep=lambda _s: None)
    bz.ensure_connected(MAC_A)
    methods = [c[-1] for c in runner.calls if c[0] == "busctl" and "call" in c and c[-1] != "GetManagedObjects"]
    assert methods == []


def test_connect_failure_has_hint_and_retries(runner):
    world = World(runner)
    world.add_device(MAC_A, "Kitchen", paired=True, trusted=True)
    world.fail_connect.add(MAC_A)
    bz = BlueZ(runner, sleep=lambda _s: None)
    with pytest.raises(BluetoothError) as exc:
        bz.ensure_connected(MAC_A, retries=1)
    assert "out of range" in str(exc.value)
    assert len([c for c in runner.calls if c[-1] == "Connect"]) == 2


def test_pair_failure_message(runner):
    world = World(runner)
    world.add_device(MAC_A, "Kitchen")
    world.fail_pair.add(MAC_A)
    bz = BlueZ(runner, sleep=lambda _s: None)
    with pytest.raises(BluetoothError) as exc:
        bz.ensure_connected(MAC_A)
    assert "pairing mode" in str(exc.value)


def test_unknown_device(runner):
    World(runner)
    bz = BlueZ(runner, sleep=lambda _s: None)
    with pytest.raises(BluetoothError) as exc:
        bz.ensure_connected("AA:BB:CC:DD:EE:99")
    assert "soundscape scan" in exc.value.hint
    assert bz.device("AA:BB:CC:DD:EE:99") is None


def test_power_on_sets_property(runner):
    world = World(runner)
    world.adapter_powered = False
    bz = BlueZ(runner, sleep=lambda _s: None)
    ad = bz.power_on()
    assert ad.powered and world.adapter_powered


def test_bluetoothd_unreachable(runner):
    runner.on(("busctl",), (1, "", "Failed to issue method call: The name org.bluez was not provided by any .service files"))
    bz = BlueZ(runner)
    with pytest.raises(BluetoothError) as exc:
        bz.devices()
    assert "bluetoothd" in str(exc.value)


def test_missing_bluetoothctl_raises_toolmissing():
    runner = FakeRunner(tools={"busctl"})
    World(runner)
    with pytest.raises(ToolMissing):
        BlueZ(runner, sleep=lambda _s: None).scan(1)


def test_explain_adds_hint_for_known_errors():
    res = Result(("busctl",), 1, "", "Call failed: br-connection-profile-unavailable")
    assert "A2DP profile" in explain(res)
    assert explain(Result(("busctl",), 3, "", "")) == "command failed with exit code 3"


def test_device_label_prefers_alias():
    d = Device(path="/x", address="AA:BB:CC:DD:EE:01", name="n", alias="Alias")
    assert d.label == "Alias"
    assert Device(path="/x", address="AA:BB:CC:DD:EE:01").label == "AA:BB:CC:DD:EE:01"
