"""User configuration: which speakers, which engine, per-speaker sync offsets."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from .errors import ConfigError

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
POSITIONS = ("left", "right", "third", "fourth")
ENGINES = ("auto", "pipewire", "pulse")
SINK_NAME = "soundscape"


def normalize_mac(value: str) -> str:
    value = value.strip().replace("-", ":").replace("_", ":").upper()
    if not MAC_RE.match(value):
        raise ConfigError(f"not a Bluetooth address: {value!r}", "expected the form AA:BB:CC:DD:EE:FF")
    return value


def mac_underscored(mac: str) -> str:
    return normalize_mac(mac).replace(":", "_")


@dataclass
class Speaker:
    address: str
    name: str = ""
    position: str = "left"
    offset_ms: float = 0.0  # extra delay applied to THIS speaker so it lines up with the others
    volume: Optional[int] = None  # per-speaker trim in percent; None = leave alone

    def __post_init__(self) -> None:
        self.address = normalize_mac(self.address)
        if self.position not in POSITIONS:
            raise ConfigError(f"unknown position {self.position!r}", f"use one of {', '.join(POSITIONS)}")

    @property
    def label(self) -> str:
        return self.name or self.address

    @property
    def mac_key(self) -> str:
        return mac_underscored(self.address)


@dataclass
class Config:
    speakers: List[Speaker] = field(default_factory=list)
    engine: str = "auto"  # auto | pipewire | pulse
    stereo_split: bool = False  # left speaker gets L channel only, right gets R only (PipeWire engine)
    latency_compensate: bool = True  # let the engine align speakers from their reported latency
    set_default_sink: bool = True  # route all desktop audio through Soundscape while it is up
    resample_quality: int = 10  # PipeWire 0..14; higher = cleaner drift correction
    pulse_resample_method: str = "soxr-vhq"  # PulseAudio combine-sink resampler
    description: str = "Soundscape (both speakers)"
    sink_name: str = SINK_NAME
    connect_timeout_s: float = 25.0
    sink_timeout_s: float = 15.0

    def __post_init__(self) -> None:
        if self.engine not in ENGINES:
            raise ConfigError(f"unknown engine {self.engine!r}", f"use one of {', '.join(ENGINES)}")

    # -- speaker helpers -------------------------------------------------
    def find(self, selector: str) -> Optional[Speaker]:
        """Match by position (left/right), address, or case-insensitive name/alias prefix."""
        sel = selector.strip()
        for sp in self.speakers:
            if sp.position == sel.lower():
                return sp
        try:
            mac = normalize_mac(sel)
        except ConfigError:
            mac = None
        for sp in self.speakers:
            if mac and sp.address == mac:
                return sp
        lowered = sel.lower()
        matches = [sp for sp in self.speakers if sp.name.lower().startswith(lowered)]
        return matches[0] if len(matches) == 1 else None

    def require(self, selector: str) -> Speaker:
        sp = self.find(selector)
        if sp is None:
            known = ", ".join(f"{s.position}={s.label}" for s in self.speakers) or "none configured"
            raise ConfigError(f"no speaker matches {selector!r}", f"known speakers: {known}")
        return sp

    def add(self, speaker: Speaker, replace: bool = False) -> None:
        existing = [s for s in self.speakers if s.address == speaker.address]
        if existing:
            if not replace:
                raise ConfigError(f"{speaker.address} is already configured", "pass --replace to update it")
            self.speakers = [s for s in self.speakers if s.address != speaker.address]
        same_pos = [s for s in self.speakers if s.position == speaker.position]
        if same_pos:
            if not replace:
                raise ConfigError(
                    f"position {speaker.position!r} is already taken by {same_pos[0].label}",
                    "choose another --position or pass --replace",
                )
            self.speakers = [s for s in self.speakers if s.position != speaker.position]
        self.speakers.append(speaker)
        order = {p: i for i, p in enumerate(POSITIONS)}
        self.speakers.sort(key=lambda s: order[s.position])

    def remove(self, selector: str) -> Speaker:
        sp = self.require(selector)
        self.speakers = [s for s in self.speakers if s is not sp]
        return sp

    def normalize_offsets(self) -> None:
        """Shift offsets so the most-delayed speaker sits at 0 and nothing is negative."""
        if not self.speakers:
            return
        low = min(s.offset_ms for s in self.speakers)
        if low != 0:
            for s in self.speakers:
                s.offset_ms = round(s.offset_ms - low, 3)

    # -- persistence -----------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        data = dict(data)
        speakers = [Speaker(**s) for s in data.pop("speakers", [])]
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        unknown = set(data) - known
        if unknown:
            raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown))}")
        return cls(speakers=speakers, **data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> Config:
        if not path.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, TypeError) as exc:
            raise ConfigError(f"could not read {path}: {exc}", "fix or delete the file") from exc
