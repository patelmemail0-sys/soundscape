"""Relay engine process for macOS.

Runs as ``python -m soundscape.mac.ioproc``. It builds a private aggregate device out of the
BlackHole loopback (input) and the Bluetooth speakers (outputs), then copies every input block
to each speaker with a per-speaker delay measured in whole samples. CoreAudio's drift
compensation keeps the speakers' clocks locked to the master; this process only shifts time
and (optionally) splits stereo. Control is a Unix socket: ``delay <uid> <ms>``,
``stereo on|off``, ``status``, ``ping``, ``quit``.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import sys
import threading
import time
from array import array
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from ..config import SINK_NAME
from .coreaudio import (
    AGGREGATE_UID_PREFIX,
    CoreAudio,
    kAudioObjectPropertyScopeInput,
)
from .hal_io import Buffer, IOProcHost

MAX_DELAY_S = 1.0
FLOAT = 4


@dataclass
class Output:
    uid: str
    position: str
    delay_ms: float = 0.0
    delay_frames: int = 0
    streams: int = 1
    channels: int = 2
    frames_written: int = 0
    muted: bool = False  # calibration solo: output silence while the delay line keeps running


@dataclass
class Mixer:
    """Pure-Python state machine: history buffer in, delayed copies out. Unit-tested."""
    rate: int
    in_channels: int = 2
    outputs: List[Output] = field(default_factory=list)
    stereo_split: bool = False
    max_delay_s: float = MAX_DELAY_S
    skip_outputs: int = 0  # leading output buffers that belong to the loopback device itself
    buffer_map: Optional[List[Optional[Output]]] = None  # aggregate output buffer index -> speaker (None = silence)
    history: bytearray = field(default_factory=bytearray)
    frames_seen: int = 0
    cycles: int = 0

    def __post_init__(self) -> None:
        self.frame_bytes = self.in_channels * FLOAT
        self.capacity_frames = int(self.max_delay_s * self.rate) + 8192
        self.history = bytearray(self.capacity_frames * self.frame_bytes)  # starts silent

    def set_delay(self, uid: str, delay_ms: float) -> Output:
        for out in self.outputs:
            if out.uid == uid or out.position == uid:
                delay_ms = max(0.0, min(self.max_delay_s * 1000.0, float(delay_ms)))
                out.delay_ms = delay_ms
                out.delay_frames = int(round(delay_ms * self.rate / 1000.0))
                return out
        raise KeyError(uid)

    def push(self, data: bytes) -> None:
        """Append an input block, dropping the oldest frames beyond capacity."""
        hist = self.history
        hist += data
        overflow = len(hist) - self.capacity_frames * self.frame_bytes
        if overflow > 0:
            del hist[:overflow]
        self.frames_seen += len(data) // self.frame_bytes

    def take(self, frames: int, delay_frames: int) -> bytes:
        """The last ``frames`` frames, ``delay_frames`` ago (zero-padded at the start)."""
        end = len(self.history) - delay_frames * self.frame_bytes
        start = end - frames * self.frame_bytes
        if end <= 0:
            return bytes(frames * self.frame_bytes)
        if start < 0:
            return bytes(-start) + bytes(self.history[0:end])
        return bytes(self.history[start:end])

    def set_muted(self, uid: str, muted: bool) -> Output:
        for out in self.outputs:
            if out.uid == uid or out.position == uid:
                out.muted = muted
                return out
        raise KeyError(uid)

    def render_output(self, out: Output, frames: int) -> bytes:
        if out.muted:
            return bytes(frames * out.channels * FLOAT)
        block = self.take(frames, out.delay_frames)
        if out.channels == self.in_channels and not self.stereo_split:
            return block
        src = array("f")
        src.frombytes(block)
        if self.in_channels == 1:
            mono = src
        else:
            if self.stereo_split and out.position in ("left", "right"):
                mono = src[0::self.in_channels] if out.position == "left" else src[1::self.in_channels]
            else:
                mono = None
        dst = array("f", bytes(frames * out.channels * FLOAT))
        if mono is not None:
            for ch in range(out.channels):
                dst[ch::out.channels] = mono
        else:  # channel count differs: copy what matches, duplicate the last source channel
            for ch in range(out.channels):
                dst[ch::out.channels] = src[min(ch, self.in_channels - 1)::self.in_channels]
        return dst.tobytes()

    def process(self, inputs: Sequence[Buffer], outputs: Sequence[Buffer]) -> None:
        """IOProc body: read the first input buffer, write every output buffer."""
        if inputs:
            ch, size, addr = inputs[0]
            if size and addr:
                self.push(ctypes.string_at(addr, size))
        self.cycles += 1
        if self.buffer_map is not None:
            for index, (ch, size, addr) in enumerate(outputs):
                out = self.buffer_map[index] if index < len(self.buffer_map) else None
                if not size or not addr:
                    continue
                if out is None:
                    ctypes.memset(addr, 0, size)  # loopback's own output or an unknown device: silence
                    continue
                frames = size // (max(1, ch) * FLOAT)
                if out.channels != ch:
                    out.channels = ch
                data = self.render_output(out, frames)
                ctypes.memmove(addr, data, min(size, len(data)))
                out.frames_written += frames
            return
        index = 0
        for _ in range(min(self.skip_outputs, len(outputs))):
            ch, size, addr = outputs[index]
            index += 1
            if size and addr:
                ctypes.memset(addr, 0, size)  # never feed audio back into the loopback
        for out in self.outputs:
            for _ in range(out.streams):
                if index >= len(outputs):
                    return
                ch, size, addr = outputs[index]
                index += 1
                frames = size // (max(1, ch) * FLOAT)
                if out.channels != ch:
                    out.channels = ch
                data = self.render_output(out, frames)
                ctypes.memmove(addr, data, min(size, len(data)))
                out.frames_written += frames

    def status(self) -> Dict[str, object]:
        return {"rate": self.rate, "stereo_split": self.stereo_split, "frames_seen": self.frames_seen,
                "cycles": self.cycles,
                "buffer_map": [o.position if o else None for o in self.buffer_map] if self.buffer_map is not None else None,
                "outputs": [{"uid": o.uid, "position": o.position, "delay_ms": o.delay_ms,
                             "delay_frames": o.delay_frames, "channels": o.channels,
                             "frames_written": o.frames_written, "muted": o.muted} for o in self.outputs]}


class ControlServer:
    def __init__(self, path: str, mixer: Mixer, on_quit):
        self.path, self.mixer, self.on_quit = path, mixer, on_quit
        try:
            os.unlink(path)
        except OSError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(path)
        self.sock.listen(4)
        self.sock.settimeout(0.5)
        self.running = True

    def handle(self, line: str) -> str:
        parts = line.strip().split()
        if not parts:
            return "err empty"
        cmd = parts[0].lower()
        if cmd == "ping":
            return "pong"
        if cmd == "status":
            return json.dumps(self.mixer.status())
        if cmd == "delay" and len(parts) >= 3:
            # "delay <uid or position> <ms>"; UIDs may contain spaces, the value is always last
            target, value = line.strip()[len(parts[0]):].strip().rsplit(None, 1)
            try:
                out = self.mixer.set_delay(target, float(value))
            except (KeyError, ValueError):
                return "err unknown output or value"
            return f"ok {out.delay_ms:.3f} {out.delay_frames}"
        if cmd == "mute" and len(parts) >= 3:
            target, value = line.strip()[len(parts[0]):].strip().rsplit(None, 1)
            try:
                out = self.mixer.set_muted(target, value.lower() in ("1", "on", "true"))
            except KeyError:
                return "err unknown output"
            return f"ok {'muted' if out.muted else 'unmuted'}"
        if cmd == "stereo" and len(parts) == 2:
            self.mixer.stereo_split = parts[1].lower() in ("on", "1", "true")
            return "ok"
        if cmd == "quit":
            self.running = False
            self.on_quit()
            return "bye"
        return "err unknown command"

    def serve(self) -> None:
        while self.running:
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(2.0)
                try:
                    data = conn.recv(4096).decode("utf-8", "replace")
                    for line in data.splitlines() or [""]:
                        conn.sendall((self.handle(line) + "\n").encode())
                except OSError:
                    pass
        try:
            self.sock.close()
            os.unlink(self.path)
        except OSError:
            pass


MAX_SOCKET_PATH = 100


def short_socket_path(preferred: str) -> str:
    """AF_UNIX paths are limited to ~104 bytes on macOS; fall back to a private /tmp dir when needed."""
    if len(preferred.encode()) <= MAX_SOCKET_PATH:
        return preferred
    import hashlib
    import stat
    digest = hashlib.sha1(preferred.encode()).hexdigest()[:8]
    folder = f"/tmp/soundscape-{os.getuid()}"
    try:
        os.mkdir(folder, 0o700)
    except FileExistsError:
        st = os.lstat(folder)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
            raise OSError(f"{folder} exists and is not our private directory") from None
        os.chmod(folder, 0o700)
    return f"{folder}/{digest}.sock"


def send_command(path: str, command: str, timeout: float = 3.0) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(path)
        s.sendall((command.strip() + "\n").encode())
        return s.recv(65536).decode("utf-8", "replace").strip()


def parse_output_spec(spec: str) -> Output:
    """``position:UID:delay_ms``; UIDs themselves contain colons (``AA-BB-...:output``)."""
    position, rest = spec.split(":", 1)
    uid, delay = rest.rsplit(":", 1)
    if not position or not uid:
        raise ValueError(f"bad --output spec {spec!r}")
    return Output(uid=uid, position=position, delay_ms=float(delay))


def build_buffer_map(ca: CoreAudio, agg_id: int, input_uid: str, outputs: Sequence[Output]) -> Optional[List[Optional[Output]]]:
    """Map every output buffer of the aggregate to a speaker using the aggregate's *active*
    sub-device order (not the order we asked for). None entries are silenced."""
    sub_ids = ca.aggregate_subdevices(agg_id)
    if not sub_ids:
        return None
    by_uid = {o.uid: o for o in outputs}
    mapping: List[Optional[Output]] = []
    for sub in sub_ids:
        try:
            dev = ca.device(sub)
        except Exception:
            continue
        streams = max(1, dev.output_streams) if dev.output_channels else 0
        out = by_uid.get(dev.uid)
        if dev.uid == input_uid or out is None:
            mapping.extend([None] * streams)
        else:
            out.streams = streams
            out.channels = max(1, dev.output_channels // streams)
            mapping.extend([out] * streams)
    missing = [o.uid for o in outputs if o not in mapping]
    if missing:
        print(f"warning: not active inside the aggregate (silent): {', '.join(missing)}", file=sys.stderr, flush=True)
    return mapping


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="soundscape-relay")
    p.add_argument("--socket", required=True)
    p.add_argument("--input", required=True, help="UID of the loopback device (BlackHole)")
    p.add_argument("--output", action="append", required=True, help="position:UID:delay_ms")
    p.add_argument("--name", default="Soundscape relay")
    p.add_argument("--uid", default=f"{AGGREGATE_UID_PREFIX}{SINK_NAME}.relay")
    p.add_argument("--stereo", action="store_true")
    p.add_argument("--buffer", type=int, default=1024, help="buffer size in frames")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    ca = CoreAudio()
    outputs: List[Output] = []
    for spec in args.output:
        outputs.append(parse_output_spec(spec))
    master = outputs[0].uid
    subdevices = [(args.input, True)] + [(o.uid, o.uid != master) for o in outputs]
    members = [d.id for d in (ca.device_by_uid(u) for u, _ in subdevices) if d is not None]
    rate_chosen = ca.align_sample_rates(members)
    print(f"RATE {rate_chosen}", flush=True)
    agg_id = ca.create_aggregate(args.uid, args.name, subdevices, master_uid=master, private=True)
    try:
        time.sleep(0.5)
        try:
            ca.set_buffer_frame_size(agg_id, args.buffer)
        except Exception:
            pass
        rate = int(round(ca.device(agg_id).sample_rate)) or 48000
        in_formats = ca.stream_formats(agg_id, kAudioObjectPropertyScopeInput)
        in_channels = in_formats[0].channels if in_formats else 2
        for out in outputs:
            dev = ca.device_by_uid(out.uid)
            if dev is None:
                print(f"error: output device {out.uid} not found", file=sys.stderr, flush=True)
                return 2
            out.streams = max(1, dev.output_streams)
            out.channels = max(1, dev.output_channels // out.streams)
        loopback = ca.device_by_uid(args.input)
        buffer_map = build_buffer_map(ca, agg_id, args.input, outputs)
        mixer = Mixer(rate=rate, in_channels=in_channels, outputs=outputs, stereo_split=args.stereo,
                      skip_outputs=loopback.output_streams if loopback else 0, buffer_map=buffer_map)
        print("LAYOUT " + json.dumps(mixer.status()["buffer_map"]), flush=True)
        for out in outputs:
            mixer.set_delay(out.uid, out.delay_ms)
        host = IOProcHost(ca.hal)
        host.start(agg_id, mixer.process)
        server = ControlServer(args.socket, mixer, on_quit=lambda: None)
        print(f"READY rate={rate} in_channels={in_channels} outputs={len(outputs)}", flush=True)
        thread = threading.Thread(target=server.serve, daemon=True)
        thread.start()
        try:
            while server.running:
                time.sleep(0.25)
        except KeyboardInterrupt:
            pass
        finally:
            host.stop()
    finally:
        try:
            ca.destroy_aggregate(agg_id)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
