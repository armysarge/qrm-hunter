import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from qrm_hunter.api.protocol import decode_spectrum_frame
from qrm_hunter.api.server import create_app
from qrm_hunter.config.settings import SettingsStore
from qrm_hunter.engine.session import RadioSession
from qrm_hunter.sdr.base import DeviceError
from qrm_hunter.sdr.synthetic import SYNTHETIC_INFO, SyntheticDevice


def factory(device_id):
    if device_id == "synthetic":
        return SyntheticDevice(realtime=True)
    raise DeviceError(f"unknown device id {device_id!r}")


def make_client(tmp_path, token=None, static_dir=None):
    session = RadioSession(factory)
    store = SettingsStore(tmp_path / "s.toml")
    app = create_app(session, store, token=token, static_dir=static_dir, discover_fn=lambda: [SYNTHETIC_INFO])
    return TestClient(app, base_url="http://127.0.0.1:7070"), session, store


@pytest.fixture
def client(tmp_path):
    c, session, store = make_client(tmp_path)
    with c:
        c.session, c.store = session, store
        yield c


def test_devices(client):
    assert [d["id"] for d in client.get("/api/devices").json()] == ["synthetic"]


def test_open_patch_close(client):
    r = client.post("/api/session/open", json={"device_id": "synthetic", "config": {"tune_hz": 7_074_000}})
    assert r.status_code == 200 and r.json()["running"] and r.json()["tune_hz"] == 7_074_000
    assert client.patch("/api/state", json={"mode": "USB"}).json()["bw_hz"] == 2700
    assert client.get("/api/state").json()["mode"] == "USB"
    assert client.post("/api/session/close", json={}).json()["running"] is False
    assert client.store.load().last_device_id == "synthetic"


def test_unknown_device_is_409(client):
    r = client.post("/api/session/open", json={"device_id": "nope"})
    assert r.status_code == 409 and "unknown device" in r.json()["detail"]


@pytest.mark.parametrize("body", [{"tune_hz": 5e9}, {"fft_size": 1000}, {"db_min": 0, "db_max": -10}])
def test_bad_patch_is_422_and_state_unchanged(client, body):
    client.post("/api/session/open", json={"device_id": "synthetic"})
    before = client.get("/api/state").json()
    r = client.patch("/api/state", json=body)
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]
    assert client.get("/api/state").json() == before


def test_unknown_field_is_422(client):
    assert client.patch("/api/state", json={"volume": 3}).status_code == 422


def test_spectrum_websocket_honours_view(client):
    client.post("/api/session/open", json={"device_id": "synthetic"})
    with client.websocket_connect("ws://127.0.0.1:7070/ws/spectrum") as ws:
        ws.send_text(json.dumps({"width": 512}))
        for _ in range(30):
            frame = decode_spectrum_frame(ws.receive_bytes())
            if frame["n_bins"] == 512:
                break
        assert frame["n_bins"] == 512


def test_events_websocket_starts_with_state(client):
    with client.websocket_connect("ws://127.0.0.1:7070/ws/events") as ws:
        ev = ws.receive_json()
        assert ev["type"] == "state" and "tune_hz" in ev["state"]


def test_audio_websocket_sends_format_then_pcm(client):
    client.post("/api/session/open", json={"device_id": "synthetic", "config": {"audio": True, "tune_hz": 7_245_000}})
    with client.websocket_connect("ws://127.0.0.1:7070/ws/audio") as ws:
        fmt = ws.receive_json()
        assert fmt["type"] == "format" and fmt["format"] == "s16le" and 10_000 < fmt["rate"] < 14_000
        assert len(ws.receive_bytes()) % 2 == 0


def test_disconnecting_client_leaves_radio_running(client):
    client.post("/api/session/open", json={"device_id": "synthetic"})
    with client.websocket_connect("ws://127.0.0.1:7070/ws/spectrum") as ws:
        ws.receive_bytes()
    deadline = time.monotonic() + 2
    while client.session.spectrum.subscriber_count and time.monotonic() < deadline:
        time.sleep(0.05)
    assert client.session.spectrum.subscriber_count == 0
    assert client.get("/api/state").json()["running"] is True


def test_settings_patch_persists_and_hides_token(client):
    r = client.patch("/api/settings", json={"region": 2, "colormap": "viridis"})
    assert r.status_code == 200 and "token" not in r.json()
    assert client.store.load().region == 2
    assert client.patch("/api/settings", json={"region": 7}).status_code == 422


def test_bandplan_follows_region(client):
    assert client.get("/api/bandplan").json()["bands"][3]["hi_hz"] == 7_200_000
    client.patch("/api/settings", json={"region": 2})
    body = client.get("/api/bandplan").json()
    assert body["region"] == 2 and body["bands"][3]["hi_hz"] == 7_300_000
    assert body["bands"][3]["default_hz"] == 7_150_000


def test_token_is_enforced(tmp_path):
    c, _, _ = make_client(tmp_path, token="s3cret")
    with c:
        assert c.get("/api/state").status_code == 401
        assert c.get("/api/state?token=s3cret").status_code == 200
        assert c.get("/api/state", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("ws://127.0.0.1:7070/ws/events") as ws:
                ws.receive_json()
        with c.websocket_connect("ws://127.0.0.1:7070/ws/events?token=s3cret") as ws:
            assert ws.receive_json()["type"] == "state"


def test_placeholder_page_when_frontend_not_built(tmp_path):
    c, _, _ = make_client(tmp_path, static_dir=tmp_path / "missing")
    with c:
        r = c.get("/")
        assert r.status_code == 200 and "npm run build" in r.text


def test_static_frontend_is_served(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html><title>QRM Hunter</title>")
    c, _, _ = make_client(tmp_path, token="t", static_dir=web)
    with c:
        assert "QRM Hunter" in c.get("/").text  # static files need no token


def test_non_ascii_token_is_401_not_500(tmp_path):
    c, _, _ = make_client(tmp_path, token="s3cret")
    with c:
        assert c.get("/api/state?token=%C3%A9").status_code == 401


def test_patch_device_error_is_409(client, monkeypatch):
    def boom(_patch):
        raise DeviceError("device not responding")
    monkeypatch.setattr(client.session, "update", boom)
    r = client.patch("/api/state", json={"mode": "USB"})
    assert r.status_code == 409 and "not responding" in r.json()["detail"]


def test_open_state_error_is_422(client):
    r = client.post("/api/session/open",
                    json={"device_id": "synthetic", "config": {"mode": "CW", "bw_hz": 20000}})
    assert r.status_code == 422 and r.json()["detail"]
    assert client.get("/api/state").json()["running"] is False


# --- final review fixes ------------------------------------------------------------------------


def test_saved_frequency_is_restored_at_startup(tmp_path):
    from qrm_hunter.config.settings import Settings

    SettingsStore(tmp_path / "s.toml").save(Settings(last_tune_hz=14_200_000, last_device_id="synthetic"))
    c, session, store = make_client(tmp_path)
    with c:
        state = c.get("/api/state").json()
        assert state["tune_hz"] == 14_200_000 and not state["running"]
        assert c.get("/api/settings").json()["last_device_id"] == "synthetic"
        # Start without an explicit frequency comes up on the saved one
        opened = c.post("/api/session/open", json={"device_id": "synthetic"}).json()
        assert opened["running"] and opened["tune_hz"] == 14_200_000
        assert opened["center_hz"] == 14_200_000 + opened["sample_rate"] / 4
        c.post("/api/session/close", json={})


def test_invalid_saved_frequency_falls_back_to_default(tmp_path):
    (tmp_path / "s.toml").write_text("last_tune_hz = -5\n", encoding="utf-8")
    c, _, _ = make_client(tmp_path)
    with c:
        assert c.get("/api/state").json()["tune_hz"] == 7_100_000


# --- I3: cross-site protections ------------------------------------------------------------------

WS = "ws://127.0.0.1:7070"


@pytest.mark.parametrize("host", ["127.0.0.1:7070", "localhost:5173", "[::1]:7070", "LOCALHOST", "127.0.0.1"])
def test_loopback_host_names_are_served(client, host):
    assert client.get("/api/state", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["rebind.evil.example", "rebind.evil.example:7070", "127.0.0.1.evil.example",
                                  "localhost:abc", "[::1]x", "192.168.1.5:7070", ""])
def test_foreign_host_is_refused_without_token(client, host):
    r = client.post("/api/session/open", json={"device_id": "synthetic"}, headers={"Host": host})
    assert r.status_code == 403
    assert client.session.state.running is False
    assert client.get("/", headers={"Host": host}).status_code == 403
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"{WS}/ws/events", headers={"Host": host}) as ws:
            ws.receive_json()
    assert exc.value.code == 4403


def test_token_mode_serves_any_host(tmp_path):
    c, _, _ = make_client(tmp_path, token="s3cret")
    with c:
        r = c.get("/api/state?token=s3cret", headers={"Host": "192.168.1.5:7070"})
        assert r.status_code == 200
        r = c.post("/api/session/close", json={}, headers={"Host": "pi.local:7070", "Authorization": "Bearer s3cret"})
        assert r.status_code == 200
        with c.websocket_connect("ws://pi.local:7070/ws/events?token=s3cret",
                                 headers={"Origin": "http://pi.local:7070"}) as ws:
            assert ws.receive_json()["type"] == "state"
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("ws://pi.local:7070/ws/events?token=s3cret",
                                     headers={"Origin": "https://evil.example"}) as ws:
                ws.receive_json()
        assert exc.value.code == 4403


@pytest.mark.parametrize("path", ["/ws/events", "/ws/audio", "/ws/spectrum"])
@pytest.mark.parametrize("origin", ["https://evil.example", "http://localhost:7070", "null", "http://127.0.0.1:8080"])
def test_cross_origin_websocket_is_refused(client, path, origin):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"{WS}{path}", headers={"Origin": origin}) as ws:
            ws.receive_bytes()
    assert exc.value.code == 4403


def test_same_origin_and_originless_websockets_are_allowed(client):
    with client.websocket_connect(f"{WS}/ws/events", headers={"Origin": "http://127.0.0.1:7070"}) as ws:
        assert ws.receive_json()["type"] == "state"
    with client.websocket_connect(f"{WS}/ws/events") as ws:  # no Origin: a non-browser client
        assert ws.receive_json()["type"] == "state"
    # the Vite dev proxy forwards the page's Host (localhost:5173) along with its Origin
    with client.websocket_connect("ws://localhost:5173/ws/events",
                                  headers={"Origin": "http://localhost:5173"}) as ws:
        assert ws.receive_json()["type"] == "state"


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
def test_state_changing_requests_need_json_content_type(client, ctype):
    client.post("/api/session/open", json={"device_id": "synthetic"})
    headers = {"Origin": "https://evil.example"}
    if ctype:
        headers["Content-Type"] = ctype
    r = client.post("/api/session/close", headers=headers)  # a CORS "simple request"
    assert r.status_code == 415
    assert client.session.state.running is True
    r = client.patch("/api/state", content=b'{"mode":"USB"}', headers=headers)
    assert r.status_code == 415
    assert client.post("/api/session/close", json={}).json()["running"] is False


def test_json_content_type_with_charset_is_accepted(client):
    r = client.patch("/api/state", content=b'{"mode":"USB"}',
                     headers={"Content-Type": "application/json; charset=utf-8"})
    assert r.status_code == 200 and r.json()["mode"] == "USB"


def test_cors_preflight_is_not_granted(client):
    r = client.options("/api/session/close", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


# --- frequency memories -------------------------------------------------------------------------

MEMORY = {"name": "FT8 20m", "tune_hz": 14_074_000, "mode": "USB", "bw_hz": 2700,
          "gain_db": None, "direct_sampling": None, "note": ""}
JSON_HEADERS = {"Content-Type": "application/json"}


def test_memories_crud(client, tmp_path):
    assert client.get("/api/memories").json() == []
    r = client.post("/api/memories", json=MEMORY)
    assert r.status_code == 201
    m = r.json()
    assert m["id"] and m["created"] and m["name"] == "FT8 20m"
    client.post("/api/memories", json={**MEMORY, "name": "40m", "tune_hz": 7_074_000})
    assert [x["name"] for x in client.get("/api/memories").json()] == ["40m", "FT8 20m"]  # by frequency
    r = client.patch(f"/api/memories/{m['id']}", json={"note": "evening", "gain_db": 30})
    assert r.status_code == 200 and r.json()["note"] == "evening" and r.json()["gain_db"] == 30
    assert client.delete(f"/api/memories/{m['id']}", headers=JSON_HEADERS).status_code == 204
    assert [x["name"] for x in client.get("/api/memories").json()] == ["40m"]
    saved = json.loads((tmp_path / "memories.json").read_text(encoding="utf-8"))
    assert [x["name"] for x in saved] == ["40m"]


def test_memories_survive_restart(tmp_path):
    c, _, _ = make_client(tmp_path)
    with c:
        c.post("/api/memories", json=MEMORY)
    c, _, _ = make_client(tmp_path)
    with c:
        assert [m["name"] for m in c.get("/api/memories").json()] == ["FT8 20m"]


def test_memory_404s(client):
    assert client.patch("/api/memories/nope", json={"note": "x"}).status_code == 404
    assert client.delete("/api/memories/nope", headers=JSON_HEADERS).status_code == 404


@pytest.mark.parametrize("body", [
    {**MEMORY, "name": ""}, {**MEMORY, "name": "   "}, {**MEMORY, "name": "x" * 61}, {**MEMORY, "tune_hz": 0},
    {**MEMORY, "mode": "SSB"}, {**MEMORY, "note": "x" * 501}, {**MEMORY, "direct_sampling": "z"},
    {**MEMORY, "bw_hz": 30_000}, {**MEMORY, "id": "mine"}, {k: v for k, v in MEMORY.items() if k != "mode"}])
def test_memory_validation(client, body):
    assert client.post("/api/memories", json=body).status_code == 422
    assert client.get("/api/memories").json() == []


def test_memory_patch_validation(client):
    m = client.post("/api/memories", json=MEMORY).json()
    for body in ({"name": None}, {"bw_hz": 10}, {"id": "x"}, {"created": "now"}):
        assert client.patch(f"/api/memories/{m['id']}", json=body).status_code == 422
    assert client.get("/api/memories").json() == [m]


def test_memory_export_json_and_csv(client):
    client.post("/api/memories", json={**MEMORY, "note": "a, b", "direct_sampling": "q"})
    r = client.get("/api/memories/export?format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="qrm-hunter-memories.csv"'
    assert r.text.splitlines() == ["name,tune_hz,mode,bw_hz,gain_db,direct_sampling,note",
                                   'FT8 20m,14074000.0,USB,2700.0,,q,"a, b"']
    r = client.get("/api/memories/export?format=json")
    assert r.headers["content-disposition"].startswith("attachment;") and r.json()[0]["name"] == "FT8 20m"
    assert client.get("/api/memories/export?format=xml").status_code == 422


def test_memory_import_round_trip_and_skips(client):
    client.post("/api/memories", json=MEMORY)
    csv_text = client.get("/api/memories/export?format=csv").text
    csv_text += "New,7074000,LSB,2400,,,\nBroken,abc,USB,2700,,,\n"
    r = client.post("/api/memories/import", json={"format": "csv", "data": csv_text, "replace": False})
    assert r.json() == {"added": 1, "skipped": 2}  # one duplicate, one invalid
    exported = client.get("/api/memories/export?format=json").text
    r = client.post("/api/memories/import", json={"format": "json", "data": exported, "replace": True})
    assert r.json() == {"added": 2, "skipped": 0}
    assert len(client.get("/api/memories").json()) == 2
    assert client.post("/api/memories/import", json={"format": "json", "data": "{bad"}).status_code == 422
    assert client.post("/api/memories/import", json={"format": "xml", "data": ""}).status_code == 422


def test_memory_import_size_limit(client):
    from qrm_hunter.config.memories import MAX_IMPORT_BYTES

    big = "x" * (MAX_IMPORT_BYTES + 1)
    assert client.post("/api/memories/import", json={"format": "csv", "data": big}).status_code == 413


def test_memory_count_is_capped(client):
    rows = [{**MEMORY, "name": f"m{i}", "tune_hz": 1e6 + i} for i in range(1005)]
    r = client.post("/api/memories/import", json={"format": "json", "data": json.dumps(rows)})
    assert r.json() == {"added": 1000, "skipped": 5}
    assert client.post("/api/memories", json=MEMORY).status_code == 409
    assert len(client.get("/api/memories").json()) == 1000


def test_memory_changes_are_published_as_events(client):
    with client.websocket_connect("ws://127.0.0.1:7070/ws/events") as ws:
        assert ws.receive_json()["type"] == "state"
        client.post("/api/memories", json=MEMORY)
        for _ in range(20):
            msg = ws.receive_json()
            if msg["type"] == "memories":
                break
        assert msg == {"type": "memories"}


def test_memory_writes_need_json_content_type(client):
    r = client.post("/api/memories", content=json.dumps(MEMORY), headers={"Content-Type": "text/plain"})
    assert r.status_code == 415
    m = client.post("/api/memories", json=MEMORY).json()
    assert client.delete(f"/api/memories/{m['id']}").status_code == 415
    r = client.post("/api/memories/import", content=b"{}",
                    headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 415
    assert len(client.get("/api/memories").json()) == 1


def _fail_saves(monkeypatch, exc):
    from qrm_hunter.config.memories import MemoryStore

    def fail(self, memories):
        raise exc

    monkeypatch.setattr(MemoryStore, "save", fail)


def _record_events(monkeypatch, session):
    sent = []
    monkeypatch.setattr(session.events, "publish", sent.append)
    return sent


def test_memory_save_failure_is_500_and_leaves_list_unchanged(client, monkeypatch):
    m = client.post("/api/memories", json=MEMORY).json()
    sent = _record_events(monkeypatch, client.session)
    _fail_saves(monkeypatch, PermissionError(13, "Access is denied"))
    r = client.post("/api/memories", json={**MEMORY, "name": "other"})
    assert r.status_code == 500 and "Access is denied" in r.json()["detail"]
    assert client.patch(f"/api/memories/{m['id']}", json={"note": "x"}).status_code == 500
    assert client.delete(f"/api/memories/{m['id']}", headers=JSON_HEADERS).status_code == 500
    assert client.get("/api/memories").json() == [m]
    assert sent == []


def test_memory_unbacked_file_is_503_and_not_published(client, monkeypatch):
    from qrm_hunter.config.memories import MemoriesNotWritable

    sent = _record_events(monkeypatch, client.session)
    _fail_saves(monkeypatch, MemoriesNotWritable("never read, cannot back up"))
    r = client.post("/api/memories", json=MEMORY)
    assert r.status_code == 503 and r.json()["detail"] == "settings folder not writable"
    assert client.get("/api/memories").json() == [] and sent == []


def test_replace_import_with_no_valid_rows_is_refused(client):
    client.post("/api/memories", json=MEMORY)
    bad_csv = "name,tune_hz,mode,bw_hz\nBroken,abc,USB,2700\n"
    r = client.post("/api/memories/import", json={"format": "csv", "data": bad_csv, "replace": True})
    assert r.status_code == 422
    r = client.post("/api/memories/import", json={"format": "json", "data": "[]", "replace": True})
    assert r.status_code == 422
    assert [m["name"] for m in client.get("/api/memories").json()] == ["FT8 20m"]


def test_memory_routes_need_the_token(tmp_path):
    c, _, _ = make_client(tmp_path, token="s3cret")
    with c:
        assert c.get("/api/memories").status_code == 401
        assert c.post("/api/memories", json=MEMORY).status_code == 401
        assert c.get("/api/memories/export?format=csv").status_code == 401
        assert c.post("/api/memories/import", json={"format": "csv", "data": ""}).status_code == 401
        assert c.patch("/api/memories/x", json={"note": "n"}).status_code == 401
        assert c.delete("/api/memories/x", headers=JSON_HEADERS).status_code == 401
        ok = c.get("/api/memories", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200 and ok.json() == []


def test_direct_sampling_is_ignored_not_rejected_by_devices_without_it(client):
    """A recalled direct_sampling value is not an error on a device without direct sampling: the
    engine clamps it to "off". The UI still omits it there to avoid a pointless reconfigure."""
    client.post("/api/session/open", json={"device_id": "synthetic"})
    r = client.patch("/api/state", json={"direct_sampling": "q"})
    assert r.status_code == 200 and r.json()["direct_sampling"] == "off"


# --- broadcast FM and RDS ----------------------------------------------------------------------


def wait_for_rds(client, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rds = client.get("/api/rds").json()
        if rds and rds["ps"] == "QRM DEMO":
            return rds
        time.sleep(0.1)
    raise AssertionError("no RDS from the demo station")


def test_rds_endpoint_and_events_on_subscribe(client):
    assert client.get("/api/rds").json() is None
    r = client.post("/api/session/open", json={"device_id": "synthetic",
                                               "config": {"tune_hz": 100_000_000, "mode": "WFM"}})
    assert r.status_code == 200 and r.json()["bw_hz"] == 180_000
    rds = wait_for_rds(client)
    assert rds["pi"] == "6201" and rds["pty_name"] == "Pop Music"
    with client.websocket_connect("ws://127.0.0.1:7070/ws/events") as ws:
        assert ws.receive_json()["type"] == "state"
        ev = ws.receive_json()
        assert ev["type"] == "rds" and ev["rds"]["ps"] == "QRM DEMO"
    client.patch("/api/state", json={"mode": "FM"})
    assert client.get("/api/rds").json() is None


def test_region_setting_reaches_the_session(client):
    assert client.session.region == 1
    client.patch("/api/settings", json={"region": 2})
    assert client.session.region == 2


def test_wfm_patch_validation(client):
    client.post("/api/session/open", json={"device_id": "synthetic", "config": {"tune_hz": 100_000_000}})
    assert client.patch("/api/state", json={"mode": "WFM", "bw_hz": 400_000}).status_code == 422
    assert client.patch("/api/state", json={"mode": "WFM", "bw_hz": 200_000}).json()["bw_hz"] == 200_000


def test_wfm_memory(client):
    body = {**MEMORY, "name": "QRM DEMO", "tune_hz": 100_000_000, "mode": "WFM", "bw_hz": 180_000}
    r = client.post("/api/memories", json=body)
    assert r.status_code == 201 and r.json()["mode"] == "WFM"
    assert client.post("/api/memories", json={**body, "bw_hz": 2700}).status_code == 422
    assert client.post("/api/memories", json={**MEMORY, "bw_hz": 180_000}).status_code == 422
