import struct

import numpy as np

from qrm_hunter.api.protocol import (HEADER, ClientView, decode_spectrum_frame, encode_spectrum_frame,
                                     parse_view, render_frame)
from qrm_hunter.engine.state import SpectrumFrame


def test_header_layout_is_pinned():
    assert HEADER.size == 32
    data = encode_spectrum_frame(7, 7_100_000.0, 192_000.0, -120, -20, np.array([0, 128, 255], np.uint8))
    assert data[:4] == b"QRMS"
    assert struct.unpack_from("<H", data, 4)[0] == 1
    assert struct.unpack_from("<I", data, 8)[0] == 7
    assert struct.unpack_from("<d", data, 12)[0] == 7_100_000.0
    assert struct.unpack_from("<f", data, 20)[0] == 192_000.0
    assert struct.unpack_from("<hhH", data, 24) == (-120, -20, 3)
    assert data[32:] == bytes([0, 128, 255])


def test_round_trip():
    d = decode_spectrum_frame(encode_spectrum_frame(1, 14e6, 2e6, -130, -30, np.arange(10, dtype=np.uint8)))
    assert (d["seq"], d["center_hz"], d["n_bins"]) == (1, 14e6, 10)
    assert d["bins"].tolist() == list(range(10))


def test_parse_view_validates():
    cur = ClientView()
    assert parse_view('{"width": 640, "f_lo": 7e6, "f_hi": 7.1e6}', cur) == ClientView(640, 7e6, 7.1e6)
    assert parse_view("not json", cur) is cur
    assert parse_view('{"width": 999999}', cur).width == 8192
    assert parse_view('{"width": 2}', cur).width == 16
    assert parse_view('{"f_lo": 8e6, "f_hi": 7e6}', cur) == cur  # inverted range ignored


def test_parse_view_rejects_non_finite():
    cur = ClientView()
    assert parse_view('{"f_lo": NaN, "f_hi": 7e6}', cur) == cur
    assert parse_view('{"f_lo": 7e6, "f_hi": Infinity}', cur) == cur
    assert parse_view('{"width": Infinity}', cur) == cur
    assert parse_view('{"width": NaN}', cur) == cur


def test_render_frame_uses_view_width_and_reports_real_span():
    spec = np.full(8192, -80.0, np.float32)
    frame = SpectrumFrame(3, 7_612_000.0, 2_048_000.0, spec)
    d = decode_spectrum_frame(render_frame(frame, ClientView(512, 7.0e6, 7.2e6), -120, -20))
    assert d["n_bins"] == 512
    assert abs(d["span_hz"] - 200_000) < 2 * 250
    assert abs(d["center_hz"] - 7.1e6) < 250
    assert set(d["bins"].tolist()) == {102}  # (-80+120)/100*255 = 102
