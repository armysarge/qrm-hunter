"""Radio state models shared by the engine and the API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

Mode = Literal["AM", "USB", "LSB", "CW"]
DirectSampling = Literal["off", "i", "q"]


class RadioState(BaseModel):
    device_id: str | None = None
    running: bool = False
    center_hz: float = 7_100_000.0
    sample_rate: float = 2_048_000.0
    gain_db: float | None = None
    ppm: int = 0
    direct_sampling: DirectSampling = "off"
    tune_hz: float = 7_100_000.0
    mode: Mode = "AM"
    bw_hz: float = 6000.0
    fft_size: int = 8192
    avg_alpha: float = 0.3
    db_min: float = -120.0
    db_max: float = -20.0
    fps: float = 25.0
    audio: bool = False

    @field_validator("fft_size")
    @classmethod
    def _fft_pow2(cls, v: int) -> int:
        if v & (v - 1) or not 1024 <= v <= 65536:
            raise ValueError("fft_size must be a power of two between 1024 and 65536")
        return v

    @model_validator(mode="after")
    def _ranges(self) -> "RadioState":
        if self.db_max <= self.db_min:
            raise ValueError("db_max must be greater than db_min")
        if not 0 < self.avg_alpha <= 1:
            raise ValueError("avg_alpha must be in (0, 1]")
        if not 1 <= self.fps <= 60:
            raise ValueError("fps must be between 1 and 60")
        if not 50 <= self.bw_hz <= 20_000:
            raise ValueError("bw_hz must be between 50 and 20000")
        if self.tune_hz <= 0 or self.center_hz <= 0 or self.sample_rate <= 0:
            raise ValueError("frequencies and sample rate must be positive")
        return self


class StatePatch(BaseModel):
    """A partial update. Only fields present in the request are applied (gain_db: null = AGC)."""

    model_config = ConfigDict(extra="forbid")

    center_hz: float | None = None
    sample_rate: float | None = None
    gain_db: float | None = None
    ppm: int | None = None
    direct_sampling: DirectSampling | None = None
    tune_hz: float | None = None
    mode: Mode | None = None
    bw_hz: float | None = None
    fft_size: int | None = None
    avg_alpha: float | None = None
    db_min: float | None = None
    db_max: float | None = None
    fps: float | None = None
    audio: bool | None = None


class Stats(BaseModel):
    dropped: int = 0
    overflows: int = 0
    frame_rate: float = 0.0
    clients: int = 0


@dataclass(frozen=True)
class SpectrumFrame:
    seq: int
    center_hz: float
    sample_rate: float
    spectrum_db: np.ndarray


@dataclass(frozen=True)
class AudioChunk:
    seq: int
    rate: float
    pcm: np.ndarray
