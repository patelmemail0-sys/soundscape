"""Speaker-to-speaker sync calibration.

Two ways to find the residual offset after the engine's automatic compensation:

* **microphone** (`soundscape sync --auto`): play the calibration track through the virtual
  output while muting one speaker then the other, record with any microphone, locate both
  chirps by cross-correlation, and turn the measured gap into a delay on the early speaker.
* **by ear** (`soundscape sync`): loop a click track and nudge the offset live until the
  double-click collapses into one.
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import tempfile
import termios
import time
import tty
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from . import dsp, tones
from .config import Speaker
from .errors import AudioError, ConfigError
from .session import Session

Log = Callable[[str], None]
RECORD_RATE = 16000


@dataclass
class Measurement:
    first: Speaker
    second: Speaker
    gap_ms: float  # second arrival - first arrival - nominal 3 s, in ms (positive: second is later)
    confidence: float
    applied_to: Optional[Speaker] = None
    applied_ms: float = 0.0


class Calibrator:
    def __init__(self, session: Session, log: Log = print, sleep: Callable[[float], None] = time.sleep):
        self.session = session
        self.log = log
        self._sleep = sleep
        self.runner = session.runner
        self.pulse = session.pulse

    # -- helpers ---------------------------------------------------------
    def _pair(self) -> List[Speaker]:
        speakers = self.session.config.speakers
        if len(speakers) < 2:
            raise ConfigError("need two configured speakers to calibrate", "run `soundscape setup` first")
        return speakers[:2]

    def _require_up(self) -> str:
        state = self.session.state()
        if not state or not self.session.engine(state).alive(state):
            raise AudioError("Soundscape is not up", "run `soundscape up` first")
        return state.sink_name

    def _sink_of(self, sp: Speaker) -> str:
        sink = self.pulse.bluetooth_sink(sp.address)
        if sink is None:
            raise AudioError(f"{sp.label} has no audio sink", "is it connected?")
        return sink.name

    def _play(self, path: Path, sink: str) -> subprocess.Popen:
        self.runner.require("paplay")
        return self.runner.popen(["paplay", "--device", sink, str(path)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # -- microphone ------------------------------------------------------
    def measure(self, source: Optional[str] = None, workdir: Optional[Path] = None) -> Measurement:
        a, b = self._pair()
        sink = self._require_up()
        sink_a, sink_b = self._sink_of(a), self._sink_of(b)
        workdir = workdir or Path(tempfile.mkdtemp(prefix="soundscape-"))
        track = tones.calibration_track(workdir / "calibration.wav")
        raw = workdir / "mic.raw"
        self.runner.require("parec")
        if not source:
            source = self.pulse.default_source()
        if not source:
            raise AudioError("no microphone source found", "pass --source <name> (see `pactl list short sources`)")
        if sink in source or any(s in source for s in (sink_a, sink_b)):
            raise AudioError(f"recording source {source} is a loopback of the speakers, not a microphone",
                             "pass --source with a real microphone")

        self.log(f"recording from {source}; keep the room quiet for ~7 seconds")
        with open(raw, "wb") as fh:
            rec = self.runner.popen(["parec", "--device", source, "--format=s16le",
                                     f"--rate={RECORD_RATE}", "--channels=1", "--raw"],
                                    stdout=fh, stderr=subprocess.DEVNULL)
            try:
                self._sleep(0.6)  # let the capture stream settle before the first chirp
                self.pulse.set_mute(sink_b, True)
                self.pulse.set_mute(sink_a, False)
                started = time.monotonic()
                player = self._play(track, sink)
                self._sleep(tones.CALIBRATION_SWITCH_S)
                self.pulse.set_mute(sink_a, True)
                self.pulse.set_mute(sink_b, False)
                remaining = tones.CALIBRATION_TOTAL_S + 0.8 - (time.monotonic() - started)
                self._sleep(max(0.0, remaining))
                try:
                    player.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    player.kill()
            finally:
                self.pulse.set_mute(sink_a, False)
                self.pulse.set_mute(sink_b, False)
                rec.terminate()
                try:
                    rec.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    rec.kill()

        data = raw.read_bytes()
        if len(data) < RECORD_RATE * 2 * 4:
            raise AudioError("the recording is too short; the microphone did not deliver audio",
                             "check `pactl list short sources` and pass --source")
        recording = tones.pcm_s16le_to_floats(data)
        template = tones.chirp(RECORD_RATE)
        # chirps were sent at 1.0 s and 4.0 s *after paplay started*; the recorder started
        # ~0.6 s earlier, so search from those marks minus a little slack.
        lead = 0.6
        gap_s, c1, c2 = dsp.measure_gap(
            recording, template, RECORD_RATE,
            first_at_s=tones.CALIBRATION_FIRST_S + lead - 0.3,
            second_at_s=tones.CALIBRATION_SECOND_S + lead - 0.3,
            max_latency_s=1.5)
        nominal = tones.CALIBRATION_SECOND_S - tones.CALIBRATION_FIRST_S
        gap_ms = dsp.best_offset_ms(gap_s, nominal)
        return Measurement(first=a, second=b, gap_ms=gap_ms, confidence=min(c1, c2))

    def auto(self, source: Optional[str] = None, apply: bool = True, min_confidence: float = 4.0,
             passes: int = 1) -> Measurement:
        result: Optional[Measurement] = None
        for i in range(passes):
            m = self.measure(source)
            self.log(f"pass {i + 1}: {m.second.label} arrives {m.gap_ms:+.1f} ms relative to "
                     f"{m.first.label} (confidence {m.confidence:.1f})")
            if m.confidence < min_confidence:
                raise AudioError(f"chirps were not detected reliably (confidence {m.confidence:.1f})",
                                 "reduce background noise, raise the speaker volume, move the mic closer, "
                                 "and make sure both speakers are actually playing")
            if apply and abs(m.gap_ms) >= 0.5:
                early, late = (m.first, m.second) if m.gap_ms > 0 else (m.second, m.first)
                self.session.apply_measured_gap(early, late, abs(m.gap_ms))
                m.applied_to, m.applied_ms = early, abs(m.gap_ms)
                self.log(f"delayed {early.label} by {abs(m.gap_ms):.1f} ms (now {early.offset_ms:.1f} ms)")
            result = m
        assert result is not None
        return result

    # -- by ear ----------------------------------------------------------
    def by_ear(self, step_ms: float = 5.0, fine_ms: float = 1.0) -> None:
        a, b = self._pair()
        sink = self._require_up()
        if not sys.stdin.isatty():
            raise AudioError("by-ear sync needs an interactive terminal", "use `soundscape sync --auto`")
        workdir = Path(tempfile.mkdtemp(prefix="soundscape-"))
        track = tones.click_track(workdir / "clicks.wav", seconds=60)
        target = b
        self.log("Listening test: you should hear ONE click per beat. If it sounds doubled or smeared,")
        self.log("nudge the delay until the clicks merge.")
        self.log(f"  [ / ]  delay {target.label} by -{step_ms:.0f} / +{step_ms:.0f} ms     , / .  fine -{fine_ms:.0f} / +{fine_ms:.0f} ms")
        self.log("  t  switch which speaker you are nudging      0  reset both to 0      q  save and quit")
        player = self._play(track, sink)
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while True:
                if player.poll() is not None:
                    player = self._play(track, sink)
                self._print_offsets(a, b, target)
                r, _, _ = select.select([fd], [], [], 0.5)
                if not r:
                    continue
                ch = os.read(fd, 1).decode(errors="ignore")
                if ch == "q":
                    break
                if ch == "t":
                    target = a if target is b else b
                elif ch == "0":
                    a.offset_ms = b.offset_ms = 0.0
                    self.session.set_offset(a.address, 0.0)
                    self.session.set_offset(b.address, 0.0)
                else:
                    delta = {"[": -step_ms, "]": step_ms, ",": -fine_ms, ".": fine_ms}.get(ch)
                    if delta is None:
                        continue
                    self._nudge(target, delta, a, b)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            if player.poll() is None:
                player.terminate()
            sys.stdout.write("\n")
        self.log(f"saved: {a.label} +{a.offset_ms:.1f} ms, {b.label} +{b.offset_ms:.1f} ms")

    def _nudge(self, target: Speaker, delta: float, a: Speaker, b: Speaker) -> None:
        other = a if target is b else b
        if target.offset_ms + delta < 0:
            # delaying the target "negatively" means delaying the other one instead
            other.offset_ms = round(other.offset_ms - (target.offset_ms + delta), 3)
            target.offset_ms = 0.0
        else:
            target.offset_ms = round(target.offset_ms + delta, 3)
        self.session.set_offset(target.address, target.offset_ms)
        self.session.set_offset(other.address, other.offset_ms)

    def _print_offsets(self, a: Speaker, b: Speaker, target: Speaker) -> None:
        mark_a = ">" if target is a else " "
        mark_b = ">" if target is b else " "
        sys.stdout.write(f"\r{mark_a}{a.label}: +{a.offset_ms:6.1f} ms   {mark_b}{b.label}: +{b.offset_ms:6.1f} ms   ")
        sys.stdout.flush()
