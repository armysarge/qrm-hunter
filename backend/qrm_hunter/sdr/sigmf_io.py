"""Minimal SigMF (https://sigmf.org) reader/writer for IQ recordings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .base import DeviceError

_BYTES_PER_SAMPLE = {"cf32_le": 8, "ci16_le": 4, "cu8": 2}


@dataclass(frozen=True)
class SigmfRecording:
    meta_path: Path
    data_path: Path
    datatype: str
    sample_rate: float
    center_hz: float
    num_samples: int


def write_sigmf(base: Path, iq: np.ndarray, sample_rate: float, center_hz: float, description: str = "") -> Path:
    base = Path(base)
    data_path = base.with_name(base.name + ".sigmf-data")
    meta_path = base.with_name(base.name + ".sigmf-meta")
    np.asarray(iq, dtype=np.complex64).tofile(data_path)
    doc = {
        "global": {
            "core:datatype": "cf32_le",
            "core:sample_rate": float(sample_rate),
            "core:version": "1.0.0",
            "core:description": description,
            "core:recorder": "qrm-hunter",
        },
        "captures": [{
            "core:sample_start": 0,
            "core:frequency": float(center_hz),
            "core:datetime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }],
        "annotations": [],
    }
    meta_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return meta_path


def open_sigmf(path: Path) -> SigmfRecording:
    path = Path(path)
    stem = path.name.removesuffix(".sigmf-meta").removesuffix(".sigmf-data")
    meta_path = path.with_name(stem + ".sigmf-meta")
    data_path = path.with_name(stem + ".sigmf-data")
    try:
        doc = json.loads(meta_path.read_text(encoding="utf-8"))
        glob = doc["global"]
        datatype = glob["core:datatype"]
        sample_rate = float(glob["core:sample_rate"])
        captures = doc.get("captures") or [{}]
        center_hz = float(captures[0].get("core:frequency", 0.0))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DeviceError(f"cannot read SigMF metadata {meta_path}: {exc}") from exc
    if datatype not in _BYTES_PER_SAMPLE:
        raise DeviceError(f"unsupported SigMF datatype {datatype!r} (supported: {', '.join(_BYTES_PER_SAMPLE)})")
    try:
        num = data_path.stat().st_size // _BYTES_PER_SAMPLE[datatype]
    except OSError as exc:
        raise DeviceError(f"SigMF data file missing: {data_path}") from exc
    return SigmfRecording(meta_path, data_path, datatype, sample_rate, center_hz, num)


def load_iq(rec: SigmfRecording) -> np.ndarray:
    if rec.datatype == "cf32_le":
        if rec.num_samples == 0:
            return np.empty(0, np.complex64)
        return np.memmap(rec.data_path, dtype=np.complex64, mode="r", shape=(rec.num_samples,))
    if rec.datatype == "ci16_le":
        f = np.fromfile(rec.data_path, dtype="<i2").astype(np.float32) / 32768.0
    else:  # cu8
        f = (np.fromfile(rec.data_path, dtype=np.uint8).astype(np.float32) - 127.5) / 127.5
    return (f[0::2] + 1j * f[1::2]).astype(np.complex64)
