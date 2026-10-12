"""SigMF ci16 recordings with QRM Hunter extension metadata."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from .. import __version__
from ..sdr.sigmf_io import to_ci16

NS = "qrm_hunter:"
_WRITE_CHUNK = 1 << 18  # complex samples converted to ci16 per file write
FOLDER_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{6}_[0-9]+k(-[0-9]+)?$")


def folder_name(center_hz: float, when: datetime) -> str:
    return f"{when:%Y-%m-%d_%H%M%S}_{round(center_hz / 1000)}k"


def write_json_atomic(path: Path, doc) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)  # only our own temporary file
        raise


class SigmfWriter:
    """Streams IQ to iq.sigmf-data; close() writes iq.sigmf-meta."""

    def __init__(self, folder: Path, sample_rate: float, center_hz: float, t0: float, meta: dict) -> None:
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.data_path = self.folder / "iq.sigmf-data"
        self.meta_path = self.folder / "iq.sigmf-meta"
        self.sample_rate = float(sample_rate)
        self.center_hz = float(center_hz)
        self.t0 = t0
        self.meta = dict(meta)
        self.samples = 0
        self._fh = open(self.data_path, "wb")

    @property
    def bytes(self) -> int:
        return self.samples * 4

    def write(self, iq: np.ndarray) -> None:
        # Converted a chunk at a time: to_ci16's float temporaries are several times the input.
        for i in range(0, iq.size, _WRITE_CHUNK):
            part = iq[i:i + _WRITE_CHUNK]
            self._fh.write(to_ci16(part).astype("<i2", copy=False).tobytes())
            self.samples += int(part.size)

    def close(self, **final) -> Path:
        if not self._fh.closed:
            self._fh.close()
        when = datetime.fromtimestamp(self.t0, UTC).isoformat().replace("+00:00", "Z")
        info = {**self.meta, "version": __version__, "duration_s": self.samples / self.sample_rate, **final}
        callsign = info.get("callsign") or None
        glob = {
            "core:datatype": "ci16_le",
            "core:sample_rate": self.sample_rate,
            "core:version": "1.0.0",
            "core:recorder": "qrm-hunter",
            "core:description": f"QRM Hunter recording at {self.center_hz / 1e3:.1f} kHz",
            "core:extensions": [{"name": "qrm_hunter", "version": "1.0.0", "optional": True}],
            **({"core:author": callsign} if callsign else {}),
            **{NS + k: v for k, v in info.items()},
        }
        doc = {"global": glob,
               "captures": [{"core:sample_start": 0, "core:frequency": self.center_hz, "core:datetime": when}],
               "annotations": []}
        write_json_atomic(self.meta_path, doc)
        return self.meta_path
