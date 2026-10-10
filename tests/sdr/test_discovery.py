import pytest

from qrm_hunter.sdr.base import DeviceError
from qrm_hunter.sdr.discovery import create_device, discover
from qrm_hunter.sdr.replay import ReplayDevice
from qrm_hunter.sdr.synthetic import SYNTHETIC_INFO, SyntheticDevice


def test_failing_backend_does_not_hide_others():
    def broken():
        raise OSError("librtlsdr.dll not found")

    infos = discover([("synthetic", lambda: [SYNTHETIC_INFO]), ("rtlsdr", broken)])
    assert [i.id for i in infos] == ["synthetic"]


def test_default_discover_always_lists_synthetic():
    assert "synthetic" in [i.id for i in discover()]


def test_create_known_devices(tmp_path):
    assert isinstance(create_device("synthetic"), SyntheticDevice)
    from qrm_hunter.sdr.base import DeviceConfig
    from qrm_hunter.sdr.sigmf_io import write_sigmf

    dev = SyntheticDevice(realtime=False)
    dev.open()
    dev.configure(DeviceConfig())
    meta = write_sigmf(tmp_path / "r", dev.read(4096), 2.048e6, 7.1e6)
    assert isinstance(create_device(f"replay:{meta}"), ReplayDevice)


@pytest.mark.parametrize("bad", ["bogus", "rtlsdr:abc", ""])
def test_unknown_ids_raise(bad):
    with pytest.raises(DeviceError):
        create_device(bad)
