"""Environment checks with concrete fixes. `soundscape doctor` prints them; `setup` runs them first."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .bluez import BlueZ
from .errors import INSTALL_HINTS, SoundscapeError
from .pipewire import PipeWireCtl, version_tuple
from .pulse import PulseCtl
from .runner import Runner

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    status: str
    title: str
    detail: str = ""
    fix: str = ""

    @property
    def symbol(self) -> str:
        return {OK: "+", WARN: "!", FAIL: "x"}[self.status]


def run_checks(runner: Runner, bluez: Optional[BlueZ] = None, pulse: Optional[PulseCtl] = None,
               pw: Optional[PipeWireCtl] = None) -> List[Check]:
    bluez = bluez or BlueZ(runner)
    pulse = pulse or PulseCtl(runner)
    pw = pw or PipeWireCtl(runner)
    checks: List[Check] = []

    for tool, required in (("busctl", True), ("bluetoothctl", True), ("pactl", True), ("paplay", False), ("parec", False)):
        if runner.which(tool):
            checks.append(Check(OK, f"{tool} found"))
        else:
            checks.append(Check(FAIL if required else WARN, f"{tool} missing",
                                "needed for " + {"paplay": "test tones and sync", "parec": "microphone sync"}.get(tool, "core operation"),
                                INSTALL_HINTS.get(tool, "")))

    # sound server
    info = None
    try:
        info = pulse.info()
        checks.append(Check(OK, f"sound server: {info.server_name} {info.server_version}".rstrip()))
    except SoundscapeError as exc:
        checks.append(Check(FAIL, "no sound server", exc.message, exc.hint or
                            "start PipeWire: systemctl --user enable --now pipewire pipewire-pulse wireplumber"))

    if info is not None and info.is_pipewire:
        version = pw.version()
        if not runner.which("pipewire") or not runner.which("pw-dump") or not runner.which("pw-cli"):
            checks.append(Check(WARN, "pipewire CLI tools missing",
                                "falling back to the pactl combine-sink engine (no live sync offsets)",
                                INSTALL_HINTS["pw-cli"]))
        elif version and version_tuple(version) < (0, 3, 65):
            checks.append(Check(WARN, f"PipeWire {version} is old",
                                "combine-stream and the delay filter need >= 0.3.65; 1.0+ recommended",
                                "upgrade PipeWire or use the pactl engine (`soundscape config set engine pulse`)"))
        else:
            checks.append(Check(OK, f"PipeWire native engine available ({version or 'version unknown'})"))
    elif info is not None:
        checks.append(Check(OK, "PulseAudio: using module-combine-sink engine",
                            "sync offsets are applied through port latency offsets"))

    # bluetooth
    try:
        adapters = bluez.adapters()
        if not adapters:
            checks.append(Check(FAIL, "no Bluetooth adapter", "", "rfkill unblock bluetooth, or plug in a dongle"))
        else:
            a = adapters[0]
            checks.append(Check(OK if a.powered else WARN, f"adapter {a.hci} ({a.address}) {'powered' if a.powered else 'off'}",
                                "", "" if a.powered else "bluetoothctl power on"))
            audio = [d for d in bluez.devices() if d.is_audio_sink]
            paired = [d for d in audio if d.paired]
            checks.append(Check(OK if paired else WARN, f"{len(paired)} paired audio device(s), {len(audio)} seen",
                                ", ".join(d.label for d in paired[:6]),
                                "" if paired else "put a speaker in pairing mode and run `soundscape scan`"))
    except SoundscapeError as exc:
        checks.append(Check(FAIL, "bluetoothd unreachable", exc.message,
                            exc.hint or "sudo systemctl enable --now bluetooth"))

    # audio-side bluetooth support
    if info is not None:
        try:
            mods = pulse.modules()
            names = {m.name for m in mods}
            if info.is_pipewire:
                checks.append(Check(OK, "Bluetooth audio handled by WirePlumber/PipeWire",
                                    "if speakers connect but no sink appears, install libspa-0.2-bluetooth / pipewire-audio"))
            elif "module-bluetooth-discover" in names or "module-bluez5-discover" in names:
                checks.append(Check(OK, "pulseaudio-module-bluetooth loaded"))
            else:
                checks.append(Check(FAIL, "PulseAudio has no Bluetooth module",
                                    "speakers will connect but produce no sink",
                                    "sudo apt install pulseaudio-module-bluetooth && pactl load-module module-bluetooth-discover"))
        except SoundscapeError:
            pass

    for tool in ("mpv", "ffplay"):
        if runner.which(tool):
            checks.append(Check(OK, f"player: {tool}"))
            break
    else:
        checks.append(Check(WARN, "no media player found", "`soundscape play` needs mpv or ffplay; other apps still work",
                            INSTALL_HINTS["mpv"]))
    return checks


def summarize(checks: List[Check]) -> str:
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    if fails:
        return f"{fails} blocking problem(s), {warns} warning(s)"
    if warns:
        return f"ready with {warns} warning(s)"
    return "everything looks good"
