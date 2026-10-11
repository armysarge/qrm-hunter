import time

import numpy as np
import pytest

from qrm_hunter.sdr.base import DeviceConfig, DeviceError
from qrm_hunter.sdr.discovery import create_device, discover
from qrm_hunter.sdr.synthetic import (
    CLIP_CENTER_HZ,
    CLIP_RATE,
    PROFILE_IDS,
    PROFILE_LABELS,
    Source,
    SyntheticDevice,
    profile_sources,
    render_clip,
    render_profile_clip,
)

NFFT = 8192
BIN = CLIP_RATE / NFFT


def psd_db(x, nfft=NFFT):
    segs = x[: x.size // nfft * nfft].reshape(-1, nfft) * np.hanning(nfft)
    return 10 * np.log10(np.fft.fftshift((np.abs(np.fft.fft(segs, axis=1)) ** 2).mean(0)) + 1e-30)


def peak_track(x, k0, frame_s=0.2, search=20):
    """Peak bin near k0 in consecutive frames of frame_s seconds."""
    n = int(frame_s * CLIP_RATE)
    out = []
    for i in range(x.size // n):
        p = psd_db(x[i * n:(i + 1) * n])
        lo = max(0, k0 - search)
        out.append(lo + int(np.argmax(p[lo:k0 + search + 1])))
    return np.array(out)


def test_every_class_has_a_profile_and_a_label():
    assert len(PROFILE_IDS) == 14
    assert set(PROFILE_LABELS) == set(PROFILE_IDS)


@pytest.mark.parametrize("name", PROFILE_IDS)
def test_profile_renders_a_finite_signal_above_the_noise(name):
    quiet = render_profile_clip(None, seconds=1.0)
    x = render_profile_clip(name, 15.0, seconds=1.0)
    assert x.dtype == np.complex64 and x.size == int(CLIP_RATE)
    assert np.isfinite(x).all()
    louder = np.mean(np.abs(x) ** 2) > 1.2 * np.mean(np.abs(quiet) ** 2)
    peakier = np.max(np.abs(x)) > 3 * np.max(np.abs(quiet))
    assert louder or peakier


def test_same_seed_same_clip_and_random_profiles_stay_in_span():
    a = render_profile_clip("smps", 10.0, seconds=0.5, seed=3)
    b = render_profile_clip("smps", 10.0, seconds=0.5, seed=3)
    assert np.array_equal(a, b)
    rng = np.random.default_rng(5)
    for name in PROFILE_IDS:
        for src in profile_sources(name, rng=rng):
            if src.kind in ("cw", "ofdm", "buzz"):
                assert abs(src.freq_hz - CLIP_CENTER_HZ) <= 125_000


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError):
        profile_sources("toaster")


def test_pv_inverter_lines_drift_and_ev_lines_do_not():
    x = render_profile_clip("pv_inverter", 20.0, seconds=4.0)
    k0 = int(np.argmax(psd_db(x[: int(CLIP_RATE)])))
    track = peak_track(x, k0)
    assert abs(int(track[-1]) - int(track[0])) * BIN > 100
    y = render_profile_clip("ev_charger", 20.0, seconds=4.0)
    k1 = int(np.argmax(psd_db(y[: int(CLIP_RATE)])))
    assert np.ptp(peak_track(y, k1)) <= 2


def test_smps_lines_wobble():
    x = render_profile_clip("smps", 20.0, seconds=4.0)
    k0 = int(np.argmax(psd_db(x)))
    assert np.ptp(peak_track(x, k0, frame_s=0.1, search=12)) * BIN >= 100


def test_square_chopping_has_the_requested_duty():
    d = SyntheticDevice(sources=[], realtime=False)
    env = d._am({"am_shape": "square", "am_hz": 100.0, "am_duty": 0.3, "am_depth": 1.0}, 0, 25_000, CLIP_RATE)
    assert np.mean(env) == pytest.approx(0.3, abs=0.01)


def test_gated_plc_is_on_for_about_gate_p_of_the_slots():
    sources = profile_sources("plc")
    p_on = sources[0].params["gate_p"]
    x = render_clip(sources, seconds=5.0, noise_db=-45.0)
    slot = int(0.08 * CLIP_RATE)
    power = (np.abs(x[: x.size // slot * slot]) ** 2).reshape(-1, slot).mean(1)
    on = power > 3 * np.median(np.sort(power)[: len(power) // 4])
    assert abs(on.mean() - p_on) < 0.2


def test_relay_clicks_at_the_given_times():
    x = render_profile_clip("relay", 15.0, seconds=5.0)
    # 1 ms blocks: a 200 us click is 45-100x the noise median there (only ~10x in 10 ms blocks)
    block = int(0.001 * CLIP_RATE)
    power = (np.abs(x[: x.size // block * block]) ** 2).reshape(-1, block).mean(1)
    loud = np.flatnonzero(power > 10 * np.median(power)) * 0.001
    clicks = loud[np.insert(np.diff(loud) > 0.05, 0, True)]
    assert np.allclose(clicks, [0.4, 0.9, 2.9], atol=0.02)


def test_spread_line_is_wider_than_a_clean_carrier():
    def width_hz(params):
        x = render_clip([Source("cw", CLIP_CENTER_HZ + 30_000, -40.0, params)], seconds=2.0, noise_db=-50.0)
        p = psd_db(x)
        k = int(np.argmax(p))
        return np.count_nonzero(p[k - 200:k + 200] > p[k] - 6) * BIN

    assert width_hz({}) < 120
    assert 400 <= width_hz({"spread_hz": 500.0}) <= 1400


def test_vsd_lines_carry_strong_pwm_sidebands():
    src = profile_sources("vsd")[0]
    x = render_clip([src], seconds=2.0, noise_db=-60.0)
    p = psd_db(x)
    k = int(np.argmax(p))
    side = int(round(src.params["pm_hz"] / BIN))
    assert max(p[k - side], p[k + side]) > p[k] - 10


@pytest.mark.parametrize("name", ["smps", "ev_charger", "vsd"])
def test_comb_profiles_render_at_vhf(name):
    x = render_profile_clip(name, 20.0, seconds=0.5, center_hz=145_000_000.0)
    p = psd_db(x)
    assert np.count_nonzero(p > np.median(p) + 20) >= 3


def test_profile_devices_are_listed_and_created():
    ids = [i.id for i in discover()]
    assert "synthetic" in ids and "synthetic:vdsl" in ids
    d = create_device("synthetic:vdsl")
    assert isinstance(d, SyntheticDevice) and d.info.id == "synthetic:vdsl"
    assert "VDSL" in d.info.label
    with pytest.raises(DeviceError):
        create_device("synthetic:toaster")


def test_profile_and_sources_are_mutually_exclusive():
    with pytest.raises(ValueError):
        SyntheticDevice(sources=[], profile="smps")


@pytest.mark.performance
@pytest.mark.parametrize("name", PROFILE_IDS)
def test_profile_generates_faster_than_real_time_at_full_rate(name):
    rate, n = 2_400_000.0, 32_768
    d = SyntheticDevice(profile=name, realtime=False)
    d.open()
    d.configure(DeviceConfig(center_hz=CLIP_CENTER_HZ, sample_rate=rate))
    times = []
    for _ in range(10):
        t0 = time.perf_counter()
        d.read(n)
        times.append(time.perf_counter() - t0)
    assert times[0] < 0.2  # nothing is built lazily on the first read
    assert sorted(times)[len(times) // 2] < 0.4 * n / rate
