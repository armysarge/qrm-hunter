"""Plays a SigMF recording as if it were a live SDR (looping, optionally real-time paced)."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from .base import DeviceConfig, DeviceError, DeviceInfo, apply_limits
from .sigmf_io import load_iq, open_sigmf


class ReplayDevice:
    def __init__(self, path: Path, realtime: bool = True) -> None:
        self.rec = open_sigmf(path)
        r = self.rec
        self.info = DeviceInfo(
            id=f"replay:{r.meta_path}",
            driver="replay",
            label=f"Recording: {r.meta_path.name.removesuffix('.sigmf-meta')}",
            freq_range=(r.center_hz - r.sample_rate / 2, r.center_hz + r.sample_rate / 2),
            sample_rates=(r.sample_rate,),
            gain_range=(0.0, 0.0),
        )
        self.realtime = realtime
        self.overflows = 0
        self._iq: np.ndarray | None = None
        self._cfg = DeviceConfig(center_hz=r.center_hz, sample_rate=r.sample_rate)
        self._pos = 0
        self._n = 0
        self._t0 = 0.0
        self._paced = 0

    def open(self) -> None:
        if self._iq is not None:
            return
        iq = load_iq(self.rec)
        if iq.size == 0:
            raise DeviceError(f"recording {self.rec.data_path.name} is empty")
        self._iq = iq
        self._t0 = time.monotonic()
        self._paced = 0

    def close(self) -> None:
        self._iq = None

    def configure(self, cfg: DeviceConfig) -> DeviceConfig:
        if self._iq is None:
            raise DeviceError("device is not open")
        self._cfg = apply_limits(self.info, cfg)
        return self._cfg

    def read(self, n: int) -> np.ndarray:
        if self._iq is None:
            raise DeviceError("device is not open")
        idx = (self._pos + np.arange(n)) % self._iq.size
        x = np.asarray(self._iq[idx], dtype=np.complex64)
        shift = self.rec.center_hz - self._cfg.center_hz
        if shift:
            t = (self._n + np.arange(n)) / self.rec.sample_rate
            x = (x * np.exp(2j * np.pi * shift * t)).astype(np.complex64)
        self._pos = (self._pos + n) % self._iq.size
        self._n += n
        if self.realtime:
            self._paced += n
            delay = self._t0 + self._paced / self.rec.sample_rate - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        return x
