from fastapi import FastAPI

from qrm_hunter.app import build_app, is_loopback, parse_args, resolve_token, server_kwargs, ui_url
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
