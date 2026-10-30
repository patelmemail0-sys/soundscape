"""CoreAudio HAL access over ctypes.

Two layers: ``HAL`` is the thin binding (one method per C call); ``CoreAudio`` is the logic
built on it (device listing, aggregate devices, volume, default output). Tests inject a fake
HAL, so everything above the binding runs on any OS.
"""

from __future__ import annotations

import ctypes
import re
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..errors import AudioError
from .cf import CF, load_framework


def fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode("ascii"))[0]


# object ids and selectors
kAudioObjectSystemObject = 1
kAudioObjectPropertyScopeGlobal = fourcc("glob")
kAudioObjectPropertyScopeInput = fourcc("inpt")
kAudioObjectPropertyScopeOutput = fourcc("outp")
kAudioObjectPropertyElementMain = 0

kAudioHardwarePropertyDevices = fourcc("dev#")
kAudioHardwarePropertyDefaultOutputDevice = fourcc("dOut")
kAudioHardwarePropertyDefaultSystemOutputDevice = fourcc("sOut")
kAudioHardwarePropertyDefaultInputDevice = fourcc("dIn ")
kAudioObjectPropertyName = fourcc("lnam")
kAudioDevicePropertyDeviceUID = fourcc("uid ")
kAudioDevicePropertyTransportType = fourcc("tran")
kAudioDevicePropertyStreams = fourcc("stm#")
kAudioDevicePropertyNominalSampleRate = fourcc("nsrt")
kAudioDevicePropertyAvailableNominalSampleRates = fourcc("nsr#")
kAudioDevicePropertyLatency = fourcc("ltnc")
kAudioDevicePropertySafetyOffset = fourcc("saft")
kAudioDevicePropertyBufferFrameSize = fourcc("fsiz")
kAudioDevicePropertyMute = fourcc("mute")
kAudioDevicePropertyVolumeScalar = fourcc("volm")
kAudioHardwareServiceDeviceProperty_VirtualMainVolume = fourcc("vmvc")
kAudioStreamPropertyVirtualFormat = fourcc("sfmt")
kAudioAggregateDevicePropertyFullSubDeviceList = fourcc("grup")  # CFArray of UIDs
kAudioAggregateDevicePropertyActiveSubDeviceList = fourcc("agrp")  # array of AudioObjectIDs

TRANSPORT_NAMES = {
    fourcc("bltn"): "builtin", fourcc("blue"): "bluetooth", fourcc("blea"): "bluetooth-le",
    fourcc("usb "): "usb", fourcc("grup"): "aggregate", fourcc("virt"): "virtual",
    fourcc("hdmi"): "hdmi", fourcc("dprt"): "displayport", fourcc("airp"): "airplay",
    fourcc("thun"): "thunderbolt", fourcc("pci "): "pci", fourcc("1394"): "firewire",
    fourcc("avb "): "avb", fourcc("cont"): "continuity",
}

AGGREGATE_UID_PREFIX = "com.soundscape."
MAC_UID_RE = re.compile(r"^([0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}(:output|:input)?$")


class AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32), ("mScope", ctypes.c_uint32), ("mElement", ctypes.c_uint32)]


class AudioStreamBasicDescription(ctypes.Structure):
    _fields_ = [("mSampleRate", ctypes.c_double), ("mFormatID", ctypes.c_uint32), ("mFormatFlags", ctypes.c_uint32),
                ("mBytesPerPacket", ctypes.c_uint32), ("mFramesPerPacket", ctypes.c_uint32),
                ("mBytesPerFrame", ctypes.c_uint32), ("mChannelsPerFrame", ctypes.c_uint32),
                ("mBitsPerChannel", ctypes.c_uint32), ("mReserved", ctypes.c_uint32)]


kAudioFormatLinearPCM = fourcc("lpcm")
kAudioFormatFlagIsFloat = 1 << 0
kAudioFormatFlagIsNonInterleaved = 1 << 5


@dataclass
class StreamFormat:
    sample_rate: float
    channels: int
    bits: int
    is_float: bool
    interleaved: bool
    bytes_per_frame: int


@dataclass
class AudioDevice:
    id: int
    uid: str
    name: str
    transport: str
    output_channels: int
    input_channels: int
    sample_rate: float
    output_streams: int = 0
    input_streams: int = 0

    @property
    def bluetooth_address(self) -> Optional[str]:
        m = MAC_UID_RE.match(self.uid)
        if not m:
            return None
        return self.uid.split(":output")[0].split(":input")[0].replace("-", ":").upper()

    @property
    def is_bluetooth(self) -> bool:
        return self.transport.startswith("bluetooth") or self.bluetooth_address is not None

    @property
    def is_soundscape_aggregate(self) -> bool:
        return self.transport == "aggregate" and self.uid.startswith(AGGREGATE_UID_PREFIX)


class HALError(AudioError):
    pass


class HAL:
    """Thin ctypes binding. Every method maps to one CoreAudio call."""

    def __init__(self) -> None:
        self.lib = load_framework("CoreAudio")
        L = self.lib
        addr = ctypes.POINTER(AudioObjectPropertyAddress)
        L.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        L.AudioObjectGetPropertyDataSize.argtypes = [ctypes.c_uint32, addr, ctypes.c_uint32, ctypes.c_void_p,
                                                     ctypes.POINTER(ctypes.c_uint32)]
        L.AudioObjectGetPropertyData.restype = ctypes.c_int32
        L.AudioObjectGetPropertyData.argtypes = [ctypes.c_uint32, addr, ctypes.c_uint32, ctypes.c_void_p,
                                                 ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        L.AudioObjectSetPropertyData.restype = ctypes.c_int32
        L.AudioObjectSetPropertyData.argtypes = [ctypes.c_uint32, addr, ctypes.c_uint32, ctypes.c_void_p,
                                                 ctypes.c_uint32, ctypes.c_void_p]
        L.AudioObjectHasProperty.restype = ctypes.c_bool
        L.AudioObjectHasProperty.argtypes = [ctypes.c_uint32, addr]
        L.AudioHardwareCreateAggregateDevice.restype = ctypes.c_int32
        L.AudioHardwareCreateAggregateDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        L.AudioHardwareDestroyAggregateDevice.restype = ctypes.c_int32
        L.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]
        self.cf = CF()

    @staticmethod
    def _addr(selector: int, scope: int, element: int) -> AudioObjectPropertyAddress:
        return AudioObjectPropertyAddress(selector, scope, element)

    def has(self, obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal, element: int = 0) -> bool:
        return bool(self.lib.AudioObjectHasProperty(obj, ctypes.byref(self._addr(selector, scope, element))))

    def get_raw(self, obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal, element: int = 0) -> bytes:
        a = self._addr(selector, scope, element)
        size = ctypes.c_uint32(0)
        status = self.lib.AudioObjectGetPropertyDataSize(obj, ctypes.byref(a), 0, None, ctypes.byref(size))
        if status != 0:
            raise HALError(f"CoreAudio property size query failed (status {status}) for selector {selector:#x} on {obj}")
        buf = ctypes.create_string_buffer(max(1, size.value))
        status = self.lib.AudioObjectGetPropertyData(obj, ctypes.byref(a), 0, None, ctypes.byref(size), buf)
        if status != 0:
            raise HALError(f"CoreAudio property read failed (status {status}) for selector {selector:#x} on {obj}")
        return buf.raw[: size.value]

    def set_raw(self, obj: int, selector: int, data: bytes, scope: int = kAudioObjectPropertyScopeGlobal, element: int = 0) -> None:
        a = self._addr(selector, scope, element)
        buf = ctypes.create_string_buffer(data, len(data))
        status = self.lib.AudioObjectSetPropertyData(obj, ctypes.byref(a), 0, None, len(data), buf)
        if status != 0:
            raise HALError(f"CoreAudio property write failed (status {status}) for selector {selector:#x} on {obj}")

    def get_cfstring(self, obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal) -> str:
        raw = self.get_raw(obj, selector, scope)
        ref = struct.unpack("P", raw[:8])[0] if len(raw) >= 8 else 0
        return self.cf.to_str(ref)

    def create_aggregate(self, description: Dict[str, Any]) -> int:
        cf = CF()
        try:
            ref = cf.from_python(description)
            out = ctypes.c_uint32(0)
            status = self.lib.AudioHardwareCreateAggregateDevice(ref, ctypes.byref(out))
        finally:
            cf.release_all()
        if status != 0:
            raise HALError(f"AudioHardwareCreateAggregateDevice failed (status {status})")
        return out.value

    def destroy_aggregate(self, device_id: int) -> None:
        status = self.lib.AudioHardwareDestroyAggregateDevice(device_id)
        if status != 0:
            raise HALError(f"AudioHardwareDestroyAggregateDevice failed (status {status})")


class CoreAudio:
    """Device logic on top of a HAL (real or fake)."""

    def __init__(self, hal: Optional[Any] = None):
        self.hal = hal or HAL()

    # -- primitives ------------------------------------------------------
    def _u32(self, obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal, element: int = 0) -> int:
        return struct.unpack("I", self.hal.get_raw(obj, selector, scope, element)[:4])[0]

    def _u32_list(self, obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal) -> List[int]:
        raw = self.hal.get_raw(obj, selector, scope)
        return list(struct.unpack(f"{len(raw) // 4}I", raw[: len(raw) // 4 * 4]))

    def _f64(self, obj: int, selector: int, scope: int = kAudioObjectPropertyScopeGlobal) -> float:
        return struct.unpack("d", self.hal.get_raw(obj, selector, scope)[:8])[0]

    def _f32(self, obj: int, selector: int, scope: int, element: int = 0) -> float:
        return struct.unpack("f", self.hal.get_raw(obj, selector, scope, element)[:4])[0]

    def stream_formats(self, device_id: int, scope: int) -> List[StreamFormat]:
        out: List[StreamFormat] = []
        try:
            streams = self._u32_list(device_id, kAudioDevicePropertyStreams, scope)
        except HALError:
            return out
        for sid in streams:
            raw = self.hal.get_raw(sid, kAudioStreamPropertyVirtualFormat)
            asbd = AudioStreamBasicDescription.from_buffer_copy(raw[: ctypes.sizeof(AudioStreamBasicDescription)])
            out.append(StreamFormat(
                sample_rate=asbd.mSampleRate, channels=asbd.mChannelsPerFrame, bits=asbd.mBitsPerChannel,
                is_float=bool(asbd.mFormatFlags & kAudioFormatFlagIsFloat),
                interleaved=not (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved),
                bytes_per_frame=asbd.mBytesPerFrame))
        return out

    def device(self, device_id: int) -> AudioDevice:
        uid = self.hal.get_cfstring(device_id, kAudioDevicePropertyDeviceUID)
        name = self.hal.get_cfstring(device_id, kAudioObjectPropertyName)
        try:
            transport = TRANSPORT_NAMES.get(self._u32(device_id, kAudioDevicePropertyTransportType), "unknown")
        except HALError:
            transport = "unknown"
        outs = self.stream_formats(device_id, kAudioObjectPropertyScopeOutput)
        ins = self.stream_formats(device_id, kAudioObjectPropertyScopeInput)
        try:
            rate = self._f64(device_id, kAudioDevicePropertyNominalSampleRate)
        except HALError:
            rate = 0.0
        return AudioDevice(id=device_id, uid=uid, name=name, transport=transport,
                           output_channels=sum(f.channels for f in outs), input_channels=sum(f.channels for f in ins),
                           sample_rate=rate, output_streams=len(outs), input_streams=len(ins))

    def devices(self) -> List[AudioDevice]:
        ids = self._u32_list(kAudioObjectSystemObject, kAudioHardwarePropertyDevices)
        out = []
        for did in ids:
            try:
                out.append(self.device(did))
            except HALError:
                continue
        return out

    def device_by_uid(self, uid: str) -> Optional[AudioDevice]:
        for d in self.devices():
            if d.uid == uid:
                return d
        return None

    def bluetooth_output(self, mac: str) -> Optional[AudioDevice]:
        mac = mac.upper().replace("-", ":")
        for d in self.devices():
            if d.output_channels and d.bluetooth_address == mac:
                return d
        return None

    # -- defaults ----------------------------------------------------------
    def default_output(self) -> int:
        return self._u32(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultOutputDevice)

    def default_input(self) -> int:
        return self._u32(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultInputDevice)

    def set_default_input(self, device_id: int) -> None:
        self.hal.set_raw(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultInputDevice, struct.pack("I", device_id))

    def set_default_output(self, device_id: int, system_sounds_too: bool = True) -> None:
        data = struct.pack("I", device_id)
        self.hal.set_raw(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultOutputDevice, data)
        if system_sounds_too:
            try:
                self.hal.set_raw(kAudioObjectSystemObject, kAudioHardwarePropertyDefaultSystemOutputDevice, data)
            except HALError:
                pass

    # -- volume / mute -----------------------------------------------------
    def volume(self, device_id: int) -> Optional[float]:
        scope = kAudioObjectPropertyScopeOutput
        for selector, element in ((kAudioHardwareServiceDeviceProperty_VirtualMainVolume, 0),
                                  (kAudioDevicePropertyVolumeScalar, 0), (kAudioDevicePropertyVolumeScalar, 1)):
            if self.hal.has(device_id, selector, scope, element):
                try:
                    return max(0.0, min(1.0, self._f32(device_id, selector, scope, element)))
                except HALError:
                    continue
        return None

    def set_volume(self, device_id: int, scalar: float) -> bool:
        scalar = max(0.0, min(1.0, scalar))
        data = struct.pack("f", scalar)
        scope = kAudioObjectPropertyScopeOutput
        if self.hal.has(device_id, kAudioHardwareServiceDeviceProperty_VirtualMainVolume, scope, 0):
            try:
                self.hal.set_raw(device_id, kAudioHardwareServiceDeviceProperty_VirtualMainVolume, data, scope, 0)
                return True
            except HALError:
                pass
        done = False
        for element in (0, 1, 2):
            if self.hal.has(device_id, kAudioDevicePropertyVolumeScalar, scope, element):
                try:
                    self.hal.set_raw(device_id, kAudioDevicePropertyVolumeScalar, data, scope, element)
                    done = True
                except HALError:
                    continue
        return done

    def mute(self, device_id: int) -> Optional[bool]:
        if not self.hal.has(device_id, kAudioDevicePropertyMute, kAudioObjectPropertyScopeOutput, 0):
            return None
        try:
            return bool(self._u32(device_id, kAudioDevicePropertyMute, kAudioObjectPropertyScopeOutput, 0))
        except HALError:
            return None

    def set_mute(self, device_id: int, muted: bool) -> bool:
        if not self.hal.has(device_id, kAudioDevicePropertyMute, kAudioObjectPropertyScopeOutput, 0):
            return False
        try:
            self.hal.set_raw(device_id, kAudioDevicePropertyMute, struct.pack("I", 1 if muted else 0),
                             kAudioObjectPropertyScopeOutput, 0)
            return True
        except HALError:
            return False

    # -- misc device properties ----------------------------------------------
    def latency_frames(self, device_id: int, scope: int = kAudioObjectPropertyScopeOutput) -> int:
        total = 0
        for selector in (kAudioDevicePropertyLatency, kAudioDevicePropertySafetyOffset):
            try:
                total += self._u32(device_id, selector, scope)
            except HALError:
                pass
        return total

    def buffer_frame_size(self, device_id: int) -> int:
        return self._u32(device_id, kAudioDevicePropertyBufferFrameSize)

    def set_buffer_frame_size(self, device_id: int, frames: int) -> None:
        self.hal.set_raw(device_id, kAudioDevicePropertyBufferFrameSize, struct.pack("I", int(frames)))

    def set_nominal_sample_rate(self, device_id: int, rate: float) -> None:
        self.hal.set_raw(device_id, kAudioDevicePropertyNominalSampleRate, struct.pack("d", float(rate)))

    # -- aggregates -----------------------------------------------------------
    def create_aggregate(self, uid: str, name: str, subdevices: Sequence[Tuple[str, bool]], master_uid: str,
                         private: bool = False, stacked: bool = False) -> int:
        """``subdevices``: (device uid, drift_compensation). The master device is the clock.

        ``stacked`` makes a Multi-Output Device: every sub-device receives the same channels,
        which is what a stereo app needs to be heard on all speakers. Unstacked aggregates
        concatenate channels (speaker 1 = ch 1-2, speaker 2 = ch 3-4) and are for callers that
        address each output themselves (the relay)."""
        if not uid.startswith(AGGREGATE_UID_PREFIX):
            uid = AGGREGATE_UID_PREFIX + uid
        description = {
            "uid": uid,
            "name": name,
            "subdevices": [{"uid": sub_uid, "drift": 1 if drift else 0} for sub_uid, drift in subdevices],
            "master": master_uid,
            "private": 1 if private else 0,
            "stacked": 1 if stacked else 0,
        }
        return self.hal.create_aggregate(description)

    def available_sample_rates(self, device_id: int) -> List[Tuple[float, float]]:
        """(min, max) ranges the device supports."""
        try:
            raw = self.hal.get_raw(device_id, kAudioDevicePropertyAvailableNominalSampleRates)
        except HALError:
            return []
        count = len(raw) // 16
        return [struct.unpack_from("dd", raw, i * 16) for i in range(count)]

    def supports_rate(self, device_id: int, rate: float) -> bool:
        ranges = self.available_sample_rates(device_id)
        if not ranges:
            return True  # unknown: assume it follows the aggregate
        return any(lo - 0.5 <= rate <= hi + 0.5 for lo, hi in ranges)

    def align_sample_rates(self, device_ids: Sequence[int], preferred: Sequence[float] = (48000.0, 44100.0)) -> Optional[float]:
        """Put every device on one nominal rate (a sub-device that cannot follow the aggregate's
        rate stays silent). Prefers 48 kHz, then 44.1 kHz, then whatever the first device runs."""
        if not device_ids:
            return None
        candidates = list(preferred)
        try:
            candidates.append(self._f64(device_ids[0], kAudioDevicePropertyNominalSampleRate))
        except HALError:
            pass
        for rate in candidates:
            if all(self.supports_rate(d, rate) for d in device_ids):
                for d in device_ids:
                    try:
                        if abs(self._f64(d, kAudioDevicePropertyNominalSampleRate) - rate) > 0.5:
                            self.set_nominal_sample_rate(d, rate)
                    except HALError:
                        continue
                return rate
        return None

    def destroy_aggregate(self, device_id: int) -> None:
        self.hal.destroy_aggregate(device_id)

    def aggregate_subdevices(self, device_id: int) -> List[int]:
        """AudioObjectIDs of an aggregate's active sub-devices."""
        try:
            return self._u32_list(device_id, kAudioAggregateDevicePropertyActiveSubDeviceList)
        except HALError:
            return []

    def soundscape_aggregates(self) -> List[AudioDevice]:
        return [d for d in self.devices() if d.is_soundscape_aggregate]


__all__ = ["CoreAudio", "HAL", "HALError", "AudioDevice", "StreamFormat", "fourcc", "AGGREGATE_UID_PREFIX",
           "kAudioObjectPropertyScopeInput", "kAudioObjectPropertyScopeOutput", "kAudioObjectPropertyScopeGlobal"]
