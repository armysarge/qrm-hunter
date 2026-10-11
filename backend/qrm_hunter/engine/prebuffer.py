"""A ring of the last N seconds of raw IQ, stored as interleaved int16 to quarter the memory."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from ..sdr.sigmf_io import from_ci16, to_ci16

MAX_PREBUFFER_S = 60.0
DEFAULT_PREBUFFER_S = 10.0


@dataclass(frozen=True)
class IqSnapshot:
    iq: np.ndarray  # complex64, oldest first
    center_hz: float
    sample_rate: float
    gen: int
    t0: float  # wall-clock time (time.time()) of the first sample
    end_index: int  # run-relative index one past the last sample

    @property
    def seconds(self) -> float:
        return self.iq.size / self.sample_rate


class PreBuffer:
    """Written by the reader thread, read by request threads. A change of generation, centre or
    rate empties the ring, so one snapshot never mixes samples from two tunings."""

    def __init__(self, seconds: float) -> None:
        if not 0 <= seconds <= MAX_PREBUFFER_S:
            raise ValueError(f"pre-buffer must be 0–{MAX_PREBUFFER_S:g} s, got {seconds}")
        self.seconds = float(seconds)
        self._lock = threading.Lock()
        self._buf = np.empty(0, np.int16)
        self._cap = 0  # complex samples
        self._pos = 0  # next write position (complex samples)
        self._filled = 0
        self._key: tuple[int, float, float] | None = None  # (gen, center_hz, sample_rate)
        self._t_end = 0.0
        self._end_index = 0

    @property
    def enabled(self) -> bool:
        return self.seconds > 0

    def clear(self) -> None:
        with self._lock:
            self._pos = self._filled = 0
            self._key = None

    def write(self, block: np.ndarray, gen: int, center_hz: float, sample_rate: float, index: int,
              t_end: float | None = None) -> None:
        if not self.enabled or block.size == 0:
            return
        t_end = time.time() if t_end is None else t_end
        ints = to_ci16(block)
        key = (gen, float(center_hz), float(sample_rate))
        with self._lock:
            if key != self._key:
                cap = max(1, int(round(self.seconds * sample_rate)))
                if cap != self._cap:
                    self._buf = np.zeros(2 * cap, np.int16)
                    self._cap = cap
                self._pos = self._filled = 0
                self._key = key
            cap, n = self._cap, block.size
            if n > cap:
                ints, n = ints[-2 * cap:], cap
            first = min(n, cap - self._pos)
            self._buf[2 * self._pos:2 * (self._pos + first)] = ints[:2 * first]
            if n > first:
                self._buf[:2 * (n - first)] = ints[2 * first:2 * n]
            self._pos = (self._pos + n) % cap
            self._filled = min(cap, self._filled + n)
            self._t_end = t_end
            self._end_index = index + block.size

    def snapshot(self, seconds: float | None = None) -> IqSnapshot | None:
        with self._lock:
            if self._key is None or self._filled == 0:
                return None
            gen, center, rate = self._key
            n = self._filled if seconds is None else min(self._filled, max(1, int(round(seconds * rate))))
            start = (self._pos - n) % self._cap
            if start + n <= self._cap:
                raw = self._buf[2 * start:2 * (start + n)].copy()
            else:
                raw = np.concatenate([self._buf[2 * start:], self._buf[:2 * (start + n - self._cap)]])
            t_end, end_index = self._t_end, self._end_index
        return IqSnapshot(from_ci16(raw), center, rate, gen, t_end - n / rate, end_index)
