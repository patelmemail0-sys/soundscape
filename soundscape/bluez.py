"""BlueZ control over D-Bus.

State queries and one-shot actions go through ``busctl --json`` (ships with systemd, no
Python bindings needed). Discovery is the one thing that must outlive a single D-Bus call:
BlueZ stops scanning the moment the requesting client disconnects, so scanning drives a
long-lived ``bluetoothctl`` process instead.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .config import mac_underscored, normalize_mac
from .errors import BluetoothError
from .runner import Result, Runner

BLUEZ = "org.bluez"
ADAPTER_IFACE = "org.bluez.Adapter1"
DEVICE_IFACE = "org.bluez.Device1"
OBJECT_MANAGER = "org.freedesktop.DBus.ObjectManager"

A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"
AUDIO_ICONS = {"audio-card", "audio-speakers", "audio-headphones", "audio-headset"}

Log = Callable[[str], None]


def unwrap(value: Any) -> Any:
    """Strip busctl's ``{"type": ..., "data": ...}`` variant wrappers, recursively."""
    if isinstance(value, dict):
        if set(value.keys()) == {"type", "data"} and isinstance(value["type"], str):
            return unwrap(value["data"])
        return {k: unwrap(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwrap(v) for v in value]
    return value


@dataclass
class Adapter:
    path: str
    address: str = ""
    name: str = ""
    powered: bool = False
    discovering: bool = False

    @property
    def hci(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass
class Device:
    path: str
    address: str
    name: str = ""
    alias: str = ""
    paired: bool = False
    trusted: bool = False
    connected: bool = False
    icon: str = ""
    uuids: List[str] = field(default_factory=list)
    rssi: Optional[int] = None
    adapter: str = ""

    @property
    def label(self) -> str:
        return self.alias or self.name or self.address

    @property
    def is_audio_sink(self) -> bool:
        return A2DP_SINK_UUID in {u.lower() for u in self.uuids} or self.icon in AUDIO_ICONS

    @property
    def is_headphones(self) -> bool:
        return self.icon in ("audio-headphones", "audio-headset")

    @property
    def is_speaker(self) -> bool:
        """An audio device that is not a pair of headphones: what Soundscape adopts on its own."""
        return self.is_audio_sink and not self.is_headphones


def parse_managed_objects(payload: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    data = unwrap(payload)
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        data = data["data"]
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise BluetoothError("unexpected busctl output for GetManagedObjects")
    return data


def device_from_props(path: str, props: Dict[str, Any]) -> Device:
    return Device(
        path=path,
        address=str(props.get("Address", "")).upper(),
        name=str(props.get("Name", "") or ""),
        alias=str(props.get("Alias", "") or ""),
        paired=bool(props.get("Paired", False)),
        trusted=bool(props.get("Trusted", False)),
        connected=bool(props.get("Connected", False)),
        icon=str(props.get("Icon", "") or ""),
        uuids=list(props.get("UUIDs", []) or []),
        rssi=props.get("RSSI"),
        adapter=str(props.get("Adapter", "") or ""),
    )


ERROR_HINTS = [
    ("br-connection-profile-unavailable",
     "no audio server registered the A2DP profile. Make sure PipeWire (with wireplumber and "
     "libspa-0.2-bluetooth / pipewire-audio) or pulseaudio-module-bluetooth is running for your user."),
    ("br-connection-page-timeout", "the speaker is off or out of range. Turn it on and move closer."),
    ("br-connection-refused", "the speaker refused. Put it in pairing mode or remove it and pair again."),
    ("br-connection-canceled", "the connection was cancelled by the speaker; try again."),
    ("br-connection-busy", "the speaker is busy, probably connected to another phone. Disconnect it there."),
    ("AuthenticationFailed", "pairing was rejected. Put the speaker in pairing mode first."),
    ("AuthenticationCanceled", "pairing was cancelled. Put the speaker in pairing mode and retry."),
    ("AuthenticationRejected", "the speaker rejected pairing. Forget this computer on the speaker and retry."),
    ("AuthenticationTimeout", "pairing timed out. Put the speaker in pairing mode and retry quickly."),
    ("AlreadyExists", ""),
    ("AlreadyConnected", ""),
    ("NotReady", "the Bluetooth adapter is not powered. Run: bluetoothctl power on"),
    ("org.freedesktop.DBus.Error.ServiceUnknown",
     "bluetoothd is not running. Run: sudo systemctl enable --now bluetooth"),
    ("Host is down", "the speaker is off or out of range."),
    ("Operation already in progress", "another connection attempt is still running; wait a few seconds."),
]


def _fold(text: str) -> str:
    """busctl prints BlueZ errors either as ``org.bluez.Error.AuthenticationFailed`` or as
    ``Call failed: Authentication Failed``; compare without case or spaces."""
    return text.replace(" ", "").replace("-", "").lower()


def explain(result: Result) -> str:
    text = (result.stderr or result.stdout).strip()
    folded = _fold(text)
    for needle, hint in ERROR_HINTS:
        if _fold(needle) in folded and hint:
            return f"{text}\n  hint: {hint}"
    return text or f"command failed with exit code {result.returncode}"


def _is_benign(result: Result, *needles: str) -> bool:
    text = _fold(result.stderr + result.stdout)
    return any(_fold(n) in text for n in needles)


class BlueZ:
    def __init__(self, runner: Runner, adapter: Optional[str] = None, sleep: Callable[[float], None] = time.sleep):
        self.runner = runner
        self.adapter_name = adapter  # e.g. "hci0"; None = first adapter
        self._sleep = sleep

    # -- low level -------------------------------------------------------
    def _busctl(self, *args: str, timeout: float = 30) -> Result:
        return self.runner.run(["busctl", "--system", *args], timeout=timeout)

    def managed_objects(self) -> Dict[str, Dict[str, Dict[str, Any]]]:
        res = self._busctl("--json=short", "call", BLUEZ, "/", OBJECT_MANAGER, "GetManagedObjects", timeout=15)
        if not res.ok:
            raise BluetoothError(f"cannot talk to bluetoothd: {explain(res)}")
        try:
            payload = json.loads(res.stdout)
        except json.JSONDecodeError as exc:
            raise BluetoothError(f"busctl returned malformed JSON: {exc}") from exc
        return parse_managed_objects(payload)

    # -- adapters --------------------------------------------------------
    def adapters(self) -> List[Adapter]:
        out = []
        for path, ifaces in self.managed_objects().items():
            props = ifaces.get(ADAPTER_IFACE)
            if props is None:
                continue
            out.append(Adapter(
                path=path,
                address=str(props.get("Address", "")),
                name=str(props.get("Alias", props.get("Name", ""))),
                powered=bool(props.get("Powered", False)),
                discovering=bool(props.get("Discovering", False)),
            ))
        return sorted(out, key=lambda a: a.path)

    def adapter(self) -> Adapter:
        adapters = self.adapters()
        if not adapters:
            raise BluetoothError("no Bluetooth adapter found",
                                 "plug in a Bluetooth dongle or enable the built-in radio (rfkill unblock bluetooth)")
        if self.adapter_name:
            for a in adapters:
                if a.hci == self.adapter_name:
                    return a
            raise BluetoothError(f"adapter {self.adapter_name} not found",
                                 f"available: {', '.join(a.hci for a in adapters)}")
        return adapters[0]

    def power_on(self) -> Adapter:
        ad = self.adapter()
        if not ad.powered:
            res = self._busctl("set-property", BLUEZ, ad.path, ADAPTER_IFACE, "Powered", "b", "true")
            if not res.ok:
                raise BluetoothError(f"could not power on {ad.hci}: {explain(res)}",
                                     "check `rfkill list` and unblock bluetooth")
            ad.powered = True
        return ad

    # -- devices ---------------------------------------------------------
    def devices(self, adapter_path: Optional[str] = None) -> List[Device]:
        out = []
        for path, ifaces in self.managed_objects().items():
            props = ifaces.get(DEVICE_IFACE)
            if props is None:
                continue
            if adapter_path and not path.startswith(adapter_path + "/"):
                continue
            out.append(device_from_props(path, props))
        return out

    def device_path(self, mac: str) -> str:
        return f"{self.adapter().path}/dev_{mac_underscored(mac)}"

    def device(self, mac: str) -> Optional[Device]:
        mac = normalize_mac(mac)
        for d in self.devices():
            if d.address == mac:
                return d
        return None

    def require_device(self, mac: str) -> Device:
        dev = self.device(mac)
        if dev is None:
            raise BluetoothError(f"{normalize_mac(mac)} is not known to BlueZ",
                                 "run `soundscape scan` with the speaker in pairing mode, then `soundscape add`")
        return dev

    def scan(self, seconds: float = 10, log: Optional[Log] = None) -> List[Device]:
        """Discover for ``seconds`` using bluetoothctl kept alive on a pipe."""
        self.power_on()
        self.runner.require("bluetoothctl")
        proc = self.runner.popen(["bluetoothctl"], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        try:
            assert proc.stdin is not None
            proc.stdin.write("scan on\n")
            proc.stdin.flush()
            if log:
                log(f"scanning for {seconds:.0f}s ...")
            self._sleep(seconds)
            try:
                proc.stdin.write("scan off\nquit\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        finally:
            if proc.poll() is None:
                proc.kill()
        return self.devices()

    # -- actions ---------------------------------------------------------
    def _device_call(self, mac: str, method: str, timeout: float) -> Result:
        return self._busctl("--timeout", f"{int(timeout)}s", "call", BLUEZ, self.device_path(mac),
                            DEVICE_IFACE, method, timeout=timeout + 5)

    def pair(self, mac: str, timeout: float = 60) -> None:
        res = self._device_call(mac, "Pair", timeout)
        if not res.ok and not _is_benign(res, "AlreadyExists", "Already Exists"):
            raise BluetoothError(f"pairing with {normalize_mac(mac)} failed: {explain(res)}")

    def trust(self, mac: str, trusted: bool = True) -> None:
        res = self._busctl("set-property", BLUEZ, self.device_path(mac), DEVICE_IFACE, "Trusted", "b",
                           "true" if trusted else "false")
        if not res.ok:
            raise BluetoothError(f"could not mark {normalize_mac(mac)} trusted: {explain(res)}")

    def connect(self, mac: str, timeout: float = 30) -> None:
        res = self._device_call(mac, "Connect", timeout)
        if not res.ok and not _is_benign(res, "AlreadyConnected", "Already Connected", "InProgress"):
            raise BluetoothError(f"connecting to {normalize_mac(mac)} failed: {explain(res)}")

    def disconnect(self, mac: str) -> None:
        res = self._device_call(mac, "Disconnect", 15)
        if not res.ok and not _is_benign(res, "NotConnected", "Not Connected"):
            raise BluetoothError(f"disconnecting {normalize_mac(mac)} failed: {explain(res)}")

    def remove(self, mac: str) -> None:
        ad = self.adapter()
        res = self._busctl("call", BLUEZ, ad.path, ADAPTER_IFACE, "RemoveDevice", "o", self.device_path(mac))
        if not res.ok and not _is_benign(res, "DoesNotExist", "Does Not Exist"):
            raise BluetoothError(f"removing {normalize_mac(mac)} failed: {explain(res)}")

    def wait_connected(self, mac: str, timeout: float = 10, poll: float = 0.5) -> Device:
        deadline = time.monotonic() + timeout
        last: Optional[Device] = None
        while True:
            last = self.device(mac)
            if last and last.connected:
                return last
            if time.monotonic() >= deadline:
                break
            self._sleep(poll)
        raise BluetoothError(f"{normalize_mac(mac)} did not report Connected within {timeout:.0f}s")

    def ensure_connected(self, mac: str, log: Optional[Log] = None, pair_timeout: float = 60,
                         connect_timeout: float = 30, retries: int = 2) -> Device:
        """Pair if needed, trust, connect, and verify. Idempotent."""
        mac = normalize_mac(mac)
        say = log or (lambda _m: None)
        self.power_on()
        dev = self.device(mac)
        if dev is None:
            raise BluetoothError(f"{mac} has not been discovered yet",
                                 "put the speaker in pairing mode and run `soundscape scan`")
        if not dev.paired:
            say(f"pairing with {dev.label} (make sure it is in pairing mode) ...")
            self.pair(mac, timeout=pair_timeout)
        if not dev.trusted:
            self.trust(mac)
        if dev.connected:
            return dev
        last_error: Optional[BluetoothError] = None
        for attempt in range(1, retries + 2):
            say(f"connecting to {dev.label} (attempt {attempt}) ...")
            try:
                self.connect(mac, timeout=connect_timeout)
                return self.wait_connected(mac, timeout=10)
            except BluetoothError as exc:
                last_error = exc
                self._sleep(1.5)
        assert last_error is not None
        raise last_error
