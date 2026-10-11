"""Spectral estimates for the feature extractor: Welch PSD, floor curve, one short-time pass."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import median_filter

TARGET_BIN_HZ = 30.0
FLOOR_WINDOW_HZ = 10_000.0
_BATCH = 32  # FFT segments per batch: bounds memory on long, wide captures


def nfft_for(rate: float) -> int:
    return int(min(65536, max(1024, 2 ** round(math.log2(rate / TARGET_BIN_HZ)))))


@dataclass(frozen=True)
class Psd:
    freqs_hz: np.ndarray  # absolute RF frequency of each bin, ascending
    density: np.ndarray  # linear power density, full scale^2 per Hz
    bin_hz: float
    nfft: int

    @property
    def db(self) -> np.ndarray:
        return 10 * np.log10(self.density + 1e-30)


def _window(nfft: int) -> tuple[np.ndarray, float]:
    w = np.hanning(nfft)
    return w.astype(np.float32), float(np.sum(w * w))


def _segments(x: np.ndarray, nfft: int) -> np.ndarray:
    return sliding_window_view(x, nfft)[:: nfft // 2]


def welch_psd(iq: np.ndarray, rate: float, center_hz: float, nfft: int | None = None) -> Psd:
    nfft = nfft or nfft_for(rate)
    x = np.asarray(iq, np.complex64)
    if x.size < nfft:
        raise ValueError("not enough samples for one FFT segment")
    win, wsum = _window(nfft)
    segs = _segments(x, nfft)
    acc = np.zeros(nfft)
    for i in range(0, segs.shape[0], _BATCH):
        spec = np.fft.fft(segs[i:i + _BATCH] * win, axis=1)
        acc += (spec.real ** 2 + spec.imag ** 2).sum(axis=0)
    density = np.fft.fftshift(acc / segs.shape[0] / (rate * wsum))
    freqs = center_hz + (np.arange(nfft) - nfft // 2) * (rate / nfft)
    return Psd(freqs, density, rate / nfft, nfft)


def floor_curve_db(psd_db: np.ndarray, bin_hz: float) -> np.ndarray:
    """The local noise floor: a running median wide enough to ignore narrow lines."""
    size = max(31, int(FLOOR_WINDOW_HZ / bin_hz) | 1)
    return median_filter(psd_db, size=size, mode="nearest")


@dataclass(frozen=True)
class StftStats:
    hop_s: float
    energy: np.ndarray  # per segment: summed density over the mask bins
    # per segment: median density in dB over all bins. For noise each bin's power is exponentially
    # distributed, whose median is ln 2 times the mean: about 1.6 dB below the mean density.
    floor_db: np.ndarray
    windows: list[np.ndarray]  # per window: (segments, width) linear density


def stft_pass(iq: np.ndarray, rate: float, nfft: int, mask: np.ndarray,
              windows: list[tuple[int, int]]) -> StftStats:
    """One pass over the same half-overlapping Hann segments (fftshifted) that welch_psd uses."""
    x = np.asarray(iq, np.complex64)
    win, wsum = _window(nfft)
    segs = _segments(x, nfft)
    n = segs.shape[0]
    norm = 1.0 / (rate * wsum)
    energy, floor = np.zeros(n), np.empty(n)
    out = [np.empty((n, b - a)) for a, b in windows]
    use_mask = bool(mask.any())
    for i in range(0, n, _BATCH):
        spec = np.fft.fftshift(np.fft.fft(segs[i:i + _BATCH] * win, axis=1), axes=1)
        p = (spec.real ** 2 + spec.imag ** 2) * norm
        rows = slice(i, i + p.shape[0])
        if use_mask:
            energy[rows] = p[:, mask].sum(axis=1)
        floor[rows] = 10 * np.log10(np.median(p, axis=1) + 1e-30)  # median: see StftStats.floor_db
        for k, (a, b) in enumerate(windows):
            out[k][rows] = p[:, a:b]
    return StftStats((nfft // 2) / rate, energy, floor, out)
