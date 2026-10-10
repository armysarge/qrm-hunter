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


def test_ofdm_does_not_alias_outside_span():
    # carriers 7.3-7.7 MHz, span 6.588-7.612 MHz: the 7.612-7.7 MHz part must vanish, not wrap
    src = Source("ofdm", 7_300_000.0, -20.0, {"bw_hz": 400_000.0, "spacing_hz": 4312.5})
    d = make(sources=[src], noise_db=-120.0)
    rate = 1_024_000.0
    d.configure(DeviceConfig(center_hz=7_100_000.0, sample_rate=rate))
    s = spectrum_db(d.read(65536))
    f = (np.arange(s.size) - s.size // 2) * rate / s.size
    inband = s[(f >= 220_000.0) & (f <= 380_000.0)]
    alias = s[(f >= -500_000.0) & (f <= -440_000.0)]  # where wrapped carriers would land
    assert alias.mean() < inband.mean() - 25.0


def _burst_chunks(x: np.ndarray, chunk: int) -> int:
    energy = np.sum(np.abs(x.reshape(-1, chunk)) ** 2, axis=1)
    return int(np.count_nonzero(energy > 1e-6))


def test_impulse_bursts_per_second_match_rate_hz():
    src = Source("impulses", 0.0, -10.0, {"rate_hz": 100.0, "burst_len": 32})
    rate = 1_024_000.0  # bursts start every 10 240 samples, i.e. on 1024-sample chunk boundaries
    d = make(sources=[src], noise_db=-300.0)
    d.configure(DeviceConfig(center_hz=7_100_000.0, sample_rate=rate))
    assert _burst_chunks(d.read(int(rate)), 1024) == 100


def test_impulse_bursts_are_independent_of_read_chunking():
    src = Source("impulses", 0.0, -10.0, {"rate_hz": 100.0, "burst_len": 32})

    def run(sizes):
        d = make(sources=[src], noise_db=-300.0)
        d.configure(DeviceConfig(center_hz=7_100_000.0, sample_rate=1_024_000.0))
        return np.concatenate([d.read(n) for n in sizes])

    whole = run([20_480])
    split = run([10_250, 10_230])  # second burst starts at 10 240: the cut is 10 samples into it
    assert np.count_nonzero(whole) > 0
    # Thermal noise (noise_db=-300, ~1e-15) comes from the chunk-dependent RNG stream; bursts are
    # ~0.3 in amplitude, so a 1e-9 tolerance still catches any truncated or duplicated burst.
    assert np.allclose(whole, split, rtol=0, atol=1e-9)


def test_buzz_power_and_centre():
    src = Source("buzz", 7_150_000.0, -20.0, {"bw_hz": 120_000.0, "am_hz": 100.0})
    d = make(sources=[src], noise_db=-120.0)
    d.configure(DeviceConfig(center_hz=7_100_000.0, sample_rate=RATE))
    x = d.read(262144)
    p_db = 10 * np.log10(np.mean(np.abs(x) ** 2))
    assert abs(p_db - (-20.0)) < 1.0
    p = np.abs(np.fft.fftshift(np.fft.fft(x))) ** 2
    f = (np.arange(p.size) - p.size // 2) * RATE / p.size
    centroid = np.sum(f * p) / np.sum(p)
    assert abs(centroid - 50_000.0) < 5_000.0


@pytest.mark.parametrize("depth", [0.5, 1.0])
def test_am_broadcast_sidebands_follow_depth(depth):
    src = Source("am_broadcast", 7_100_000.0, -20.0, {"tone_hz": 1000.0, "depth": depth})
    d = make(sources=[src], noise_db=-150.0)
    d.configure(DeviceConfig(center_hz=7_100_000.0, sample_rate=RATE))
    n = 65536  # 1000 Hz falls exactly on bin 32, so no window is needed
    X = np.abs(np.fft.fft(d.read(n))) / n
    expected_db = 20 * np.log10(depth / 2)  # each sideband is depth/2 of the carrier
    for k in (32, n - 32):
        assert abs(20 * np.log10(X[k] / X[0]) - expected_db) < 0.5


@pytest.mark.parametrize("kind,freq,params", [
    ("cw", 7_074_000.0, {}),
    ("am_broadcast", 7_245_000.0, {"tone_hz": 1000.0, "depth": 0.5}),
    ("smps_comb", 150_000.0, {"spacing_hz": 68_000.0, "am_hz": 100.0, "am_depth": 0.6}),
    ("ofdm", 7_300_000.0, {"bw_hz": 400_000.0, "spacing_hz": 4312.5}),
])
def test_tones_are_phase_continuous_across_reads(kind, freq, params):
    # cached unit phasors rotated by a per-block start phase must equal one long read
    src = Source(kind, freq, -20.0, params)

    def run(sizes):
        d = make(sources=[src], noise_db=-300.0)
        d.configure(DeviceConfig(center_hz=7_200_000.0, sample_rate=RATE))
        return np.concatenate([d.read(n) for n in sizes])

    whole = run([65_536])
    split = run([10_000, 22_768, 32_768])
    assert np.max(np.abs(whole)) > 0.01
    assert np.allclose(whole, split, rtol=0, atol=1e-4)


@pytest.mark.performance
def test_generates_faster_than_real_time_on_40m():
    # the default demo tune (7.1 MHz, centre 7.612 MHz at 2.048 MS/s) renders every demo source
    d = make()
    d.configure(DeviceConfig(center_hz=7_612_000.0, sample_rate=RATE))
    n = 32_768
    d.read(n)  # warm the phasor/OFDM caches
    times = []
    for _ in range(9):
        t0 = time.perf_counter()
        d.read(n)
        times.append(time.perf_counter() - t0)
    assert sorted(times)[len(times) // 2] < 0.5 * n / RATE


def test_demo_scene_has_a_broadcast_station_at_100_mhz():
    d = make()
    d.configure(DeviceConfig(center_hz=100_500_000.0, sample_rate=RATE))
    x = d.read(65_536)
    s = spectrum_db(x)
    f = (np.arange(x.size) - x.size // 2) * RATE / x.size
    station = s[np.abs(f + 500_000.0) < 75_000.0].mean()
    quiet = s[np.abs(f - 500_000.0) < 75_000.0].mean()
    assert station - quiet > 10
    d.configure(DeviceConfig(center_hz=110_000_000.0, sample_rate=RATE))
    x = d.read(65_536)
    assert np.std(spectrum_db(x)[1000:-1000]) < 8  # just noise (and impulses) far from the station


@pytest.mark.performance
def test_generates_faster_than_real_time_at_the_broadcast_station():
    d = make()
    d.configure(DeviceConfig(center_hz=100_512_000.0, sample_rate=RATE))
    n = 32_768
    d.read(n)
    times = []
    for _ in range(9):
        t0 = time.perf_counter()
        d.read(n)
        times.append(time.perf_counter() - t0)
    assert sorted(times)[len(times) // 2] < 0.5 * n / RATE
