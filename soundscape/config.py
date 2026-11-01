"""User configuration: which speakers, which engine, per-speaker sync offsets."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from .errors import ConfigError

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
# "left" and "right" are special (stereo split); every other position is a free name such as
# "kitchen" or "speaker3". Legacy names from 0.1 ("third", "fourth") are ordinary names now.
STEREO_POSITIONS = ("left", "right")
POSITIONS = STEREO_POSITIONS  # kept for callers that list the well-known names
POSITION_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
LEGACY_POSITIONS = {"third": 3, "fourth": 4}
ENGINES = ("auto", "pipewire", "pulse", "aggregate", "relay")
SINK_NAME = "soundscape"
SPEED_OF_SOUND_M_S = 343.0  # dry air at 20 C; temperature moves it by under 2 %
MAX_DISTANCE_M = 100.0
_DISTANCE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*(m|meter|meters|metre|metres|cm|ft|feet|foot|'|in|inch|inches|\")?$")
_UNIT_M = {"m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0, "cm": 0.01,
           "ft": 0.3048, "feet": 0.3048, "foot": 0.3048, "'": 0.3048,
           "in": 0.0254, "inch": 0.0254, "inches": 0.0254, '"': 0.0254}


def parse_distance(value: object) -> Optional[float]:
    """Distance from the calibration microphone to a speaker, in metres.

    Accepts a number (metres) or text with a unit: ``"1.2 m"``, ``"120 cm"``, ``"4 ft"``,
    ``"48 in"``. Empty, ``none`` or ``-`` clears it (returns ``None``).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ConfigError(f"not a distance: {value!r}", "examples: 1.2 m, 120 cm, 4 ft, 48 in")
    if isinstance(value, (int, float)):
        metres = float(value)
    else:
        text = str(value).strip().lower().replace(",", ".")
        if text in ("", "none", "-", "clear", "unset", "null"):
            return None
        m = _DISTANCE_RE.match(text)
        if not m:
            raise ConfigError(f"not a distance: {value!r}", "examples: 1.2 m, 120 cm, 4 ft, 48 in")
        metres = float(m.group(1)) * _UNIT_M[m.group(2) or "m"]
    if metres != metres or not 0 <= metres <= MAX_DISTANCE_M:
        raise ConfigError(f"distance {value!r} is out of range", f"use 0 to {MAX_DISTANCE_M:.0f} m")
    return round(metres, 3)


def acoustic_ms(distance_m: float) -> float:
    """How long sound takes to travel ``distance_m`` metres, in ms (about 2.9 ms per metre)."""
    return distance_m / SPEED_OF_SOUND_M_S * 1000.0


def normalize_mac(value: str) -> str:
    value = value.strip().replace("-", ":").replace("_", ":").upper()
    if not MAC_RE.match(value):
        raise ConfigError(f"not a Bluetooth address: {value!r}", "expected the form AA:BB:CC:DD:EE:FF")
    return value


def mac_underscored(mac: str) -> str:
    return normalize_mac(mac).replace(":", "_")


def normalize_position(value: str) -> str:
    """Lower-case slug: ``"Living Room"`` -> ``"living_room"``. Must start with a letter."""
    slug = re.sub(r"[\s]+", "_", str(value).strip().lower())
    if not POSITION_RE.match(slug):
        raise ConfigError(f"invalid position {value!r}",
                          "use left, right, or a short name starting with a letter (letters, digits, _ and -)")
    return slug


def position_rank(position: str) -> Tuple[int, str]:
    """Sort key: left, right, then legacy third/fourth, then everything else by name."""
    if position in STEREO_POSITIONS:
        return (STEREO_POSITIONS.index(position), position)
    if position in LEGACY_POSITIONS:
        return (LEGACY_POSITIONS[position], position)
    m = re.match(r"^speaker(\d+)$", position)
    if m:
        return (int(m.group(1)), position)
    return (100, position)


@dataclass
class Speaker:
    address: str
    name: str = ""
    position: str = "left"
    offset_ms: float = 0.0  # extra delay applied to THIS speaker so it lines up with the others
    volume: Optional[int] = None  # per-speaker trim in percent; None = full level (the speaker's own max)
    distance_m: Optional[float] = None  # metres from the calibration microphone (the laptop); None = unknown

    def __post_init__(self) -> None:
        self.address = normalize_mac(self.address)
        self.position = normalize_position(self.position)
        self.distance_m = parse_distance(self.distance_m)

    @property
    def acoustic_ms(self) -> Optional[float]:
        """Sound travel time from this speaker to the microphone, when the distance is known."""
        return None if self.distance_m is None else acoustic_ms(self.distance_m)

    @property
    def is_stereo_side(self) -> bool:
        return self.position in STEREO_POSITIONS

    @property
    def label(self) -> str:
        return self.name or self.address

    @property
    def mac_key(self) -> str:
        return mac_underscored(self.address)


@dataclass
class Config:
    speakers: List[Speaker] = field(default_factory=list)
    engine: str = "auto"  # auto | pipewire | pulse (Linux) | aggregate | relay (macOS)
    stereo_split: bool = False  # advanced: left speaker gets L channel only, right gets R only. Off = every speaker plays the full mix
    pin_microphone: bool = True  # keep the system microphone on the built-in mic so no speaker drops into headset mode
    microphone: str = "builtin"  # "builtin" or an input device name/UID
    latency_compensate: bool = True  # let the engine align speakers from their reported latency
    set_default_sink: bool = True  # route all desktop audio through Soundscape while it is up
    resample_quality: int = 10  # PipeWire 0..14; higher = cleaner drift correction
    pulse_resample_method: str = "soxr-vhq"  # PulseAudio combine-sink resampler
    description: str = "Soundscape (both speakers)"
    sink_name: str = SINK_NAME
    remote: bool = False  # let paired phones on the local network use the dashboard; switchable live
    remote_token: str = ""  # legacy (0.7.x protected the network side with this token); kept so old configs load
    ui_port: int = 8787
    follow_connected: bool = True  # speakers connected to this machine join the group by themselves (headphones never do)
    connect_timeout_s: float = 25.0
    sink_timeout_s: float = 15.0

    def __post_init__(self) -> None:
        if self.engine not in ENGINES:
            raise ConfigError(f"unknown engine {self.engine!r}", f"use one of {', '.join(ENGINES)}")

    # -- speaker helpers -------------------------------------------------
    def find(self, selector: str) -> Optional[Speaker]:
        """Match by position (left/right/any name), address, or case-insensitive name prefix."""
        sel = selector.strip()
        try:
            slug = normalize_position(sel)
        except ConfigError:
            slug = ""
        for sp in self.speakers:
            if slug and sp.position == slug:
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
        self.sort()

    def sort(self) -> None:
        self.speakers.sort(key=lambda s: position_rank(s.position))

    def next_position(self) -> str:
        """left, right, then speaker3, speaker4, ... whichever is free."""
        taken = {s.position for s in self.speakers}
        for p in STEREO_POSITIONS:
            if p not in taken:
                return p
        n = 3
        while f"speaker{n}" in taken or (n == 3 and "third" in taken) or (n == 4 and "fourth" in taken):
            n += 1
        return f"speaker{n}"

    def ordered(self) -> List[Speaker]:
        return sorted(self.speakers, key=lambda s: position_rank(s.position))

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

    # -- settings ----------------------------------------------------------
    SETTABLE = ("engine", "stereo_split", "latency_compensate", "set_default_sink", "resample_quality",
                "pulse_resample_method", "description", "connect_timeout_s", "sink_timeout_s",
                "pin_microphone", "microphone", "remote", "remote_token", "ui_port", "follow_connected")

    def set_option(self, key: str, raw: object) -> object:
        """Set a scalar option from user input with type coercion; returns the stored value."""
        if key not in self.SETTABLE:
            raise ConfigError(f"unknown setting {key!r}", "settings: " + ", ".join(self.SETTABLE))
        current = getattr(self, key)
        value: object = raw
        try:
            if isinstance(current, bool):
                text = str(raw).strip().lower()
                if text not in ("1", "true", "yes", "on", "0", "false", "no", "off"):
                    raise ValueError(raw)
                value = text in ("1", "true", "yes", "on")
            elif isinstance(current, int):
                value = int(str(raw))
            elif isinstance(current, float):
                value = float(str(raw))
            else:
                value = str(raw)
        except ValueError:
            raise ConfigError(f"{key} expects a {type(current).__name__}, got {raw!r}") from None
        if key == "engine" and value not in ENGINES:
            raise ConfigError(f"engine must be one of {', '.join(ENGINES)}")
        if key == "resample_quality" and not 0 <= int(value) <= 14:  # type: ignore[call-overload]
            raise ConfigError("resample_quality must be between 0 and 14")
        if key == "ui_port" and not 1 <= int(value) <= 65535:  # type: ignore[call-overload]
            raise ConfigError("ui_port must be between 1 and 65535")
        setattr(self, key, value)
        return value

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
