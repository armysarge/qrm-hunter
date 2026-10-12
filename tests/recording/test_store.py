import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from qrm_hunter.recording import store as store_mod
from qrm_hunter.recording.files import SigmfWriter
from qrm_hunter.recording.store import (
    RecordingNotFound,
    RecordingStore,
    TrashError,
    default_recordings_dir,
    resolve_recordings_dir,
)
from qrm_hunter.recording.waterfall import write_products

RATE = 25_000.0


def make(store, centre, when, seconds=2.0, **meta):
    folder = store.new_folder(centre, when)
    w = SigmfWriter(folder, RATE, centre, t0=when.timestamp(), meta={"lo_hz": centre - 10_000, "hi_hz": centre + 10_000, **meta})
    w.write((0.1 * np.ones(int(seconds * RATE))).astype(np.complex64))
    w.close(truncated=False, end_reason="snapshot")
    write_products(folder, RATE, centre, "turbo")
    return folder.name


T = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)


def test_default_dir_is_under_documents():
    d = default_recordings_dir()
    assert d.parts[-2:] == ("QRM Hunter", "recordings")
    assert resolve_recordings_dir("") == d
    assert resolve_recordings_dir("~/x").name == "x"


def test_list_is_newest_first_with_details(tmp_path):
    s = RecordingStore(tmp_path / "rec")
    old = make(s, 7_100_000.0, T, band="40m")
    new = make(s, 435_000_000.0, T + timedelta(minutes=5), band="70cm")
    infos = s.list()
    assert [i.id for i in infos] == [new, old]
    i = infos[0]
    assert i.center_hz == 435_000_000.0 and i.duration_s == pytest.approx(2.0)
    assert i.span_hz == 20_000 and i.size_bytes > 4 * 50_000 and i.band == "70cm"
    assert i.device_id.startswith("replay:") and i.device_id.endswith("iq.sigmf-meta")
    assert not i.has_analysis and i.top_cause is None and i.name == new


def test_same_second_folders_get_a_suffix(tmp_path):
    s = RecordingStore(tmp_path)
    a, b = s.new_folder(7e6, T), s.new_folder(7e6, T)
    assert a.name == "2026-10-10_120000_7000k" and b.name == "2026-10-10_120000_7000k-2"


def test_list_skips_foreign_and_broken_folders(tmp_path):
    s = RecordingStore(tmp_path)
    good = make(s, 7e6, T)
    (tmp_path / "holiday photos").mkdir()
    (tmp_path / "notes.txt").write_text("x")
    half = s.new_folder(7e6, T + timedelta(minutes=1))  # still recording: data, no metadata
    (half / "iq.sigmf-data").write_bytes(b"\0" * 400)
    broken = s.new_folder(7e6, T + timedelta(minutes=2))
    (broken / "iq.sigmf-meta").write_text("{not json")
    odd_docs = ({"global": {"core:sample_rate": 1e3}, "captures": [5]}, {"global": [], "captures": []},
                {"global": {"core:sample_rate": "fast"}, "captures": [{}]}, [1, 2])
    for i, doc in enumerate(odd_docs):  # valid JSON, hand-edited into the wrong shapes
        odd = s.new_folder(7e6, T + timedelta(minutes=3 + i))
        (odd / "iq.sigmf-meta").write_text(json.dumps(doc))
    assert [i.id for i in s.list()] == [good]
    assert RecordingStore(tmp_path / "missing").list() == []


def test_analysis_marks_the_recording(tmp_path):
    s = RecordingStore(tmp_path)
    rid = make(s, 7e6, T)
    s.save_analysis(rid, json.dumps({"causes": [{"name": "Plasma TV"}]}))
    info = s.info(rid)
    assert info.has_analysis and info.top_cause == "Plasma TV"
    s.save_analysis(rid, "{broken")
    assert s.info(rid).top_cause is None


def test_rename_keeps_the_id(tmp_path):
    s = RecordingStore(tmp_path)
    rid = make(s, 7e6, T)
    assert s.rename(rid, "  Neighbour's charger  ").name == "Neighbour's charger"
    assert s.info(rid).id == rid and s.info(rid).name == "Neighbour's charger"
    for bad in ("", "   ", "x" * 81):
        with pytest.raises(ValueError):
            s.rename(rid, bad)


@pytest.mark.parametrize("bad", ["..", "../x", "C:\\Windows", "/etc", "2026-10-10_120000_7000k/../..", "nope"])
def test_bad_ids_are_not_found(tmp_path, bad):
    with pytest.raises(RecordingNotFound):
        RecordingStore(tmp_path).path(bad)


def test_trash_uses_the_recycle_bin(tmp_path, monkeypatch):
    s = RecordingStore(tmp_path)
    rid = make(s, 7e6, T)
    sent = []
    monkeypatch.setattr(store_mod, "send2trash", lambda p: sent.append(p))
    s.trash(rid)
    assert sent == [str(tmp_path / rid)]


def test_trash_failure_keeps_the_folder(tmp_path, monkeypatch):
    s = RecordingStore(tmp_path)
    rid = make(s, 7e6, T)

    def fail(_p):
        raise PermissionError(13, "no trash directory on this drive")

    monkeypatch.setattr(store_mod, "send2trash", fail)
    with pytest.raises(TrashError, match="recycle bin"):
        s.trash(rid)
    assert (tmp_path / rid / "iq.sigmf-data").exists()


def test_load_iq_is_capped(tmp_path):
    s = RecordingStore(tmp_path)
    rid = make(s, 7e6, T, seconds=3.0)
    iq, rate, centre, meta = s.load_iq(rid, max_seconds=1.0)
    assert iq.size == int(RATE) and rate == RATE and centre == 7e6
    assert meta["qrm_hunter:end_reason"] == "snapshot"


def test_reveal_uses_the_platform_opener(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(store_mod.subprocess, "Popen", lambda args: calls.append(args))
    monkeypatch.setattr(store_mod.os, "startfile", lambda p: calls.append(["startfile", p]), raising=False)
    store_mod.reveal(tmp_path)
    assert calls and str(tmp_path) in calls[0][-1]
