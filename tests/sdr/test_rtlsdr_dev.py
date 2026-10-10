import numpy as np
import pytest

from qrm_hunter.sdr.base import DeviceConfig, DeviceError
from qrm_hunter.sdr.rtlsdr_dev import RtlSdrDevice


class FakeRtl:
    def __init__(self, index):
        self.index = index
        self.ds = None
        self.sample_rate = None
        self.center_freq = None
        self.gain = None
        self.bias = None
        self.ppm_sets = []
        self.closed = False
        self.fail_read = False

    @property
    def freq_correction(self):
        return self.ppm_sets[-1] if self.ppm_sets else 0

    @freq_correction.setter
    def freq_correction(self, v):
        self.ppm_sets.append(v)

    def set_direct_sampling(self, v):
        self.ds = v

    def set_bias_tee(self, v):
        self.bias = v

    def read_samples(self, n):
        if self.fail_read:
            raise OSError("LIBUSB_ERROR_NO_DEVICE")
        return np.ones(n, dtype=np.complex128)

    def close(self):
        self.closed = True


def make():
    fakes = []

    def factory(i):
        fakes.append(FakeRtl(i))
        return fakes[-1]

    d = RtlSdrDevice(0, "00000001", factory=factory)
    d.open()
    return d, fakes


def test_configure_maps_settings():
    d, (fake,) = make()
    applied = d.configure(DeviceConfig(center_hz=7.1e6, sample_rate=2.0e6, gain_db=None, direct_sampling="q"))
    assert fake.ds == 2
    assert fake.gain == "auto"
    assert fake.center_freq == 7.1e6
    assert applied.sample_rate == 2_048_000.0  # snapped to the nearest supported rate
    assert fake.sample_rate == 2_048_000.0
    assert fake.ppm_sets == []  # ppm 0 is the default and librtlsdr rejects setting it again
    d.configure(DeviceConfig(center_hz=7.1e6, gain_db=20.0, ppm=3))
    assert (fake.gain, fake.ppm_sets, fake.ds) == (20.0, [3], 0)


def test_read_returns_complex64():
    d, _ = make()
    d.configure(DeviceConfig())
    assert d.read(512).dtype == np.complex64


def test_open_failure_explains_likely_causes():
    def boom(i):
        raise OSError("usb_claim_interface error -6")

    with pytest.raises(DeviceError, match="another program"):
        RtlSdrDevice(0, factory=boom).open()


def test_read_failure_is_a_device_error():
    d, (fake,) = make()
    d.configure(DeviceConfig())
    fake.fail_read = True
    with pytest.raises(DeviceError, match="unplugged"):
        d.read(512)


def test_close_is_idempotent():
    d, (fake,) = make()
    d.close()
    d.close()
    assert fake.closed
