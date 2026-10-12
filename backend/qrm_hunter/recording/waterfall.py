"""Server-side waterfall.png and spectrum.json for a recording, in the app's colours."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

from ..sdr.sigmf_io import from_ci16
from .files import write_json_atomic

WATERFALL_WIDTH = 1024
MAX_ROWS = 600
MAX_FRAMES_PER_ROW = 64

_VIRIDIS = np.array([
    [68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142],
    [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37]], float) / 255


def _turbo(x: np.ndarray) -> np.ndarray:
    # polynomial approximation of the Turbo colormap, as in frontend/src/lib/colormap.ts
    r = 0.13572138 + x * (4.6153926 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943))))
    g = 0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604))))
    b = 0.1066733 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973))))
    return np.stack([r, g, b], axis=1)


def lut(name: str) -> np.ndarray:
    x = np.arange(256) / 255
    if name == "turbo":  # the waterfall variant: trimmed ends, bottom fading to black
        c = _turbo(0.05 + x * (0.84 - 0.05))
        k = np.clip(x / 0.15, 0, 1)
        c = c * np.where(x < 0.15, 0.15 + 0.85 * k * k, 1.0)[:, None]
    elif name == "viridis":
        pos = x * (len(_VIRIDIS) - 1)
        i = np.minimum(len(_VIRIDIS) - 2, np.floor(pos).astype(int))
        f = (pos - i)[:, None]
        c = _VIRIDIS[i] + (_VIRIDIS[i + 1] - _VIRIDIS[i]) * f
    else:
        c = np.stack([x, x, x], axis=1)
    return np.round(np.clip(c, 0, 1) * 255).astype(np.uint8)


def encode_png(rgb: np.ndarray) -> bytes:
    h, w, _ = rgb.shape
    raw = np.hstack([np.zeros((h, 1), np.uint8), rgb.reshape(h, 3 * w)]).tobytes()

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def write_products(folder: Path, sample_rate: float, center_hz: float, colormap: str) -> None:
    folder = Path(folder)
    data = folder / "iq.sigmf-data"
    n = data.stat().st_size // 4 if data.exists() else 0
    w = WATERFALL_WIDTH
    frames = max(1, n // w)
    rows = min(MAX_ROWS, frames)
    per_row = frames // rows
    use = min(per_row, MAX_FRAMES_PER_ROW)
    win = np.hanning(w)
    norm = 1.0 / float(win.sum()) ** 2  # a full-scale tone reads 0 dBFS
    power = np.empty((rows, w))
    # Read with plain file I/O (no memmap) so no handle stays open on Windows afterwards.
    with open(data, "rb") if n else open(folder / "iq.sigmf-meta", "rb") as fh:
        for r in range(rows):
            starts = r * per_row + np.linspace(0, per_row - 1, use).astype(int)
            block = np.zeros((use, w), np.complex64)
            for j, s in enumerate(starts):
                if n:
                    fh.seek(4 * int(s) * w)
                    x = from_ci16(np.fromfile(fh, dtype="<i2", count=2 * w))
                    block[j, :x.size] = x
            spec = np.fft.fftshift(np.fft.fft(block * win, axis=1), axes=1)
            power[r] = (np.abs(spec) ** 2).mean(axis=0) * norm
    db = 10 * np.log10(power + 1e-20)
    lo = float(np.percentile(db, 10)) - 5.0
    hi = max(float(db.max()), lo + 20.0)
    idx = np.clip(np.rint((db - lo) * (255 / (hi - lo))), 0, 255).astype(np.uint8)
    rgb = lut(colormap)[idx][::-1]  # newest row at the top, like the live waterfall
    (folder / "waterfall.png").write_bytes(encode_png(np.ascontiguousarray(rgb)))
    avg = 10 * np.log10(power.mean(axis=0) + 1e-20)
    write_json_atomic(folder / "spectrum.json", {
        "center_hz": center_hz, "sample_rate": sample_rate, "f_start_hz": center_hz - sample_rate / 2,
        "bin_hz": sample_rate / w, "db": [round(float(v), 2) for v in avg], "db_min": lo, "db_max": hi})
