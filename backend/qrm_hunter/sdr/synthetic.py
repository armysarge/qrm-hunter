"""A deterministic fake SDR that renders typical HF QRM. Used for tests, demos and development."""

from __future__ import annotations

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
        t = (n0 + np.arange(n)) / rate
        sigma = np.sqrt(10 ** (self.noise_db / 10) / 2)
        x = self._rng.normal(0.0, sigma, n) + 1j * self._rng.normal(0.0, sigma, n)
        for src in self.sources:
            part = self._render(src, t, n0, fc, rate)
            if part is not None:
                x += part
        if self._cfg.gain_db is not None:
            x *= 10 ** ((self._cfg.gain_db - 30.0) / 20)
        self._n += n
        if self.realtime:
            self._pace(n, rate)
        return x.astype(np.complex64)

    # --- rendering -------------------------------------------------------------------------

    def _render(self, src: Source, t: np.ndarray, n0: int, fc: float, rate: float):
        amp = 10 ** (src.level_db / 20)
        p = src.params
        half = rate / 2
        if src.kind == "cw":
            off = src.freq_hz - fc
            return None if abs(off) >= half else amp * np.exp(2j * np.pi * off * t)
        if src.kind == "am_broadcast":
            off = src.freq_hz - fc
            if abs(off) >= half:
                return None
            env = 1 + p.get("depth", 0.5) * np.cos(2 * np.pi * p.get("tone_hz", 1000.0) * t)
            return amp * env * np.exp(2j * np.pi * off * t)
        if src.kind == "smps_comb":
            return self._comb(src, t, fc, rate, amp)
        if src.kind == "buzz":
            return self._buzz(src, t, fc, rate, amp)
        if src.kind == "ofdm":
            return self._ofdm(src, t, n0, fc, rate, amp)
        if src.kind == "impulses":
            return self._impulses(src, t.size, n0, rate, amp)
        raise ValueError(f"unknown source kind {src.kind!r}")

    def _comb(self, src, t, fc, rate, amp):
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
        # sum_{k<count} r^k in closed form: a comb of equal lines with fixed, decorrelated phases
        r = np.exp(1j * (2 * np.pi * s * t + _GOLDEN_ANGLE))
        den = 1 - r
        small = np.abs(den) < 1e-9
        lines = np.where(small, count, (1 - r**count) / np.where(small, 1, den))
        env = 1 + p.get("am_depth", 0.6) * np.cos(2 * np.pi * p.get("am_hz", 100.0) * t)
        return amp * env * np.exp(2j * np.pi * f0 * t) * lines

    def _buzz(self, src, t, fc, rate, amp):
        p = src.params
        bw = p.get("bw_hz", 120_000.0)
        off = src.freq_hz - fc
        if abs(off) - bw / 2 >= rate / 2:
            return None
        n = t.size
        spec = np.fft.fft(self._rng.normal(size=n) + 1j * self._rng.normal(size=n))
        f = np.fft.fftfreq(n, 1 / rate)
        spec[np.abs(f - off) > bw / 2] = 0
        x = np.fft.ifft(spec) * (amp / np.sqrt(2 * bw / rate))
        env = np.abs(np.sin(np.pi * p.get("am_hz", 100.0) * t)) * np.sqrt(2)  # rectified mains
        return x * env

    def _ofdm(self, src, t, n0, fc, rate, amp):
        p = src.params
        bw = p.get("bw_hz", 400_000.0)
        spacing = p.get("spacing_hz", 4312.5)
        off = src.freq_hz - fc
        if off >= rate / 2 or off + bw <= -rate / 2:
            return None
        key = (rate, bw, spacing)
        wave = self._ofdm_cache.get(key)
        if wave is None:
            wave = self._ofdm_cache[key] = _ofdm_wave(rate, bw, spacing, self._seed)
        idx = (n0 + np.arange(t.size)) % wave.size
        return amp * wave[idx] * np.exp(2j * np.pi * off * t)

    def _impulses(self, src, n, n0, rate, amp):
        p = src.params
        r = p.get("rate_hz", 100.0)
        length = int(p.get("burst_len", 32))
        out = np.zeros(n, np.complex128)
        t0, t1 = n0 / rate, (n0 + n) / rate
        decay = np.exp(-np.arange(length) / (length / 4))
        for m in range(int(np.ceil(t0 * r)), int(np.ceil(t1 * r))):
            start = int(round(m / r * rate)) - n0
            if not 0 <= start < n:
                continue
            seg = min(length, n - start)
            noise = self._rng.normal(size=seg) + 1j * self._rng.normal(size=seg)
            out[start:start + seg] += noise * decay[:seg] * (amp / np.sqrt(2))
        return out

    def _pace(self, n: int, rate: float) -> None:
        self._paced += n
        delay = self._t0 + self._paced / rate - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        elif delay < -0.5:  # fell far behind (debugger, suspend): resynchronise
            self._t0 = time.monotonic()
            self._paced = 0


def _ofdm_wave(rate: float, bw: float, spacing: float, seed: int,
               symbols: int = 64, symbol_rate: float = 4000.0) -> np.ndarray:
    """A looped VDSL-like OFDM block: QPSK carriers every `spacing` Hz, new phases each symbol."""
    rng = np.random.default_rng(seed + 7)
    seg = max(1, int(round(rate / symbol_rate)))
    length = seg * symbols
    t = np.arange(length) / rate
    carriers = np.arange(int(bw // spacing)) * spacing
    phases = rng.integers(0, 4, (symbols, carriers.size)) * (np.pi / 2) + np.pi / 4
    sym = np.arange(length) // seg
    wave = np.zeros(length, np.complex128)
    for j, f in enumerate(carriers):
        wave += np.exp(1j * (2 * np.pi * f * t + phases[sym, j]))
    wave /= np.sqrt(np.mean(np.abs(wave) ** 2))
    return wave.astype(np.complex64)
