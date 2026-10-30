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
import threading
import time
import tty
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import dsp, tones
from .config import Speaker
from .errors import AudioError, ConfigError
from .session import Session

Log = Callable[[str], None]
RECORD_RATE = 16000  # analysis rate; recordings at other rates are decimated to this
LEAD_S = 0.6  # recording starts this long before playback


@dataclass
class Measurement:
    """Arrival of every speaker relative to the first one, in ms (positive: later)."""
    speakers: List[Speaker]
    lag_ms: List[float]  # lag_ms[0] is always 0.0
    confidence: float
    applied: Dict[str, float] = field(default_factory=dict)  # address -> extra delay applied

    @property
    def first(self) -> Speaker:
        return self.speakers[0]

    @property
    def second(self) -> Speaker:
        return self.speakers[1]

    @property
    def gap_ms(self) -> float:
        """Two-speaker convenience: how much later the second speaker arrives."""
        return self.lag_ms[1] if len(self.lag_ms) > 1 else 0.0

    @property
    def applied_to(self) -> Optional[Speaker]:
        for sp in self.speakers:
            if self.applied.get(sp.address):
                return sp
        return None

    @property
    def applied_ms(self) -> float:
        return max(self.applied.values(), default=0.0)

    @property
    def spread_ms(self) -> float:
        return max(self.lag_ms) - min(self.lag_ms)


class Calibrator:
    def __init__(self, session: Session, log: Log = print, sleep: Callable[[float], None] = time.sleep):
        self.session = session
        self.log = log
        self._sleep = sleep
        self.runner = session.runner
        self.pulse = session.pulse

    # -- helpers ---------------------------------------------------------
    def _group(self) -> List[Speaker]:
        speakers = self.session.config.ordered()
        if len(speakers) < 2:
            raise ConfigError("need at least two configured speakers to calibrate", "run `soundscape setup` first")
        return speakers

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
        """Playback through the audio backend (paplay on Linux, afplay on macOS)."""
        return self.pulse.play_file(path, sink, wait=False)

    # -- microphone ------------------------------------------------------
    def measure(self, source: Optional[str] = None, workdir: Optional[Path] = None) -> Measurement:
        speakers = self._group()
        sink = self._require_up()
        sinks = [self._sink_of(sp) for sp in speakers]
        count = len(speakers)
        workdir = workdir or Path(tempfile.mkdtemp(prefix="soundscape-"))
        track = tones.calibration_track(workdir / "calibration.wav", count=count)
        source = source or self.pulse.default_source()
        if source and (sink in source or any(s in source for s in sinks) or source.endswith(".monitor")):
            raise AudioError(f"recording source {source} is a loopback of the speakers, not a microphone",
                             "pass --source with a real microphone")

        length = tones.calibration_length(count)
        total = LEAD_S + length + 0.8
        self.log(f"recording from {source or 'the default microphone'}; keep the room quiet for ~{total:.0f} seconds")
        result: Dict[str, Any] = {}

        def recorder() -> None:
            try:
                result["data"] = self.pulse.record(total, source or None)
            except Exception as exc:  # surfaced after the playback sequence
                result["error"] = exc

        def solo(index: int) -> None:
            for i, name in enumerate(sinks):
                self.pulse.set_mute(name, i != index)

        thread = threading.Thread(target=recorder, daemon=True)
        thread.start()
        try:
            self._sleep(LEAD_S)  # let the capture stream settle before the first chirp
            solo(0)
            started = time.monotonic()
            player = self._play(track, sink)
            elapsed = 0.0
            for k, switch_at in enumerate(tones.calibration_switches(count), start=1):
                self._sleep(max(0.0, switch_at - elapsed))
                elapsed = switch_at
                solo(k)
            remaining = length + 0.8 - max(elapsed, time.monotonic() - started)
            self._sleep(max(0.0, remaining))
            try:
                player.wait(timeout=5)
            except subprocess.TimeoutExpired:
                player.kill()
        finally:
            for name in sinks:
                self.pulse.set_mute(name, False)
        thread.join(timeout=total + 10)
        if "error" in result:
            raise result["error"]
        if "data" not in result:
            raise AudioError("the recording did not finish", "try again; if it persists pass --source")
        recording: List[float]
        rate: int
        recording, rate = result["data"]
        if rate != RECORD_RATE:
            recording = dsp.decimate(recording, rate, RECORD_RATE)
            rate = RECORD_RATE
        if len(recording) < rate * 4:
            raise AudioError("the recording is too short; the microphone did not deliver audio",
                             "check the input device and pass --source")
        template = tones.chirp(rate)
        # chirp k was sent at calibration_times()[k] after playback started; the recorder began
        # LEAD_S earlier, so search from those marks minus a little slack.
        sent = [t + LEAD_S - 0.3 for t in tones.calibration_times(count)]
        arrivals = dsp.locate(recording, template, rate, sent, max_latency_s=1.5)
        base = arrivals[0][0]
        lags = [round((a - base - k * tones.CALIBRATION_SPACING_S) * 1000.0, 2) for k, (a, _c) in enumerate(arrivals)]
        return Measurement(speakers=speakers, lag_ms=lags, confidence=min(c for _a, c in arrivals))

    def auto(self, source: Optional[str] = None, apply: bool = True, min_confidence: float = 4.0,
             passes: int = 1) -> Measurement:
        result: Optional[Measurement] = None
        for i in range(passes):
            m = self.measure(source)
            ref = m.first
            for sp, lag in zip(m.speakers[1:], m.lag_ms[1:]):
                self.log(f"pass {i + 1}: {sp.label} arrives {lag:+.1f} ms relative to {ref.label}")
            self.log(f"pass {i + 1}: confidence {m.confidence:.1f}, spread {m.spread_ms:.1f} ms")
            if m.confidence < min_confidence:
                raise AudioError(f"chirps were not detected reliably (confidence {m.confidence:.1f})",
                                 "reduce background noise, raise the speaker volume, move the mic closer, "
                                 "and make sure every speaker is actually playing")
            if apply and m.spread_ms >= 0.5:
                latest = max(m.lag_ms)
                for sp, lag in zip(m.speakers, m.lag_ms):
                    extra = round(latest - lag, 2)  # everyone waits for the slowest speaker
                    if extra >= 0.5:
                        self.session.set_offset(sp.address, extra, relative=True)
                        m.applied[sp.address] = extra
                        self.log(f"delayed {sp.label} by {extra:.1f} ms (now {sp.offset_ms:.1f} ms)")
            result = m
        assert result is not None
        return result

    # -- by ear ----------------------------------------------------------
    def by_ear(self, step_ms: float = 5.0, fine_ms: float = 1.0) -> None:
        speakers = self._group()
        sink = self._require_up()
        if not sys.stdin.isatty():
            raise AudioError("by-ear sync needs an interactive terminal", "use `soundscape sync --auto`")
        workdir = Path(tempfile.mkdtemp(prefix="soundscape-"))
        track = tones.click_track(workdir / "clicks.wav", seconds=60)
        target = speakers[1]
        self.log("Listening test: you should hear ONE click per beat. If it sounds doubled or smeared,")
        self.log("nudge the delay until the clicks merge.")
        self.log(f"  [ / ]  delay the marked speaker by -{step_ms:.0f} / +{step_ms:.0f} ms     , / .  fine -{fine_ms:.0f} / +{fine_ms:.0f} ms")
        self.log("  t  mark the next speaker      0  reset all to 0      q  save and quit")
        player = self._play(track, sink)
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while True:
                if player.poll() is not None:
                    player = self._play(track, sink)
                self._print_offsets(speakers, target)
                r, _, _ = select.select([fd], [], [], 0.5)
                if not r:
                    continue
                ch = os.read(fd, 1).decode(errors="ignore")
                if ch == "q":
                    break
                if ch == "t":
                    target = speakers[(speakers.index(target) + 1) % len(speakers)]
                elif ch == "0":
                    for sp in speakers:
                        sp.offset_ms = 0.0
                        self.session.set_offset(sp.address, 0.0)
                else:
                    delta = {"[": -step_ms, "]": step_ms, ",": -fine_ms, ".": fine_ms}.get(ch)
                    if delta is None:
                        continue
                    self._nudge(target, delta, speakers)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
            if player.poll() is None:
                player.terminate()
            sys.stdout.write("\n")
        self.log("saved: " + ", ".join(f"{sp.label} +{sp.offset_ms:.1f} ms" for sp in speakers))

    def _nudge(self, target: Speaker, delta: float, speakers: List[Speaker]) -> None:
        """Delay ``target`` by ``delta``; a negative result is turned into a delay on everyone else."""
        new = target.offset_ms + delta
        if new < 0:
            for sp in speakers:
                if sp is not target:
                    sp.offset_ms = round(sp.offset_ms - new, 3)
            target.offset_ms = 0.0
        else:
            target.offset_ms = round(new, 3)
        for sp in speakers:
            self.session.set_offset(sp.address, sp.offset_ms)

    def _print_offsets(self, speakers: List[Speaker], target: Speaker) -> None:
        cells = [f"{'>' if sp is target else ' '}{sp.label}: +{sp.offset_ms:6.1f} ms" for sp in speakers]
        sys.stdout.write("\r" + "   ".join(cells) + "   ")
        sys.stdout.flush()
