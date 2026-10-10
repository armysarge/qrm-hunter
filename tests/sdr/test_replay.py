import json

import numpy as np
import pytest

from qrm_hunter.sdr.base import DeviceConfig, DeviceError
from qrm_hunter.sdr.replay import ReplayDevice
from qrm_hunter.sdr.sigmf_io import load_iq, open_sigmf, write_sigmf
from qrm_hunter.sdr.synthetic import Source, SyntheticDevice

RATE = 2.048e6


def record(tmp_path, sources=None, n=131072, centre=7_100_000.0):
    dev = SyntheticDevice(sources=sources, realtime=False)
    dev.open()
    dev.configure(DeviceConfig(center_hz=centre, sample_rate=RATE))
    iq = dev.read(n)
    return write_sigmf(tmp_path / "rec", iq, RATE, centre, "test"), iq


def test_write_then_open_round_trip(tmp_path):
    meta, iq = record(tmp_path)
    rec = open_sigmf(meta)
    assert (rec.datatype, rec.sample_rate, rec.center_hz, rec.num_samples) == ("cf32_le", RATE, 7_100_000.0, iq.size)
    assert np.array_equal(load_iq(rec), iq)
    assert open_sigmf(meta.with_suffix(".sigmf-data")).meta_path == meta


def test_replay_reproduces_and_loops(tmp_path):
    meta, iq = record(tmp_path, n=4096)
    d = ReplayDevice(meta, realtime=False)
    d.open()
    d.configure(DeviceConfig(center_hz=7_100_000.0, sample_rate=RATE))
    assert np.array_equal(d.read(4096), iq)
    assert np.array_equal(d.read(10), iq[:10])  # wrapped to the start


def test_retune_inside_span_shifts_signal(tmp_path):
    meta, _ = record(tmp_path, sources=[Source("cw", 7_150_000.0, -20.0)])
    d = ReplayDevice(meta, realtime=False)
    d.open()
    d.configure(DeviceConfig(center_hz=7_120_000.0, sample_rate=RATE))
    x = d.read(65536)
    k = int(np.argmax(np.abs(np.fft.fftshift(np.fft.fft(x * np.hanning(x.size))))))
    assert abs((k - 32768) * RATE / 65536 - 30_000.0) <= RATE / 65536


def test_ci16_and_cu8_are_decoded(tmp_path):
    for dtype, raw, expected in [
        ("ci16_le", np.array([16384, -16384], np.int16), 0.5 - 0.5j),
        ("cu8", np.array([255, 0], np.uint8), 1.0 - 1.0j),
    ]:
        base = tmp_path / dtype
        raw.tofile(base.with_suffix(".sigmf-data"))
        base.with_suffix(".sigmf-meta").write_text(json.dumps({
            "global": {"core:datatype": dtype, "core:sample_rate": 1e6, "core:version": "1.0.0"},
            "captures": [{"core:sample_start": 0, "core:frequency": 7e6}], "annotations": []}))
        iq = load_iq(open_sigmf(base.with_suffix(".sigmf-meta")))
        assert iq.dtype == np.complex64
        assert abs(iq[0] - expected) < 0.01


def test_unsupported_datatype_is_a_device_error(tmp_path):
    meta = tmp_path / "x.sigmf-meta"
    meta.write_text(json.dumps({"global": {"core:datatype": "rf64_le", "core:sample_rate": 1e6}, "captures": []}))
    (tmp_path / "x.sigmf-data").write_bytes(b"")
    with pytest.raises(DeviceError, match="rf64_le"):
        open_sigmf(meta)


def test_broken_meta_is_a_device_error(tmp_path):
    meta = tmp_path / "x.sigmf-meta"
    meta.write_text("{not json")
    with pytest.raises(DeviceError):
        open_sigmf(meta)
