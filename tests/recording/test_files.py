import json
import struct
import zlib
from datetime import UTC, datetime

import numpy as np
import pytest

from qrm_hunter.recording.files import FOLDER_RE, SigmfWriter, folder_name
from qrm_hunter.recording.waterfall import encode_png, lut, write_products
from qrm_hunter.sdr.sigmf_io import load_iq, open_sigmf

RATE = 25_000.0
C = 7_100_000.0


def tone_iq(seconds=2.0, offset=5_000.0, amp=0.3):
    n = int(seconds * RATE)
    rng = np.random.default_rng(0)
    x = amp * np.exp(2j * np.pi * offset * np.arange(n) / RATE) + 1e-3 * (rng.normal(size=n) + 1j * rng.normal(size=n))
    return x.astype(np.complex64)


def decode_png(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    idat, pos = b"", 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        if tag == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, 1 + 3 * w)
    assert (raw[:, 0] == 0).all()
    return raw[:, 1:].reshape(h, w, 3)


def test_folder_name():
    name = folder_name(7_100_400.0, datetime(2026, 10, 10, 14, 30, 5, tzinfo=UTC))
    assert name == "2026-10-10_143005_7100k" and FOLDER_RE.match(name)
    assert FOLDER_RE.match("2026-10-10_143005_435000k-2") and not FOLDER_RE.match("../etc")


def test_sigmf_round_trip_through_the_existing_reader(tmp_path):
    iq = tone_iq()
    w = SigmfWriter(tmp_path / "rec", RATE, C, t0=1_760_000_000.0, meta={"band": "40m", "callsign": "ZS6SHZ"})
    w.write(iq[:20_000])
    w.write(iq[20_000:])
    assert w.samples == iq.size and w.bytes == 4 * iq.size
    meta_path = w.close(truncated=False, end_reason="snapshot")
    rec = open_sigmf(meta_path)
    assert (rec.datatype, rec.sample_rate, rec.center_hz, rec.num_samples) == ("ci16_le", RATE, C, iq.size)
    assert np.max(np.abs(load_iq(rec) - iq)) <= 1 / 32767
    glob = json.loads(meta_path.read_text(encoding="utf-8"))["global"]
    assert glob["qrm_hunter:band"] == "40m" and glob["qrm_hunter:callsign"] == "ZS6SHZ"
    assert glob["qrm_hunter:truncated"] is False and glob["qrm_hunter:end_reason"] == "snapshot"
    assert glob["qrm_hunter:duration_s"] == pytest.approx(2.0)
    assert glob["core:author"] == "ZS6SHZ" and glob["qrm_hunter:version"]
    assert glob["core:extensions"][0]["name"] == "qrm_hunter"


def test_lut_matches_the_frontend_colormaps():
    grey = lut("grey")
    assert grey.shape == (256, 3) and tuple(grey[0]) == (0, 0, 0) and tuple(grey[255]) == (255, 255, 255)
    viridis = lut("viridis")
    assert tuple(viridis[0]) == (68, 1, 84) and tuple(viridis[255]) == (253, 231, 37)
    turbo = lut("turbo")
    assert int(turbo[0].sum()) < 60
    assert turbo[255][0] > turbo[255][1] > turbo[255][2]


def test_png_encoder_round_trip():
    rgb = np.random.default_rng(1).integers(0, 255, (5, 7, 3), dtype=np.uint8)
    assert np.array_equal(decode_png(encode_png(rgb)), rgb)


def test_waterfall_and_spectrum_products(tmp_path):
    w = SigmfWriter(tmp_path / "rec", RATE, C, t0=0.0, meta={})
    w.write(tone_iq())
    w.close(truncated=False, end_reason="snapshot")
    write_products(tmp_path / "rec", RATE, C, "turbo")
    img = decode_png((tmp_path / "rec" / "waterfall.png").read_bytes())
    assert img.shape[1] == 1024 and 1 <= img.shape[0] <= 600
    spec = json.loads((tmp_path / "rec" / "spectrum.json").read_text(encoding="utf-8"))
    db = np.array(spec["db"])
    peak_hz = spec["f_start_hz"] + int(np.argmax(db)) * spec["bin_hz"]
    assert abs(peak_hz - (C + 5_000.0)) <= 2 * spec["bin_hz"]
    assert spec["db_min"] < spec["db_max"]


def test_products_for_an_empty_recording(tmp_path):
    w = SigmfWriter(tmp_path / "rec", RATE, C, t0=0.0, meta={})
    w.close(truncated=True, end_reason="closed")
    write_products(tmp_path / "rec", RATE, C, "grey")
    assert decode_png((tmp_path / "rec" / "waterfall.png").read_bytes()).shape[0] == 1
