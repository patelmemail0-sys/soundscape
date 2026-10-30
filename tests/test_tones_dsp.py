import math
import wave

import pytest

from soundscape import dsp, tones


def test_write_and_read_wav(tmp_path):
    path = tones.write_wav(tmp_path / "t.wav", [0.0, 0.5, -0.5, 2.0], [0.0, 0.5, -0.5, -2.0], rate=8000)
    with wave.open(str(path)) as wf:
        assert wf.getnchannels() == 2 and wf.getframerate() == 8000 and wf.getnframes() == 4
    mono, rate = tones.read_wav_mono(path)
    assert rate == 8000 and mono[1] == pytest.approx(0.5, abs=1e-3) and mono[3] == pytest.approx(0.0, abs=1e-3)


def test_generators_have_expected_lengths(tmp_path):
    assert len(tones.sine(440, 0.5, rate=1000)) == 500
    assert len(tones.chirp(16000)) == 800
    assert len(tones.click()) == int(0.004 * tones.RATE)
    path = tones.click_track(tmp_path / "c.wav", seconds=2, bpm=120, rate=8000)
    mono, _ = tones.read_wav_mono(path)
    assert len(mono) == 16000
    # clicks at 0, 0.5, 1.0, 1.5 s; silence in between
    assert max(abs(v) for v in mono[0:40]) > 0.3
    assert max(abs(v) for v in mono[2000:3000]) == 0


def test_calibration_track_timing(tmp_path):
    path = tones.calibration_track(tmp_path / "cal.wav", rate=8000)
    mono, rate = tones.read_wav_mono(path)
    assert len(mono) == int(tones.CALIBRATION_TOTAL_S * rate)
    energy = lambda a, b: sum(v * v for v in mono[int(a * rate):int(b * rate)])  # noqa: E731
    assert energy(1.0, 1.06) > 1.0 and energy(4.0, 4.06) > 1.0
    assert energy(0.0, 0.9) == 0 and energy(2.0, 3.9) == 0 and energy(4.2, 6.0) == 0


def test_position_tone_distinct(tmp_path):
    l_path = tones.position_tone(tmp_path / "l.wav", "left", rate=8000)
    r_path = tones.position_tone(tmp_path / "r.wav", "right", rate=8000)
    assert tones.read_wav_mono(l_path)[0] != tones.read_wav_mono(r_path)[0]


def test_pcm_conversion():
    data = (32767).to_bytes(2, "little", signed=True) + (-32768).to_bytes(2, "little", signed=True)
    assert tones.pcm_s16le_to_floats(data) == pytest.approx([32767 / 32768, -1.0])


def test_fft_matches_naive_dft():
    import cmath
    x = [complex(math.sin(i * 0.7) + 0.1 * i) for i in range(16)]
    fast = dsp._fft(x)
    naive = [sum(x[n] * cmath.exp(-2j * math.pi * k * n / 16) for n in range(16)) for k in range(16)]
    for a, b in zip(fast, naive):
        assert abs(a - b) < 1e-9
    back = dsp._fft(fast, inverse=True)
    for a, b in zip(back, x):
        assert abs(a - b) < 1e-9
    with pytest.raises(ValueError):
        dsp._fft([1j] * 6)


def test_cross_correlation_finds_template():
    rate = 4000
    template = tones.chirp(rate, seconds=0.02)
    signal = [0.0] * 2000
    for i, v in enumerate(template):
        signal[1234 + i] += v * 0.3
    corr = dsp.cross_correlate(signal, template)
    peak, _ = dsp.peak_in_window(corr, 0, len(signal))
    assert peak == 1234


def test_measure_gap_recovers_known_offset():
    rate = 8000
    template = tones.chirp(rate, seconds=0.03)
    rec = [0.0] * (7 * rate)
    first = int((1.0 + 0.180) * rate)
    second = int((4.0 + 0.180 + 0.0215) * rate)  # second speaker 21.5 ms late
    for i, v in enumerate(template):
        rec[first + i] += 0.5 * v
        rec[second + i] += 0.4 * v
    # small noise
    import random
    rnd = random.Random(1)
    rec = [v + rnd.uniform(-0.01, 0.01) for v in rec]
    gap, c1, c2 = dsp.measure_gap(rec, template, rate, 1.0, 4.0)
    assert dsp.best_offset_ms(gap, 3.0) == pytest.approx(21.5, abs=0.3)
    assert c1 > 6 and c2 > 6


def test_peak_helpers():
    corr = [0.0, 1.0, 3.0, 1.0, 0.0]
    assert dsp.refine_peak(corr, 2) == pytest.approx(2.0)
    assert dsp.refine_peak(corr, 0) == 0.0
    assert dsp.refine_peak([1.0, 1.0, 1.0], 1) == 1.0
    assert dsp.confidence(corr, 2, 0, 5, 0) > 1
    assert dsp.confidence([1.0], 0, 0, 1, 0) == 0.0
    with pytest.raises(ValueError):
        dsp.peak_in_window(corr, 4, 2)
    assert dsp.normalize([0.0, 0.0]) == [0.0, 0.0]
    assert dsp.normalize([0.5, -0.25]) == [1.0, -0.5]


def test_resample_linear():
    out = dsp.resample_linear([0.0, 1.0, 2.0, 3.0], 4, 2)
    assert out == pytest.approx([0.0, 2.0])
    assert dsp.resample_linear([1.0], 8, 8) == [1.0]
