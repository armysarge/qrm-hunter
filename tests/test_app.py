import logging
import sys
import threading
import time
import types

from fastapi import FastAPI

import qrm_hunter.app as app_mod
from qrm_hunter.app import (
    RedactTokenFilter,
    build_app,
    is_loopback,
    parse_args,
    redaction_log_config,
    resolve_token,
    run_desktop,
    server_kwargs,
    ui_url,
)
from qrm_hunter.config.settings import SettingsStore


def test_is_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.10")


def test_token_only_for_non_loopback_and_is_persisted(tmp_path):
    store = SettingsStore(tmp_path / "s.toml")
    assert resolve_token("127.0.0.1", store) is None
    tok = resolve_token("0.0.0.0", store)
    assert tok and len(tok) >= 20
    assert resolve_token("0.0.0.0", store) == tok
    assert store.load().token == tok


def test_ui_url():
    assert ui_url("127.0.0.1", 7070, None) == "http://127.0.0.1:7070/"
    assert ui_url("0.0.0.0", 7070, "abc") == "http://127.0.0.1:7070/?token=abc"


def test_defaults():
    args = parse_args([])
    assert (args.serve, args.host, args.port) == (False, "127.0.0.1", 7070)


def test_build_app(tmp_path):
    app, token = build_app(parse_args(["--serve", "--config", str(tmp_path / "s.toml")]))
    assert isinstance(app, FastAPI) and token is None


def test_server_kwargs_disable_access_log_when_token_in_use():
    args = parse_args(["--serve", "--host", "0.0.0.0", "--port", "7072"])
    kwargs = server_kwargs(args, "secret-token-value-123")
    assert kwargs["access_log"] is False
    assert (kwargs["host"], kwargs["port"]) == ("0.0.0.0", 7072)


def test_server_kwargs_keep_access_log_on_loopback():
    args = parse_args(["--serve"])
    assert server_kwargs(args, None)["access_log"] is True


def test_ui_url_brackets_ipv6():
    assert ui_url("::1", 7070, None) == "http://[::1]:7070/"
    assert ui_url("::1", 7070, "abc") == "http://[::1]:7070/?token=abc"


def _redact(record: logging.LogRecord) -> str:
    assert RedactTokenFilter().filter(record)
    return logging.Formatter("%(levelname)s %(message)s").format(record)


def test_filter_redacts_token_in_websocket_log_line():
    record = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1,
        '%s - "WebSocket %s" [accepted]', ("127.0.0.1:5000", "/ws/events?token=abc123"), None)
    line = _redact(record)
    assert "abc123" not in line
    assert "token=***" in line and "/ws/events" in line


def test_filter_redacts_token_in_plain_message_and_keeps_other_text():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1,
                               "GET /api/x?token=s3cret&foo=1 200", (), None)
    line = _redact(record)
    assert "s3cret" not in line and "foo=1" in line


def test_filter_leaves_untokened_records_alone():
    record = logging.LogRecord("uvicorn.error", logging.INFO, __file__, 1, "Uvicorn running on %s", ("x",), None)
    assert _redact(record) == "INFO Uvicorn running on x"


def test_redaction_log_config_only_when_token_set():
    assert redaction_log_config(None) is None
    cfg = redaction_log_config("tok")
    assert "redact_token" in cfg["filters"]
    for name in ("default", "access"):
        assert "redact_token" in cfg["handlers"][name]["filters"]


def test_server_kwargs_installs_redaction_only_with_token():
    args = parse_args(["--serve", "--host", "0.0.0.0"])
    assert "log_config" in server_kwargs(args, "tok")
    assert "log_config" not in server_kwargs(args, None)


class _FakeServer:
    instances: list = []

    def __init__(self, config):
        self.config = config
        self.started = False
        self.should_exit = False
        _FakeServer.instances.append(self)

    def run(self):
        self.started = True
        while not self.should_exit:
            time.sleep(0.01)


def _fake_webview(start_error: Exception | None) -> types.ModuleType:
    mod = types.ModuleType("webview")

    def start():
        if start_error is not None:
            raise start_error

    mod.create_window = lambda *a, **k: None
    mod.start = start
    return mod


def _uvicorn_threads_alive() -> bool:
    return any(t.name == "uvicorn" and t.is_alive() for t in threading.enumerate())


def test_run_desktop_signals_fallback_and_stops_server_when_window_fails(monkeypatch):
    _FakeServer.instances.clear()
    monkeypatch.setattr(app_mod.uvicorn, "Server", _FakeServer)
    monkeypatch.setitem(sys.modules, "webview", _fake_webview(RuntimeError("no GUI backend")))
    args = parse_args(["--port", "7075"])
    assert run_desktop(FastAPI(), args) is False
    assert len(_FakeServer.instances) == 1
    assert _FakeServer.instances[0].should_exit is True
    assert not _uvicorn_threads_alive()


def test_run_desktop_returns_true_when_window_closes_normally(monkeypatch):
    _FakeServer.instances.clear()
    monkeypatch.setattr(app_mod.uvicorn, "Server", _FakeServer)
    monkeypatch.setitem(sys.modules, "webview", _fake_webview(None))
    assert run_desktop(FastAPI(), parse_args(["--port", "7075"])) is True
    assert _FakeServer.instances[0].should_exit is True
    assert not _uvicorn_threads_alive()


def test_run_desktop_falls_back_without_starting_server_when_webview_missing(monkeypatch):
    _FakeServer.instances.clear()
    monkeypatch.setattr(app_mod.uvicorn, "Server", _FakeServer)
    monkeypatch.setitem(sys.modules, "webview", None)  # makes `import webview` raise ImportError
    assert run_desktop(FastAPI(), parse_args(["--port", "7075"])) is False
    assert _FakeServer.instances == []
