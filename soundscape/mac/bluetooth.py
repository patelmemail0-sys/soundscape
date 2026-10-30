"""Bluetooth on macOS.

Actions (connect, disconnect, pair, inquiry) need ``blueutil`` (``brew install blueutil``).
Listing works without it through ``system_profiler``, which also tells us which devices are
speakers. The class duck-types :class:`soundscape.bluez.BlueZ` so the session code is shared.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from ..bluez import Device
from ..config import normalize_mac
from ..errors import BluetoothError
from ..runner import Runner

Log = Callable[[str], None]
AUDIO_MINOR_TYPES = {"speaker", "headphones", "headset", "loudspeaker", "portable audio", "hifi audio",
                     "car audio", "video display and loudspeaker"}
BLUEUTIL_HINT = "brew install blueutil   (lets Soundscape connect speakers itself; otherwise click them in the Bluetooth menu)"


def _mac_from_blueutil(address: str) -> str:
    return normalize_mac(address.replace("-", ":"))


def parse_blueutil_json(text: str) -> List[Dict[str, Any]]:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError as exc:
        raise BluetoothError(f"blueutil returned malformed JSON: {exc}") from exc
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def parse_system_profiler(text: str) -> Dict[str, Dict[str, Any]]:
    """Return {MAC: {"name", "connected", "minor_type", "paired"}} from ``system_profiler -json``."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return out
    if not isinstance(data, dict):
        return out
    sections = data.get("SPBluetoothDataType") or []
    if not isinstance(sections, list):
        return out
    for section in sections:
        if not isinstance(section, dict):
            continue
        for key, connected in (("device_connected", True), ("device_not_connected", False),
                               ("device_title", None)):
            entries = section.get(key)
            if not entries:
                continue
            if isinstance(entries, dict):  # macOS 11 shape
                entries = [{k: v} for k, v in entries.items()]
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                for name, props in entry.items():
                    if not isinstance(props, dict):
                        continue
                    addr = props.get("device_address") or props.get("device_addr")
                    if not addr:
                        continue
                    try:
                        mac = normalize_mac(str(addr).replace("-", ":"))
                    except Exception:
                        continue
                    is_connected = connected if connected is not None else str(props.get("device_isconnected", "")).lower() == "attrib_yes"
                    out[mac] = {"name": name, "connected": bool(is_connected),
                                "minor_type": str(props.get("device_minorType", props.get("device_minor_type", ""))),
                                "paired": True}
    return out


class MacBluetooth:
    def __init__(self, runner: Runner, sleep: Callable[[float], None] = time.sleep):
        self.runner = runner
        self._sleep = sleep

    # -- capabilities --------------------------------------------------------
    def has_blueutil(self) -> bool:
        return bool(self.runner.which("blueutil"))

    def _blueutil(self, *args: str, timeout: float = 30):
        res = self.runner.run(["blueutil", *args], timeout=timeout)
        return res

    def _require_blueutil(self, action: str) -> None:
        if not self.has_blueutil():
            raise BluetoothError(f"cannot {action} without blueutil", BLUEUTIL_HINT)

    def power_on(self) -> None:
        if self.has_blueutil():
            res = self._blueutil("--power", "1", timeout=10)
            if not res.ok:
                raise BluetoothError(f"could not power on Bluetooth: {res.stderr.strip()}")

    # -- listing -------------------------------------------------------------
    def _profiler(self) -> Dict[str, Dict[str, Any]]:
        res = self.runner.run(["system_profiler", "SPBluetoothDataType", "-json"], timeout=40)
        return parse_system_profiler(res.stdout) if res.ok else {}

    def devices(self) -> List[Device]:
        info = self._profiler()
        merged: Dict[str, Device] = {}
        for mac, p in info.items():
            minor = p["minor_type"].lower()
            merged[mac] = Device(path=f"mac:{mac}", address=mac, name=p["name"], alias=p["name"], paired=True,
                                 trusted=True, connected=p["connected"],
                                 icon="audio-card" if minor in AUDIO_MINOR_TYPES else "")
        if self.has_blueutil():
            res = self._blueutil("--paired", "--format", "json", timeout=15)
            if res.ok:
                for item in parse_blueutil_json(res.stdout):
                    try:
                        mac = _mac_from_blueutil(str(item.get("address", "")))
                    except Exception:
                        continue
                    existing = merged.get(mac)
                    name = str(item.get("name") or (existing.name if existing else "") or mac)
                    merged[mac] = Device(path=f"mac:{mac}", address=mac, name=name, alias=name,
                                         paired=bool(item.get("paired", True)), trusted=True,
                                         connected=bool(item.get("connected", existing.connected if existing else False)),
                                         icon=existing.icon if existing else "", rssi=item.get("RSSI"))
        return list(merged.values())

    def device(self, mac: str) -> Optional[Device]:
        mac = normalize_mac(mac)
        for d in self.devices():
            if d.address == mac:
                return d
        return None

    def require_device(self, mac: str) -> Device:
        dev = self.device(mac)
        if dev is None:
            raise BluetoothError(f"{normalize_mac(mac)} is not paired with this Mac",
                                 "pair it once in System Settings > Bluetooth, or run `soundscape scan` with blueutil installed")
        return dev

    def scan(self, seconds: float = 10, log: Optional[Log] = None) -> List[Device]:
        known = {d.address: d for d in self.devices()}
        if not self.has_blueutil():
            if log:
                log("blueutil is not installed; listing paired devices only. " + BLUEUTIL_HINT)
            return list(known.values())
        if log:
            log(f"scanning for {seconds:.0f}s ...")
        res = self._blueutil("--inquiry", str(int(seconds)), "--format", "json", timeout=seconds + 20)
        if res.ok:
            for item in parse_blueutil_json(res.stdout):
                try:
                    mac = _mac_from_blueutil(str(item.get("address", "")))
                except Exception:
                    continue
                if mac in known:
                    continue
                name = str(item.get("name") or mac)
                known[mac] = Device(path=f"mac:{mac}", address=mac, name=name, alias=name, paired=False,
                                    connected=bool(item.get("connected", False)), rssi=item.get("RSSI"),
                                    icon="audio-card")  # unknown class: assume the user is scanning for speakers
        return list(known.values())

    # -- actions ---------------------------------------------------------------
    def pair(self, mac: str, timeout: float = 60) -> None:
        self._require_blueutil("pair")
        res = self._blueutil("--pair", normalize_mac(mac), timeout=timeout)
        if not res.ok:
            raise BluetoothError(f"pairing with {normalize_mac(mac)} failed: {res.stderr.strip() or res.stdout.strip()}",
                                 "put the speaker in pairing mode, or pair it once in System Settings > Bluetooth")

    def trust(self, mac: str, trusted: bool = True) -> None:  # macOS has no separate trust flag
        return None

    def connect(self, mac: str, timeout: float = 30) -> None:
        self._require_blueutil("connect")
        res = self._blueutil("--connect", normalize_mac(mac), timeout=timeout)
        if not res.ok:
            raise BluetoothError(f"connecting to {normalize_mac(mac)} failed: {res.stderr.strip() or res.stdout.strip()}",
                                 "is the speaker on and in range? If it is connected to a phone, disconnect it there")

    def disconnect(self, mac: str) -> None:
        self._require_blueutil("disconnect")
        self._blueutil("--disconnect", normalize_mac(mac), timeout=15)

    def remove(self, mac: str) -> None:
        self._require_blueutil("unpair")
        res = self._blueutil("--unpair", normalize_mac(mac), timeout=15)
        if not res.ok:
            raise BluetoothError(f"unpairing {normalize_mac(mac)} failed: {res.stderr.strip()}")

    def is_connected(self, mac: str) -> bool:
        if self.has_blueutil():
            res = self._blueutil("--is-connected", normalize_mac(mac), timeout=10)
            if res.ok:
                return res.stdout.strip() == "1"
        dev = self.device(mac)
        return bool(dev and dev.connected)

    def wait_connected(self, mac: str, timeout: float = 10, poll: float = 0.5) -> Device:
        deadline = time.monotonic() + timeout
        while True:
            if self.is_connected(mac):
                return self.device(mac) or Device(path=f"mac:{mac}", address=normalize_mac(mac), connected=True, paired=True)
            if time.monotonic() >= deadline:
                break
            self._sleep(poll)
        raise BluetoothError(f"{normalize_mac(mac)} did not connect within {timeout:.0f}s")

    def ensure_connected(self, mac: str, log: Optional[Log] = None, pair_timeout: float = 60,
                         connect_timeout: float = 30, retries: int = 2) -> Device:
        mac = normalize_mac(mac)
        say = log or (lambda _m: None)
        dev = self.device(mac)
        if dev is None:
            if not self.has_blueutil():
                raise BluetoothError(f"{mac} is not paired with this Mac",
                                     "pair it in System Settings > Bluetooth (or install blueutil and run `soundscape add` again)")
            say(f"pairing with {mac} (make sure it is in pairing mode) ...")
            self.pair(mac, timeout=pair_timeout)
            dev = self.device(mac) or Device(path=f"mac:{mac}", address=mac, paired=True)
        if dev.connected:
            return dev
        if not self.has_blueutil():
            raise BluetoothError(f"{dev.label} is paired but not connected",
                                 f"click it in the Bluetooth menu, or {BLUEUTIL_HINT}")
        last: Optional[BluetoothError] = None
        for attempt in range(1, retries + 2):
            say(f"connecting to {dev.label} (attempt {attempt}) ...")
            try:
                self.connect(mac, timeout=connect_timeout)
                return self.wait_connected(mac, timeout=10)
            except BluetoothError as exc:
                last = exc
                self._sleep(1.5)
        assert last is not None
        raise last
