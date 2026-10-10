"""Any SDR with a SoapySDR module (SDRplay, Airspy, HackRF, RTL-SDR, ...). SoapySDR is optional."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from urllib.parse import parse_qsl, urlencode

import numpy as np

from .base import DeviceConfig, DeviceError, DeviceInfo, apply_limits

SOAPY_MISSING_MSG = (
    "SoapySDR is not installed, so SDRplay/Airspy/HackRF devices are unavailable. Install it with "
    "radioconda or conda-forge (soapysdr + soapysdr-module-*), PothosSDR on Windows, or your Linux "
    "distribution's python3-soapysdr package."
)
_FALLBACK_RATES = (250e3, 1.024e6, 2.048e6, 2.4e6, 3e6, 6e6, 8e6, 10e6)
_DIRECT = {"off": "0", "i": "1", "q": "2"}


def soapy_module() -> Any:
    try:
        import SoapySDR  # type: ignore[import-not-found]
    except ImportError:
        return None
    return SoapySDR


def device_id_from_args(args: dict) -> str:
    items = sorted((str(k), str(v)) for k, v in dict(args).items() if k != "label")
    return "soapy:" + urlencode(items)


def args_from_device_id(device_id: str) -> dict:
    return dict(parse_qsl(device_id.removeprefix("soapy:")))


def _info_from_args(args: dict) -> DeviceInfo:
    args = dict(args)
    driver = str(args.get("driver", "soapy"))
    return DeviceInfo(id=device_id_from_args(args), driver=driver,
                      label=str(args.get("label") or f"Soapy {driver}"), serial=str(args.get("serial", "")),
                      has_direct_sampling=driver == "rtlsdr")


def enumerate_soapy(module: Any = None) -> list[DeviceInfo]:
    mod = module or soapy_module()
    if mod is None:
        return []
    return [_info_from_args(a) for a in mod.Device.enumerate()]


class SoapyDevice:
    def __init__(self, args: dict, module: Any = None) -> None:
        self.args = dict(args)
        self.info = _info_from_args(self.args)
        self.overflows = 0
        self._module = module
        self._mod: Any = None
        self._dev: Any = None
        self._stream: Any = None

    def open(self) -> None:
        if self._dev is not None:
            return
        mod = self._module or soapy_module()
        if mod is None:
            raise DeviceError(SOAPY_MISSING_MSG)
        try:
            dev = mod.Device(self.args)
            rx = mod.SOAPY_SDR_RX
            ranges = dev.getFrequencyRange(rx, 0)
            freq_range = (min(r.minimum() for r in ranges), max(r.maximum() for r in ranges))
            g = dev.getGainRange(rx, 0)
            rates = tuple(sorted(dev.listSampleRates(rx, 0))) or _FALLBACK_RATES
            self.info = replace(self.info, freq_range=freq_range, sample_rates=rates,
                                gain_range=(g.minimum(), g.maximum()))
            stream = dev.setupStream(rx, mod.SOAPY_SDR_CF32)
            dev.activateStream(stream)
        except DeviceError:
            raise
        except Exception as exc:
            raise DeviceError(f"cannot open {self.info.label}: {exc}") from exc
        self._mod, self._dev, self._stream = mod, dev, stream

    def close(self) -> None:
        if self._dev is None:
            return
        try:
            self._dev.deactivateStream(self._stream)
            self._dev.closeStream(self._stream)
        except Exception:
            pass
        self._dev = self._stream = None

    def configure(self, cfg: DeviceConfig) -> DeviceConfig:
        if self._dev is None:
            raise DeviceError("device is not open")
        applied = apply_limits(self.info, cfg)
        dev, rx = self._dev, self._mod.SOAPY_SDR_RX
        try:
            dev.setSampleRate(rx, 0, applied.sample_rate)
            dev.setFrequency(rx, 0, applied.center_hz)
            dev.setGainMode(rx, 0, applied.gain_db is None)
            if applied.gain_db is not None:
                dev.setGain(rx, 0, applied.gain_db)
            if applied.antenna:
                dev.setAntenna(rx, 0, applied.antenna)
        except Exception as exc:
            raise DeviceError(f"{self.info.label} rejected the settings: {exc}") from exc
        for optional in (lambda: dev.setFrequencyCorrection(rx, 0, applied.ppm),
                         lambda: dev.writeSetting("biastee", "true" if applied.bias_tee else "false"),
                         lambda: self.info.has_direct_sampling
                         and dev.writeSetting("direct_samp", _DIRECT[applied.direct_sampling])):
            try:
                optional()
            except Exception:
                pass  # not every driver supports every setting
        return applied

    def read(self, n: int) -> np.ndarray:
        if self._dev is None:
            raise DeviceError("device is not open")
        mod = self._mod
        buf = np.empty(n, np.complex64)
        got = timeouts = 0
        while got < n:
            sr = self._dev.readStream(self._stream, [buf[got:]], n - got, timeoutUs=500_000)
            if sr.ret > 0:
                got += sr.ret
            elif sr.ret == mod.SOAPY_SDR_OVERFLOW:
                self.overflows += 1
            elif sr.ret == mod.SOAPY_SDR_TIMEOUT:
                timeouts += 1
                if timeouts >= 3:
                    raise DeviceError(f"no samples from {self.info.label} for 1.5 s (unplugged?)")
            else:
                raise DeviceError(f"{self.info.label} read error {sr.ret}: {mod.errToStr(sr.ret)}")
        return buf
