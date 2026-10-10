"""Frequency memories: named tune/mode/bandwidth/gain presets stored as JSON beside the settings file."""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ..engine.state import DirectSampling, Mode

log = logging.getLogger(__name__)

MAX_MEMORIES = 1000
MAX_IMPORT_BYTES = 1_000_000
CSV_COLUMNS = ("name", "tune_hz", "mode", "bw_hz", "gain_db", "direct_sampling", "note")


class MemoriesNotWritable(OSError):
    """The memories file was never read and cannot be backed up, so it must not be overwritten."""


class MemoryFields(BaseModel):
    """What the UI sends to create a memory (POST /api/memories)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    name: str = Field(min_length=1, max_length=60)
    tune_hz: float = Field(gt=0)
    mode: Mode
    bw_hz: float = Field(ge=50, le=20_000)  # the range RadioState accepts
    gain_db: float | None = None  # None = AGC
    direct_sampling: DirectSampling | None = None  # None = leave the radio's setting alone
    note: str = Field(default="", max_length=500)


class Memory(MemoryFields):
    id: str
    created: str

    @classmethod
    def new(cls, fields: MemoryFields, created: str | None = None) -> Memory:
        return cls(**fields.model_dump(), id=str(uuid.uuid4()),
                   created=created or datetime.now(UTC).isoformat(timespec="seconds"))

    def key(self) -> tuple:
        """Identity used to skip duplicates on import."""
        return (self.tune_hz, self.mode, self.name)


class MemoryPatch(BaseModel):
    """A partial update (PATCH /api/memories/{id}); the merged result is validated as a Memory."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    tune_hz: float | None = None
    mode: Mode | None = None
    bw_hz: float | None = None
    gain_db: float | None = None
    direct_sampling: DirectSampling | None = None
    note: str | None = None


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["json", "csv"]
    data: str
    replace: bool = False


_MEMORY_LIST = TypeAdapter(list[Memory])


def sorted_memories(memories: list[Memory]) -> list[Memory]:
    return sorted(memories, key=lambda m: (m.tune_hz, m.name))


def _valid_created(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def parse_import(fmt: str, data: str) -> tuple[list[Memory], int]:
    """Parse an export file into new Memory objects. Returns (valid rows, invalid row count).
    Raises ValueError when the document as a whole cannot be read."""
    rows: list[dict]
    if fmt == "json":
        try:
            doc = json.loads(data)
        except json.JSONDecodeError as exc:
            raise ValueError(f"not valid JSON: {exc.msg}") from exc
        if isinstance(doc, dict) and isinstance(doc.get("memories"), list):
            doc = doc["memories"]
        if not isinstance(doc, list):
            raise ValueError("expected a JSON list of memories")
        rows = doc
    else:
        reader = csv.DictReader(io.StringIO(data.removeprefix("\ufeff")))  # Excel writes a UTF-8 BOM
        if reader.fieldnames is None or not {"name", "tune_hz", "mode", "bw_hz"} <= set(reader.fieldnames):
            raise ValueError("CSV needs a header row with at least name,tune_hz,mode,bw_hz")
        rows = []
        for raw in reader:
            row = {k: v for k, v in raw.items() if k in CSV_COLUMNS}
            for opt in ("gain_db", "direct_sampling"):  # empty cell = null
                if not row.get(opt):
                    row[opt] = None
            row.setdefault("note", "")
            if row["note"] is None:
                row["note"] = ""
            if None in raw:  # more cells than header columns
                row["__extra__"] = raw[None]
            rows.append(row)
    out: list[Memory] = []
    bad = 0
    for row in rows:
        if not isinstance(row, dict):
            bad += 1
            continue
        fields = {k: v for k, v in row.items() if k not in ("id", "created")}
        try:
            out.append(Memory.new(MemoryFields.model_validate(fields), _valid_created(row.get("created"))))
        except ValidationError:
            bad += 1
    return out, bad


def merge_import(existing: list[Memory], incoming: list[Memory], replace: bool,
                 limit: int = MAX_MEMORIES) -> tuple[list[Memory], int, int]:
    """Add `incoming` to `existing` (or to nothing when replace), skipping duplicates and anything
    over the limit. Returns (new list, added, skipped)."""
    result = [] if replace else list(existing)
    seen = {m.key() for m in result}
    added = skipped = 0
    for m in incoming:
        if m.key() in seen or len(result) >= limit:
            skipped += 1
            continue
        seen.add(m.key())
        result.append(m)
        added += 1
    return result, added, skipped


def export_json(memories: list[Memory]) -> str:
    return json.dumps([m.model_dump() for m in sorted_memories(memories)], indent=2)


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: object) -> object:
    """Neutralise spreadsheet formulas (OWASP CSV injection): text cells starting with
    = + - @ tab or CR get a leading apostrophe. Numbers are written as numbers."""
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def export_csv(memories: list[Memory]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for m in sorted_memories(memories):
        d = m.model_dump()
        writer.writerow([_csv_cell(d[c]) for c in CSV_COLUMNS])
    return buf.getvalue()


def memories_path_for(settings_path: Path) -> Path:
    return Path(settings_path).with_name("memories.json")


class MemoryStore:
    """JSON file of memories with the same failure handling as SettingsStore: atomic writes, and a
    load that never fails (a corrupt file is moved to .bad; an unreadable one is backed up to .bak
    before the first save overwrites it)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._unread_original = False

    def load(self) -> list[Memory]:
        try:
            return _MEMORY_LIST.validate_json(self.path.read_bytes())
        except FileNotFoundError:
            return []
        except ValidationError as exc:  # bad JSON or a bad entry
            bad = self.path.with_name(self.path.name + ".bad")
            try:
                os.replace(self.path, bad)
            except OSError as move_exc:
                log.warning("memories file %s is invalid (%s) and could not be moved aside (%s); "
                            "starting with no memories", self.path, exc, move_exc)
                self._unread_original = True
            else:
                log.warning("memories file %s is invalid (%s); moved to %s, starting with no memories",
                            self.path, exc, bad)
            return []
        except OSError as exc:
            log.warning("cannot read memories file %s (%s); starting with no memories", self.path, exc)
            self._unread_original = True
            return []

    def save(self, memories: list[Memory]) -> None:
        if self._unread_original and self.path.exists():
            bak = self.path.with_name(self.path.name + ".bak")
            try:
                shutil.copy2(self.path, bak)
            except OSError as exc:
                raise MemoriesNotWritable(f"memories file {self.path} was never read and cannot be "
                                          f"backed up ({exc}); not overwriting it") from exc
            log.warning("memories file %s was never read; backed up to %s before saving", self.path, bak)
        self._unread_original = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([m.model_dump() for m in memories], indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".memories-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
