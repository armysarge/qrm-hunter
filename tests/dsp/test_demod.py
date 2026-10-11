import math

import numpy as np
import pytest
from scipy.signal import lfilter

from qrm_hunter.dsp.demod import Decimator, Demodulator, choose_decimation

RATE = 2.048e6


def blocks(x, sizes):
    out, i = [], 0
    for s in sizes:
        out.append(x[i:i + s])
        i += s
    out.append(x[i:])
    return out


def am_signal(rate, offset, seconds, tone=1000.0, depth=0.5, amp=0.1, seed=0):
    n = int(rate * seconds)
    t = np.arange(n) / rate
    rng = np.random.default_rng(seed)
    x = amp * (1 + depth * np.cos(2 * np.pi * tone * t)) * np.exp(2j * np.pi * offset * t)
    x = x + 1e-4 * (rng.normal(size=n) + 1j * rng.normal(size=n))
    return x.astype(np.complex64)


def tone_snr_db(audio, fs, tone):
    a = audio.astype(np.float64)
    a -= a.mean()
    p = np.abs(np.fft.rfft(a * np.hanning(a.size))) ** 2
    f = np.fft.rfftfreq(a.size, 1 / fs)
    sig = np.abs(f - tone) < 20
    noise = (f > 100) & ~sig & (f < 0.45 * fs)
    return 10 * np.log10(p[sig].sum() / p[noise].sum())


def dominant_hz(audio, fs):
    a = audio.astype(np.float64) - audio.mean()
    p = np.abs(np.fft.rfft(a * np.hanning(a.size)))
    return np.fft.rfftfreq(a.size, 1 / fs)[int(np.argmax(p))]


@pytest.mark.parametrize("rate", [250e3, 1.024e6, 2.048e6, 2.4e6, 3.2e6])
def test_decimation_reaches_about_12k_with_small_stages(rate):
    stages = choose_decimation(rate)
    assert all(1 <= s <= 16 for s in stages)
    assert abs(rate / math.prod(stages) - 12_000) / 12_000 < 0.1


def test_decimator_matches_reference_and_is_block_invariant():
    rng = np.random.default_rng(3)
    x = (rng.normal(size=50_000) + 1j * rng.normal(size=50_000)).astype(np.complex64)
    whole = Decimator(13).process(x)
    ref = lfilter(Decimator(13).taps, 1.0, x)[::13]
    assert np.allclose(whole, ref, atol=1e-4)
    d = Decimator(13)
    chunked = np.concatenate([d.process(b) for b in blocks(x, [1, 7, 1000, 12, 30_000])])
    assert np.allclose(whole, chunked, atol=1e-4)


def test_am_recovers_tone():
    d = Demodulator(RATE, "AM", offset_hz=50_000.0, agc=False)
    x = am_signal(RATE, 50_000.0, 1.0)
    audio = np.concatenate([d.process(b) for b in np.array_split(x, 63)])
    assert audio.dtype == np.int16
    skip = int(0.2 * d.out_rate)
    assert tone_snr_db(audio[skip:], d.out_rate, 1000.0) > 30


def test_usb_rejects_lower_sideband():
    def run(tone_offset):
        d = Demodulator(RATE, "USB", offset_hz=50_000.0, agc=False)
        t = np.arange(int(RATE * 0.3)) / RATE
        x = (0.1 * np.exp(2j * np.pi * (50_000.0 + tone_offset) * t)).astype(np.complex64)
        a = np.concatenate([d.process(b) for b in np.array_split(x, 20)]).astype(np.float64)
        return np.sqrt(np.mean(a[len(a) // 3:] ** 2)) + 1e-9

    assert 20 * np.log10(run(1500.0) / run(-1500.0)) > 30


def test_lsb_passes_lower_sideband():
    d = Demodulator(RATE, "LSB", offset_hz=0.0, agc=False)
    t = np.arange(int(RATE * 0.3)) / RATE
    x = (0.1 * np.exp(2j * np.pi * -1500.0 * t)).astype(np.complex64)
    a = np.concatenate([d.process(b) for b in np.array_split(x, 10)])
    assert abs(dominant_hz(a[len(a) // 3:], d.out_rate) - 1500.0) < 10


def test_cw_carrier_becomes_pitch_tone():
    d = Demodulator(RATE, "CW", offset_hz=20_000.0, agc=False)
    t = np.arange(int(RATE * 0.3)) / RATE
    x = (0.1 * np.exp(2j * np.pi * 20_000.0 * t)).astype(np.complex64)
    a = np.concatenate([d.process(b) for b in np.array_split(x, 10)])
    assert abs(dominant_hz(a[len(a) // 3:], d.out_rate) - 700.0) < 10


def test_output_is_continuous_across_irregular_blocks():
    x = am_signal(RATE, 30_000.0, 0.3)
    whole = Demodulator(RATE, "USB", offset_hz=30_000.0, agc=False).process(x)
    d = Demodulator(RATE, "USB", offset_hz=30_000.0, agc=False)
    chunked = np.concatenate([d.process(b) for b in blocks(x, [5, 40_000, 333, 100_000, 7])])
    assert whole.shape == chunked.shape
    assert np.max(np.abs(whole.astype(np.int32) - chunked.astype(np.int32))) <= 1


def test_agc_brings_weak_signal_to_usable_level():
    d = Demodulator(RATE, "AM", offset_hz=0.0, agc=True)
    x = am_signal(RATE, 0.0, 1.0, amp=0.001)
    a = np.concatenate([d.process(b) for b in np.array_split(x, 50)])
    assert np.max(np.abs(a[len(a) // 2:])) > 3000


def test_offset_outside_span_rejected():
    d = Demodulator(RATE)
    with pytest.raises(ValueError):
        d.set_offset(RATE)


def test_set_mode_rejects_bad_bandwidth_and_leaves_state_unchanged():
    d = Demodulator(RATE, "USB", bw_hz=2400.0, agc=False)
    chan = d._chan
    for bad in (10.0, 49.0, 20_000.0, -5.0):
        with pytest.raises(ValueError):
            d.set_mode("AM", bad)
    assert (d.mode, d.bw_hz) == ("USB", 2400.0)
    assert d._chan is chan


def test_am_first_block_has_no_dc_step():
    d = Demodulator(RATE, "AM", offset_hz=0.0, agc=False)
    x = am_signal(RATE, 0.0, 0.2, amp=0.5, depth=0.2)
    a = d.process(x[:40_000]).astype(np.float64)
    assert np.max(np.abs(a[200:])) < 0.25 * 32767  # carrier ~0.5 would step to ~16000 unprimed


@pytest.mark.performance
def test_demodulator_runs_faster_than_real_time_at_3p2_msps():
    """Loose CI-safe bound (0.5 s per 1 s of input); typical is ~0.07 s."""
    import time

    rate = 3.2e6
    d = Demodulator(rate, "USB", offset_hz=50_000.0)
    rng = np.random.default_rng(1)
    x = (rng.normal(size=int(rate)) + 1j * rng.normal(size=int(rate))).astype(np.complex64)
    bl = np.array_split(x, int(rate / 32768))
    d.process(bl[0])
    t0 = time.perf_counter()
    for b in bl:
        d.process(b)
    assert time.perf_counter() - t0 < 0.5


def fm_signal(rate, carrier_hz, tone_hz, dev_hz, seconds=0.5):
    t = np.arange(int(rate * seconds)) / rate
    phase = 2 * np.pi * carrier_hz * t + (dev_hz / tone_hz) * np.sin(2 * np.pi * tone_hz * t)
    return (0.1 * np.exp(1j * phase)).astype(np.complex64)


def test_fm_recovers_tone_and_level_tracks_deviation():
    def run(dev):
        d = Demodulator(RATE, "FM", offset_hz=40_000.0, agc=False)
        x = fm_signal(RATE, 40_000.0, 1000.0, dev)
        a = np.concatenate([d.process(b) for b in np.array_split(x, 37)])
        return d, a[len(a) // 3:]

    d, a = run(2500.0)
    assert d.bw_hz == 10_000.0
    assert abs(dominant_hz(a, d.out_rate) - 1000.0) < 10
    assert tone_snr_db(a, d.out_rate, 1000.0) > 25
    rms = lambda v: np.sqrt(np.mean(v.astype(np.float64) ** 2))
    assert 1.6 < rms(a) / rms(run(1250.0)[1]) < 2.4  # discriminator output is linear in deviation


def test_fm_mistuned_carrier_gives_no_dc():
    d = Demodulator(RATE, "FM", offset_hz=40_000.0, agc=False)
    t = np.arange(int(RATE * 0.5)) / RATE
    x = (0.1 * np.exp(2j * np.pi * 41_000.0 * t)).astype(np.complex64)  # 1 kHz off, unmodulated
    a = np.concatenate([d.process(b) for b in np.array_split(x, 10)]).astype(np.float64)
    assert abs(np.mean(a[len(a) // 2:])) < 300


def test_fm_runs_at_24k_audio_and_switching_modes_rebuilds_decimation():
    d = Demodulator(RATE, "FM", bw_hz=16_000.0, offset_hz=40_000.0, agc=False)
    assert abs(d.out_rate - 24_000) / 24_000 < 0.1
    d.set_mode("USB")
    assert abs(d.out_rate - 12_000) / 12_000 < 0.1
    d.set_mode("FM", 20_000.0)
    assert abs(d.out_rate - 24_000) / 24_000 < 0.1


def test_wide_fm_with_5khz_deviation_is_not_clipped():
    d = Demodulator(RATE, "FM", bw_hz=20_000.0, offset_hz=40_000.0, agc=False)
    x = fm_signal(RATE, 40_000.0, 1000.0, 5000.0)
    a = np.concatenate([d.process(b) for b in np.array_split(x, 23)])[4000:]
    assert abs(dominant_hz(a, d.out_rate) - 1000.0) < 10
    assert np.max(np.abs(a.astype(np.int32))) < 32000  # 5 kHz deviation fits a 20 kHz channel


def test_rejected_fm_bandwidth_keeps_previous_mode_and_rate():
    d = Demodulator(RATE, "USB", agc=False)
    rate = d.out_rate
    with pytest.raises(ValueError):
        d.set_mode("FM", 60_000.0)
    assert (d.mode, d.out_rate) == ("USB", rate)


# --- broadcast FM ------------------------------------------------------------------------------


def wfm_signal(rate, offset, mpx, amp=0.1, noise=0.0, seed=0):
    """Frequency-modulate an MPX signal (1.0 = 75 kHz deviation) onto a carrier at `offset`."""
    n = mpx.size
    phase = 2 * np.pi * (offset * np.arange(n) / rate + np.cumsum(mpx, dtype=np.float64) * 75_000.0 / rate)
    x = amp * np.exp(1j * phase)
    if noise:
        rng = np.random.default_rng(seed)
        x = x + noise * (rng.normal(size=n) + 1j * rng.normal(size=n))
    return x.astype(np.complex64)


def tone_mpx(rate, seconds, tone=1000.0, level=0.5, pilot=0.0):
    t = np.arange(int(rate * seconds)) / rate
    return level * np.cos(2 * np.pi * tone * t) + pilot * np.cos(2 * np.pi * 19_000.0 * t)


def tone_amplitude(audio, fs, tone):
    a = audio.astype(np.float64)
    t = np.arange(a.size) / fs
    return 2 * abs(np.mean(a * np.exp(-2j * np.pi * tone * t)))


def test_wfm_rates_and_default_bandwidth():
    d = Demodulator(RATE, "WFM", offset_hz=300_000.0)
    assert (d.bw_hz, d.mpx_rate, d.out_rate) == (180_000.0, 256_000.0, 32_000.0)
    d.set_mode("WFM", 150_000.0)
    assert d.bw_hz == 150_000.0
    assert Demodulator(2.4e6, "WFM").out_rate == pytest.approx(30_000.0)


@pytest.mark.parametrize("rate", [250e3, 900e3])
def test_wfm_needs_one_megasample(rate):
    with pytest.raises(ValueError, match="Broadcast FM needs a sample rate of at least 1 MS/s"):
        Demodulator(rate, "WFM")
    d = Demodulator(rate, "USB")
    with pytest.raises(ValueError, match="at least 1 MS/s"):
        d.set_mode("WFM")
    assert d.mode == "USB"


@pytest.mark.parametrize("tau", [50e-6, 75e-6])
def test_wfm_tone_and_deemphasis(tau):
    rate, tone, level = 1.024e6, 1000.0, 0.5
    x = wfm_signal(rate, 200_000.0, tone_mpx(rate, 0.6, tone, level))
    d = Demodulator(rate, "WFM", offset_hz=200_000.0, deemphasis_s=tau)
    a = np.concatenate([d.process(b) for b in blocks(x, [7, 30_000, 1, 65_536, 4096, 12_345])])
    a = a[len(a) // 3:]
    assert abs(dominant_hz(a, d.out_rate) - tone) < 5
    expected = level * 0.5 * 32767 / np.sqrt(1 + (2 * np.pi * tone * tau) ** 2)  # one-pole RC
    assert 20 * np.log10(tone_amplitude(a, d.out_rate, tone) / expected) == pytest.approx(0.0, abs=0.1)


def test_wfm_75us_attenuates_more_than_50us_by_the_rc_ratio():
    rate, tone = 1.024e6, 1000.0
    x = wfm_signal(rate, 0.0, tone_mpx(rate, 0.5, tone))
    amps = []
    for tau in (50e-6, 75e-6):
        d = Demodulator(rate, "WFM", deemphasis_s=tau)
        a = d.process(x)
        amps.append(tone_amplitude(a[len(a) // 3:], d.out_rate, tone))
    w = 2 * np.pi * tone
    expected_db = 10 * np.log10((1 + (w * 75e-6) ** 2) / (1 + (w * 50e-6) ** 2))  # about 0.46 dB
    assert 20 * np.log10(amps[0] / amps[1]) == pytest.approx(expected_db, abs=0.05)


@pytest.mark.parametrize("pilot,stereo", [(0.09, True), (0.0, False)])
def test_wfm_pilot_detection(pilot, stereo):
    rate = 2.048e6
    x = wfm_signal(rate, -400_000.0, tone_mpx(rate, 0.3, pilot=pilot), noise=0.003)
    d = Demodulator(rate, "WFM", offset_hz=-400_000.0)
    for b in np.array_split(x, 19):
        d.process(b)
    assert d.stereo is stereo
    assert d.mpx.dtype == np.float32 and d.mpx.size > 0


def test_wfm_mpx_is_scaled_to_75khz_deviation():
    rate = 1.024e6
    x = wfm_signal(rate, 0.0, tone_mpx(rate, 0.2, 5000.0, 0.8))
    d = Demodulator(rate, "WFM", bw_hz=200_000.0)
    d.process(x[:100_000])
    d.process(x[100_000:])
    assert np.max(np.abs(d.mpx)) == pytest.approx(0.8, abs=0.05)


def test_wfm_mode_switch_and_back():
    d = Demodulator(RATE, "USB", offset_hz=100_000.0)
    d.set_mode("WFM")
    assert d.out_rate == 32_000.0
    d.set_mode("USB")
    assert abs(d.out_rate - 12_000) / 12_000 < 0.1 and d.mpx.size == 0 and not d.stereo


@pytest.mark.parametrize("rate", [1.024e6, 2.048e6, 2.4e6])
@pytest.mark.parametrize("bw", [100_000.0, 150_000.0, 180_000.0, 200_000.0, 250_000.0])
def test_every_allowed_wfm_bandwidth_works_at_every_rate(rate, bw):
    d = Demodulator(rate, "WFM", bw_hz=bw)
    assert d.bw_hz == bw
    x = wfm_signal(rate, 0.0, tone_mpx(rate, 0.1))
    assert d.process(x).size > 0


def test_wfm_front_decimator_is_flat_to_the_channel_edge():
    from scipy.signal import freqz

    d = Demodulator(RATE, "WFM")
    _, h = freqz(d._stages[0].taps, worN=[50e3, 100e3, 110e3], fs=RATE)
    assert np.all(np.abs(20 * np.log10(np.abs(h))) < 0.1)


@pytest.mark.parametrize("tau", [50e-6, 75e-6])
def test_wfm_deemphasis_at_10_khz(tau):
    rate, tone, level = 1.024e6, 10_000.0, 0.5
    x = wfm_signal(rate, 0.0, tone_mpx(rate, 0.5, tone, level))
    d = Demodulator(rate, "WFM", deemphasis_s=tau)
    a = d.process(x)
    a = a[len(a) // 3:]
    expected = level * 0.5 * 32767 / np.sqrt(1 + (2 * np.pi * tone * tau) ** 2)
    assert 20 * np.log10(tone_amplitude(a, d.out_rate, tone) / expected) == pytest.approx(0.0, abs=0.2)
