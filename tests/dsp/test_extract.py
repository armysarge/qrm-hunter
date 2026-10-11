import math

import numpy as np
import pytest

from qrm_hunter.dsp.extract import (
    RATE_LADDER,
    RegionError,
    SpanExtractor,
    extract_span,
    plan_decimation,
    validate_region,
)

RATE = 2.048e6
C = 7_100_000.0


def tone(offset_hz, n, amp=0.1, rate=RATE):
    return (amp * np.exp(2j * np.pi * offset_hz * np.arange(n) / rate)).astype(np.complex64)


def peak_hz(x, rate):
    spec = np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(x.size))))
    return (int(np.argmax(spec)) - x.size // 2) * rate / x.size


@pytest.mark.parametrize("width, ladder", [(8_000, 12_500), (30_000, 50_000), (150_000, 200_000),
                                           (600_000, 800_000)])
def test_output_rate_is_the_smallest_ladder_rate_covering_125_percent(width, ladder):
    ex = SpanExtractor(RATE, C, C, C + width)
    # D is an integer, so the rate is the rung rounded up to an exact divisor of the input rate:
    # within 10 % of the rung, or the next divisor up when the rung is close to the input rate
    assert ladder <= ex.out_rate <= max(ladder * 1.1, RATE / math.floor(RATE / ladder))
    assert ex.out_rate >= 1.25 * width
    assert RATE / ex.out_rate == pytest.approx(round(RATE / ex.out_rate))
    assert all(f <= 16 for f in plan_decimation(RATE, width))


def test_wide_regions_and_the_full_span_stay_at_full_rate():
    assert plan_decimation(RATE, 700_000) == []
    ex = SpanExtractor(RATE, C)
    x = tone(12_345, 4096)
    assert ex.out_rate == RATE and ex.out_center_hz == C
    assert np.array_equal(ex.process(x), x)


def test_a_tone_keeps_its_frequency_after_decimation():
    x = tone(38_000, int(0.5 * RATE))
    out = extract_span(x, RATE, C, C + 25_000, C + 45_000)
    assert out.center_hz == C + 35_000
    assert abs(peak_hz(out.iq[200:], out.rate) - 3_000) <= out.rate / (out.iq.size - 200)


def test_extraction_is_the_same_at_uhf():
    centre = 435_000_000.0
    x = tone(38_000, int(0.5 * RATE))
    out = extract_span(x, RATE, centre, centre + 25_000, centre + 45_000)
    assert out.center_hz == centre + 35_000
    assert abs(peak_hz(out.iq[200:], out.rate) - 3_000) <= out.rate / (out.iq.size - 200)


def test_out_of_band_rejection_is_at_least_50_db():
    n = int(0.5 * RATE)
    inside = extract_span(tone(38_000, n), RATE, C, C + 25_000, C + 45_000).iq[400:]
    outside = extract_span(tone(150_000, n), RATE, C, C + 25_000, C + 45_000).iq[400:]
    ratio_db = 10 * math.log10(np.mean(np.abs(outside) ** 2) / np.mean(np.abs(inside) ** 2))
    assert ratio_db <= -50


def test_chunked_processing_equals_one_shot():
    rng = np.random.default_rng(1)
    x = (rng.normal(size=200_000) + 1j * rng.normal(size=200_000)).astype(np.complex64) * 0.1
    whole = SpanExtractor(RATE, C, C - 40_000, C - 10_000).process(x)
    ex = SpanExtractor(RATE, C, C - 40_000, C - 10_000)
    parts, i = [], 0
    for size in (1000, 33_333, 4096, 70_001):
        parts.append(ex.process(x[i:i + size]))
        i += size
    parts.append(ex.process(x[i:]))
    split = np.concatenate(parts)
    assert split.size == whole.size
    assert np.allclose(split, whole, atol=1e-5)


def test_region_validation():
    assert validate_region(C, RATE, C + 20_000, C + 10_000) == (C + 10_000, C + 20_000)
    assert validate_region(C, RATE, None, None) == (C - RATE / 2, C + RATE / 2)
    assert validate_region(C, RATE, C + 900_000, C + 2_000_000) == (C + 900_000, C + RATE / 2)
    for lo, hi in [(C + 2e6, C + 3e6), (C, C + 50), (float("nan"), C), (C, float("inf"))]:
        with pytest.raises(RegionError):
            validate_region(C, RATE, lo, hi)
    assert RATE_LADDER[0] == 12_500.0 and RATE_LADDER[-1] == 800_000.0
