import concurrent.futures
import contextlib
import shutil
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from helpers import wait_until

from qrm_hunter.analysis.service import AnalysisResult, AnalysisSource
from qrm_hunter.api import server as server_mod
from qrm_hunter.api.server import create_app
from qrm_hunter.config.settings import Settings, SettingsStore
from qrm_hunter.engine.session import RadioSession
from qrm_hunter.recording import store as store_mod
from qrm_hunter.sdr.synthetic import SYNTHETIC_INFO, SyntheticDevice

BASE = "http://127.0.0.1:7070"
WS = "ws://127.0.0.1:7070"  # a loopback Host, as RequestGuard requires without a token


@contextlib.contextmanager
def ws_connect(client, url, **kwargs):
    """TestClient.websocket_connect, minus the exit-time CancelledError the client can raise when
    the handler has not finished unwinding yet on a busy machine."""
    session = client.websocket_connect(url, **kwargs)
    ws = session.__enter__()
    try:
        yield ws
    except BaseException:
        info = sys.exc_info()
        with contextlib.suppress(concurrent.futures.CancelledError):
            session.__exit__(*info)
        raise
    with contextlib.suppress(concurrent.futures.CancelledError):
        session.__exit__(None, None, None)


def make(tmp_path, **kw):
    store = SettingsStore(tmp_path / "cfg" / "s.toml")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.save(Settings(recordings_dir=str(tmp_path / "rec"), prebuffer_s=4.0))
    session = RadioSession(lambda _id: SyntheticDevice(realtime=True))
    app = create_app(session, store, discover_fn=lambda: [SYNTHETIC_INFO], **kw)
    c = TestClient(app, base_url=BASE)
    c.session, c.tmp = session, tmp_path
    return c


@pytest.fixture
def client(tmp_path):
    c = make(tmp_path)
    with c:
        yield c


def start_radio(c, buffered=None, config=None):
    body = {"device_id": "synthetic"} if config is None else {"device_id": "synthetic", "config": config}
    assert c.post("/api/session/open", json=body).status_code == 200
    if buffered:
        deadline = time.monotonic() + buffered + 3
        while time.monotonic() < deadline:
            snap = c.session.snapshot_iq()
            if snap is not None and snap.seconds >= buffered:
                return
            time.sleep(0.05)
        raise AssertionError("pre-buffer did not fill")


def fake_result(kind="live"):
    src = AnalysisSource(kind=kind, center_hz=7e6, sample_rate=25e3, lo_hz=7e6 - 1e4, hi_hz=7e6 + 1e4, seconds=2.0)
    return AnalysisResult(id="fake", created="", source=src, causes=[], features={}, timing_ms={},
                          versions={}, ranker="ok")


class SlowAnalyser:
    ranker_status = "ok"

    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()

    def analyse(self, iq, rate, center_hz, ctx, *, check, **kw):
        self.started.set()
        while not self.release.wait(0.01):
            check()
        return fake_result()


def test_snapshot_lists_and_serves_the_waterfall(client):
    start_radio(client, buffered=2.2)
    c = client.session.state.center_hz
    r = client.post("/api/record/snapshot", json={"seconds": 2.1, "lo_hz": c - 10_000, "hi_hz": c + 10_000})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["span_hz"] == 20_000 and rec["duration_s"] == pytest.approx(2.1, abs=0.05)
    listed = client.get("/api/recordings").json()
    assert [x["id"] for x in listed] == [rec["id"]]
    png = client.get(f"/api/recordings/{rec['id']}/waterfall.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"


def test_short_buffer_and_bad_region_are_422(client):
    start_radio(client)
    r = client.post("/api/record/snapshot", json={"seconds": 5})
    assert r.status_code == 422 and "at least 2 s" in r.json()["detail"]
    assert client.post("/api/analyse", json={"source": "live"}).status_code == 422
    c = client.session.state.center_hz
    assert client.post("/api/record/start", json={"lo_hz": c + 5e6, "hi_hz": c + 6e6}).status_code == 422
    assert client.post("/api/record/snapshot", json={"seconds": 0}).status_code == 422


def test_start_stop_status_and_409s(client):
    assert client.post("/api/record/start", json={}).status_code == 409  # radio not running
    start_radio(client)
    r = client.post("/api/record/start", json={"lead_in_s": 0})
    assert r.status_code == 200 and r.json()["recording"] is True
    assert client.get("/api/record/status").json()["recording"] is True
    assert client.post("/api/record/start", json={}).status_code == 409
    time.sleep(0.5)
    stopped = client.post("/api/record/stop", json={})
    assert stopped.status_code == 200 and stopped.json()["end_reason"] == "stopped"
    assert client.post("/api/record/stop", json={}).status_code == 409
    assert client.get("/api/record/status").json()["recording"] is False


def test_low_disk_is_507(tmp_path):
    with make(tmp_path, free_bytes=lambda _p: 10 * 1024 ** 2) as c:
        start_radio(c)
        r = c.post("/api/record/start", json={})
        assert r.status_code == 507 and "500 MB" in r.json()["detail"]


def test_progress_events_reach_the_websocket(client):
    start_radio(client)
    with ws_connect(client, f"{WS}/ws/events") as ws:
        client.post("/api/record/start", json={"lead_in_s": 0})
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            msg = ws.receive_json()
            if msg["type"] == "recording":
                assert msg["elapsed_s"] >= 1 and msg["bytes"] > 0
                break
        else:
            raise AssertionError("no recording event")
        client.post("/api/record/stop", json={})


def test_rename_and_recycle(client, monkeypatch):
    start_radio(client, buffered=2.2)
    rid = client.post("/api/record/snapshot", json={"seconds": 2.1}).json()["id"]
    r = client.patch(f"/api/recordings/{rid}", json={"name": "Next door's charger"})
    assert r.status_code == 200 and r.json()["name"] == "Next door's charger" and r.json()["id"] == rid
    assert client.patch(f"/api/recordings/{rid}", json={"name": " "}).status_code == 422
    bin_dir = client.tmp / "bin"
    bin_dir.mkdir()
    monkeypatch.setattr(store_mod, "send2trash", lambda p: shutil.move(p, bin_dir))
    assert client.request("DELETE", f"/api/recordings/{rid}", json={}).status_code == 204
    assert client.get("/api/recordings").json() == [] and (bin_dir / rid).is_dir()


def test_delete_reports_recycle_bin_failure(client, monkeypatch):
    start_radio(client, buffered=2.2)
    rid = client.post("/api/record/snapshot", json={"seconds": 2.1}).json()["id"]

    def refuse(_p):
        raise OSError(5, "trash not supported on this volume")

    monkeypatch.setattr(store_mod, "send2trash", refuse)
    r = client.request("DELETE", f"/api/recordings/{rid}", json={})
    assert r.status_code == 409 and "recycle bin" in r.json()["detail"]
    assert [x["id"] for x in client.get("/api/recordings").json()] == [rid]


@pytest.mark.parametrize("rid", ["nope", "2026-10-10_120000_7000k", "..", "%2E%2E"])
def test_unknown_recordings_are_404(client, rid):
    assert client.get(f"/api/recordings/{rid}/waterfall.png").status_code == 404
    assert client.patch(f"/api/recordings/{rid}", json={"name": "x"}).status_code == 404
    assert client.post("/api/analyse", json={"source": "recording", "id": rid}).status_code == 404


def test_open_folder_is_desktop_only(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(server_mod, "reveal", lambda p: opened.append(p))
    with make(tmp_path / "web") as web:
        start_radio(web, buffered=2.2)
        rid = web.post("/api/record/snapshot", json={"seconds": 2.1}).json()["id"]
        assert web.post(f"/api/recordings/{rid}/open-folder", json={}).status_code == 403
        assert web.get("/api/app").json()["desktop"] is False
    with make(tmp_path / "web", desktop=True) as desk:
        assert desk.post(f"/api/recordings/{rid}/open-folder", json={}).status_code == 204
        assert opened and opened[0].name == rid and desk.get("/api/app").json()["desktop"] is True


def test_unusable_recordings_dir_is_a_readable_error(client):
    blocker = client.tmp / "not-a-folder"
    blocker.write_text("x")
    assert client.patch("/api/settings", json={"recordings_dir": str(blocker)}).status_code == 200
    start_radio(client, buffered=2.2)
    for path, body in [("/api/record/snapshot", {"seconds": 2.1}), ("/api/record/start", {})]:
        r = client.post(path, json=body)
        assert r.status_code == 422 and "not-a-folder" in r.json()["detail"], r.text
    assert client.get("/api/state").json()["running"] is True
    assert client.get("/api/recordings").json() == []


def test_live_analysis_and_save_as_recording(client):
    start_radio(client, buffered=2.2)
    c = client.session.state.center_hz
    r = client.post("/api/analyse", json={"source": "live", "seconds": 2.1, "lo_hz": c - 50_000, "hi_hz": c + 50_000})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["source"]["kind"] == "live" and result["causes"] and "features" in result
    saved = client.post("/api/record/snapshot", json={"analysis_id": result["id"]})
    assert saved.status_code == 200
    rec = saved.json()
    assert rec["has_analysis"] and rec["top_cause"] == result["causes"][0]["name"]
    assert rec["lo_hz"] == pytest.approx(c - 50_000)
    assert client.post("/api/record/snapshot", json={"analysis_id": "gone"}).status_code == 404


def test_recording_analysis_is_saved(client):
    start_radio(client, buffered=2.2)
    c = client.session.state.center_hz
    body = {"seconds": 2.1, "lo_hz": c - 50_000, "hi_hz": c + 50_000}
    rid = client.post("/api/record/snapshot", json=body).json()["id"]
    r = client.post("/api/analyse", json={"source": "recording", "id": rid})
    assert r.status_code == 200 and r.json()["source"]["recording_id"] == rid
    info = client.get("/api/recordings").json()[0]
    assert info["has_analysis"] and info["top_cause"] == r.json()["causes"][0]["name"]
    assert client.post("/api/analyse", json={"source": "recording"}).status_code == 422


def test_second_analysis_is_409(tmp_path):
    slow = SlowAnalyser()
    with make(tmp_path, analyser=slow) as c:
        start_radio(c, buffered=2.2)
        first = {}
        t = threading.Thread(target=lambda: first.update(r=c.post("/api/analyse", json={"source": "live", "seconds": 2})))
        t.start()
        assert slow.started.wait(5)
        r = c.post("/api/analyse", json={"source": "live", "seconds": 2})
        assert r.status_code == 409 and "already running" in r.json()["detail"]
        slow.release.set()
        t.join(5)
        assert first["r"].status_code == 200


def test_analysis_timeout_is_504_and_the_worker_recovers(tmp_path, monkeypatch):
    monkeypatch.setattr(server_mod, "ANALYSIS_TIMEOUT_S", 0.2)
    slow = SlowAnalyser()
    with make(tmp_path, analyser=slow) as c:
        start_radio(c, buffered=2.2)
        r = c.post("/api/analyse", json={"source": "live", "seconds": 2})
        assert r.status_code == 504 and "longer than" in r.json()["detail"]
        time.sleep(0.1)  # the cancelled job notices at its next check
        slow.release.set()
        assert c.post("/api/analyse", json={"source": "live", "seconds": 2}).status_code == 200
        assert c.get("/api/state").json()["running"] is True


def test_app_info_and_signature_warning(tmp_path):
    (tmp_path / "cfg").mkdir()
    (tmp_path / "cfg" / "signatures.yaml").write_text("signatures: [[[", encoding="utf-8")
    with make(tmp_path) as c:
        info = c.get("/api/app").json()
        assert info["version"] and info["ranker"] == "ok"
        assert info["recordings_dir"] == str(tmp_path / "rec") and "signatures.yaml" in info["signatures_warning"]
        with ws_connect(c, f"{WS}/ws/events") as ws:
            assert ws.receive_json()["type"] == "state"
            msg = ws.receive_json()
            assert msg["type"] == "error" and msg["code"] == "signatures"


def test_new_routes_need_the_token_and_json(tmp_path):
    with make(tmp_path, token="s3cret") as c:
        assert c.get("/api/recordings").status_code == 401
        assert c.get("/api/app").status_code == 401
        auth = {"Authorization": "Bearer s3cret"}
        assert c.get("/api/recordings", headers=auth).status_code == 200
        assert c.post("/api/record/start", headers=auth, content="{}").status_code == 415
        assert c.post("/api/analyse", headers={**auth, "Content-Type": "text/plain"}, content="{}").status_code == 415
        assert c.get("/api/recordings/x/waterfall.png?token=s3cret").status_code == 404


def test_a_wfm_capture_records_and_analyses(client):
    # the 100 MHz demo station: analysis need not name anything sensible, only work
    start_radio(client, buffered=2.2, config={"tune_hz": 100_000_000, "mode": "WFM"})
    rec = client.post("/api/record/snapshot", json={"seconds": 2.1})
    assert rec.status_code == 200 and rec.json()["band"] == "FM BC", rec.text
    assert client.post("/api/analyse", json={"source": "recording", "id": rec.json()["id"]}).status_code == 200
    assert client.get("/api/state").json()["mode"] == "WFM"


def test_recording_ids_must_match_the_folder_pattern_exactly(client):
    start_radio(client, buffered=2.2)
    rid = client.post("/api/record/snapshot", json={"seconds": 2.1}).json()["id"]
    assert client.patch(f"/api/recordings/{rid}", json={"name": "ok"}).status_code == 200
    # a trailing newline and non-ASCII digits are not part of an id
    assert client.patch(f"/api/recordings/{rid}%0A", json={"name": "x"}).status_code == 404
    assert client.get(f"/api/recordings/{rid}%0A/waterfall.png").status_code == 404
    arabic = rid.translate({ord("0") + n: 0x660 + n for n in range(10)})  # Arabic-Indic digits
    assert arabic != rid
    assert client.patch(f"/api/recordings/{arabic}", json={"name": "x"}).status_code == 404
    store = store_mod.RecordingStore(client.tmp / "rec")
    for bad in (rid + "\n", arabic):
        with pytest.raises(store_mod.RecordingNotFound):
            store.path(bad)


@pytest.mark.parametrize("corrupt", ["{broken", "[]", '{"global": {}}', '{"global": {"core:sample_rate": "fast"}}',
                                     '{"global": 3, "captures": []}'])
def test_corrupt_metadata_is_not_found_never_a_500(tmp_path, corrupt):
    with make(tmp_path) as c:
        start_radio(c, buffered=2.2)
        rid = c.post("/api/record/snapshot", json={"seconds": 2.1}).json()["id"]
        (tmp_path / "rec" / rid / "iq.sigmf-meta").write_text(corrupt, encoding="utf-8")
        assert c.get("/api/recordings").json() == []
        assert c.patch(f"/api/recordings/{rid}", json={"name": "fine"}).status_code == 404
        assert c.post("/api/analyse", json={"source": "recording", "id": rid}).status_code == 404
        store = store_mod.RecordingStore(tmp_path / "rec")
        with pytest.raises(store_mod.RecordingNotFound):
            store.info(rid)


def test_start_and_stop_do_not_block_the_event_loop(client):
    start_radio(client, buffered=3.2)
    ticks = []
    stop = threading.Event()

    def ticker():
        # a request served on the loop itself would stall this heartbeat endpoint
        while not stop.is_set():
            t0 = time.monotonic()
            client.get("/api/record/status")
            ticks.append(time.monotonic() - t0)
            time.sleep(0.02)

    th = threading.Thread(target=ticker)
    th.start()
    try:
        assert client.post("/api/record/start", json={"lead_in_s": 3}).status_code == 200
        assert wait_until(lambda: client.get("/api/record/status").json()["bytes"] > 0)
        assert client.post("/api/record/stop", json={}).status_code == 200
    finally:
        stop.set()
        th.join(10)
    assert ticks and max(ticks) < 2.0
