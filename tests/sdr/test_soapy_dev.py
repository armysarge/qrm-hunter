from collections import namedtuple

import numpy as np
import pytest

from qrm_hunter.sdr.base import DeviceConfig, DeviceError
from qrm_hunter.sdr.soapy_dev import SoapyDevice, args_from_device_id, device_id_from_args, enumerate_soapy

SR = namedtuple("SR", "ret flags timeNs")


class FakeRange:
    def __init__(self, lo, hi):
        self.lo, self.hi = lo, hi

    def minimum(self):
        return self.lo

    def maximum(self):
        return self.hi


class FakeDevice:
    script: list = []

    def __init__(self, args):
        self.args = args
        self.settings = {}
        self.calls = []

    @staticmethod
    def enumerate():
        return [{"driver": "sdrplay", "serial": "1234", "label": "SDRplay RSP1A, Dev0"}]

    def getFrequencyRange(self, d, c):
        return [FakeRange(1e3, 2e9)]

    def listSampleRates(self, d, c):
        return [2e6, 6e6]

    def getGainRange(self, d, c):
        return FakeRange(0, 59)

    def setupStream(self, d, fmt):
        return "stream"

    def activateStream(self, s):
        pass

    def deactivateStream(self, s):
        pass

    def closeStream(self, s):
        pass

    def setSampleRate(self, d, c, r):
        self.calls.append(("rate", r))

    def setFrequency(self, d, c, f):
        self.calls.append(("freq", f))

    def setGainMode(self, d, c, auto):
        self.calls.append(("agc", auto))

    def setGain(self, d, c, g):
        self.calls.append(("gain", g))

    def setFrequencyCorrection(self, d, c, ppm):
        self.calls.append(("ppm", ppm))

    def writeSetting(self, k, v):
        self.settings[k] = v

    def readStream(self, stream, bufs, n, timeoutUs=0):
        ret = FakeDevice.script.pop(0) if FakeDevice.script else n
        if ret > 0:
            bufs[0][:ret] = 1 + 0j
        return SR(ret, 0, 0)


class FakeSoapy:
    SOAPY_SDR_RX = 1
    SOAPY_SDR_CF32 = "CF32"
    SOAPY_SDR_TIMEOUT = -1
    SOAPY_SDR_OVERFLOW = -4
    Device = FakeDevice

    @staticmethod
    def errToStr(code):
        return f"err{code}"


def test_device_id_round_trip_survives_commas_in_values():
    args = {"driver": "rtlsdr", "serial": "0001", "label": "Generic, RTL :: 0001"}
    dev_id = device_id_from_args(args)
    assert dev_id.startswith("soapy:")
    assert "label" not in dev_id
    assert args_from_device_id(dev_id) == {"driver": "rtlsdr", "serial": "0001"}


def test_enumerate_builds_infos():
    (info,) = enumerate_soapy(FakeSoapy)
    assert info.driver == "sdrplay"
    assert info.label.startswith("SDRplay RSP1A")


def test_open_reads_ranges_and_configure_maps():
    d = SoapyDevice({"driver": "sdrplay", "serial": "1234"}, module=FakeSoapy)
    d.open()
    assert d.info.freq_range == (1e3, 2e9)
    assert d.info.sample_rates == (2e6, 6e6)
    assert d.info.gain_range == (0, 59)
    applied = d.configure(DeviceConfig(center_hz=7.1e6, sample_rate=2.048e6, gain_db=30.0, ppm=2))
    assert applied.sample_rate == 2e6
    assert ("agc", False) in d._dev.calls and ("gain", 30.0) in d._dev.calls and ("ppm", 2) in d._dev.calls


def test_read_counts_overflows_and_fills_buffer():
    FakeDevice.script = [-4, 300, 724]
    d = SoapyDevice({"driver": "sdrplay"}, module=FakeSoapy)
    d.open()
    x = d.read(1024)
    assert x.dtype == np.complex64 and x.shape == (1024,)
    assert np.all(x == 1)
    assert d.overflows == 1


def test_repeated_timeouts_raise():
    FakeDevice.script = [-1, -1, -1]
    d = SoapyDevice({"driver": "sdrplay"}, module=FakeSoapy)
    d.open()
    with pytest.raises(DeviceError, match="no samples"):
        d.read(1024)


def test_missing_soapy_gives_install_hint(monkeypatch):
    import qrm_hunter.sdr.soapy_dev as mod

    monkeypatch.setattr(mod, "soapy_module", lambda: None)
    with pytest.raises(DeviceError, match="SoapySDR"):
        SoapyDevice({"driver": "rtlsdr"}).open()
