"""HAL IOProc plumbing: run a Python callback on a CoreAudio device's I/O cycle.

Used by the relay engine (BlackHole -> two speakers with per-speaker delay) and by the
microphone recorder for sync calibration. Buffers are handed to the callback as
``(channels, byte_size, address)`` tuples so the callback can memmove without allocating.
"""

from __future__ import annotations

import ctypes
import gc
import struct
import threading
import time
from typing import Callable, List, Optional, Sequence, Tuple

from ..errors import AudioError
from .coreaudio import HAL, HALError

Buffer = Tuple[int, int, int]  # channels, byte size, data address
Callback = Callable[[Sequence[Buffer], Sequence[Buffer]], None]

# AudioBufferList layout on 64-bit: UInt32 mNumberBuffers; (4 bytes padding); AudioBuffer[]
# AudioBuffer: UInt32 mNumberChannels; UInt32 mDataByteSize; void* mData  -> 16 bytes
_ABL_HEADER = 8
_AUDIO_BUFFER = 16


def parse_buffer_list(address: int) -> List[Buffer]:
    if not address:
        return []
    count = struct.unpack("I", ctypes.string_at(address, 4))[0]
    out: List[Buffer] = []
    for i in range(count):
        raw = ctypes.string_at(address + _ABL_HEADER + i * _AUDIO_BUFFER, _AUDIO_BUFFER)
        channels, size, data = struct.unpack("IIQ", raw)
        out.append((channels, size, data))
    return out


IOPROC = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class IOProcHost:
    """Attach a callback to a device; ``start``/``stop`` manage the IOProc lifetime."""

    def __init__(self, hal: Optional[HAL] = None):
        self.hal = hal or HAL()
        L = self.hal.lib
        L.AudioDeviceCreateIOProcID.restype = ctypes.c_int32
        L.AudioDeviceCreateIOProcID.argtypes = [ctypes.c_uint32, IOPROC, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        L.AudioDeviceDestroyIOProcID.restype = ctypes.c_int32
        L.AudioDeviceDestroyIOProcID.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        L.AudioDeviceStart.restype = ctypes.c_int32
        L.AudioDeviceStart.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        L.AudioDeviceStop.restype = ctypes.c_int32
        L.AudioDeviceStop.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        self._proc_id = ctypes.c_void_p(None)
        self._device: Optional[int] = None
        self._cb_ref: Optional[Callable] = None  # keep the CFUNCTYPE object alive
        self.errors = 0

    def start(self, device_id: int, callback: Callback) -> None:
        def ioproc(dev, now, in_data, in_time, out_data, out_time, client):  # noqa: ARG001
            try:
                callback(parse_buffer_list(in_data), parse_buffer_list(out_data))
            except Exception:
                self.errors += 1
            return 0

        self._cb_ref = IOPROC(ioproc)
        status = self.hal.lib.AudioDeviceCreateIOProcID(device_id, self._cb_ref, None, ctypes.byref(self._proc_id))
        if status != 0:
            raise HALError(f"AudioDeviceCreateIOProcID failed (status {status})")
        status = self.hal.lib.AudioDeviceStart(device_id, self._proc_id)
        if status != 0:
            self.hal.lib.AudioDeviceDestroyIOProcID(device_id, self._proc_id)
            raise HALError(f"AudioDeviceStart failed (status {status})")
        self._device = device_id
        gc.disable()  # no collector pauses while a real-time callback runs in this process

    def stop(self) -> None:
        if self._device is None:
            return
        self.hal.lib.AudioDeviceStop(self._device, self._proc_id)
        self.hal.lib.AudioDeviceDestroyIOProcID(self._device, self._proc_id)
        self._device = None
        self._cb_ref = None
        gc.enable()


def record(device_id: int, seconds: float, hal: Optional[HAL] = None, channels_hint: int = 1) -> bytes:
    """Capture raw Float32 interleaved audio from ``device_id`` for ``seconds``."""
    chunks: List[bytes] = []
    done = threading.Event()

    def on_io(inputs: Sequence[Buffer], outputs: Sequence[Buffer]) -> None:  # noqa: ARG001
        if inputs:
            ch, size, addr = inputs[0]
            if size and addr:
                chunks.append(ctypes.string_at(addr, size))

    host = IOProcHost(hal)
    host.start(device_id, on_io)
    try:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not done.is_set():
            time.sleep(0.05)
    finally:
        host.stop()
    data = b"".join(chunks)
    if not data:
        raise AudioError("the microphone delivered no audio",
                         "check the input device in System Settings > Sound and grant microphone access to the terminal")
    return data
