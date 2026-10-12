"""Shift a frequency region to baseband and decimate it onto a fixed ladder of output rates."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from ..sdr.sigmf_io import from_ci16
from .demod import Decimator, choose_decimation

RATE_LADDER = (12_500.0, 25_000.0, 50_000.0, 100_000.0, 200_000.0, 400_000.0, 800_000.0)
MIN_REGION_HZ = 100.0
_CHUNK = 1 << 20


class RegionError(ValueError):
    """The requested region cannot be extracted. The message is shown to the user."""


def validate_region(center_hz: float, rate: float, lo_hz: float | None,
                    hi_hz: float | None) -> tuple[float, float]:
    span_lo, span_hi = center_hz - rate / 2, center_hz + rate / 2
    lo = span_lo if lo_hz is None else float(lo_hz)
    hi = span_hi if hi_hz is None else float(hi_hz)
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise RegionError("the region edges must be finite frequencies")
    if hi < lo:
        lo, hi = hi, lo
    lo, hi = max(lo, span_lo), min(hi, span_hi)
    if hi - lo < MIN_REGION_HZ:
        raise RegionError(f"the region must overlap the receiver's span ({span_lo / 1e6:.4f}–"
                          f"{span_hi / 1e6:.4f} MHz) by at least {MIN_REGION_HZ:.0f} Hz")
    return lo, hi


def plan_decimation(in_rate: float, width_hz: float) -> list[int]:
    """Stage factors bringing in_rate down to the smallest ladder rate >= 1.25 x width_hz."""
    need = 1.25 * width_hz
    target = next((r for r in RATE_LADDER if r >= need), None)
    if target is None or target >= in_rate:
        return []
    # choose_decimation picks the nearest rate that splits into small stages, which can land just
    # under the ladder rate; raise the goal until the result is at or above it.
    goal = target
    for _ in range(40):
        factors = choose_decimation(in_rate, goal)
        if in_rate / math.prod(factors) >= target:
            return [f for f in factors if f > 1]
        goal *= 1.01
    return []


class SpanExtractor:
    def __init__(self, in_rate: float, center_hz: float, lo_hz: float | None = None,
                 hi_hz: float | None = None) -> None:
        self.in_rate = float(in_rate)
        self.lo_hz, self.hi_hz = validate_region(center_hz, in_rate, lo_hz, hi_hz)
        self.out_center_hz = (self.lo_hz + self.hi_hz) / 2
        self._offset = self.out_center_hz - center_hz
        factors = plan_decimation(self.in_rate, self.hi_hz - self.lo_hz)
        self._stages = [Decimator(f) for f in factors]
        self.out_rate = self.in_rate / math.prod(factors) if factors else self.in_rate
        self._phase = 0.0
        self._step = -2 * np.pi * self._offset / self.in_rate
        # The rotator for one block length is cached: live blocks all have the same size, so
        # each block costs one multiply instead of a complex exp.
        self._rot_n = -1
        self._rot = np.empty(0, np.complex64)

    def process(self, block: np.ndarray) -> np.ndarray:
        x = np.asarray(block, np.complex64)
        if x.size == 0:
            return np.empty(0, np.complex64)
        if self._offset:
            n = x.size
            if n != self._rot_n:
                self._rot = np.exp(1j * self._step * np.arange(n)).astype(np.complex64)
                self._rot_n = n
            x = x * self._rot * np.complex64(np.exp(1j * self._phase))
            self._phase = float((self._phase + self._step * n) % (2 * np.pi))
        for stage in self._stages:
            x = stage.process(x)
        return x

    def process_chunks(self, iq: np.ndarray, chunk: int = _CHUNK) -> Iterator[np.ndarray]:
        """process() over `iq` a chunk at a time, yielding each output. `iq` is complex samples or
        interleaved int16 I/Q (ci16); ci16 is converted one chunk at a time, so a long input never
        exists as complex64 all at once."""
        if iq.dtype == np.int16:
            for i in range(0, iq.size, 2 * chunk):
                yield self.process(from_ci16(iq[i:i + 2 * chunk]))
        else:
            x = np.asarray(iq, np.complex64)
            for i in range(0, x.size, chunk):
                yield self.process(x[i:i + chunk])

    def output_count(self, n_in: int) -> int:
        """Samples a new extractor returns for `n_in` input samples, however they are split up."""
        n = n_in
        for stage in self._stages:
            n = -(-n // stage.factor)
        return n


@dataclass(frozen=True)
class Extracted:
    iq: np.ndarray
    rate: float
    center_hz: float
    lo_hz: float
    hi_hz: float


def extract_span(iq: np.ndarray, in_rate: float, center_hz: float, lo_hz: float | None = None,
                 hi_hz: float | None = None) -> Extracted:
    """`iq` is complex samples or interleaved int16 I/Q (see SpanExtractor.process_chunks)."""
    ex = SpanExtractor(in_rate, center_hz, lo_hz, hi_hz)
    parts = list(ex.process_chunks(iq))
    out = np.concatenate(parts) if parts else np.empty(0, np.complex64)
    return Extracted(out, ex.out_rate, ex.out_center_hz, ex.lo_hz, ex.hi_hz)
