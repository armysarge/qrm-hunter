"""Every SdrDevice implementation must pass these. Hardware drivers join via -m hardware."""

import numpy as np
import pytest

from qrm_hunter.sdr.base import DeviceConfig, DeviceError
from qrm_hunter.sdr.replay import ReplayDevice
from qrm_hunter.sdr.sigmf_io import write_sigmf
from qrm_hunter.sdr.synthetic import SyntheticDevice


def _synthetic(tmp_path):
    return SyntheticDevice(realtime=False)


def _replay(tmp_path):
    rec = SyntheticDevice(realtime=False)
    rec.open()
    rec.configure(DeviceConfig(center_hz=7_100_000.0, sample_rate=2.048e6))
    meta = write_sigmf(tmp_path / "rec", rec.read(65536), 2.048e6, 7_100_000.0)
    return ReplayDevice(meta, realtime=False)


FACTORIES = {"synthetic": _synthetic, "replay": _replay}


@pytest.fixture(params=sorted(FACTORIES))
def device(request, tmp_path):
    d = FACTORIES[request.param](tmp_path)
    yield d
    d.close()


def valid_config(d) -> DeviceConfig:
    lo, hi = d.info.freq_range
    centre = 7_100_000.0 if lo <= 7_100_000.0 <= hi else (lo + hi) / 2
    return DeviceConfig(center_hz=centre, sample_rate=d.info.sample_rates[0])


def test_read_before_open_raises(device):
    with pytest.raises(DeviceError):
        device.read(1024)


def test_configure_before_open_raises(device):
    with pytest.raises(DeviceError):
        device.configure(valid_config(device))


def test_configure_returns_applied_values(device):
    device.open()
    applied = device.configure(valid_config(device))
    assert applied.sample_rate in device.info.sample_rates
    assert applied.center_hz == valid_config(device).center_hz


@pytest.mark.parametrize("n", [1, 1000, 16384])
def test_read_returns_exactly_n_complex64(device, n):
    device.open()
    device.configure(valid_config(device))
    x = device.read(n)
    assert x.dtype == np.complex64
    assert x.shape == (n,)


def test_out_of_range_centre_raises(device):
    device.open()
    with pytest.raises(DeviceError):
        device.configure(DeviceConfig(center_hz=device.info.freq_range[1] + 1e6,
                                      sample_rate=device.info.sample_rates[0]))


def test_close_is_idempotent_and_reopen_works(device):
    device.open()
    device.close()
    device.close()
    device.open()
    device.configure(valid_config(device))
    assert device.read(256).shape == (256,)
