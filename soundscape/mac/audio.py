"""MacAudio: the macOS counterpart of :class:`soundscape.pulse.PulseCtl`.

Same method names, so :class:`soundscape.session.Session` does not care which OS it is on.
A "sink" here is a CoreAudio output device; its ``name`` is the device UID.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..errors import AudioError, ToolMissing
from ..pulse import ServerInfo, Sink
from ..runner import Runner
from .coreaudio import AGGREGATE_UID_PREFIX, AudioDevice, CoreAudio, HALError

BLACKHOLE_HINT = ("install BlackHole (free): brew install blackhole-2ch  "
                  "or download the 2ch installer from existential.audio/blackhole")


def device_to_sink(d: AudioDevice, volume: Optional[float], mute: Optional[bool]) -> Sink:
    return Sink(index=d.id, name=d.uid, description=d.name, state="RUNNING",
                mute=bool(mute), volume_percent=int(round((volume if volume is not None else 1.0) * 100)),
                properties={"transport": d.transport, "device.api": "coreaudio",
                            "sample_rate": str(int(d.sample_rate))},
                sample_spec=f"float32 {d.output_channels}ch {int(d.sample_rate)}Hz")


class MacAudio:
    def __init__(self, runner: Runner, coreaudio: Optional[CoreAudio] = None, recorder=None):
        self.runner = runner
        self._ca = coreaudio
        self._recorder = recorder  # callable(device_id, seconds) -> bytes, injected in tests
        self._saved_volume: Dict[str, float] = {}  # uid -> volume before we muted it by volume
        self.group: List[str] = []  # device UIDs a "master" volume applies to

    @property
    def ca(self) -> CoreAudio:
        if self._ca is None:
            try:
                self._ca = CoreAudio()
            except OSError as exc:
                raise AudioError(f"CoreAudio is not available: {exc}") from exc
        return self._ca

    # -- queries ---------------------------------------------------------
    def info(self) -> ServerInfo:
        try:
            default_id = self.ca.default_output()
            default = self.ca.device(default_id).uid if default_id else ""
        except HALError:
            default = ""
        try:
            in_id = self.ca.default_input()
            default_in = self.ca.device(in_id).uid if in_id else ""
        except HALError:
            default_in = ""
        return ServerInfo(server_name="CoreAudio", server_version="", default_sink=default, default_source=default_in)

    def sinks(self) -> List[Sink]:
        out = []
        for d in self.ca.devices():
            if d.output_channels:
                out.append(device_to_sink(d, self.ca.volume(d.id), self.ca.mute(d.id)))
        return out

    def sink(self, name: str) -> Optional[Sink]:
        d = self.ca.device_by_uid(name)
        if d is None or not d.output_channels and not d.is_soundscape_aggregate:
            return None
        return device_to_sink(d, self.ca.volume(d.id), self.ca.mute(d.id))

    def bluetooth_sink(self, mac: str) -> Optional[Sink]:
        d = self.ca.bluetooth_output(mac)
        return device_to_sink(d, self.ca.volume(d.id), self.ca.mute(d.id)) if d else None

    def blackhole(self) -> Optional[AudioDevice]:
        for d in self.ca.devices():
            if "blackhole" in d.uid.lower() or "blackhole" in d.name.lower():
                if d.input_channels and d.output_channels:
                    return d
        return None

    def default_source(self) -> str:
        return self.info().default_source

    def sources(self) -> List[dict]:
        out = []
        for d in self.ca.devices():
            if d.input_channels and "blackhole" not in d.uid.lower() and not d.is_soundscape_aggregate:
                out.append({"name": d.uid, "description": d.name})
        return out

    def modules(self) -> list:  # PulseAudio concept; nothing to report here
        return []

    # -- control ------------------------------------------------------------
    def _is_master(self, name: str, d: Optional[AudioDevice]) -> bool:
        """Only a Soundscape aggregate fans a volume out to its speakers; in relay mode the
        master is BlackHole itself (what the Mac's volume keys control), so the full level
        reaches the speakers whenever it is at 100%."""
        if name.startswith(AGGREGATE_UID_PREFIX):
            return True
        return d is not None and d.is_soundscape_aggregate

    def _targets(self, name: str) -> List[AudioDevice]:
        d = self.ca.device_by_uid(name)
        if not self._is_master(name, d):
            if d is None:
                raise AudioError(f"no audio device named {name}")
            return [d]
        # a Soundscape master (aggregate or relay) -> every speaker in the group
        targets = []
        for uid in self.group:
            dev = self.ca.device_by_uid(uid)
            if dev:
                targets.append(dev)
        if not targets and d is not None:
            for sub in self.ca.aggregate_subdevices(d.id):
                try:
                    targets.append(self.ca.device(sub))
                except HALError:
                    continue
        if not targets:
            raise AudioError(f"no speakers behind {name}", "is Soundscape up?")
        return targets

    def set_volume(self, name: str, percent: int) -> None:
        scalar = max(0, min(100, int(percent))) / 100.0
        for dev in self._targets(name):
            if not self.ca.set_volume(dev.id, scalar):
                raise AudioError(f"{dev.name} does not expose a volume control",
                                 "set its volume on the speaker itself")

    def get_volume(self, name: str) -> Optional[int]:
        s = self.sink(name)
        return s.volume_percent if s else None

    def set_mute(self, name: str, mute: bool) -> None:
        for dev in self._targets(name):
            if self.ca.set_mute(dev.id, mute):
                continue
            # no mute control: emulate with volume, remembering what we silenced
            if mute:
                if dev.uid in self._saved_volume:
                    continue  # already muted by us; keep the original level
                current = self.ca.volume(dev.id)
                self._saved_volume[dev.uid] = current if current is not None else 1.0
                self.ca.set_volume(dev.id, 0.0)
            elif dev.uid in self._saved_volume:
                self.ca.set_volume(dev.id, self._saved_volume.pop(dev.uid))
            # not muted by us: nothing to restore

    def set_default_sink(self, name: str) -> None:
        d = self.ca.device_by_uid(name)
        if d is None:
            raise AudioError(f"no audio device named {name}")
        self.ca.set_default_output(d.id)

    def move_all_inputs(self, to_sink: str) -> int:  # CoreAudio apps follow the default output on their own
        return 0

    # -- playback / capture --------------------------------------------------
    def play_file(self, path: Path, sink: Optional[str] = None, wait: bool = True) -> subprocess.Popen[Any]:
        """Play a WAV through ``sink`` (a device UID) or the default output, using afplay."""
        if not self.runner.which("afplay"):
            raise ToolMissing("afplay", "afplay ships with macOS; is /usr/bin on your PATH?")
        previous: Optional[int] = None
        if sink:
            target = self.ca.device_by_uid(sink)
            if target is not None:
                current = self.ca.default_output()
                if current != target.id:
                    previous = current
                    self.ca.set_default_output(target.id)
                    time.sleep(0.3)
        proc = self.runner.popen(["afplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if wait or previous is not None:
            proc.wait()
            if previous is not None:
                self.ca.set_default_output(previous)
        return proc

    def record(self, seconds: float, source: Optional[str] = None) -> Tuple[List[float], int]:
        """Record mono floats from ``source`` (device UID) or the default input."""
        dev = self.ca.device_by_uid(source) if source else None
        if source and dev is None:
            raise AudioError(f"no input device named {source}", "see `soundscape status --json` for device UIDs")
        device_id = dev.id if dev else self.ca.default_input()
        device = dev or self.ca.device(device_id)
        if not device.input_channels:
            raise AudioError(f"{device.name} has no inputs", "pick a microphone with --source")
        rate = int(round(device.sample_rate)) or 48000
        recorder = self._recorder
        if recorder is None:
            from .hal_io import record as hal_record
            recorder = lambda did, secs: hal_record(did, secs, self.ca.hal)  # noqa: E731
        raw = recorder(device_id, seconds)
        from array import array
        samples = array("f")
        samples.frombytes(raw[: len(raw) // 4 * 4])
        channels = max(1, device.input_channels if device.input_streams <= 1 else device.input_channels // device.input_streams)
        mono = samples[0::channels] if channels > 1 else samples
        return list(mono), rate
