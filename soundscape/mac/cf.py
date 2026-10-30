"""Minimal CoreFoundation helpers over ctypes (strings, numbers, arrays, dictionaries)."""

from __future__ import annotations

import ctypes
import ctypes.util
from typing import Any, Dict, List, Optional, Sequence

kCFStringEncodingUTF8 = 0x08000100
kCFNumberSInt32Type = 3
kCFNumberFloat64Type = 13


def load_framework(name: str) -> ctypes.CDLL:
    path = ctypes.util.find_library(name) or f"/System/Library/Frameworks/{name}.framework/{name}"
    return ctypes.CDLL(path)


class CF:
    """Owns a CoreFoundation handle and converts between Python and CF objects."""

    def __init__(self, lib: Optional[ctypes.CDLL] = None):
        self.lib = lib or load_framework("CoreFoundation")
        L = self.lib
        L.CFStringCreateWithCString.restype = ctypes.c_void_p
        L.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        L.CFNumberCreate.restype = ctypes.c_void_p
        L.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p]
        L.CFArrayCreate.restype = ctypes.c_void_p
        L.CFArrayCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p]
        L.CFDictionaryCreate.restype = ctypes.c_void_p
        L.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                         ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
        L.CFRelease.restype = None
        L.CFRelease.argtypes = [ctypes.c_void_p]
        L.CFStringGetLength.restype = ctypes.c_long
        L.CFStringGetLength.argtypes = [ctypes.c_void_p]
        L.CFStringGetMaximumSizeForEncoding.restype = ctypes.c_long
        L.CFStringGetMaximumSizeForEncoding.argtypes = [ctypes.c_long, ctypes.c_uint32]
        L.CFStringGetCString.restype = ctypes.c_bool
        L.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        self._array_cb = ctypes.addressof(ctypes.c_byte.in_dll(L, "kCFTypeArrayCallBacks"))
        self._dict_key_cb = ctypes.addressof(ctypes.c_byte.in_dll(L, "kCFTypeDictionaryKeyCallBacks"))
        self._dict_val_cb = ctypes.addressof(ctypes.c_byte.in_dll(L, "kCFTypeDictionaryValueCallBacks"))
        self._owned: List[int] = []

    # -- creation (tracked, released by release_all) ----------------------
    def string(self, value: str) -> int:
        ref = self.lib.CFStringCreateWithCString(None, value.encode("utf-8"), kCFStringEncodingUTF8)
        self._owned.append(ref)
        return ref

    def number(self, value: int) -> int:
        v = ctypes.c_int32(int(value))
        ref = self.lib.CFNumberCreate(None, kCFNumberSInt32Type, ctypes.byref(v))
        self._owned.append(ref)
        return ref

    def array(self, items: Sequence[int]) -> int:
        buf = (ctypes.c_void_p * max(1, len(items)))(*items)
        ref = self.lib.CFArrayCreate(None, buf, len(items), self._array_cb)
        self._owned.append(ref)
        return ref

    def dictionary(self, mapping: Dict[int, int]) -> int:
        keys = (ctypes.c_void_p * max(1, len(mapping)))(*mapping.keys())
        vals = (ctypes.c_void_p * max(1, len(mapping)))(*mapping.values())
        ref = self.lib.CFDictionaryCreate(None, keys, vals, len(mapping), self._dict_key_cb, self._dict_val_cb)
        self._owned.append(ref)
        return ref

    def from_python(self, value: Any) -> int:
        """Build a CF object tree from str / int / bool / list / dict."""
        if isinstance(value, bool):
            return self.number(1 if value else 0)
        if isinstance(value, int):
            return self.number(value)
        if isinstance(value, str):
            return self.string(value)
        if isinstance(value, (list, tuple)):
            return self.array([self.from_python(v) for v in value])
        if isinstance(value, dict):
            return self.dictionary({self.string(str(k)): self.from_python(v) for k, v in value.items()})
        raise TypeError(f"cannot convert {type(value).__name__} to CF")

    def release_all(self) -> None:
        for ref in reversed(self._owned):
            if ref:
                self.lib.CFRelease(ref)
        self._owned.clear()

    # -- reading ---------------------------------------------------------
    def to_str(self, ref: int, release: bool = True) -> str:
        if not ref:
            return ""
        length = self.lib.CFStringGetLength(ref)
        size = self.lib.CFStringGetMaximumSizeForEncoding(length, kCFStringEncodingUTF8) + 1
        buf = ctypes.create_string_buffer(size)
        ok = self.lib.CFStringGetCString(ref, buf, size, kCFStringEncodingUTF8)
        if release:
            self.lib.CFRelease(ref)
        return buf.value.decode("utf-8", "replace") if ok else ""
