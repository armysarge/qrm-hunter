import numpy as np
import pytest

from qrm_hunter.sdr.base import DeviceConfig, DeviceError
from qrm_hunter.sdr.rtlsdr_dev import RtlSdrDevice


class FakeRtl:
    def __init__(self, index):
        self.index = index
        self.ds = None
        self.ds_sets = 0
        self._rate = None
        self.rate_sets = 0
        self.center_sets = []
        self.gain_sets = []
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

    @property
    def center_freq(self):
        return self.center_sets[-1] if self.center_sets else None

    @center_freq.setter
    def center_freq(self, v):
        self.center_sets.append(v)

    @property
    def gain(self):
        return self.gain_sets[-1] if self.gain_sets else None

    @gain.setter
    def gain(self, v):
        self.gain_sets.append(v)

    @property
    def sample_rate(self):
        return self._rate

    @sample_rate.setter
    def sample_rate(self, v):
        self._rate = v
        self.rate_sets += 1

    def set_direct_sampling(self, v):
        self.ds = v
        self.ds_sets += 1

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


def test_unchanged_direct_sampling_and_rate_are_not_rewritten():
    d, (fake,) = make()
    d.configure(DeviceConfig(center_hz=7.1e6, gain_db=10.0))
    d.configure(DeviceConfig(center_hz=7.1e6, gain_db=20.0))
    assert fake.gain == 20.0  # gain is still applied on every call
    assert fake.ds_sets == 1
    assert fake.rate_sets == 1


def test_missing_library_is_reported_as_not_installed():
    def no_lib(i):
        raise ImportError("No module named 'rtlsdr'")

    with pytest.raises(DeviceError, match="not installed or could not be loaded"):
        RtlSdrDevice(0, factory=no_lib).open()


def test_library_load_oserror_is_reported_as_not_installed():
    def no_dll(i):
        raise OSError("librtlsdr.dll not found")

    with pytest.raises(DeviceError, match="not installed or could not be loaded"):
        RtlSdrDevice(0, factory=no_dll).open()


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


def test_gain_change_does_not_retune_and_unchanged_settings_are_not_rewritten():
    d, (fake,) = make()
    d.configure(DeviceConfig(center_hz=7.1e6, gain_db=10.0))
    d.configure(DeviceConfig(center_hz=7.1e6, gain_db=10.0))
    assert (fake.center_sets, fake.gain_sets) == ([7.1e6], [10.0])
    for g in (11.0, 12.0, None):  # dragging the gain slider, then auto
        d.configure(DeviceConfig(center_hz=7.1e6, gain_db=g))
    assert fake.center_sets == [7.1e6]  # no PLL re-tune per gain tick
    assert fake.gain_sets == [10.0, 11.0, 12.0, "auto"]
    d.configure(DeviceConfig(center_hz=7.2e6, gain_db=None))
    assert fake.center_sets == [7.1e6, 7.2e6] and fake.gain_sets[-1] == "auto" and len(fake.gain_sets) == 4


def test_direct_sampling_and_rate_changes_rewrite_frequency():
    d, (fake,) = make()
    d.configure(DeviceConfig(center_hz=7.1e6, gain_db=10.0))
    d.configure(DeviceConfig(center_hz=7.1e6, gain_db=10.0, direct_sampling="q"))
    assert fake.center_sets == [7.1e6, 7.1e6]  # the tuner was re-initialised
    assert fake.gain_sets == [10.0, 10.0]
    d.configure(DeviceConfig(center_hz=7.1e6, sample_rate=1_024_000.0, gain_db=10.0, direct_sampling="q"))
    assert fake.center_sets == [7.1e6] * 3
    assert fake.gain_sets == [10.0, 10.0]


def test_reopen_rewrites_everything():
    d, fakes = make()
    d.configure(DeviceConfig(center_hz=7.1e6, gain_db=10.0))
    d.close()
    d.open()
    d.configure(DeviceConfig(center_hz=7.1e6, gain_db=10.0))
    assert (fakes[1].center_sets, fakes[1].gain_sets) == ([7.1e6], [10.0])


def make_with_tuner(monkeypatch, tuner):
    import qrm_hunter.sdr.rtlsdr_dev as mod

    monkeypatch.setattr(mod, "_tuner_type", lambda sdr: tuner)
    return make()


def test_r820t_below_24mhz_switches_to_direct_q(monkeypatch):
    d, (fake,) = make_with_tuner(monkeypatch, 5)  # R820T: RTL-SDR v3 and most clones
    applied = d.configure(DeviceConfig(center_hz=7.612e6, direct_sampling="off"))
    assert (applied.direct_sampling, fake.ds) == ("q", 2)
    applied = d.configure(DeviceConfig(center_hz=144.8e6, direct_sampling="q"))
    assert (applied.direct_sampling, fake.ds) == ("off", 0)  # direct sampling can't reach 2 m
    applied = d.configure(DeviceConfig(center_hz=28.5e6, direct_sampling="q"))
    assert applied.direct_sampling == "q"  # 10 m works in either mode: keep the user's choice


def test_r828d_v4_keeps_tuner_mode_on_hf(monkeypatch):
    d, (fake,) = make_with_tuner(monkeypatch, 6)  # R828D: RTL-SDR Blog V4 (HF upconverter)
    applied = d.configure(DeviceConfig(center_hz=7.612e6, direct_sampling="off"))
    assert (applied.direct_sampling, fake.ds) == ("off", 0)


def test_unknown_tuner_keeps_requested_input(monkeypatch):
    d, (fake,) = make_with_tuner(monkeypatch, None)
    assert d.configure(DeviceConfig(center_hz=7.1e6, direct_sampling="off")).direct_sampling == "off"
