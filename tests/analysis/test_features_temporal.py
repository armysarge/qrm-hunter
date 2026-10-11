import dataclasses
import math

import numpy as np
import pytest

from qrm_hunter.analysis.features import (
    FEATURE_IDS,
    AnalysisContext,
    Cancelled,
    Features,
    ShortInput,
    extract_features,
    find_lines,
    impulse_features,
    level_features,
    mains_envelope,
    mains_features,
    ofdm_features,
)
from qrm_hunter.analysis.spectra import welch_psd
from qrm_hunter.sdr.synthetic import CLIP_CENTER_HZ as C
from qrm_hunter.sdr.synthetic import CLIP_RATE as RATE
from qrm_hunter.sdr.synthetic import Source, profile_sources, render_clip, render_profile_clip


def carrier(mod_hz, depth, seconds=4.0, shape="sine", duty=0.5, offset=20_000.0, seed=0):
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    if shape == "sine":
        env = 1 + depth * np.cos(2 * np.pi * mod_hz * t)
    else:
        env = np.where(np.mod(t * mod_hz, 1.0) < duty, 1.0, 1.0 - depth)
    rng = np.random.default_rng(seed)
    noise = 1e-4 * (rng.normal(size=n) + 1j * rng.normal(size=n))
    return (0.03 * env * np.exp(2j * np.pi * offset * t) + noise).astype(np.complex64)


def mains_of(x, ctx=AnalysisContext(), center=C):
    psd = welch_psd(x, RATE, center)
    lines = find_lines(psd, level_features(psd).curve)
    env, env_rate = mains_envelope(x, RATE, center, lines)
    return mains_features(env, env_rate, ctx)


@pytest.mark.parametrize("mod, family", [(100.0, 50.0), (120.0, 60.0)])
def test_mains_family_is_detected_from_the_envelope(mod, family):
    m = mains_of(carrier(mod, 0.5))
    assert m.hz == family
    assert m.depth == pytest.approx(0.5, abs=0.08)
    assert m.harmonics >= 1


def test_mains_detection_does_not_depend_on_the_rf_band():
    assert mains_of(carrier(100.0, 0.5), center=435_000_000.0).hz == 50.0


def test_configured_mains_is_used_even_when_the_signal_disagrees():
    m = mains_of(carrier(100.0, 0.5), AnalysisContext(mains_hz=60))
    assert m.hz == 60 and m.depth < 0.05


def test_square_chopping_has_many_mains_harmonics():
    assert mains_of(carrier(100.0, 1.0, shape="square")).harmonics >= 4


def test_unmodulated_carrier_has_no_mains():
    m = mains_of(carrier(100.0, 0.0))
    assert math.isnan(m.hz) and m.depth == 0.0 and m.harmonics == 0


def train_100hz():
    return render_clip([Source("impulses", 0.0, -15.0, {"rate_hz": 100.0, "burst_s": 20e-6})],
                       seconds=4.0, noise_db=-45.0)


def test_impulses_without_lines_use_the_whole_band_envelope():
    assert mains_of(train_100hz()).hz == 50.0


def test_regular_impulse_train():
    imp = impulse_features(train_100hz(), RATE)
    assert imp.rate_hz == pytest.approx(100.0, abs=3.0)
    assert imp.periodicity >= 0.9 and imp.period_s == pytest.approx(0.01, abs=5e-4)
    assert imp.kurtosis > 4.0 and imp.crest_db > 20


def test_irregular_impulses_have_low_periodicity():
    rng = np.random.default_rng(3)
    times = [t for t in np.cumsum(rng.exponential(0.05, 70)) if t < 3.9]
    src = Source("impulses", 0.0, -15.0, {"times_s": times, "repeat_s": 4.0, "burst_s": 20e-6})
    imp = impulse_features(render_clip([src], seconds=4.0, noise_db=-45.0), RATE)
    assert imp.rate_hz == pytest.approx(len(times) / 4.0, rel=0.2)
    assert imp.periodicity < 0.6


def test_bursts_closer_than_one_millisecond_are_one_event():
    src = Source("impulses", 0.0, -15.0, {"times_s": [1.0, 1.0005, 2.0], "repeat_s": 4.0, "burst_s": 20e-6})
    x = render_clip([src], seconds=4.0, noise_db=-45.0)
    assert impulse_features(x, RATE).event_times_s.size == 2


def test_noise_has_no_impulses():
    imp = impulse_features(render_profile_clip(None, seconds=4.0), RATE)
    assert imp.rate_hz < 0.5 and math.isnan(imp.period_s) and imp.crest_db < 16


@pytest.mark.parametrize("name, grid", [("vdsl", 4312.5), ("plc", 24_414.0625)])
def test_ofdm_grid_spacing(name, grid):
    x = render_profile_clip(name, 15.0, seconds=4.0)
    psd = welch_psd(x, RATE, C)
    o = ofdm_features(psd, level_features(psd))
    assert abs(o.spacing_hz - grid) / grid < 0.02
    assert o.flatness > 0.5 and o.edges_db > 3


def test_a_comb_is_not_ofdm():
    x = render_profile_clip("ev_charger", 15.0, seconds=3.0)
    psd = welch_psd(x, RATE, C)
    assert math.isnan(ofdm_features(psd, level_features(psd)).spacing_hz)


def test_time_features():
    plc = extract_features(render_profile_clip("plc", 15.0), RATE, C)
    assert 0.2 <= plc.time_duty <= 0.75 and not math.isnan(plc.time_on_off_period_s)
    ev = extract_features(render_profile_clip("ev_charger", 15.0), RATE, C)
    assert ev.time_duty > 0.9 and ev.time_persistence > 0.9
    quiet = extract_features(render_profile_clip(None), RATE, C)
    assert math.isnan(quiet.time_duty) and quiet.time_persistence < 0.05


def test_extract_features_assembles_every_group():
    f = extract_features(render_profile_clip("smps", 15.0), RATE, C)
    assert isinstance(f, Features)
    assert abs(f.comb_spacing_hz - 30_000) / 30_000 < 0.005
    assert f.mains_hz == 50 and 0.2 < f.mains_depth < 0.55
    assert f.comb_stability < 0.5 and not math.isnan(f.floor_rise_db)
    assert set(f.as_dict()) == set(FEATURE_IDS)


def test_baseline_sets_the_floor_rise():
    f = extract_features(render_profile_clip(None, seconds=2.5), RATE, C, AnalysisContext(baseline_db=-120.0))
    assert f.floor_rise_db == pytest.approx(f.floor_db + 120.0)


def test_short_input_and_cancellation():
    with pytest.raises(ShortInput):
        extract_features(np.zeros(int(1.9 * RATE), np.complex64), RATE, C)

    def stop():
        raise Cancelled("stop")

    with pytest.raises(Cancelled):
        extract_features(render_profile_clip(None, seconds=2.5), RATE, C, check=stop)


def test_a_narrow_rate_clip_is_analysed_with_unmeasurable_features_as_nan():
    rate = 12_500.0
    f = extract_features(render_profile_clip("smps", 15.0, seconds=3.0, rate=rate), rate, C)
    assert math.isnan(f.slope_db_per_mhz) and math.isnan(f.ofdm_spacing_hz)
    assert not math.isnan(f.floor_db) and not math.isnan(f.imp_rate_hz)


def test_a_clip_at_the_minimum_length_is_analysed():
    f = extract_features(render_profile_clip(None, seconds=2.0), RATE, C)
    assert not math.isnan(f.floor_db) and math.isnan(f.comb_spacing_hz) and math.isnan(f.time_duty)


@pytest.mark.parametrize("spacing", [4312.5, 4250.0, 8625.0])
@pytest.mark.parametrize("seed", [0, 1])
def test_a_comb_on_a_noise_block_is_not_ofdm(spacing, seed):
    src = profile_sources("plasma_tv", rng=np.random.default_rng(seed))
    src[0] = dataclasses.replace(src[0], params={**src[0].params, "spacing_hz": spacing})
    psd = welch_psd(render_clip(src, seconds=4.0, seed=seed + 1), RATE, C)
    assert math.isnan(ofdm_features(psd, level_features(psd)).spacing_hz)


@pytest.mark.parametrize("rate", [12_500.0, 25_000.0, 50_000.0])
def test_noise_has_no_impulses_at_narrow_rates(rate):
    assert impulse_features(render_profile_clip(None, seconds=4.0, rate=rate), rate).rate_hz < 0.5


@pytest.mark.parametrize("rate", [12_500.0, 25_000.0])
def test_impulses_are_found_at_narrow_rates(rate):
    imp = impulse_features(render_profile_clip("arcing", 15.0, seconds=4.0, rate=rate), rate)
    assert imp.rate_hz == pytest.approx(86.0, rel=0.15) and imp.periodicity >= 0.9


@pytest.mark.parametrize("stop_at", [2, 3, 4])
def test_cancellation_between_later_stages(stop_at):
    calls = []

    def check():
        calls.append(1)
        if len(calls) == stop_at:
            raise Cancelled("stop")

    with pytest.raises(Cancelled):
        extract_features(render_profile_clip(None, seconds=2.5), RATE, C, check=check)
    assert len(calls) == stop_at
