"""pactl wrapper. Works against PulseAudio and against PipeWire's pipewire-pulse."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .config import mac_underscored, normalize_mac
from .errors import AudioError
from .runner import Result, Runner


@dataclass
class ServerInfo:
    server_name: str = ""
    server_version: str = ""
    default_sink: str = ""
    default_source: str = ""

    @property
    def is_pipewire(self) -> bool:
        return "pipewire" in self.server_name.lower()

    @property
    def flavour(self) -> str:
        if "coreaudio" in self.server_name.lower():
            return "CoreAudio"
        return "PipeWire" if self.is_pipewire else "PulseAudio"


@dataclass
class Sink:
    index: int
    name: str
    description: str = ""
    state: str = ""
    mute: bool = False
    volume_percent: int = 100
    owner_module: Optional[int] = None
    active_port: Optional[str] = None
    ports: List[str] = field(default_factory=list)
    properties: Dict[str, str] = field(default_factory=dict)
    sample_spec: str = ""
    monitor_source: str = ""

    @property
    def sample_rate(self) -> Optional[int]:
        for text in (self.properties.get("sample_rate", ""), self.sample_spec):
            m = re.search(r"(\d{4,6})\s*Hz", text) or re.fullmatch(r"\s*(\d{4,6})\s*", text or "")
            if m:
                return int(m.group(1))
        return None

    @property
    def headset_mode(self) -> bool:
        """Bluetooth speakers in the hands-free profile run at 8 or 16 kHz: music is silent or
        phone quality there. It happens when something records from the speaker's own mic."""
        rate = self.sample_rate
        profile = (self.properties.get("api.bluez5.profile") or self.properties.get("bluetooth.protocol") or "").lower()
        return bool(self.bluetooth_address) and ((rate is not None and rate < 32000) or "hfp" in profile or "hsp" in profile
                                                 or "headset" in profile)

    @property
    def bluetooth_address(self) -> Optional[str]:
        for key in ("api.bluez5.address", "device.string", "bluetooth.address"):
            value = self.properties.get(key, "")
            if re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", value or ""):
                return value.upper()
        m = re.search(r"bluez_(?:output|sink)\.(([0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2})", self.name)
        if m:
            return m.group(1).replace("_", ":").upper()
        m = re.match(r"^(([0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2})(:output)?$", self.name)  # macOS device UID
        return m.group(1).replace("-", ":").upper() if m else None


@dataclass
class Card:
    index: int
    name: str
    active_profile: str = ""
    ports: List[str] = field(default_factory=list)
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class Module:
    index: int
    name: str
    args: str = ""


@dataclass
class SinkInput:
    index: int
    sink: int


def _percent(value: Any) -> Optional[int]:
    if value is None:
        return None
    m = re.search(r"(-?\d+)%", str(value))
    return int(m.group(1)) if m else None


def _avg(values: Iterable[Optional[int]]) -> int:
    vals = [v for v in values if v is not None]
    return int(round(sum(vals) / len(vals))) if vals else 100


# -- parsers ---------------------------------------------------------------

def parse_info(text: str) -> ServerInfo:
    info = ServerInfo()
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "server name":
            info.server_name = value
        elif key == "server version":
            info.server_version = value
        elif key == "default sink":
            info.default_sink = value
        elif key == "default source":
            info.default_source = value
    return info


def parse_sinks_json(text: str) -> List[Sink]:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of sinks")
    sinks = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("index"), int) or not item.get("name"):
            continue  # pactl never emits this; be tolerant of truncated output
        volume = item.get("volume") or {}
        if isinstance(volume, dict):
            vol = _avg(_percent(ch.get("value_percent")) for ch in volume.values() if isinstance(ch, dict))
        else:
            vol = 100
        ports = item.get("ports") or []
        if not isinstance(ports, list):
            ports = []
        owner = item.get("owner_module")
        raw_props = item.get("properties")
        props: Dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
        sinks.append(Sink(
            index=int(item["index"]),
            name=str(item["name"]),
            description=str(item.get("description", "")),
            state=str(item.get("state", "")),
            mute=bool(item.get("mute", False)),
            volume_percent=vol,
            owner_module=int(owner) if isinstance(owner, int) and owner < 4294967295 else None,
            active_port=item.get("active_port"),
            ports=[str(p.get("name")) for p in ports if isinstance(p, dict) and p.get("name")],
            properties={str(k): str(v) for k, v in props.items()},
            sample_spec=str(item.get("sample_specification", "")),
            monitor_source=str(item.get("monitor_source", "")),
        ))
    return sinks


_PROP_RE = re.compile(r'^\s*([\w.\-:]+)\s*=\s*"?(.*?)"?\s*$')


def _split_blocks(text: str, header: str) -> List[List[str]]:
    blocks: List[List[str]] = []
    for line in text.splitlines():
        if line.startswith(header):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
    return blocks


def _block_index(header: str) -> Optional[int]:
    tail = header.split("#", 1)[1].strip() if "#" in header else ""
    return int(tail) if tail.isdigit() else None


def parse_sinks_text(text: str) -> List[Sink]:
    """Fallback for pactl < 16 which has no ``-f json``."""
    sinks = []
    for block in _split_blocks(text, "Sink #"):
        index = _block_index(block[0])
        if index is None:
            continue
        sink = Sink(index=index, name="")
        section = ""
        for raw in block[1:]:
            line = raw.strip()
            if raw.startswith("\t") and not raw.startswith("\t\t"):
                section = ""
                key, _, value = line.partition(":")
                k = key.strip().lower()
                v = value.strip()
                if k == "name":
                    sink.name = v
                elif k == "description":
                    sink.description = v
                elif k == "state":
                    sink.state = v
                elif k == "mute":
                    sink.mute = v.lower() == "yes"
                elif k == "volume":
                    sink.volume_percent = _avg(_percent(p) for p in v.split(",") if "%" in p)
                elif k == "owner module":
                    sink.owner_module = int(v) if v.isdigit() and int(v) < 4294967295 else None
                elif k == "state" and not v:
                    pass
                elif k == "active port":
                    sink.active_port = v or None
                elif k == "sample specification":
                    sink.sample_spec = v
                elif k == "monitor source":
                    sink.monitor_source = v
                elif k == "properties":
                    section = "properties"
                elif k == "ports":
                    section = "ports"
            elif raw.startswith("\t\t"):
                if section == "properties":
                    m = _PROP_RE.match(line)
                    if m:
                        sink.properties[m.group(1)] = m.group(2)
                elif section == "ports":
                    name = line.split(":", 1)[0].strip()
                    if name:
                        sink.ports.append(name)
        if sink.name:
            sinks.append(sink)
    return sinks


def parse_cards_json(text: str) -> List[Card]:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of cards")
    cards = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("index"), int) or not item.get("name"):
            continue
        ports = item.get("ports") or {}
        if isinstance(ports, dict):
            port_names = [str(k) for k in ports.keys()]
        elif isinstance(ports, list):
            port_names = [str(p.get("name")) for p in ports if isinstance(p, dict) and p.get("name")]
        else:
            port_names = []
        raw_props = item.get("properties")
        props: Dict[str, Any] = raw_props if isinstance(raw_props, dict) else {}
        cards.append(Card(
            index=int(item["index"]),
            name=str(item["name"]),
            active_profile=str(item.get("active_profile", "")),
            ports=port_names,
            properties={str(k): str(v) for k, v in props.items()},
        ))
    return cards


def parse_cards_text(text: str) -> List[Card]:
    cards = []
    for block in _split_blocks(text, "Card #"):
        index = _block_index(block[0])
        if index is None:
            continue
        card = Card(index=index, name="")
        section = ""
        for raw in block[1:]:
            line = raw.strip()
            if raw.startswith("\t") and not raw.startswith("\t\t"):
                section = ""
                key, _, value = line.partition(":")
                k = key.strip().lower()
                if k == "name":
                    card.name = value.strip()
                elif k == "active profile":
                    card.active_profile = value.strip()
                elif k == "properties":
                    section = "properties"
                elif k == "ports":
                    section = "ports"
            elif raw.startswith("\t\t") and not raw.startswith("\t\t\t"):
                if section == "properties":
                    m = _PROP_RE.match(line)
                    if m:
                        card.properties[m.group(1)] = m.group(2)
                elif section == "ports":
                    name = line.split(":", 1)[0].strip()
                    if name:
                        card.ports.append(name)
        if card.name:
            cards.append(card)
    return cards


def parse_short_modules(text: str) -> List[Module]:
    mods = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip().isdigit():
            mods.append(Module(int(parts[0]), parts[1].strip(), parts[2].strip() if len(parts) > 2 else ""))
    return mods


def parse_short_sink_inputs(text: str) -> List[SinkInput]:
    out = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            out.append(SinkInput(int(parts[0]), int(parts[1])))
    return out


# -- client ------------------------------------------------------------------

class PulseCtl:
    def __init__(self, runner: Runner):
        self.runner = runner
        self._json_ok: Optional[bool] = None

    def _pactl(self, *args: str, timeout: float = 15, check: bool = True) -> Result:
        res = self.runner.run(["pactl", *args], timeout=timeout)
        if check and not res.ok:
            msg = (res.stderr or res.stdout).strip() or f"exit code {res.returncode}"
            hint = ""
            if "Connection refused" in msg or "Connection failure" in msg:
                hint = ("no sound server for this user. Start PipeWire: "
                        "systemctl --user start pipewire pipewire-pulse wireplumber")
            raise AudioError(f"pactl {' '.join(args[:2])} failed: {msg}", hint)
        return res

    def info(self) -> ServerInfo:
        return parse_info(self._pactl("info").stdout)

    def _list(self, kind: str, json_parser, text_parser):
        if self._json_ok is not False:
            res = self._pactl("-f", "json", "list", kind, check=False)
            if res.ok:
                try:
                    out = json_parser(res.stdout)
                    self._json_ok = True
                    return out
                except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
                    pass
            self._json_ok = False
        return text_parser(self._pactl("list", kind).stdout)

    def sinks(self) -> List[Sink]:
        return self._list("sinks", parse_sinks_json, parse_sinks_text)

    def cards(self) -> List[Card]:
        return self._list("cards", parse_cards_json, parse_cards_text)

    def sink(self, name: str) -> Optional[Sink]:
        for s in self.sinks():
            if s.name == name:
                return s
        return None

    def bluetooth_sink(self, mac: str) -> Optional[Sink]:
        mac = normalize_mac(mac)
        for s in self.sinks():
            if s.bluetooth_address == mac:
                return s
        return None

    def card_for_mac(self, mac: str) -> Optional[Card]:
        key = mac_underscored(mac).lower()
        for c in self.cards():
            if key in c.name.lower() or c.properties.get("api.bluez5.address", "").upper() == normalize_mac(mac) \
                    or c.properties.get("device.string", "").upper() == normalize_mac(mac):
                return c
        return None

    def modules(self) -> List[Module]:
        return parse_short_modules(self._pactl("list", "short", "modules").stdout)

    def sink_inputs(self) -> List[SinkInput]:
        return parse_short_sink_inputs(self._pactl("list", "short", "sink-inputs").stdout)

    def load_module(self, name: str, **args: Any) -> int:
        argv = ["load-module", name] + [f"{k}={v}" for k, v in args.items() if v is not None]
        res = self._pactl(*argv, check=False)
        if not res.ok:
            raise AudioError(f"pactl load-module {name} failed: {(res.stderr or res.stdout).strip()}")
        text = res.stdout.strip()
        if not text.isdigit():
            raise AudioError(f"pactl load-module {name} returned {text!r} instead of a module index")
        return int(text)

    def unload_module(self, index: int) -> bool:
        return self._pactl("unload-module", str(index), check=False).ok

    def set_default_sink(self, name: str) -> None:
        self._pactl("set-default-sink", name)

    def move_all_inputs(self, to_sink: str) -> int:
        target = self.sink(to_sink)
        moved = 0
        for si in self.sink_inputs():
            if target and si.sink == target.index:
                continue
            if self._pactl("move-sink-input", str(si.index), to_sink, check=False).ok:
                moved += 1
        return moved

    def set_volume(self, sink: str, percent: int) -> None:
        percent = max(0, min(150, int(percent)))
        self._pactl("set-sink-volume", sink, f"{percent}%")

    def get_volume(self, sink: str) -> Optional[int]:
        s = self.sink(sink)
        return s.volume_percent if s else None

    def set_mute(self, sink: str, mute: bool) -> None:
        self._pactl("set-sink-mute", sink, "1" if mute else "0")

    def set_port_latency_offset(self, card: str, port: str, offset_us: int) -> None:
        self._pactl("set-port-latency-offset", card, port, str(int(offset_us)))

    def default_source(self) -> str:
        return self.info().default_source

    def sources(self) -> List[dict]:
        """Microphones: ``[{"name", "description", "transport", "address"}]`` (monitors excluded)."""
        res = self._pactl("list", "short", "sources", check=False)
        out = []
        for line in res.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and not parts[1].endswith(".monitor"):
                name = parts[1]
                m = re.search(r"bluez_(?:input|source)\.(([0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2})", name)
                transport = "bluetooth" if m else ("builtin" if name.startswith("alsa_input") else "other")
                out.append({"name": name, "description": name, "transport": transport,
                            "address": m.group(1).replace("_", ":").upper() if m else None})
        return out

    def pick_microphone(self, exclude_addresses: Iterable[str] = ()) -> Optional[str]:
        """Best microphone for calibration: a wired/built-in input first, never a speaker's own mic."""
        excluded = {a.upper() for a in exclude_addresses}
        candidates = [d for d in self.sources() if not (d["address"] and d["address"] in excluded)]
        rank = {"builtin": 0, "other": 1, "bluetooth": 4}
        candidates.sort(key=lambda d: rank.get(d["transport"], 2))
        if candidates:
            return candidates[0]["name"]
        default = self.default_source()
        return default or None

    # -- playback / capture (used by test tones and sync calibration) --------
    def play_file(self, path, sink: Optional[str] = None, wait: bool = True):
        """Play a WAV with paplay to ``sink`` (or the default sink)."""
        import subprocess
        self.runner.require("paplay")
        argv = ["paplay"] + (["--device", sink] if sink else []) + [str(path)]
        proc = self.runner.popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wait:
            proc.wait()
        return proc

    RECORD_RATE = 16000

    def record(self, seconds: float, source: Optional[str] = None):
        """Record mono float samples with parec. Returns (samples, rate)."""
        import struct as _struct
        import subprocess
        import tempfile
        import time as _time
        self.runner.require("parec")
        source = source or self.default_source()
        if not source:
            raise AudioError("no microphone source found", "pass --source <name> (see `pactl list short sources`)")
        import os as _os
        with tempfile.NamedTemporaryFile(prefix="soundscape-mic-", suffix=".raw", delete=False) as fh:
            path = fh.name
        try:
            with open(path, "wb") as out:
                proc = self.runner.popen(["parec", "--device", source, "--format=s16le", f"--rate={self.RECORD_RATE}",
                                          "--channels=1", "--raw"], stdout=out, stderr=subprocess.DEVNULL)
                deadline = _time.monotonic() + seconds
                while _time.monotonic() < deadline and proc.poll() is None:
                    _time.sleep(0.05)
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
            with open(path, "rb") as fh:
                data = fh.read()
        finally:
            try:
                _os.unlink(path)
            except OSError:
                pass
        count = len(data) // 2
        samples = [v / 32768.0 for v in _struct.unpack(f"<{count}h", data[: count * 2])]
        return samples, self.RECORD_RATE
