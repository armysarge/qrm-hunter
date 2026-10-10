"""Power spectrum estimation and reduction to display resolution. Pure functions, no I/O."""

from __future__ import annotations

import numpy as np
from scipy.signal.windows import blackmanharris

FLOOR_DB = -200.0


class SpectrumProcessor:
    def __init__(self, fft_size: int = 8192, avg_alpha: float = 0.3, dc_fix: bool = True) -> None:
        if fft_size & (fft_size - 1) or not 1024 <= fft_size <= 65536:
            raise ValueError(f"fft_size must be a power of two in 1024..65536, got {fft_size}")
        if not 0 < avg_alpha <= 1:
            raise ValueError(f"avg_alpha must be in (0, 1], got {avg_alpha}")
        self.fft_size = fft_size
        self.avg_alpha = avg_alpha
        self.dc_fix = dc_fix
        w = blackmanharris(fft_size, sym=False)
        self._window = w.astype(np.float32)
        self._norm = 1.0 / float(w.sum()) ** 2  # a full-scale tone reads 0 dBFS
        self._noise_factor = float(np.sum(w**2)) * self._norm
        self._pending = np.empty(0, np.complex64)
        self._avg: np.ndarray | None = None

    @property
    def bin_noise_factor(self) -> float:
        """Per-bin reading (linear) for complex white noise of total power 1."""
        return self._noise_factor

    def reset(self) -> None:
        self._pending = np.empty(0, np.complex64)
        self._avg = None

    def process(self, iq: np.ndarray) -> np.ndarray | None:
        n = self.fft_size
        data = np.asarray(iq, dtype=np.complex64)
        if self._pending.size:
            data = np.concatenate([self._pending, data])
        frames = data.size // n
        self._pending = data[frames * n:].copy()
        if frames == 0:
            return None
        blocks = data[: frames * n].reshape(frames, n) * self._window
        power = np.abs(np.fft.fft(blocks, axis=1)) ** 2 * self._norm
        for p in power:
            self._avg = p if self._avg is None else self._avg + self.avg_alpha * (p - self._avg)
        out = 10 * np.log10(np.fft.fftshift(self._avg) + 1e-20)
        if self.dc_fix:
            _fix_dc(out)
        return out.astype(np.float32)


def _fix_dc(spec_db: np.ndarray) -> None:
    # Notch ±4 bins: the Blackman-Harris main lobe of a DC tone extends to ±4 bins
    # (a ±2 notch leaves ~33 dB shoulders at ±3). Neighbours are taken just outside it.
    c = spec_db.size // 2
    neighbours = np.concatenate([spec_db[c - 10:c - 4], spec_db[c + 5:c + 11]])
    spec_db[c - 4:c + 5] = np.median(neighbours)


def bin_for_display(spectrum_db: np.ndarray, center_hz: float, sample_rate: float,
                    f_lo: float, f_hi: float, width: int) -> tuple[np.ndarray, float, float]:
    """Crop to [f_lo, f_hi] and resample to `width` columns.

    Downsampling uses the max of each group so narrow lines (comb spurs, carriers) never vanish.
    Returns (bins, used_lo_hz, used_hi_hz): the frequencies of the first and last FFT bin used.
    """
    if width < 1:
        raise ValueError("width must be >= 1")
    n = spectrum_db.size
    bin_hz = sample_rate / n
    first_hz = center_hz - sample_rate / 2
    last_hz = first_hz + (n - 1) * bin_hz
    lo, hi = max(f_lo, first_hz), min(f_hi, last_hz)
    if hi <= lo:
        return np.full(width, FLOOR_DB, np.float32), f_lo, f_hi
    k_lo = int(np.ceil((lo - first_hz) / bin_hz))
    k_hi = int(np.floor((hi - first_hz) / bin_hz))
    seg = spectrum_db[k_lo:k_hi + 1]
    used_lo, used_hi = first_hz + k_lo * bin_hz, first_hz + k_hi * bin_hz
    if seg.size >= width:
        edges = np.linspace(0, seg.size, width + 1).astype(np.int64)[:-1]
        return np.maximum.reduceat(seg, edges).astype(np.float32), used_lo, used_hi
    if seg.size == 1:
        return np.full(width, seg[0], np.float32), used_lo, used_hi
    out = np.interp(np.linspace(0, seg.size - 1, width), np.arange(seg.size), seg)
    return out.astype(np.float32), used_lo, used_hi


def quantize(db: np.ndarray, db_min: float, db_max: float) -> np.ndarray:
    if db_max <= db_min:
        raise ValueError("db_max must be greater than db_min")
    scaled = (np.asarray(db, np.float32) - db_min) * (255.0 / (db_max - db_min))
    return np.clip(np.rint(scaled), 0, 255).astype(np.uint8)
