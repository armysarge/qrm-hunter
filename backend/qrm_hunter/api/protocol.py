"""Binary spectrum frame format shared with the frontend (see frontend/src/lib/protocol.ts)."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, replace

import numpy as np

from ..dsp.spectrum import bin_for_display, quantize
from ..engine.state import SpectrumFrame

HEADER = struct.Struct("<4sHHIdfhhH2x")
MAGIC = b"QRMS"
VERSION = 1
MIN_WIDTH, MAX_WIDTH = 16, 8192


def encode_spectrum_frame(seq: int, center_hz: float, span_hz: float, db_min: float, db_max: float,
                          bins: np.ndarray) -> bytes:
    bins = np.ascontiguousarray(bins, dtype=np.uint8)
    if bins.size > 65535:
        raise ValueError("too many bins")
    head = HEADER.pack(MAGIC, VERSION, 0, seq & 0xFFFFFFFF, float(center_hz), float(span_hz),
                       int(round(db_min)), int(round(db_max)), bins.size)
    return head + bins.tobytes()


def decode_spectrum_frame(data: bytes) -> dict:
    magic, version, flags, seq, center, span, db_min, db_max, n = HEADER.unpack_from(data, 0)
    if magic != MAGIC or version != VERSION:
        raise ValueError(f"bad frame header {magic!r} v{version}")
    bins = np.frombuffer(data, dtype=np.uint8, count=n, offset=HEADER.size)
    return {"seq": seq, "flags": flags, "center_hz": center, "span_hz": span, "db_min": db_min,
            "db_max": db_max, "n_bins": n, "bins": bins}


@dataclass(frozen=True)
class ClientView:
    width: int = 1024
    f_lo: float | None = None
    f_hi: float | None = None


def parse_view(text: str, current: ClientView) -> ClientView:
    try:
        msg = json.loads(text)
        view = current
        if "width" in msg:
            width = float(msg["width"])
            if not math.isfinite(width):
                return current
            view = replace(view, width=min(MAX_WIDTH, max(MIN_WIDTH, int(width))))
        if "f_lo" in msg and "f_hi" in msg:
            lo, hi = float(msg["f_lo"]), float(msg["f_hi"])
            if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
                return current
            view = replace(view, f_lo=lo, f_hi=hi)
        return view
    except (ValueError, TypeError, AttributeError):
        return current


def render_frame(frame: SpectrumFrame, view: ClientView, db_min: float, db_max: float) -> bytes:
    half = frame.sample_rate / 2
    lo = view.f_lo if view.f_lo is not None else frame.center_hz - half
    hi = view.f_hi if view.f_hi is not None else frame.center_hz + half
    bins, used_lo, used_hi = bin_for_display(frame.spectrum_db, frame.center_hz, frame.sample_rate,
                                             lo, hi, view.width)
    return encode_spectrum_frame(frame.seq, (used_lo + used_hi) / 2, used_hi - used_lo, db_min, db_max,
                                 quantize(bins, db_min, db_max))
