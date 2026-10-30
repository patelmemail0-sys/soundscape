"""Play local files or URLs through the virtual output using whatever player is installed."""

from __future__ import annotations

import os
import random
import subprocess
from typing import List, Optional, Sequence

from .errors import ToolMissing
from .runner import Runner

PLAYERS = ("mpv", "ffplay", "paplay")


def pick_player(runner: Runner, preferred: Optional[str] = None) -> str:
    candidates = [preferred] if preferred else list(PLAYERS)
    for name in candidates:
        if name and runner.which(name):
            return name
    raise ToolMissing(preferred or "mpv", "install mpv (recommended), ffmpeg (ffplay) or pulseaudio-utils (paplay)")


def build_command(player: str, files: Sequence[str], sink: str, loop: bool = False, volume: Optional[int] = None) -> List[str]:
    if player == "mpv":
        cmd = ["mpv", "--no-video", "--really-quiet", "--term-osd-bar", f"--audio-device=pulse/{sink}"]
        if loop:
            cmd.append("--loop-playlist=inf")
        if volume is not None:
            cmd.append(f"--volume={volume}")
        return cmd + list(files)
    if player == "ffplay":
        if len(files) != 1:
            raise ValueError("ffplay plays one file at a time; install mpv for playlists")
        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error"]
        if loop:
            cmd += ["-loop", "0"]
        return cmd + [files[0]]
    if player == "paplay":
        if len(files) != 1:
            raise ValueError("paplay plays one file at a time; install mpv for playlists")
        return ["paplay", "--device", sink, files[0]]
    raise ValueError(f"unknown player {player}")


def play(runner: Runner, files: Sequence[str], sink: str, *, player: Optional[str] = None,
         loop: bool = False, shuffle: bool = False, volume: Optional[int] = None) -> int:
    name = pick_player(runner, player)
    items = list(files)
    if shuffle:
        random.shuffle(items)
    env = dict(os.environ)
    env["PULSE_SINK"] = sink  # every PulseAudio/PipeWire client honours this
    if name in ("ffplay", "paplay") and len(items) > 1:
        code = 0
        for item in items:
            proc = runner.popen(build_command(name, [item], sink, loop=False), env=env)
            code = proc.wait()
            if code not in (0, None):
                break
        return int(code or 0)
    proc = runner.popen(build_command(name, items, sink, loop=loop, volume=volume), env=env)
    try:
        return int(proc.wait() or 0)
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        return 130
