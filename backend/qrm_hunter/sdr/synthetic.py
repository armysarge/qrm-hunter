"""A deterministic fake SDR that renders typical HF QRM. Used for tests, demos and development."""

from __future__ import annotations

import cmath
import math
import time
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from .base import DeviceConfig, DeviceError, DeviceInfo, apply_limits

SourceKind = Literal["cw", "smps_comb", "buzz", "ofdm", "impulses", "am_broadcast"]


@dataclass(frozen=True)
class Source:
    """One interference source. freq_hz is the carrier (cw/am), first line (comb),
    centre (buzz) or lower edge (ofdm); impulses ignore it (they are wideband)."""

    kind: SourceKind
    freq_hz: float
    level_db: float = -40.0
    params: dict = field(default_factory=dict)


def demo_scene() -> list[Source]:
    return [
        Source("smps_comb", 150_000.0, -68.0, {"spacing_hz": 68_000.0, "am_hz": 100.0, "am_depth": 0.6}),
        Source("cw", 7_074_000.0, -50.0),
        Source("buzz", 7_150_000.0, -50.0, {"bw_hz": 120_000.0, "am_hz": 100.0}),
        Source("am_broadcast", 7_245_000.0, -38.0, {"tone_hz": 1000.0, "depth": 0.5}),
        Source("ofdm", 7_300_000.0, -45.0, {"bw_hz": 400_000.0, "spacing_hz": 4312.5}),
        Source("impulses", 0.0, -10.0, {"rate_hz": 100.0, "burst_len": 32}),
        Source("cw", 14_074_000.0, -55.0),
        Source("buzz", 14_200_000.0, -48.0, {"bw_hz": 300_000.0, "am_hz": 100.0}),
    ]


SYNTHETIC_INFO = DeviceInfo(
    id="synthetic",
    driver="synthetic",
    label="Synthetic demo (no hardware)",
    freq_range=(100_000.0, 1_800_000_000.0),
    sample_rates=(1_024_000.0, 2_048_000.0, 2_400_000.0),
    gain_range=(0.0, 50.0),
)

_GOLDEN_ANGLE = 2.399963
_PHASOR_CACHE_MAX = 32


class SyntheticDevice:
    def __init__(self, sources: list[Source] | None = None, noise_db: float = -45.0,
                 seed: int = 1234, realtime: bool = True) -> None:
        self.info = SYNTHETIC_INFO
        self.sources = list(demo_scene() if sources is None else sources)
        self.noise_db = noise_db
        self.realtime = realtime
        self.overflows = 0
        self._seed = seed
        self._rng = np.random.default_rng(seed)
        self._cfg = DeviceConfig()
        self._open = False
        self._n = 0
        self._t0 = 0.0
        self._paced = 0
        self._ofdm_cache: dict[tuple, np.ndarray] = {}
        self._phasors: dict[tuple, np.ndarray] = {}

    def open(self) -> None:
        self._open = True
        self._t0 = time.monotonic()
        self._paced = 0

    def close(self) -> None:
        self._open = False

    def configure(self, cfg: DeviceConfig) -> DeviceConfig:
        if not self._open:
            raise DeviceError("device is not open")
        applied = apply_limits(self.info, cfg)
        if applied.sample_rate != self._cfg.sample_rate:
            self._t0 = time.monotonic()
            self._paced = 0
        self._cfg = applied
        return applied

    def read(self, n: int) -> np.ndarray:
        if not self._open:
            raise DeviceError("device is not open")
        rate, fc = self._cfg.sample_rate, self._cfg.center_hz
        n0 = self._n
        sigma = np.float32(np.sqrt(10 ** (self.noise_db / 10) / 2))
        # interleaved re/im pairs viewed as complex64: one RNG call, no float64 temporaries
        x = self._rng.standard_normal(2 * n, dtype=np.float32).view(np.complex64)
        x *= sigma
        for i, src in enumerate(self.sources):
            part = self._render(src, i, n0, n, fc, rate)
            if part is not None:
                x += part
        if self._cfg.gain_db is not None:
            x *= np.float32(10 ** ((self._cfg.gain_db - 30.0) / 20))
        self._n += n
        if self.realtime:
            self._pace(n, rate)
        return x

    # --- rendering -------------------------------------------------------------------------
    #
    # Everything is complex64. Tones are built from a cached unit phasor exp(2j*pi*f*k/rate),
    # k = 0..n-1, rotated by one scalar start phase exp(2j*pi*f*n0/rate) per block, instead of
    # evaluating exp() per sample per block.

    def _phasor(self, freq: float, rate: float, n0: int, n: int) -> np.ndarray:
        key = (freq, rate, n)
        unit = self._phasors.get(key)
        if unit is None:
            if len(self._phasors) >= _PHASOR_CACHE_MAX:
                self._phasors.clear()  # offsets change on every retune; keep the cache bounded
            unit = np.exp(2j * np.pi * (freq / rate) * np.arange(n)).astype(np.complex64)
            self._phasors[key] = unit
        cycles = math.fmod(freq * (n0 / rate), 1.0)
        return unit * np.complex64(cmath.exp(2j * math.pi * cycles))

    def _render(self, src: Source, index: int, n0: int, n: int, fc: float, rate: float):
        amp = np.float32(10 ** (src.level_db / 20))
        p = src.params
        half = rate / 2
        if src.kind == "cw":
            off = src.freq_hz - fc
            return None if abs(off) >= half else amp * self._phasor(off, rate, n0, n)
        if src.kind == "am_broadcast":
            off = src.freq_hz - fc
            if abs(off) >= half:
                return None
            tone = self._phasor(p.get("tone_hz", 1000.0), rate, n0, n).real
            env = 1 + np.float32(p.get("depth", 0.5)) * tone
            return (amp * env) * self._phasor(off, rate, n0, n)
        if src.kind == "smps_comb":
            return self._comb(src, n0, n, fc, rate, amp)
        if src.kind == "buzz":
            return self._buzz(src, n0, n, fc, rate, amp)
        if src.kind == "ofdm":
            return self._ofdm(src, n0, n, fc, rate, amp)
        if src.kind == "impulses":
            return self._impulses(src, index, n, n0, rate, amp)
        raise ValueError(f"unknown source kind {src.kind!r}")

    def _comb(self, src, n0, n, fc, rate, amp):
        p = src.params
        s = p.get("spacing_hz", 68_000.0)
        half = rate / 2
        k_lo = max(0, int(np.ceil((fc - half - src.freq_hz) / s)))
        k_hi = int(np.floor((fc + half - src.freq_hz) / s))
        k_hi = min(k_hi, int((p.get("max_hz", 30_000_000.0) - src.freq_hz) // s))
        if k_hi < k_lo:
            return None
        count = k_hi - k_lo + 1
        f0 = src.freq_hz + k_lo * s - fc
        # Equal-amplitude lines at f0 + k*s, line k carrying the constant phase k*golden_angle:
        #   sum_{k<count} r^k = (1 - r^count) / (1 - r),  r = exp(1j*(2*pi*s*t + golden_angle)).
        # A phase that is linear in k is only a time shift, so the lines are coherent: the result
        # is a pulse train repeating every 1/s, not a comb of lines with independent phases.
        # r is a phasor at s and r^count a phasor at count*s, each with a constant phase term.
        r = self._phasor(s, rate, n0, n) * np.complex64(cmath.exp(1j * _GOLDEN_ANGLE))
        r_count = (self._phasor(count * s, rate, n0, n)
                   * np.complex64(cmath.exp(1j * ((count * _GOLDEN_ANGLE) % (2 * math.pi)))))
        den = 1 - r
        small = np.abs(den) < 1e-6
        if small.any():  # r == 1: every line in phase, the sum is count
            den[small] = 1
            lines = (1 - r_count) / den
            lines[small] = count
        else:
            lines = (1 - r_count) / den
        am = self._phasor(p.get("am_hz", 100.0), rate, n0, n).real
        env = 1 + np.float32(p.get("am_depth", 0.6)) * am
        return (amp * env) * self._phasor(f0, rate, n0, n) * lines

    def _buzz(self, src, n0, n, fc, rate, amp):
        p = src.params
        bw = p.get("bw_hz", 120_000.0)
        off = src.freq_hz - fc
        if abs(off) - bw / 2 >= rate / 2:
            return None
        # Band-limited white noise drawn directly in the frequency domain: only the in-band bins
        # get complex Gaussian values, scaled by sqrt(n) so each bin has the statistics of the FFT
        # of unit-variance complex white noise (the same result as filtering white noise).
        f = np.fft.fftfreq(n, 1 / rate)
        band = np.flatnonzero(np.abs(f - off) <= bw / 2)
        spec = np.zeros(n, np.complex64)
        spec[band] = self._rng.standard_normal(2 * band.size, dtype=np.float32).view(np.complex64)
        x = np.fft.ifft(spec) * np.float32(amp * np.sqrt(n) / np.sqrt(2 * bw / rate))
        # |sin(pi*am*t)| = |Im exp(2j*pi*(am/2)*t)|: rectified mains
        mains = self._phasor(p.get("am_hz", 100.0) / 2, rate, n0, n).imag
        return x * (np.abs(mains) * np.float32(np.sqrt(2)))

    def _ofdm(self, src, n0, n, fc, rate, amp):
        p = src.params
        bw = p.get("bw_hz", 400_000.0)
        spacing = p.get("spacing_hz", 4312.5)
        off = src.freq_hz - fc
        if off >= rate / 2 or off + bw <= -rate / 2:
            return None
        key = (rate, bw, spacing, off)
        wave = self._ofdm_cache.get(key)
        if wave is None:
            wave = self._ofdm_cache[key] = _ofdm_wave(rate, bw, spacing, self._seed, shift_hz=off)
        seg = np.take(wave, np.arange(n) + n0 % wave.size, mode="wrap")
        return (amp * seg) * self._phasor(off, rate, n0, n)

    def _impulses(self, src, index, n, n0, rate, amp):
        """Bursts are generated per burst index from a seeded RNG, so their content does not
        depend on how reads are chunked; a burst straddling a read boundary is split across reads."""
        p = src.params
        r = p.get("rate_hz", 100.0)
        length = int(p.get("burst_len", 32))
        out = np.zeros(n, np.complex64)
        decay = np.exp(-np.arange(length) / (length / 4))
        m_lo = int(np.floor((n0 - length) / rate * r))
        m_hi = int(np.ceil((n0 + n) / rate * r)) + 1
        for m in range(max(m_lo, 0), m_hi):
            s = int(round(m / r * rate))  # absolute start sample of burst m
            if s >= n0 + n or s + length <= n0:
                continue
            rng = np.random.default_rng([self._seed, index, m])
            burst = (rng.normal(size=length) + 1j * rng.normal(size=length)) * decay * (float(amp) / np.sqrt(2))
            a, b = max(s, n0), min(s + length, n0 + n)
            out[a - n0:b - n0] += burst[a - s:b - s]
        return out

    def _pace(self, n: int, rate: float) -> None:
        self._paced += n
        delay = self._t0 + self._paced / rate - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif delay < -0.5:  # fell far behind (debugger, suspend): resynchronise
            self._t0 = time.monotonic()
            self._paced = 0


def _ofdm_wave(rate: float, bw: float, spacing: float, seed: int, shift_hz: float = 0.0,
               symbols: int = 64, symbol_rate: float = 4000.0) -> np.ndarray:
    """A looped VDSL-like OFDM block: QPSK carriers every `spacing` Hz, new phases each symbol.

    The block is built at baseband (carriers 0..bw) and normalised to unit total power, then
    every spectral bin that would fall outside the device span once shifted by `shift_hz` is
    zeroed. Carriers past the edge are removed, not wrapped to the far side of the span.
    """
    rng = np.random.default_rng(seed + 7)
    seg = max(1, round(rate / symbol_rate))
    length = seg * symbols
    t = np.arange(length) / rate
    carriers = np.arange(int(bw // spacing)) * spacing
    phases = rng.integers(0, 4, (symbols, carriers.size)) * (np.pi / 2) + np.pi / 4
    sym = np.arange(length) // seg
    wave = np.zeros(length, np.complex128)
    for j, f in enumerate(carriers):
        wave += np.exp(1j * (2 * np.pi * f * t + phases[sym, j]))
    wave /= np.sqrt(np.mean(np.abs(wave) ** 2))
    # Shifting by off moves bin frequency f to f + off; keep only bins inside the span.
    spec = np.fft.fft(wave)
    f = np.fft.fftfreq(length, 1 / rate) + shift_hz
    spec[(f < -rate / 2) | (f >= rate / 2)] = 0
    return np.fft.ifft(spec).astype(np.complex64)
