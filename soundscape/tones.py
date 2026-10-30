"""Pure-stdlib WAV generation for test tones, click tracks and calibration chirps."""

from __future__ import annotations

import math
import struct
import wave
from array import array
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

RATE = 48000


def write_wav(path: Path, left: Sequence[float], right: Sequence[float], rate: int = RATE) -> Path:
    """Write two float sequences in [-1, 1] as a 16-bit stereo WAV."""
    n = min(len(left), len(right))
    data = array("h")
    for i in range(n):
        lv = max(-1.0, min(1.0, left[i]))
        rv = max(-1.0, min(1.0, right[i]))
        data.append(int(lv * 32767))
        data.append(int(rv * 32767))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data.tobytes())
    return path


def read_wav_mono(path: Path) -> Tuple[List[float], int]:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError("only 16-bit WAV is supported")
    samples = array("h")
    samples.frombytes(frames)
    if channels == 1:
        return [s / 32768.0 for s in samples], rate
    mono = [sum(samples[i:i + channels]) / channels / 32768.0 for i in range(0, len(samples), channels)]
    return mono, rate


def pcm_s16le_to_floats(data: bytes) -> List[float]:
    count = len(data) // 2
    return [v / 32768.0 for v in struct.unpack(f"<{count}h", data[:count * 2])]


def silence(seconds: float, rate: int = RATE) -> List[float]:
    return [0.0] * int(seconds * rate)


def sine(freq: float, seconds: float, rate: int = RATE, amp: float = 0.4, fade: float = 0.01) -> List[float]:
    n = int(seconds * rate)
    fade_n = max(1, int(fade * rate))
    out = []
    for i in range(n):
        env = min(1.0, i / fade_n, (n - 1 - i) / fade_n)
        out.append(amp * env * math.sin(2 * math.pi * freq * i / rate))
    return out


def chirp(rate: int, seconds: float = 0.05, f0: float = 800.0, f1: float = 4000.0, amp: float = 0.8) -> List[float]:
    """Linear sweep with a Hann window: sharp autocorrelation, friendly to small speakers."""
    n = int(seconds * rate)
    out = []
    for i in range(n):
        t = i / rate
        phase = 2 * math.pi * (f0 * t + (f1 - f0) * t * t / (2 * seconds))
        window = 0.5 - 0.5 * math.cos(2 * math.pi * i / max(1, n - 1))
        out.append(amp * window * math.sin(phase))
    return out


def click(rate: int = RATE, seconds: float = 0.004, freq: float = 2000.0, amp: float = 0.9) -> List[float]:
    n = int(seconds * rate)
    return [amp * (1 - i / n) * math.sin(2 * math.pi * freq * i / rate) for i in range(n)]


def place(base: List[float], burst: Iterable[float], at_seconds: float, rate: int) -> None:
    start = int(at_seconds * rate)
    for i, v in enumerate(burst):
        j = start + i
        if 0 <= j < len(base):
            base[j] += v


def click_track(path: Path, seconds: float = 30.0, bpm: float = 120.0, rate: int = RATE) -> Path:
    """A metronome for syncing by ear: identical clicks on both channels."""
    mono = silence(seconds, rate)
    burst = click(rate)
    t = 0.0
    while t < seconds:
        place(mono, burst, t, rate)
        t += 60.0 / bpm
    return write_wav(path, mono, list(mono), rate)


CALIBRATION_FIRST_S = 1.0     # first chirp
CALIBRATION_SPACING_S = 3.0   # between consecutive chirps (one per speaker)
CALIBRATION_TAIL_S = 2.0      # silence after the last chirp
CALIBRATION_SECOND_S = CALIBRATION_FIRST_S + CALIBRATION_SPACING_S          # 4.0, two-speaker case
CALIBRATION_TOTAL_S = CALIBRATION_FIRST_S + CALIBRATION_SPACING_S + CALIBRATION_TAIL_S  # 6.0, two speakers
CALIBRATION_SWITCH_S = CALIBRATION_FIRST_S + CALIBRATION_SPACING_S / 2      # 2.5, two speakers


def calibration_times(count: int) -> List[float]:
    """When chirp k is sent, in track seconds."""
    return [CALIBRATION_FIRST_S + k * CALIBRATION_SPACING_S for k in range(count)]


def calibration_switches(count: int) -> List[float]:
    """When to move the un-muted speaker from k to k+1 (midway between chirps)."""
    return [CALIBRATION_FIRST_S + k * CALIBRATION_SPACING_S + CALIBRATION_SPACING_S / 2 for k in range(count - 1)]


def calibration_length(count: int) -> float:
    return CALIBRATION_FIRST_S + (count - 1) * CALIBRATION_SPACING_S + CALIBRATION_TAIL_S


def calibration_track(path: Path, rate: int = RATE, count: int = 2) -> Path:
    """``count`` identical chirps 3 s apart on both channels. The calibrator un-mutes one
    speaker per chirp, so the arrival gaps at a microphone minus the nominal spacing are
    exactly the sync errors between the speakers."""
    mono = silence(calibration_length(count), rate)
    burst = chirp(rate)
    for at in calibration_times(count):
        place(mono, burst, at, rate)
    return write_wav(path, mono, list(mono), rate)


TONE_SCALE = [440.0, 660.0, 880.0, 1100.0, 1320.0, 1540.0, 1760.0, 1980.0, 2200.0, 2420.0]


FIXED_SLOTS = {"left": 0, "right": 1, "third": 2, "fourth": 3}


def tone_slots(positions: Sequence[str]) -> dict:
    """Pitch slot per position: left/right keep their slots, free names fill the rest in order."""
    slots = {p: FIXED_SLOTS[p] for p in positions if p in FIXED_SLOTS}
    used = set(slots.values())
    nxt = 2
    for p in positions:
        if p in slots:
            continue
        while nxt in used:
            nxt += 1
        slots[p] = nxt
        used.add(nxt)
    return slots


def position_tone(path: Path, position: str, rate: int = RATE, index: int = 0, slot: Optional[int] = None) -> Path:
    """Distinct pitch per speaker so the user can tell which is which: left lowest, right
    next, then rising by position order. ``slot`` (from :func:`tone_slots`) wins over ``index``."""
    step = slot if slot is not None else FIXED_SLOTS.get(position, max(index, 2))
    freq = TONE_SCALE[min(step, len(TONE_SCALE) - 1)]
    pattern = silence(0.1, rate)
    for _ in range(2):
        pattern += sine(freq, 0.18, rate) + silence(0.08, rate)
    pattern += sine(freq, 0.4, rate) + silence(0.2, rate)
    return write_wav(path, pattern, list(pattern), rate)
