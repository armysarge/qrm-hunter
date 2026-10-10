"""Command-line entry point: `qrm-hunter` (desktop window) or `qrm-hunter --serve` (headless)."""

from __future__ import annotations

import argparse
import copy
import ipaddress
import logging
import re
import secrets
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from uvicorn.config import LOGGING_CONFIG

from .api.server import create_app
from .config.settings import SettingsStore
from .engine.session import RadioSession

WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger("qrm_hunter")

_TOKEN_RE = re.compile(r"token=[^&\s\"']+")


class RedactTokenFilter(logging.Filter):
    """Rewrites `token=<value>` to `token=***` in log messages. uvicorn's websocket layer logs the
    request path, query string included, at info level, whatever access_log is set to."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed record: leave it for the handler to report
            return True
        redacted = _TOKEN_RE.sub("token=***", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def redaction_log_config(token: str | None) -> dict | None:
    """uvicorn logging config with RedactTokenFilter on its handlers, or None when no token is in use.
    Passing it as log_config means uvicorn's own dictConfig call cannot drop the filter."""
    if not token:
        return None
    config = copy.deepcopy(LOGGING_CONFIG)
    config["filters"] = {"redact_token": {"()": RedactTokenFilter}}
    for handler in config["handlers"].values():
        handler["filters"] = ["redact_token"]
    return config


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_token(host: str, store: SettingsStore) -> str | None:
    if is_loopback(host):
        return None
    settings = store.load()
    if settings.token:
        return settings.token
    token = secrets.token_urlsafe(18)
    store.save(settings.model_copy(update={"token": token}))
    return token


def ui_url(host: str, port: int, token: str | None) -> str:
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    if ":" in shown:  # IPv6 literal
        shown = f"[{shown}]"
    return f"http://{shown}:{port}/" + (f"?token={token}" if token else "")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="qrm-hunter", description="See, record, identify and hunt down HF QRM.")
    p.add_argument("--serve", action="store_true", help="run headless and serve the UI to a web browser")
    p.add_argument("--host", default="127.0.0.1",
                   help="address to bind (default 127.0.0.1; anything else requires the printed token)")
    p.add_argument("--port", type=int, default=7070)
    p.add_argument("--config", type=Path, default=None, help="settings file (default: per-user config dir)")
    p.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    return p.parse_args(argv)


def build_app(args: argparse.Namespace) -> tuple[FastAPI, str | None]:
    store = SettingsStore(args.config)
    token = resolve_token(args.host, store)
    return create_app(RadioSession(), store, token=token, static_dir=WEB_DIR), token


def server_kwargs(args: argparse.Namespace, token: str | None) -> dict:
    """uvicorn options for headless mode. When a token is in use, access logs are off and log
    records are redacted, because the token may travel in `?token=` query strings."""
    kwargs = {"host": args.host, "port": args.port, "log_level": args.log_level, "access_log": token is None}
    redaction = redaction_log_config(token)
    if redaction is not None:
        kwargs["log_config"] = redaction
    return kwargs


def run_desktop(app: FastAPI, args: argparse.Namespace) -> bool:
    """Show the desktop window. Returns True after a normal close, or False when no window could be
    shown (the server is stopped first, so the caller can fall back to --serve on the same port)."""
    try:
        import webview
    except ImportError as exc:
        log.warning("desktop window unavailable (%s); falling back to --serve", exc)
        return False

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level=args.log_level))
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise SystemExit(f"server failed to start on port {args.port} (in use? try --port)")
        time.sleep(0.05)
    try:
        webview.create_window("QRM Hunter", ui_url("127.0.0.1", args.port, None), width=1440, height=900,
                              min_size=(960, 600), background_color="#0B0D10")
        webview.start()
        return True
    except Exception as exc:  # no GUI backend, missing WebView2 runtime, ...
        log.warning("desktop window unavailable (%s); falling back to --serve", exc)
        return False
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            raise SystemExit(f"desktop server on port {args.port} did not stop; not falling back")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    if not args.serve and not is_loopback(args.host):
        log.warning("--host only applies to --serve; the desktop window always uses 127.0.0.1")
        args.host = "127.0.0.1"
    app, token = build_app(args)
    if not args.serve:
        if run_desktop(app, args):
            return
        # The desktop server has stopped and its lifespan closed the radio session, so rebuild
        # the app for the headless server rather than reusing a closed session.
        app, token = build_app(args)
    print(f"QRM Hunter is running at {ui_url(args.host, args.port, token)}", flush=True)
    uvicorn.run(app, **server_kwargs(args, token))


if __name__ == "__main__":
    main()
