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
    return TestClient(app), session, store


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
    assert client.post("/api/session/close").json()["running"] is False
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
    with client.websocket_connect("/ws/spectrum") as ws:
        ws.send_text(json.dumps({"width": 512}))
        for _ in range(30):
            frame = decode_spectrum_frame(ws.receive_bytes())
            if frame["n_bins"] == 512:
                break
        assert frame["n_bins"] == 512


def test_events_websocket_starts_with_state(client):
    with client.websocket_connect("/ws/events") as ws:
        ev = ws.receive_json()
        assert ev["type"] == "state" and "tune_hz" in ev["state"]


def test_audio_websocket_sends_format_then_pcm(client):
    client.post("/api/session/open", json={"device_id": "synthetic", "config": {"audio": True, "tune_hz": 7_245_000}})
    with client.websocket_connect("/ws/audio") as ws:
        fmt = ws.receive_json()
        assert fmt["type"] == "format" and fmt["format"] == "s16le" and 10_000 < fmt["rate"] < 14_000
        assert len(ws.receive_bytes()) % 2 == 0


def test_disconnecting_client_leaves_radio_running(client):
    client.post("/api/session/open", json={"device_id": "synthetic"})
    with client.websocket_connect("/ws/spectrum") as ws:
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
            with c.websocket_connect("/ws/events") as ws:
                ws.receive_json()
        with c.websocket_connect("/ws/events?token=s3cret") as ws:
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
        c.post("/api/session/close")


def test_invalid_saved_frequency_falls_back_to_default(tmp_path):
    (tmp_path / "s.toml").write_text("last_tune_hz = -5\n", encoding="utf-8")
    c, _, _ = make_client(tmp_path)
    with c:
        assert c.get("/api/state").json()["tune_hz"] == 7_100_000
