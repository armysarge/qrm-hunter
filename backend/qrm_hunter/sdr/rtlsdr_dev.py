"""RTL-SDR (v3/v4 and clones) via pyrtlsdr. Imported lazily so a missing librtlsdr is not fatal."""

from __future__ import annotations

from typing import Any, Callable

import numpy as np

from .base import DeviceConfig, DeviceError, DeviceInfo, apply_limits

RTL_RATES = (250_000.0, 1_024_000.0, 1_536_000.0, 1_792_000.0, 1_920_000.0, 2_048_000.0,
             2_160_000.0, 2_400_000.0, 2_560_000.0, 2_880_000.0, 3_200_000.0)
_DIRECT = {"off": 0, "i": 1, "q": 2}
_LIBRARY_MISSING_MSG = (
    "the RTL-SDR library (librtlsdr / pyrtlsdrlib) is not installed or could not be loaded: {exc}. "
    "Install pyrtlsdr and librtlsdr, or put librtlsdr.dll next to the application on Windows."
)
_LIBRARY_LOAD_HINTS = ("librtlsdr", "rtlsdr.dll", "cannot find", "could not find")


def _default_factory(index: int) -> Any:
    from rtlsdr import RtlSdr

    return RtlSdr(device_index=index)


def rtlsdr_info(index: int, serial: str) -> DeviceInfo:
    label = f"RTL-SDR #{index}" + (f" (SN {serial})" if serial else "")
    return DeviceInfo(id=f"rtlsdr:{index}", driver="rtlsdr", label=label, serial=serial,
                      freq_range=(500_000.0, 1_766_000_000.0), sample_rates=RTL_RATES,
                      gain_range=(0.0, 49.6), has_direct_sampling=True)


def list_rtlsdr() -> list[DeviceInfo]:
    from rtlsdr import RtlSdr

    return [rtlsdr_info(i, s) for i, s in enumerate(RtlSdr.get_device_serial_addresses())]


class RtlSdrDevice:
    def __init__(self, index: int = 0, serial: str = "",
                 factory: Callable[[int], Any] = _default_factory) -> None:
        self.info = rtlsdr_info(index, serial)
        self.overflows = 0
        self._index = index
        self._factory = factory
        self._sdr: Any = None
        self._ppm = 0
        self._direct: str | None = None
        self._rate: float | None = None

    def open(self) -> None:
        if self._sdr is not None:
            return
        try:
            self._sdr = self._factory(self._index)
        except ImportError as exc:
            raise DeviceError(_LIBRARY_MISSING_MSG.format(exc=exc)) from exc
        except OSError as exc:
            if any(hint in str(exc).lower() for hint in _LIBRARY_LOAD_HINTS):
                raise DeviceError(_LIBRARY_MISSING_MSG.format(exc=exc)) from exc
            raise DeviceError(
                f"cannot open RTL-SDR #{self._index}: {exc}. Is another program using it? "
                "On Windows the WinUSB driver must be installed with Zadig."
            ) from exc
        except Exception as exc:
            raise DeviceError(
                f"cannot open RTL-SDR #{self._index}: {exc}. Is another program using it? "
                "On Windows the WinUSB driver must be installed with Zadig."
            ) from exc
        self._ppm = 0
        self._direct = None
        self._rate = None

    def close(self) -> None:
        if self._sdr is not None:
            try:
                self._sdr.close()
            except Exception:
                pass
            self._sdr = None

    def configure(self, cfg: DeviceConfig) -> DeviceConfig:
        sdr = self._require()
        applied = apply_limits(self.info, cfg)
        try:
            # Direct sampling and sample rate re-initialise the tuner/demodulator in librtlsdr,
            # so they are only written when they actually change.
            if applied.direct_sampling != self._direct:
                sdr.set_direct_sampling(_DIRECT[applied.direct_sampling])
                self._direct = applied.direct_sampling
            if applied.sample_rate != self._rate:
                sdr.sample_rate = applied.sample_rate
                self._rate = applied.sample_rate
            sdr.center_freq = applied.center_hz
            if applied.ppm != self._ppm:
                sdr.freq_correction = applied.ppm
                self._ppm = applied.ppm
            sdr.gain = "auto" if applied.gain_db is None else applied.gain_db
            if hasattr(sdr, "set_bias_tee"):
                sdr.set_bias_tee(applied.bias_tee)
        except Exception as exc:
            raise DeviceError(f"RTL-SDR rejected the settings: {exc}") from exc
        return applied

    def read(self, n: int) -> np.ndarray:
        sdr = self._require()
        try:
            return np.asarray(sdr.read_samples(n), dtype=np.complex64)
        except Exception as exc:
            raise DeviceError(f"RTL-SDR read failed (unplugged?): {exc}") from exc

    def _require(self) -> Any:
        if self._sdr is None:
            raise DeviceError("device is not open")
        return self._sdr
