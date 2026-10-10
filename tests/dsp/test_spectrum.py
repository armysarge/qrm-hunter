import numpy as np
import pytest

from qrm_hunter.dsp.spectrum import SpectrumProcessor, bin_for_display, quantize

RATE = 2.048e6


def tone(n, offset_hz, amp=1.0, rate=RATE):
    t = np.arange(n) / rate
    return (amp * np.exp(2j * np.pi * offset_hz * t)).astype(np.complex64)


def test_tone_lands_in_expected_bin_at_0_dbfs():
    sp = SpectrumProcessor(8192, avg_alpha=1.0, dc_fix=False)
    out = sp.process(tone(8192, 100_000.0))  # 100 kHz = exactly 400 bins
    assert out.dtype == np.float32 and out.shape == (8192,)
    assert int(np.argmax(out)) == 4096 + 400
    assert abs(float(out.max())) < 0.1


def test_noise_floor_matches_theory():
    rng = np.random.default_rng(0)
    sigma2 = 1e-4  # total complex power
    sp = SpectrumProcessor(4096, avg_alpha=0.05, dc_fix=False)
    s = np.sqrt(sigma2 / 2)
    out = None
    for _ in range(200):
        x = (rng.normal(0, s, 4096) + 1j * rng.normal(0, s, 4096)).astype(np.complex64)
        out = sp.process(x)
    expected = 10 * np.log10(sigma2 * sp.bin_noise_factor)
    assert abs(float(np.median(out)) - expected) < 1.0


def test_partial_blocks_carry_over():
    sp = SpectrumProcessor(4096)
    assert sp.process(tone(3000, 50_000.0)) is None
    assert sp.process(tone(2000, 50_000.0)) is not None


def test_alpha_one_means_no_averaging():
    sp = SpectrumProcessor(4096, avg_alpha=1.0, dc_fix=False)
    sp.process(tone(4096, 50_000.0))
    out = sp.process(tone(4096, -50_000.0))
    assert int(np.argmax(out)) < 2048


def test_dc_fix_removes_centre_spike():
    rng = np.random.default_rng(1)
    x = (0.5 + 0.01 * (rng.normal(size=8192) + 1j * rng.normal(size=8192))).astype(np.complex64)
    out = SpectrumProcessor(8192, avg_alpha=1.0, dc_fix=True).process(x)
    assert out[4096] < np.median(out) + 3


def test_reset_forgets_average():
    sp = SpectrumProcessor(4096, avg_alpha=0.1, dc_fix=False)
    sp.process(tone(4096, 50_000.0))
    sp.reset()
    out = sp.process(tone(4096, -50_000.0, amp=0.001))
    assert int(np.argmax(out)) < 2048


@pytest.mark.parametrize("bad", [1000, 512, 131072])
def test_invalid_fft_size(bad):
    with pytest.raises(ValueError):
        SpectrumProcessor(bad)


def test_binning_keeps_a_single_bin_spike():
    spec = np.full(8192, -100.0, np.float32)
    spec[5000] = 0.0
    bins, lo, hi = bin_for_display(spec, 7e6, RATE, 7e6 - RATE / 2, 7e6 + RATE / 2, 100)
    assert bins.shape == (100,)
    assert bins.max() == 0.0
    assert int(np.sum(bins > -100)) == 1


def test_binning_upsamples_when_zoomed_in():
    spec = np.linspace(-100, -20, 8192).astype(np.float32)
    bins, lo, hi = bin_for_display(spec, 7e6, RATE, 7.0e6, 7.0e6 + 12_500.0, 1000)
    assert bins.shape == (1000,)
    assert -100 <= bins.min() <= bins.max() <= -20
    assert 7.0e6 <= lo < hi <= 7.0e6 + 12_500.0


def test_binning_clips_view_to_span():
    spec = np.zeros(8192, np.float32)
    _, lo, hi = bin_for_display(spec, 7e6, RATE, 0.0, 99e6, 256)
    assert lo == pytest.approx(7e6 - RATE / 2)
    assert hi == pytest.approx(7e6 + RATE / 2 - RATE / 8192)


def test_binning_view_outside_span_is_floor():
    bins, _, _ = bin_for_display(np.zeros(8192, np.float32), 7e6, RATE, 20e6, 21e6, 64)
    assert np.all(bins == -200.0)


def test_quantize():
    q = quantize(np.array([-130, -120, -70, -20, 0], np.float32), -120, -20)
    assert q.dtype == np.uint8
    assert q.tolist() == [0, 0, 128, 255, 255]
    with pytest.raises(ValueError):
        quantize(np.zeros(3), -20, -20)
