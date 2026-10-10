import time

import numpy as np
import pytest

from qrm_hunter.sdr.base import DeviceConfig, DeviceError
from qrm_hunter.sdr.synthetic import Source, SyntheticDevice

RATE = 2.048e6


def make(**kw) -> SyntheticDevice:
    d = SyntheticDevice(realtime=False, **kw)
    d.open()
    return d


def spectrum_db(x: np.ndarray) -> np.ndarray:
    w = np.hanning(x.size)
    return 10 * np.log10(np.abs(np.fft.fftshift(np.fft.fft(x * w))) ** 2 + 1e-30)


def offset_of_peak(x: np.ndarray, rate: float) -> float:
    k = int(np.argmax(spectrum_db(x)))
    return (k - x.size // 2) * rate / x.size


def test_cw_lands_at_expected_offset():
    d = make(sources=[Source("cw", 7_050_000.0, -20.0)], noise_db=-80.0)
    d.configure(DeviceConfig(center_hz=7_000_000.0, sample_rate=RATE))
    x = d.read(65536)
    assert x.dtype == np.complex64
    assert abs(offset_of_peak(x, RATE) - 50_000.0) <= RATE / 65536


def test_source_outside_span_is_not_rendered():
    d = make(sources=[Source("cw", 9_000_000.0, 0.0)], noise_db=-40.0)
    d.configure(DeviceConfig(center_hz=7_000_000.0, sample_rate=RATE))
    power_db = 10 * np.log10(np.mean(np.abs(d.read(65536)) ** 2))
    assert abs(power_db - (-40.0)) < 1.0


def test_same_seed_gives_identical_samples():
    a, b = make(seed=7), make(seed=7)
    for d in (a, b):
        d.configure(DeviceConfig(center_hz=7_100_000.0, sample_rate=RATE))
    assert np.array_equal(a.read(8192), b.read(8192))


def test_smps_comb_lines_have_the_configured_spacing():
    src = Source("smps_comb", 150_000.0, -20.0, {"spacing_hz": 68_000.0, "am_depth": 0.0})
    d = make(sources=[src], noise_db=-90.0)
    d.configure(DeviceConfig(center_hz=7_000_000.0, sample_rate=RATE))
    s = spectrum_db(d.read(65536))
    peaks = np.flatnonzero(s > np.median(s) + 30)
    # collapse adjacent bins of the same line
    starts = peaks[np.insert(np.diff(peaks) > 3, 0, True)]
    spacing_hz = np.median(np.diff(starts)) * RATE / 65536
    assert len(starts) >= 20
    assert abs(spacing_hz - 68_000.0) < 2 * RATE / 65536


def test_gain_scales_power():
    d = make(noise_db=-40.0)
    d.configure(DeviceConfig(center_hz=7.1e6, sample_rate=RATE, gain_db=30.0))
    p30 = np.mean(np.abs(d.read(65536)) ** 2)
    d.configure(DeviceConfig(center_hz=7.1e6, sample_rate=RATE, gain_db=50.0))
    p50 = np.mean(np.abs(d.read(65536)) ** 2)
    assert abs(10 * np.log10(p50 / p30) - 20.0) < 1.0


def test_realtime_pacing():
    d = SyntheticDevice(realtime=True)
    d.open()
    d.configure(DeviceConfig(center_hz=7.1e6, sample_rate=1.024e6))
    t0 = time.monotonic()
    for _ in range(5):
        d.read(20_480)  # 5 x 20 ms
    assert time.monotonic() - t0 >= 0.09


def test_configure_before_open_raises():
    with pytest.raises(DeviceError):
        SyntheticDevice(realtime=False).configure(DeviceConfig())
