import json
import os
import shutil

import pytest
from qrm_hunter.config.memories import (
    MemoriesNotWritable,
    Memory,
    MemoryFields,
    MemoryStore,
    _csv_cell,
    export_csv,
    memories_path_for,
    merge_import,
    parse_import,
)


def mem(name="FT8", tune=14_074_000.0, mode="USB", bw_hz=2700, **kw):
    return Memory.new(MemoryFields(name=name, tune_hz=tune, mode=mode, bw_hz=bw_hz, **kw))


def test_missing_file_gives_empty_list(tmp_path):
    assert MemoryStore(tmp_path / "memories.json").load() == []


def test_round_trip(tmp_path):
    store = MemoryStore(tmp_path / "memories.json")
    ms = [mem(), mem("AM bcast", 6_070_000, "AM", bw_hz=6000, gain_db=None, direct_sampling="q", note="hi")]
    store.save(ms)
    assert store.load() == ms


def test_path_sits_beside_settings(tmp_path):
    assert memories_path_for(tmp_path / "s.toml") == tmp_path / "memories.json"


@pytest.mark.parametrize("raw", [b"[[[", b'[{"name": "x"}]', b'{"not": "a list"}', b"\xff\xfe"])
def test_corrupt_file_is_moved_aside(tmp_path, raw, caplog):
    path = tmp_path / "memories.json"
    path.write_bytes(raw)
    with caplog.at_level("WARNING"):
        assert MemoryStore(path).load() == []
    assert (tmp_path / "memories.json.bad").exists() and not path.exists()
    assert "moved to" in caplog.text


def test_corrupt_file_that_cannot_be_moved_aside(tmp_path, monkeypatch, caplog):
    path = tmp_path / "memories.json"
    path.write_text("[[[", encoding="utf-8")

    def busy(src, dst):
        raise PermissionError(32, "busy", str(src))

    monkeypatch.setattr(os, "replace", busy)
    with caplog.at_level("WARNING"):
        assert MemoryStore(path).load() == []
    assert "could not be moved aside" in caplog.text
    assert path.exists()


def _lock_reads(monkeypatch, path):
    def locked(self, *a, **kw):
        raise PermissionError(13, "The process cannot access the file", str(self))

    monkeypatch.setattr(type(path), "read_bytes", locked)


def test_unreadable_file_gives_empty_list_and_is_backed_up_before_save(tmp_path, monkeypatch, caplog):
    path = tmp_path / "memories.json"
    original = json.dumps([mem().model_dump()])
    path.write_text(original, encoding="utf-8")
    store = MemoryStore(path)
    _lock_reads(monkeypatch, path)
    with caplog.at_level("WARNING"):
        assert store.load() == []
    assert "cannot read memories file" in caplog.text
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == original  # left alone, not moved aside
    store.save([mem("new")])
    assert (tmp_path / "memories.json.bak").read_text(encoding="utf-8") == original
    assert [m.name for m in store.load()] == ["new"]


def test_unreadable_file_not_overwritten_when_backup_fails(tmp_path, monkeypatch, caplog):
    path = tmp_path / "memories.json"
    path.write_text("[]", encoding="utf-8")
    store = MemoryStore(path)
    _lock_reads(monkeypatch, path)
    store.load()
    monkeypatch.undo()

    def busy(*a, **kw):
        raise PermissionError(32, "busy", str(path))

    monkeypatch.setattr(shutil, "copy2", busy)
    with pytest.raises(MemoriesNotWritable, match="not overwriting"):
        store.save([mem()])
    assert path.read_text(encoding="utf-8") == "[]"


def test_save_creates_parent_and_leaves_no_temp_files(tmp_path):
    store = MemoryStore(tmp_path / "a" / "memories.json")
    store.save([mem()])
    assert [p.name for p in (tmp_path / "a").iterdir()] == ["memories.json"]


def test_parse_csv_with_empty_optionals_and_bad_rows():
    data = ("name,tune_hz,mode,bw_hz,gain_db,direct_sampling,note\n"
            "FT8,14074000,USB,2700,,,\n"
            "Bad mode,7000000,XX,2700,,,\n"
            ",7000000,AM,6000,,,\n"
            "Too many,7000000,AM,6000,,,,extra\n"
            "AM,6070000,AM,6000,20.5,q,a note\n")
    rows, bad = parse_import("csv", data)
    assert bad == 3
    assert [(r.name, r.gain_db, r.direct_sampling, r.note) for r in rows] == [
        ("FT8", None, None, ""), ("AM", 20.5, "q", "a note")]


def test_parse_json_takes_fresh_ids_and_skips_non_objects():
    src = mem()
    rows, bad = parse_import("json", json.dumps([src.model_dump(), 3, {"name": "x"}]))
    assert bad == 2 and len(rows) == 1
    assert rows[0].id != src.id and rows[0].created == src.created and rows[0].key() == src.key()


def test_parse_rejects_whole_document_errors():
    with pytest.raises(ValueError):
        parse_import("json", "{nope")
    with pytest.raises(ValueError):
        parse_import("json", '"a string"')
    with pytest.raises(ValueError):
        parse_import("csv", "foo,bar\n1,2\n")


def test_merge_skips_duplicates_and_respects_limit():
    a, b = mem("A", 1e6), mem("B", 2e6)
    merged, added, skipped = merge_import([a], [mem("A", 1e6), b, mem("C", 3e6)], replace=False, limit=2)
    assert [m.name for m in merged] == ["A", "B"] and (added, skipped) == (1, 2)
    merged, added, skipped = merge_import([a], [b, mem("B", 2e6)], replace=True)
    assert [m.name for m in merged] == ["B"] and (added, skipped) == (1, 1)


def test_parse_csv_with_utf8_bom():
    rows, bad = parse_import("csv", "﻿name,tune_hz,mode,bw_hz\nFT8,14074000,USB,2700\n")
    assert bad == 0 and [r.name for r in rows] == ["FT8"]


@pytest.mark.parametrize("text", ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx"])
def test_csv_cell_neutralises_formulas(text):
    assert _csv_cell(text) == "'" + text
    assert _csv_cell("a" + text) == "a" + text  # only a leading trigger counts
    assert _csv_cell(-5.0) == -5.0 and _csv_cell(None) == ""


def test_export_csv_formula_guard_on_text_columns():
    row = export_csv([mem(name="=HYPERLINK(1)", note="@cmd")]).splitlines()[1]
    assert row == "'=HYPERLINK(1),14074000.0,USB,2700.0,,,'@cmd"


def test_export_csv_columns():
    lines = export_csv([mem(note="x,y")]).splitlines()
    assert lines[0] == "name,tune_hz,mode,bw_hz,gain_db,direct_sampling,note"
    assert lines[1] == 'FT8,14074000.0,USB,2700.0,,,"x,y"'
