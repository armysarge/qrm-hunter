"""RTL-SDR (v3/v4 and clones) via pyrtlsdr. Imported lazily so a missing librtlsdr is not fatal."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

import numpy as np

from .base import DeviceConfig, DeviceError, DeviceInfo, DirectSampling, apply_limits

RTL_RATES = (250_000.0, 1_024_000.0, 1_536_000.0, 1_792_000.0, 1_920_000.0, 2_048_000.0,
             2_160_000.0, 2_400_000.0, 2_560_000.0, 2_880_000.0, 3_200_000.0)
_DIRECT = {"off": 0, "i": 1, "q": 2}
_UNSET = object()
_LIBRARY_MISSING_MSG = (
    "the RTL-SDR library (librtlsdr / pyrtlsdrlib) is not installed or could not be loaded: {exc}. "
    "Install pyrtlsdr and librtlsdr, or put librtlsdr.dll next to the application on Windows."
)
_LIBRARY_LOAD_HINTS = ("librtlsdr", "rtlsdr.dll", "cannot find", "could not find")

# librtlsdr's rtlsdr_tuner enum. The RTL-SDR Blog V4 uses an R828D behind an HF upconverter, so
# it tunes HF in normal (tuner) mode; R820T/R820T2 dongles (V3, clones) stop at about 24 MHz.
_TUNER_R820T, _TUNER_R828D = 5, 6
_TUNER_HF_FLOOR_HZ = 24_000_000.0  # R820T lowest usable frequency
_DIRECT_CEILING_HZ = 28_800_000.0  # direct sampling covers 0..fs/2 of the 28.8 MHz ADC clock


def _tuner_type(sdr: Any) -> int | None:
    """The tuner chip, or None if it cannot be read (e.g. an older pyrtlsdr)."""
    try:
        from rtlsdr.librtlsdr import librtlsdr

        return int(librtlsdr.rtlsdr_get_tuner_type(sdr.dev_p))
    except Exception:
        return None


def _hf_input(tuner: int | None, requested: DirectSampling, center_hz: float) -> DirectSampling:
    """Pick the HF input that can actually receive center_hz.

    An R820T in tuner mode cannot go below 24 MHz (librtlsdr fails with LIBUSB_ERROR_IO), and
    direct sampling cannot go above 28.8 MHz, so those combinations are switched automatically.
    Unknown tuners keep the requested input.
    """
    if tuner == _TUNER_R820T and requested == "off" and center_hz < _TUNER_HF_FLOOR_HZ:
        return "q"
    if tuner is not None and requested != "off" and center_hz > _DIRECT_CEILING_HZ:
        return "off"
    return requested


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
        self._center: Any = _UNSET
        self._gain: Any = _UNSET
        self._tuner: int | None = None

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
        self._tuner = _tuner_type(self._sdr)
        self._ppm = 0
        self._direct = None
        self._rate = None
        self._center = _UNSET
        self._gain = _UNSET

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
        applied = replace(applied, direct_sampling=_hf_input(self._tuner, applied.direct_sampling,
                                                             applied.center_hz))
        try:
            # Every write here costs librtlsdr a tuner/PLL reprogram (a click in the audio and a
            # glitch in the spectrum), so each setting is only written when it actually changes.
            # Toggling direct sampling re-initialises the tuner, so frequency and gain follow it;
            # a rate change alters the tuner's IF bandwidth, so the frequency is rewritten too.
            retuner = retune = False
            if applied.direct_sampling != self._direct:
                try:
                    sdr.set_direct_sampling(_DIRECT[applied.direct_sampling])
                except Exception:
                    # Leaving direct sampling, librtlsdr re-tunes to the *previous* (HF) frequency,
                    # which the tuner cannot reach, and reports failure although the mode did
                    # change. The new frequency is written just below; if that fails too, it raises.
                    if applied.direct_sampling != "off":
                        raise
                self._direct = applied.direct_sampling
                retuner = retune = True
            if applied.sample_rate != self._rate:
                sdr.sample_rate = applied.sample_rate
                self._rate = applied.sample_rate
                retune = True
            if retune or applied.center_hz != self._center:
                sdr.center_freq = applied.center_hz
                self._center = applied.center_hz
            if applied.ppm != self._ppm:
                sdr.freq_correction = applied.ppm
                self._ppm = applied.ppm
            if retuner or applied.gain_db != self._gain:
                sdr.gain = "auto" if applied.gain_db is None else applied.gain_db
                self._gain = applied.gain_db
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
