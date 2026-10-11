"""QRM features in physical units: level, lines/comb, mains, impulses, OFDM and time behaviour."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from types import SimpleNamespace
from typing import Literal

import numpy as np
from scipy.signal import find_peaks
from scipy.stats import kurtosis

from ..dsp.extract import extract_span
from .spectra import Psd, StftStats, floor_curve_db, stft_pass, welch_psd

FEATURE_VERSION = 1
MIN_SECONDS = 2.0

FEATURE_IDS: tuple[str, ...] = (
    "floor_db", "floor_rise_db", "occupied_bw_hz", "slope_db_per_mhz",
    "lines.count", "lines.width_hz",
    "comb.spacing_hz", "comb.count", "comb.stability", "comb.drift_hz_s", "comb.width_hz",
    "mains.hz", "mains.depth", "mains.harmonics",
    "imp.rate_hz", "imp.kurtosis", "imp.crest_db", "imp.periodicity", "imp.period_s",
    "ofdm.spacing_hz", "ofdm.flatness", "ofdm.edges_db",
    "time.duty", "time.persistence", "time.on_off_period_s",
)

NAN = math.nan


def _attr(fid: str) -> str:
    return fid.replace(".", "_")


@dataclass(frozen=True)
class Features:
    """Every value is in physical units; NaN means "not measurable" for this input."""

    floor_db: float = NAN  # dBFS/Hz, median of the floor curve
    floor_rise_db: float = NAN  # dB above the band baseline, or above the quietest 10 % of the capture
    occupied_bw_hz: float = NAN  # extent holding 99 % of the power above the floor
    slope_db_per_mhz: float = NAN
    lines_count: float = NAN
    lines_width_hz: float = NAN  # median -6 dB width of the narrow lines
    comb_spacing_hz: float = NAN
    comb_count: float = NAN  # lines on the comb grid (0 when there is no comb)
    comb_stability: float = NAN  # 0-1, spacing spread: scatter of the comb lines about a steady spacing
    comb_drift_hz_s: float = NAN  # absolute line drift rate
    comb_width_hz: float = NAN  # median -6 dB width of the comb lines
    mains_hz: float = NAN
    mains_depth: float = NAN  # envelope modulation index at f or 2f
    mains_harmonics: float = NAN  # n*f envelope lines up to 1 kHz
    imp_rate_hz: float = NAN
    imp_kurtosis: float = NAN
    imp_crest_db: float = NAN
    imp_periodicity: float = NAN  # 0-1
    imp_period_s: float = NAN
    ofdm_spacing_hz: float = NAN
    ofdm_flatness: float = NAN  # 0-1
    ofdm_edges_db: float = NAN
    time_duty: float = NAN  # 0-1
    time_persistence: float = NAN  # 0-1
    time_on_off_period_s: float = NAN

    def get(self, fid: str) -> float:
        return float(getattr(self, _attr(fid)))

    def to_vector(self) -> np.ndarray:
        return np.array([self.get(f) for f in FEATURE_IDS], np.float32)

    def as_dict(self) -> dict[str, float | None]:
        return {f: (None if math.isnan(v := self.get(f)) else v) for f in FEATURE_IDS}

    def format_args(self) -> dict:
        """Keyword arguments for str.format: "{comb.spacing_hz:.1f}" and "{floor_db:.0f}" both work."""
        out: dict = {}
        for fid in FEATURE_IDS:
            if "." in fid:
                group, name = fid.split(".", 1)
                setattr(out.setdefault(group, SimpleNamespace()), name, self.get(fid))
            else:
                out[fid] = self.get(fid)
        return out


assert [f.name for f in fields(Features)] == [_attr(f) for f in FEATURE_IDS]


@dataclass(frozen=True)
class AnalysisContext:
    mains_hz: Literal[50, 60, "auto"] = "auto"
    baseline_db: float | None = None  # band baseline floor in dBFS/Hz, when known
    dc_hz: float | None = None  # RF frequency of the receiver's DC spike, when it is inside the clip


class ShortInput(ValueError):
    """Less than MIN_SECONDS of data. The message is shown to the user."""


class Cancelled(Exception):
    """Raised by a cancellation check between stages."""


# --- level -------------------------------------------------------------------------------------

SLOPE_MIN_SPAN_HZ = 100_000.0  # below this span the floor slope is not measurable

@dataclass(frozen=True)
class LevelResult:
    floor_db: float
    occupied_bw_hz: float
    slope_db_per_mhz: float
    ref_db: float  # 10th percentile of the floor curve: the quietest part of the capture
    curve: np.ndarray = field(repr=False)
    occupied: np.ndarray = field(repr=False)  # bool per bin


def level_features(psd: Psd) -> LevelResult:
    db = psd.db
    curve = floor_curve_db(db, psd.bin_hz)
    ref = float(np.percentile(curve, 10))
    occupied = (curve > ref + 3.0) | (db > ref + 10.0)
    excess = np.where(occupied, psd.density - 10 ** (ref / 10), 0.0).clip(min=0.0)
    total = float(excess.sum())
    if total <= 0:
        occ_bw = 0.0
    else:
        c = np.cumsum(excess) / total
        i0, i1 = int(np.searchsorted(c, 0.005)), int(np.searchsorted(c, 0.995))
        occ_bw = float((i1 - i0 + 1) * psd.bin_hz)
    mhz = (psd.freqs_hz - psd.freqs_hz.mean()) / 1e6
    # over a narrow span the running-median floor's edge effects dominate the fit
    if psd.nfft * psd.bin_hz < SLOPE_MIN_SPAN_HZ:
        slope = NAN
    else:
        slope = float(np.polyfit(mhz, curve, 1)[0])
    return LevelResult(float(np.median(curve)), occ_bw, slope, ref, curve, occupied)


# --- lines and comb ----------------------------------------------------------------------------

LINE_MIN_DB = 10.0  # line height above the local floor
LINE_SEP_HZ = 400.0  # closer peaks are one line, so AM and PWM sidebands stay with their carrier
LINE_WIDTH_DB = 6.0
WIDTH_LIMIT_HZ = 5_000.0
DC_GUARD_BINS = 3
COMB_MIN_LINES = 4


@dataclass(frozen=True)
class LineSet:
    bins: np.ndarray
    freqs_hz: np.ndarray
    heights_db: np.ndarray
    widths_hz: np.ndarray

    @property
    def count(self) -> int:
        return int(self.bins.size)


def _parabolic(y0: np.ndarray, y1: np.ndarray, y2: np.ndarray) -> np.ndarray:
    den = y0 - 2 * y1 + y2
    ok = np.abs(den) > 1e-12
    return np.where(ok, 0.5 * (y0 - y2) / np.where(ok, den, 1.0), 0.0).clip(-0.5, 0.5)


def _width_hz(db: np.ndarray, k: int, bin_hz: float) -> float:
    """-6 dB width of the peak at bin k, with linear interpolation at both crossings."""
    level = db[k] - LINE_WIDTH_DB
    limit = max(2, int(WIDTH_LIMIT_HZ / bin_hz))
    i = k
    while i > max(0, k - limit) and db[i] > level:
        i -= 1
    j = k
    while j < min(db.size - 1, k + limit) and db[j] > level:
        j += 1
    left = i + (level - db[i]) / (db[i + 1] - db[i]) if db[i + 1] != db[i] else float(i)
    right = j - (level - db[j]) / (db[j - 1] - db[j]) if db[j - 1] != db[j] else float(j)
    return float((right - left) * bin_hz)


def find_lines(psd: Psd, curve: np.ndarray, dc_hz: float | None = None) -> LineSet:
    db = psd.db
    flat = db - curve
    dist = max(1, int(round(LINE_SEP_HZ / psd.bin_hz)))
    # a line must stand LINE_WIDTH_DB clear of its own skirt, so the noisy shoulders of a broad
    # (phase-noise spread) carrier are not counted as extra lines
    peaks, _ = find_peaks(flat, height=LINE_MIN_DB, distance=dist, prominence=LINE_WIDTH_DB)
    peaks = peaks[(peaks > 0) & (peaks < db.size - 1)]
    if dc_hz is not None:
        peaks = peaks[np.abs(psd.freqs_hz[peaks] - dc_hz) > DC_GUARD_BINS * psd.bin_hz]
    delta = _parabolic(db[peaks - 1], db[peaks], db[peaks + 1])
    widths = np.array([_width_hz(db, int(k), psd.bin_hz) for k in peaks])
    return LineSet(peaks, psd.freqs_hz[peaks] + delta * psd.bin_hz, flat[peaks], widths)


@dataclass(frozen=True)
class CombResult:
    spacing_hz: float
    members: np.ndarray  # indices into the LineSet
    harmonics: np.ndarray  # grid index of each member, relative to the reference line
    width_hz: float

    @property
    def count(self) -> int:
        return int(self.members.size)


def _candidate_spacings(psd: Psd, curve: np.ndarray) -> list[float]:
    """Spacings suggested by the autocorrelation and the cepstrum of the line spectrum."""
    flat = psd.db - curve
    w = np.where(flat >= LINE_MIN_DB, flat, 0.0)
    n = w.size
    min_lag = max(2, int(np.ceil(LINE_SEP_HZ / psd.bin_hz)))
    max_lag = n // 3
    if max_lag <= min_lag:
        return []
    out: list[float] = []
    spec = np.fft.rfft(w - w.mean(), 2 * n)
    ac = np.fft.irfft(np.abs(spec) ** 2)[:n]
    lags, _ = find_peaks(ac[:max_lag + 1])
    lags = lags[lags >= min_lag]
    out += [float(lag * psd.bin_hz) for lag in lags[np.argsort(ac[lags])[::-1][:3]]]
    cep = np.abs(np.fft.ifft(np.log1p(w)))
    qs, _ = find_peaks(cep[:n // min_lag + 1])
    qs = qs[qs >= 3]
    out += [float(n / q * psd.bin_hz) for q in qs[np.argsort(cep[qs])[::-1][:3]]]
    return out


def _fit(freqs: np.ndarray, ref: float, spacing: float, bin_hz: float):
    """Harmonic least squares f_i = a + k_i*s over the lines that sit on the grid."""
    s, a = spacing, ref
    for _ in range(4):
        tol = max(1.5 * bin_hz, 0.01 * s)
        k = np.round((freqs - a) / s)
        inl = np.abs(freqs - (a + k * s)) <= tol
        if inl.sum() < COMB_MIN_LINES or np.ptp(k[inl]) == 0:
            return None
        s, a = np.polyfit(k[inl], freqs[inl], 1)
    tol = max(1.5 * bin_hz, 0.01 * s)
    k = np.round((freqs - a) / s)
    inl = np.abs(freqs - (a + k * s)) <= tol
    if inl.sum() < COMB_MIN_LINES:
        return None
    if inl.sum() / (np.ptp(k[inl]) + 1) < 0.5:  # mostly empty grid slots: a sub-multiple
        return None
    return float(s), np.flatnonzero(inl), k[inl].astype(int)


def fit_comb(psd: Psd, curve: np.ndarray, lines: LineSet) -> CombResult | None:
    if lines.count < COMB_MIN_LINES:
        return None
    ref = float(lines.freqs_hz[int(np.argmax(lines.heights_db))])
    best = None
    for cand in _candidate_spacings(psd, curve):
        fit = _fit(lines.freqs_hz, ref, cand, psd.bin_hz)
        if fit is not None and (best is None or (fit[1].size, fit[0]) > (best[1].size, best[0])):
            best = fit
    if best is None:
        return None
    s, members, harmonics = best
    return CombResult(s, members, harmonics, float(np.median(lines.widths_hz[members])))


TRACK_FRAME_S = 0.1
TRACK_LINES = 3
TRACK_HALF_HZ = 500.0
STABILITY_SCALE_HZ = 25.0


def track_windows(psd: Psd, lines: LineSet, comb: CombResult | None) -> list[tuple[int, int]]:
    """Bin ranges around the strongest comb lines, for following them through time."""
    if comb is None:
        return []
    order = np.argsort(lines.heights_db[comb.members])[::-1][:TRACK_LINES]
    half = max(3, int(min(TRACK_HALF_HZ, 0.45 * comb.spacing_hz) / psd.bin_hz))
    out = []
    for m in comb.members[order]:
        k = int(lines.bins[m])
        out.append((max(0, k - half), min(psd.nfft, k + half + 1)))
    return out


def track_lines(stats: StftStats, windows: list[tuple[int, int]], bin_hz: float) -> tuple[float, float]:
    """(median absolute drift in Hz/s, stability 0-1) of the tracked lines. Stability measures the
    spacing spread: exp(-spread / 25 Hz), where spread is the median scatter of each tracked comb
    line about the position a steady spacing gives it (its own linear trend)."""
    per = max(1, int(round(TRACK_FRAME_S / stats.hop_s)))
    drifts, spreads = [], []
    for power in stats.windows:
        n = power.shape[0] // per
        if n < 3 or power.shape[1] < 3:
            continue
        frames = power[:n * per].reshape(n, per, -1).mean(axis=1)
        db = 10 * np.log10(frames + 1e-30)
        idx = np.clip(frames.argmax(axis=1), 1, frames.shape[1] - 2)
        rows = np.arange(n)
        f = (idx + _parabolic(db[rows, idx - 1], db[rows, idx], db[rows, idx + 1])) * bin_hz
        t = (rows + 0.5) * per * stats.hop_s
        slope, icpt = np.polyfit(t, f, 1)
        drifts.append(abs(float(slope)))
        spreads.append(float(np.std(f - (slope * t + icpt))))
    if not drifts:
        return NAN, NAN
    return float(np.median(drifts)), float(math.exp(-float(np.median(spreads)) / STABILITY_SCALE_HZ))


# --- mains -------------------------------------------------------------------------------------

MAINS_FAMILIES = (50.0, 60.0)
ENV_REGION_HZ = 1_200.0  # half-width of the region around the strongest line
ENV_MAX_HZ = 1_000.0
ENV_RES_HZ = 0.25
ENV_BLOCK_RATE = 5_000.0
MAINS_MIN_DEPTH = 0.03
HARMONIC_MIN_INDEX = 0.05


@dataclass(frozen=True)
class MainsResult:
    hz: float
    depth: float
    harmonics: float


def mains_envelope(iq: np.ndarray, rate: float, center_hz: float, lines: LineSet) -> tuple[np.ndarray, float]:
    if lines.count:
        f = float(lines.freqs_hz[int(np.argmax(lines.heights_db))])
        ex = extract_span(iq, rate, center_hz, f - ENV_REGION_HZ, f + ENV_REGION_HZ)
        return np.abs(ex.iq).astype(np.float64), ex.rate
    block = max(1, int(rate // ENV_BLOCK_RATE))
    n = iq.size // block * block
    env = np.abs(np.asarray(iq[:n], np.complex64)).reshape(-1, block).mean(axis=1)
    return env.astype(np.float64), rate / block


def mains_features(env: np.ndarray, env_rate: float, ctx: AnalysisContext) -> MainsResult:
    dc = float(np.mean(env))
    if dc <= 0 or env.size < 16:
        return MainsResult(NAN, NAN, NAN)
    win = np.hanning(env.size)
    nfft = max(env.size, int(np.ceil(env_rate / ENV_RES_HZ)))  # zero-pad to <= 0.25 Hz bins
    spec = np.abs(np.fft.rfft((env - dc) * win, nfft))
    freqs = np.fft.rfftfreq(nfft, 1 / env_rate)
    scale = 2.0 / (dc * float(win.sum()))  # spectrum value -> modulation index

    def index_at(f: float) -> float:
        sel = np.abs(freqs - f) <= 0.5
        return float(spec[sel].max()) * scale if sel.any() else 0.0

    band = (freqs > 20) & (freqs <= ENV_MAX_HZ)
    background = float(np.median(spec[band])) * scale if band.any() else 0.0
    families = {f: [index_at(n * f) for n in range(1, int(ENV_MAX_HZ // f) + 1)] for f in MAINS_FAMILIES}
    if ctx.mains_hz in (50, 60):
        f = float(ctx.mains_hz)
    else:
        f = max(MAINS_FAMILIES, key=lambda fam: sum(families[fam][:6]))
    idx = families[f]
    depth = max(idx[0], idx[1])
    if ctx.mains_hz == "auto" and depth < MAINS_MIN_DEPTH:
        return MainsResult(NAN, 0.0, 0.0)
    threshold = max(HARMONIC_MIN_INDEX, 4 * background)
    return MainsResult(f, depth, float(sum(1 for m in idx if m >= threshold)))


# --- impulses ----------------------------------------------------------------------------------

IMP_BLOCK_S = 100e-6  # power is averaged over 100 us blocks before thresholding
IMP_SIGMA = 6.0
IMP_MERGE_S = 1e-3


@dataclass(frozen=True)
class ImpulseResult:
    rate_hz: float
    kurtosis: float
    crest_db: float
    periodicity: float
    period_s: float
    event_times_s: np.ndarray = field(repr=False)


def impulse_features(iq: np.ndarray, rate: float) -> ImpulseResult:
    a = np.abs(np.asarray(iq, np.complex64))
    rms = float(np.sqrt(np.mean(a.astype(np.float64) ** 2)))
    crest = 20 * math.log10(float(a.max()) / rms) if rms > 0 else NAN
    kurt = float(kurtosis(a, fisher=False))
    block = max(1, int(round(rate * IMP_BLOCK_S)))
    n = a.size // block * block
    p = np.square(a[:n].astype(np.float64)).reshape(-1, block).mean(axis=1)
    med = float(np.median(p))
    sigma = max(1.4826 * float(np.median(np.abs(p - med))), 0.05 * med)  # robust (MAD) sigma
    above = np.flatnonzero(p > med + IMP_SIGMA * sigma)
    merge = max(1, int(round(IMP_MERGE_S * rate / block)))
    starts = above[np.insert(np.diff(above) > merge, 0, True)] if above.size else above
    times = starts * block / rate
    periodicity = period = NAN
    if times.size >= 3:
        gaps = np.diff(times)
        period = float(np.median(gaps))
        r = gaps / period
        k = np.round(r)
        # fraction of gaps that are a whole number of periods: missed pulses do not count against it
        periodicity = float(np.mean((k >= 1) & (np.abs(r - k) <= 0.1)))
    return ImpulseResult(times.size / (iq.size / rate), kurt, crest, periodicity, period, times)


# --- OFDM --------------------------------------------------------------------------------------

OFDM_GRIDS = (4312.5, 24_414.0625)  # VDSL; HomePlug AV PLC
OFDM_TOL = 0.02
OFDM_MIN_CORR = 0.04  # carrier-grid correlation at the grid's period (0-1)
OFDM_CONTRAST = 1.5  # ... and this many times the correlation well away from it
OFDM_SIDE = 0.15  # "well away": periods off by at least 15 %
OFDM_EDGE_HZ = 1_000.0
_OFDM_STEPS = np.linspace(1 - 2 * OFDM_SIDE, 1 + 2 * OFDM_SIDE, 121)  # trial periods, x 1/grid


@dataclass(frozen=True)
class OfdmResult:
    spacing_hz: float
    flatness: float
    edges_db: float


def _widest_run(mask: np.ndarray) -> tuple[int, int]:
    best, start = (0, 0), None
    for i, v in enumerate(np.append(mask, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    return best


def _grid_correlation(density: np.ndarray, bin_hz: float, grid: float) -> np.ndarray:
    """|sum P(f) exp(2j*pi*f*tau)| / sum P(f) for tau = _OFDM_STEPS / grid.

    This is the normalised autocorrelation of the signal at lag tau. An OFDM symbol is longer
    than 1/grid (cyclic prefix, or simply a symbol rate below the carrier spacing), so every
    carrier repeats itself 1/grid later within the symbol: a peak at tau = 1/grid whose height is
    the repeated fraction of the symbol. Narrow lines and smooth noise blocks give no such peak.
    """
    f = np.arange(density.size) * bin_hz
    tau = _OFDM_STEPS / grid
    return np.abs(np.exp(2j * np.pi * np.outer(tau, f)) @ density) / float(density.sum())


def ofdm_features(psd: Psd, level: LevelResult) -> OfdmResult:
    a, b = _widest_run(level.curve > level.ref_db + 3.0)
    if (b - a) * psd.bin_hz < 4 * OFDM_GRIDS[0]:
        return OfdmResult(NAN, NAN, NAN)
    region = psd.density[a:b]
    flatness = float(np.exp(np.mean(np.log(region + 1e-30))) / np.mean(region))
    w = max(1, int(OFDM_EDGE_HZ / psd.bin_hz))
    c = level.curve
    rise = c[min(a + w, c.size - 1)] - c[max(a - w, 0)]
    fall = c[max(b - 1 - w, 0)] - c[min(b - 1 + w, c.size - 1)]
    edges = float((rise + fall) / 2)
    # narrow lines (a comb riding on a noise block) are clipped to just above the floor curve
    clipped = np.minimum(region, 10 ** ((c[a:b] + 3.0) / 10))
    near = np.flatnonzero(np.abs(_OFDM_STEPS - 1) <= OFDM_TOL + 1e-9)
    away = np.abs(_OFDM_STEPS - 1) >= OFDM_SIDE
    best, best_corr = NAN, OFDM_MIN_CORR
    for grid in OFDM_GRIDS:
        if (b - a) * psd.bin_hz < 4 * grid:
            continue
        rho = _grid_correlation(clipped, psd.bin_hz, grid)
        k = int(near[int(np.argmax(rho[near]))])
        if k in (near[0], near[-1]) or rho[k] < OFDM_CONTRAST * float(rho[away].max()):
            continue  # no interior peak, or no stronger than the off-grid correlation
        if rho[k] > best_corr:
            d = float(_parabolic(rho[k - 1:k], rho[k:k + 1], rho[k + 1:k + 2])[0])
            step = _OFDM_STEPS[1] - _OFDM_STEPS[0]
            best, best_corr = grid / (_OFDM_STEPS[k] + d * step), float(rho[k])
    return OfdmResult(best, flatness, edges)


# --- time --------------------------------------------------------------------------------------

FRAME_S = 0.05
ON_DB = 6.0


@dataclass(frozen=True)
class TimeResult:
    duty: float
    persistence: float
    on_off_period_s: float


def time_features(stats: StftStats, level: LevelResult, mask_bins: int) -> TimeResult:
    per = max(1, int(round(FRAME_S / stats.hop_s)))
    n = stats.energy.size // per
    if n < 2 or mask_bins == 0:
        return TimeResult(NAN, NAN, NAN)
    e = stats.energy[:n * per].reshape(n, per).mean(axis=1)
    noise = 10 ** (level.ref_db / 10) * mask_bins
    persistence = float(np.mean(e >= noise * 10 ** (ON_DB / 10)))
    if not level.occupied.any():
        return TimeResult(NAN, persistence, NAN)
    e_db = 10 * np.log10(e + 1e-30)
    on = e_db >= np.percentile(e_db, 90) - ON_DB
    onsets = np.flatnonzero(on[1:] & ~on[:-1]) + 1
    period = float(np.median(np.diff(onsets)) * per * stats.hop_s) if onsets.size >= 3 else NAN
    return TimeResult(float(on.mean()), persistence, period)


def floor_rise_db(floor_db: float, seg_floor_db: np.ndarray, ctx: AnalysisContext) -> float:
    if ctx.baseline_db is not None:
        return float(floor_db - ctx.baseline_db)
    if seg_floor_db.size == 0:
        return NAN
    return float(np.median(seg_floor_db) - np.percentile(seg_floor_db, 10))


# --- the extractor -----------------------------------------------------------------------------

def extract_features(iq: np.ndarray, rate: float, center_hz: float,
                     ctx: AnalysisContext = AnalysisContext(),
                     check: Callable[[], None] | None = None) -> Features:
    x = np.asarray(iq, np.complex64)
    if x.size < MIN_SECONDS * rate:
        raise ShortInput(f"need at least {MIN_SECONDS:g} s of data at this frequency")
    check = check or (lambda: None)
    check()
    psd = welch_psd(x, rate, center_hz)
    level = level_features(psd)
    lines = find_lines(psd, level.curve, ctx.dc_hz)
    comb = fit_comb(psd, level.curve, lines)
    check()
    windows = track_windows(psd, lines, comb)
    mask = level.occupied if level.occupied.any() else np.ones(psd.nfft, bool)
    stats = stft_pass(x, rate, psd.nfft, mask, windows)
    drift, stability = track_lines(stats, windows, psd.bin_hz) if comb else (NAN, NAN)
    check()
    env, env_rate = mains_envelope(x, rate, center_hz, lines)
    mains = mains_features(env, env_rate, ctx)
    imp = impulse_features(x, rate)
    check()
    ofdm = ofdm_features(psd, level)
    tm = time_features(stats, level, int(mask.sum()))
    return Features(
        floor_db=level.floor_db,
        floor_rise_db=floor_rise_db(level.floor_db, stats.floor_db, ctx),
        occupied_bw_hz=level.occupied_bw_hz,
        slope_db_per_mhz=level.slope_db_per_mhz,
        lines_count=float(lines.count),
        lines_width_hz=float(np.median(lines.widths_hz)) if lines.count else NAN,
        comb_spacing_hz=comb.spacing_hz if comb else NAN,
        comb_count=float(comb.count) if comb else 0.0,
        comb_stability=stability,
        comb_drift_hz_s=drift,
        comb_width_hz=comb.width_hz if comb else NAN,
        mains_hz=mains.hz, mains_depth=mains.depth, mains_harmonics=mains.harmonics,
        imp_rate_hz=imp.rate_hz, imp_kurtosis=imp.kurtosis, imp_crest_db=imp.crest_db,
        imp_periodicity=imp.periodicity, imp_period_s=imp.period_s,
        ofdm_spacing_hz=ofdm.spacing_hz, ofdm_flatness=ofdm.flatness, ofdm_edges_db=ofdm.edges_db,
        time_duty=tm.duty, time_persistence=tm.persistence, time_on_off_period_s=tm.on_off_period_s,
    )
