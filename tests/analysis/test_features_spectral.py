import math

import numpy as np
import pytest

from qrm_hunter.analysis.features import (
    FEATURE_IDS,
    AnalysisContext,
    Features,
    find_lines,
    fit_comb,
    level_features,
    track_lines,
    track_windows,
)
from qrm_hunter.analysis.spectra import stft_pass, welch_psd
from qrm_hunter.sdr.synthetic import CLIP_CENTER_HZ as C
from qrm_hunter.sdr.synthetic import CLIP_RATE as RATE
from qrm_hunter.sdr.synthetic import Source, render_clip, render_profile_clip


def noise(seconds, power, seed=0):
    rng = np.random.default_rng(seed)
    n = int(seconds * RATE)
    return (math.sqrt(power / 2) * (rng.normal(size=n) + 1j * rng.normal(size=n))).astype(np.complex64)


def spectral(x, ctx=AnalysisContext(), center=C):
    psd = welch_psd(x, RATE, center)
    level = level_features(psd)
    lines = find_lines(psd, level.curve, ctx.dc_hz)
    return psd, level, lines, fit_comb(psd, level.curve, lines)


def test_feature_ids_and_vector_order():
    assert len(FEATURE_IDS) == 25 and len(set(FEATURE_IDS)) == 25
    f = Features(floor_db=-120.0, comb_spacing_hz=1000.0)
    v = f.to_vector()
    assert v.dtype == np.float32 and v.size == 25
    assert v[FEATURE_IDS.index("comb.spacing_hz")] == 1000.0
    assert math.isnan(v[FEATURE_IDS.index("mains.hz")])
    assert f.as_dict()["mains.hz"] is None and f.get("comb.spacing_hz") == 1000.0
    text = "{comb.spacing_hz:.1f} Hz, floor {floor_db:.0f}".format(**f.format_args())
    assert text == "1000.0 Hz, floor -120"


def test_noise_floor_matches_theory_and_nothing_is_occupied():
    _, level, lines, comb = spectral(noise(2.0, 1e-4))
    assert level.floor_db == pytest.approx(10 * math.log10(1e-4 / RATE), abs=1.0)
    assert lines.count == 0 and comb is None and level.occupied_bw_hz == 0.0


def test_a_tone_is_one_narrow_line_at_its_frequency():
    t = np.arange(int(2 * RATE)) / RATE
    x = noise(2.0, 1e-6) + (0.01 * np.exp(2j * np.pi * 12_345 * t)).astype(np.complex64)
    psd, _, lines, _ = spectral(x)
    assert abs(psd.freqs_hz[int(np.argmax(psd.density))] - (C + 12_345)) <= psd.bin_hz
    assert lines.count == 1 and abs(lines.freqs_hz[0] - (C + 12_345)) < 0.3 * psd.bin_hz
    assert lines.widths_hz[0] < 120


@pytest.mark.parametrize("spacing", [3_456.0, 23_456.0, 47_111.0])
def test_comb_spacing_within_half_a_percent(spacing):
    x = render_clip([Source("smps_comb", 0.0, -40.0, {"spacing_hz": spacing, "am_depth": 0.0})],
                    seconds=2.0, noise_db=-50.0)
    _, _, lines, comb = spectral(x)
    assert comb is not None
    assert abs(comb.spacing_hz - spacing) / spacing < 0.005
    assert comb.count >= min(8, int(0.8 * RATE / spacing))


def test_comb_spacing_is_measured_the_same_at_vhf():
    centre = 145_000_000.0
    src = Source("smps_comb", 0.0, -40.0, {"spacing_hz": 23_456.0, "am_depth": 0.0, "max_hz": 2e9})
    x = render_clip([src], seconds=2.0, noise_db=-50.0, center_hz=centre)
    *_, comb = spectral(x, center=centre)
    assert comb is not None and abs(comb.spacing_hz - 23_456.0) / 23_456.0 < 0.005


def test_irregular_lines_are_not_a_comb():
    offs = (-83_100.0, -41_700.0, 12_300.0, 57_900.0)
    x = render_clip([Source("cw", C + f, -40.0) for f in offs], seconds=2.0, noise_db=-50.0)
    _, _, lines, comb = spectral(x)
    assert lines.count == 4 and comb is None


def test_a_line_at_the_receiver_dc_is_ignored():
    x = render_clip([Source("cw", C, -40.0), Source("cw", C + 30_000, -40.0)], seconds=2.0, noise_db=-50.0)
    assert spectral(x)[2].count == 2
    assert spectral(x, AnalysisContext(dc_hz=C))[2].count == 1


def test_occupied_bandwidth_of_a_noise_block():
    x = render_clip([Source("buzz", C + 10_000, -30.0, {"bw_hz": 50_000.0, "am_hz": 100.0})],
                    seconds=2.0, noise_db=-45.0)
    assert 40_000 <= spectral(x)[1].occupied_bw_hz <= 60_000


def test_spread_carriers_are_wider_than_clean_ones():
    clean = render_clip([Source("cw", C + 30_000, -40.0)], seconds=2.0, noise_db=-50.0)
    spread = render_clip([Source("cw", C + 30_000, -40.0, {"spread_hz": 500.0})], seconds=2.0, noise_db=-50.0)
    assert spectral(clean)[2].widths_hz[0] < 120
    assert 500 <= spectral(spread)[2].widths_hz[0] <= 1400


def drift_and_stability(name):
    x = render_profile_clip(name, 20.0, seconds=4.0)
    psd, level, lines, comb = spectral(x)
    windows = track_windows(psd, lines, comb)
    stats = stft_pass(x, RATE, psd.nfft, np.zeros(psd.nfft, bool), windows)
    return track_lines(stats, windows, psd.bin_hz)


def test_tracking_separates_drift_wobble_and_stable_lines():
    drift, stability = drift_and_stability("pv_inverter")
    assert drift > 30 and stability > 0.6
    _, stability = drift_and_stability("smps")
    assert stability < 0.4
    drift, stability = drift_and_stability("ev_charger")
    assert drift < 10 and stability > 0.8


def test_stft_pass_energy_and_floor():
    x = noise(1.0, 1e-4)
    mask = np.zeros(8192, bool)
    mask[100:200] = True
    stats = stft_pass(x, RATE, 8192, mask, [(10, 20)])
    assert stats.hop_s == pytest.approx(4096 / RATE)
    assert stats.energy.shape == stats.floor_db.shape and stats.windows[0].shape[1] == 10
    assert np.median(stats.floor_db) == pytest.approx(10 * math.log10(1e-4 / RATE), abs=2.0)
