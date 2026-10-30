"""Tiny DSP toolbox used by the microphone calibrator: FFT cross-correlation and peak picking.

numpy is used when installed; otherwise a pure-Python radix-2 FFT keeps the feature working
(a few seconds of 16 kHz audio takes about a second to analyse).
"""

from __future__ import annotations

import cmath
import math
from typing import List, Sequence, Tuple

try:  # optional accelerator
    import numpy as _np  # type: ignore
except Exception:  # pragma: no cover - depends on environment
    _np = None


def _next_pow2(n: int) -> int:
    p = 1
    while p < n:
        p <<= 1
    return p


def _fft(x: List[complex], inverse: bool = False) -> List[complex]:
    n = len(x)
    if n & (n - 1):
        raise ValueError("length must be a power of two")
    # bit-reversal permutation
    j = 0
    a = list(x)
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    length = 2
    sign = 1 if inverse else -1
    while length <= n:
        ang = sign * 2 * math.pi / length
        wlen = cmath.exp(1j * ang)
        for i in range(0, n, length):
            w = 1 + 0j
            half = length >> 1
            for k in range(half):
                u = a[i + k]
                v = a[i + k + half] * w
                a[i + k] = u + v
                a[i + k + half] = u - v
                w *= wlen
        length <<= 1
    if inverse:
        a = [v / n for v in a]
    return a


def cross_correlate(signal: Sequence[float], template: Sequence[float]) -> List[float]:
    """Correlation of ``signal`` with ``template``; index k = template starting at sample k."""
    n = len(signal) + len(template) - 1
    if _np is not None:
        size = _next_pow2(n)
        s = _np.fft.rfft(_np.asarray(signal, dtype=float), size)
        t = _np.fft.rfft(_np.asarray(template, dtype=float), size)
        out = _np.fft.irfft(s * _np.conj(t), size)
        return out[: len(signal)].tolist()
    size = _next_pow2(n)
    s = [complex(v) for v in signal] + [0j] * (size - len(signal))
    t = [complex(v) for v in template] + [0j] * (size - len(template))
    fs, ft = _fft(s), _fft(t)
    prod = [a * b.conjugate() for a, b in zip(fs, ft)]
    out = _fft(prod, inverse=True)
    return [v.real for v in out[: len(signal)]]


def normalize(signal: Sequence[float]) -> List[float]:
    peak = max((abs(v) for v in signal), default=0.0)
    return [v / peak for v in signal] if peak > 0 else list(signal)


def peak_in_window(corr: Sequence[float], start: int, end: int) -> Tuple[int, float]:
    start = max(0, start)
    end = min(len(corr), end)
    if end <= start:
        raise ValueError("empty search window")
    best_i, best_v = start, -1.0
    for i in range(start, end):
        v = abs(corr[i])
        if v > best_v:
            best_i, best_v = i, v
    return best_i, best_v


def refine_peak(corr: Sequence[float], i: int) -> float:
    """Parabolic interpolation around the integer peak for sub-sample precision."""
    if i <= 0 or i >= len(corr) - 1:
        return float(i)
    y0, y1, y2 = abs(corr[i - 1]), abs(corr[i]), abs(corr[i + 1])
    denom = y0 - 2 * y1 + y2
    if denom == 0:
        return float(i)
    return i + 0.5 * (y0 - y2) / denom


def confidence(corr: Sequence[float], peak_index: int, start: int, end: int, guard: int) -> float:
    """Peak height relative to the RMS of the rest of the window (a crude SNR, > 6 is solid)."""
    rest = [abs(v) for k, v in enumerate(corr[start:end], start) if abs(k - peak_index) > guard]
    if not rest:
        return 0.0
    rms = math.sqrt(sum(v * v for v in rest) / len(rest))
    return abs(corr[peak_index]) / rms if rms > 0 else float("inf")


def resample_linear(signal: Sequence[float], src_rate: int, dst_rate: int) -> List[float]:
    if src_rate == dst_rate:
        return list(signal)
    ratio = src_rate / dst_rate
    n = int(len(signal) / ratio)
    out = []
    for i in range(n):
        x = i * ratio
        k = int(x)
        frac = x - k
        a = signal[k]
        b = signal[k + 1] if k + 1 < len(signal) else a
        out.append(a + (b - a) * frac)
    return out


def decimate(signal: Sequence[float], src_rate: int, dst_rate: int) -> List[float]:
    """Reduce the sample rate; integer ratios use a box filter (cheap anti-aliasing)."""
    if src_rate == dst_rate:
        return list(signal)
    if src_rate % dst_rate == 0:
        step = src_rate // dst_rate
        n = len(signal) // step
        if _np is not None:
            arr = _np.asarray(signal[: n * step], dtype=float).reshape(n, step)
            return arr.mean(axis=1).tolist()
        return [sum(signal[i * step:(i + 1) * step]) / step for i in range(n)]
    return resample_linear(signal, src_rate, dst_rate)


def measure_gap(recording: Sequence[float], template: Sequence[float], rate: int,
                first_at_s: float, second_at_s: float, max_latency_s: float = 1.2
                ) -> Tuple[float, float, float]:
    """Locate two template arrivals and return ``(gap_seconds, conf1, conf2)``.

    ``first_at_s``/``second_at_s`` are when the chirps were *sent*; arrivals are searched in
    ``[sent, sent + max_latency_s]`` so pipeline latency never has to be known.
    """
    corr = cross_correlate(normalize(recording), normalize(template))
    guard = len(template)
    w1 = (int(first_at_s * rate), int((first_at_s + max_latency_s) * rate))
    w2 = (int(second_at_s * rate), int((second_at_s + max_latency_s) * rate))
    p1, _ = peak_in_window(corr, *w1)
    p2, _ = peak_in_window(corr, *w2)
    c1 = confidence(corr, p1, w1[0], w1[1], guard)
    c2 = confidence(corr, p2, w2[0], w2[1], guard)
    gap = (refine_peak(corr, p2) - refine_peak(corr, p1)) / rate
    return gap, c1, c2


def best_offset_ms(gap_s: float, nominal_gap_s: float) -> float:
    return round((gap_s - nominal_gap_s) * 1000.0, 2)


__all__ = ["cross_correlate", "measure_gap", "best_offset_ms", "resample_linear", "decimate", "normalize",
           "peak_in_window", "refine_peak", "confidence"]


def has_numpy() -> bool:
    return _np is not None
