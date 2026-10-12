"""The recordings folder: list, inspect, rename, recycle, attach analyses, load IQ, reveal."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from platformdirs import user_documents_dir
from pydantic import BaseModel
from send2trash import send2trash

from ..sdr.base import DeviceError
from ..sdr.sigmf_io import from_ci16, load_iq, open_sigmf
from .files import FOLDER_RE, NS, folder_name, write_json_atomic

log = logging.getLogger(__name__)
MAX_NAME = 80


def default_recordings_dir() -> Path:
    return Path(user_documents_dir()) / "QRM Hunter" / "recordings"


def resolve_recordings_dir(setting: str) -> Path:
    return Path(setting).expanduser() if setting.strip() else default_recordings_dir()


class RecordingNotFound(LookupError):
    pass


class TrashError(OSError):
    """The recycle bin refused the folder; it is still in place."""


class RecordingInfo(BaseModel):
    id: str
    name: str
    created: str
    center_hz: float
    sample_rate: float
    lo_hz: float
    hi_hz: float
    span_hz: float
    duration_s: float
    size_bytes: int
    has_analysis: bool
    top_cause: str | None
    truncated: bool
    end_reason: str
    device_id: str
    band: str | None
    callsign: str | None


class RecordingStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    def new_folder(self, center_hz: float, when: datetime) -> Path:
        base = folder_name(center_hz, when)
        self.ensure_root()
        for i in range(1, 1000):
            folder = self.root / (base if i == 1 else f"{base}-{i}")
            try:
                folder.mkdir()
                return folder
            except FileExistsError:
                continue
        raise OSError(f"too many recordings named {base}")

    def path(self, rec_id: str) -> Path:
        if not FOLDER_RE.fullmatch(rec_id):
            raise RecordingNotFound(rec_id)
        folder = self.root / rec_id
        if not (folder / "iq.sigmf-meta").is_file():
            raise RecordingNotFound(rec_id)
        return folder

    def _read_meta(self, folder: Path) -> dict:
        """The parsed metadata; a file that cannot be read or is not a JSON object is not a recording."""
        try:
            doc = json.loads((folder / "iq.sigmf-meta").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
            raise RecordingNotFound(f"{folder.name}: unreadable metadata ({exc})") from exc
        if not isinstance(doc, dict) or not isinstance(doc.get("global"), dict):
            raise RecordingNotFound(f"{folder.name}: malformed SigMF metadata")
        return doc

    def info(self, rec_id: str) -> RecordingInfo:
        folder = self.path(rec_id)
        doc = self._read_meta(folder)
        try:
            return self._build_info(rec_id, folder, doc)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            # hand-edited or foreign metadata: anything but the expected shapes is "not a recording"
            raise RecordingNotFound(f"{rec_id}: malformed SigMF metadata ({exc!r})") from exc

    def _build_info(self, rec_id: str, folder: Path, doc: dict) -> RecordingInfo:
        g = doc["global"]
        captures = doc.get("captures")
        cap = captures[0] if isinstance(captures, list) and captures else {}
        if not isinstance(cap, dict):
            raise TypeError("captures[0] is not an object")
        rate = float(g["core:sample_rate"])
        if NS + "radio_center_hz" in g:
            float(g[NS + "radio_center_hz"])  # must be numeric; analysis reads it later
        centre = float(cap.get("core:frequency", 0.0))
        lo = float(g.get(NS + "lo_hz", centre - rate / 2))
        hi = float(g.get(NS + "hi_hz", centre + rate / 2))
        size = sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
        top = None
        analysis = folder / "analysis.json"
        if analysis.exists():
            try:
                top = json.loads(analysis.read_text(encoding="utf-8"))["causes"][0]["name"]
            except (OSError, ValueError, KeyError, IndexError, TypeError):
                top = None
        return RecordingInfo(
            id=rec_id, name=g.get(NS + "name") or rec_id, created=str(cap.get("core:datetime", "")),
            center_hz=centre, sample_rate=rate, lo_hz=lo, hi_hz=hi, span_hz=hi - lo,
            duration_s=float(g.get(NS + "duration_s", 0.0)), size_bytes=size,
            has_analysis=analysis.exists(), top_cause=top, truncated=bool(g.get(NS + "truncated", False)),
            end_reason=str(g.get(NS + "end_reason", "")), device_id=f"replay:{(folder / 'iq.sigmf-meta').resolve()}",
            band=g.get(NS + "band"), callsign=g.get(NS + "callsign"),
        )

    def list(self) -> list[RecordingInfo]:
        if not self.root.is_dir():
            return []
        out = []
        for entry in self.root.iterdir():
            if not entry.is_dir() or not FOLDER_RE.fullmatch(entry.name):
                continue
            try:
                out.append(self.info(entry.name))
            except (RecordingNotFound, OSError, ValueError, KeyError, TypeError) as exc:
                log.debug("skipping %s: %s", entry, exc)
        return sorted(out, key=lambda i: (i.created, i.id), reverse=True)

    def rename(self, rec_id: str, name: str) -> RecordingInfo:
        name = name.strip()
        if not 1 <= len(name) <= MAX_NAME:
            raise ValueError(f"the name must be 1-{MAX_NAME} characters")
        folder = self.path(rec_id)
        doc = self._read_meta(folder)
        doc["global"][NS + "name"] = name
        write_json_atomic(folder / "iq.sigmf-meta", doc)
        return self.info(rec_id)

    def trash(self, rec_id: str) -> None:
        folder = self.path(rec_id)
        try:
            send2trash(str(folder))
        except OSError as exc:
            raise TrashError(f"could not move {folder.name} to the recycle bin "
                             f"({exc.strerror or exc}); it has not been deleted") from exc

    def save_analysis(self, rec_id: str, json_text: str) -> None:
        folder = self.path(rec_id)
        tmp = folder / ".analysis.json.tmp"
        tmp.write_text(json_text, encoding="utf-8")
        os.replace(tmp, folder / "analysis.json")

    def load_iq(self, rec_id: str, max_seconds: float = 60.0) -> tuple[np.ndarray, float, float, dict]:
        folder = self.path(rec_id)
        self.info(rec_id)  # a recording whose metadata cannot be interpreted is not found
        meta = self._read_meta(folder)
        try:
            rec = open_sigmf(folder / "iq.sigmf-meta")
        except DeviceError as exc:
            raise ValueError(str(exc)) from exc
        except (KeyError, TypeError) as exc:
            raise RecordingNotFound(f"{rec_id}: malformed SigMF metadata ({exc!r})") from exc
        n = min(rec.num_samples, int(max_seconds * rec.sample_rate))
        if rec.datatype == "ci16_le":
            iq = from_ci16(np.fromfile(rec.data_path, dtype="<i2", count=2 * n))
        else:
            iq = np.asarray(load_iq(rec)[:n], np.complex64)
        return iq, rec.sample_rate, rec.center_hz, meta["global"]


def reveal(path: Path) -> None:
    """Open a folder in the platform's file manager."""
    target = str(Path(path))
    if sys.platform.startswith("win"):
        os.startfile(target)  # noqa: S606 - opens Explorer on a folder we own
    elif sys.platform == "darwin":
        subprocess.Popen(["open", target])
    else:
        subprocess.Popen(["xdg-open", target])
