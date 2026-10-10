"""Command-line entry point: `qrm-hunter` (desktop window) or `qrm-hunter --serve` (headless)."""

from __future__ import annotations

import argparse
import ipaddress
import logging
import secrets
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from .api.server import create_app
from .config.settings import SettingsStore
from .engine.session import RadioSession

WEB_DIR = Path(__file__).parent / "web"
log = logging.getLogger("qrm_hunter")


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
    """uvicorn options for headless mode. Access logs are off when a token is in use, because
    the token may travel in `?token=` query strings that the access log would record."""
    return {"host": args.host, "port": args.port, "log_level": args.log_level, "access_log": token is None}


def run_desktop(app: FastAPI, args: argparse.Namespace) -> None:
    import webview

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level=args.log_level))
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise SystemExit(f"server failed to start on port {args.port} (in use? try --port)")
        time.sleep(0.05)
    webview.create_window("QRM Hunter", ui_url("127.0.0.1", args.port, None), width=1440, height=900,
                          min_size=(960, 600), background_color="#0B0D10")
    webview.start()
    server.should_exit = True
    thread.join(timeout=5)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(name)s: %(message)s")
    if not args.serve and not is_loopback(args.host):
        log.warning("--host only applies to --serve; the desktop window always uses 127.0.0.1")
        args.host = "127.0.0.1"
    app, token = build_app(args)
    if not args.serve:
        try:
            run_desktop(app, args)
            return
        except ImportError as exc:
            log.warning("desktop window unavailable (%s); falling back to --serve", exc)
    print(f"QRM Hunter is running at {ui_url(args.host, args.port, token)}", flush=True)
    uvicorn.run(app, **server_kwargs(args, token))


if __name__ == "__main__":
    main()
