"""QRM features in physical units: level, lines/comb, mains, impulses, OFDM and time behaviour."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, fields
from types import SimpleNamespace
from typing import Callable, Literal

import numpy as np
from scipy.signal import find_peaks

from .spectra import Psd, StftStats, floor_curve_db

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
