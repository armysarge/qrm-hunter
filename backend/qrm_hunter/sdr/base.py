"""The one interface every SDR backend implements."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Protocol, runtime_checkable

import numpy as np

DirectSampling = Literal["off", "i", "q"]


class DeviceError(RuntimeError):
    """A device could not be opened, configured or read. The message is shown to the user."""


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    driver: str
    label: str
    serial: str = ""
    freq_range: tuple[float, float] = (0.0, 2e9)
    sample_rates: tuple[float, ...] = (2.048e6,)
    gain_range: tuple[float, float] = (0.0, 50.0)
    has_direct_sampling: bool = False


@dataclass(frozen=True)
class DeviceConfig:
    center_hz: float = 7_100_000.0
    sample_rate: float = 2_048_000.0
    gain_db: float | None = None  # None = hardware AGC
    ppm: int = 0
    direct_sampling: DirectSampling = "off"
    bias_tee: bool = False
    antenna: str | None = None


@runtime_checkable
class SdrDevice(Protocol):
    info: DeviceInfo

    def open(self) -> None: ...

    def close(self) -> None: ...

    def configure(self, cfg: DeviceConfig) -> DeviceConfig: ...

    def read(self, n: int) -> np.ndarray: ...


def apply_limits(info: DeviceInfo, cfg: DeviceConfig) -> DeviceConfig:
    """Validate the centre frequency and snap the other fields to what the device supports."""
    lo, hi = info.freq_range
    if not lo <= cfg.center_hz <= hi:
        raise DeviceError(
            f"{cfg.center_hz / 1e6:.6f} MHz is outside {info.label}'s range "
            f"{lo / 1e6:.3f}–{hi / 1e6:.3f} MHz"
        )
    rate = min(info.sample_rates, key=lambda r: abs(r - cfg.sample_rate))
    gain = None
    if cfg.gain_db is not None:
        gain = float(min(max(cfg.gain_db, info.gain_range[0]), info.gain_range[1]))
    ds = cfg.direct_sampling if info.has_direct_sampling else "off"
    return replace(cfg, sample_rate=float(rate), gain_db=gain, direct_sampling=ds)
