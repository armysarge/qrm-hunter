"""FastAPI app: REST control plus spectrum/audio/event WebSockets, with optional token auth."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import HTTPConnection

from ..config.bandplan import bands_for_region
from ..config.settings import Settings, SettingsPatch, SettingsStore
from ..engine.session import RadioSession, StateError
from ..engine.state import StatePatch
from ..sdr.base import DeviceError, DeviceInfo
from ..sdr.discovery import discover
from .protocol import ClientView, parse_view, render_frame

log = logging.getLogger(__name__)

PLACEHOLDER = """<!doctype html><meta charset="utf-8"><title>QRM Hunter</title>
<body style="font-family:system-ui;background:#0b0d10;color:#d8dee6;padding:2rem">
<h1>QRM Hunter engine is running</h1>
<p>The web UI has not been built yet. Run:</p>
<pre>cd frontend
npm install
npm run build</pre></body>"""


class OpenRequest(BaseModel):
    device_id: str
    config: StatePatch | None = None


async def _pump(*coros) -> None:
    """Run coroutines until the first one ends (normally a disconnect), then cancel the rest."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task.exception() is not None:
                log.debug("websocket task ended: %r", task.exception())
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _restore_last_session(session: RadioSession, settings: Settings) -> None:
    """Seed a stopped session with the last frequency from settings (spec 5.4: "last
    device/frequency"; the last device is restored by the UI from settings.last_device_id).
    While stopped this only merges and validates; open() recentres around tune_hz."""
    if session.state.running:
        return
    tune = float(settings.last_tune_hz)
    try:
        session.update(StatePatch(tune_hz=tune, center_hz=tune + session.state.sample_rate / 4))
    except StateError as exc:
        log.warning("ignoring saved frequency %s Hz: %s", settings.last_tune_hz, exc)


def create_app(session: RadioSession, store: SettingsStore, *, token: str | None = None,
               static_dir: Path | None = None,
               discover_fn: Callable[[], list[DeviceInfo]] = discover) -> FastAPI:

    settings_lock = threading.Lock()

    def save_settings(**changes) -> Settings:
        with settings_lock:
            app.state.settings = Settings.model_validate({**app.state.settings.model_dump(), **changes})
            store.save(app.state.settings)
            return app.state.settings

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        try:
            await run_in_threadpool(session.close)
        finally:
            await run_in_threadpool(lambda: save_settings(last_tune_hz=int(session.snapshot.tune_hz)))

    app = FastAPI(title="QRM Hunter", lifespan=lifespan)
    app.state.settings = store.load()
    _restore_last_session(session, app.state.settings)

    def authorized(conn: HTTPConnection) -> bool:
        if token is None:
            return True
        supplied = conn.query_params.get("token")
        auth = conn.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:]
        return supplied is not None and secrets.compare_digest(supplied.encode(), token.encode())

    def require_token(request: Request) -> None:
        if not authorized(request):
            raise HTTPException(401, "missing or invalid token")

    api = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    @api.get("/devices")
    def devices() -> list[dict]:
        return [dataclasses.asdict(i) for i in discover_fn()]

    @api.post("/session/open")
    def open_session(req: OpenRequest) -> dict:
        try:
            state = session.open(req.device_id, req.config)
        except StateError as exc:
            raise HTTPException(422, str(exc)) from exc
        except DeviceError as exc:
            raise HTTPException(409, str(exc)) from exc
        save_settings(last_device_id=req.device_id)
        return state.model_dump()

    @api.post("/session/close")
    def close_session() -> dict:
        session.close()
        save_settings(last_tune_hz=int(session.state.tune_hz))
        return session.state.model_dump()

    @api.get("/state")
    def get_state() -> dict:
        return session.state.model_dump()

    @api.patch("/state")
    def patch_state(patch: StatePatch) -> dict:
        try:
            return session.update(patch).model_dump()
        except StateError as exc:
            raise HTTPException(422, str(exc)) from exc
        except DeviceError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api.get("/bandplan")
    def bandplan() -> dict:
        region = app.state.settings.region
        return {"region": region, "bands": [
            {"name": b.name, "lo_hz": b.lo_hz, "hi_hz": b.hi_hz, "default_hz": b.default_hz}
            for b in bands_for_region(region)]}

    @api.get("/settings")
    def get_settings() -> dict:
        return app.state.settings.model_dump(exclude={"token"})

    @api.patch("/settings")
    def patch_settings(patch: SettingsPatch) -> dict:
        return save_settings(**patch.model_dump(exclude_unset=True, exclude_none=True)).model_dump(
            exclude={"token"})

    app.include_router(api)

    @app.websocket("/ws/spectrum")
    async def ws_spectrum(ws: WebSocket) -> None:
        if not authorized(ws):
            await ws.close(code=4401)
            return
        await ws.accept()
        sub = session.spectrum.subscribe()
        view = ClientView()

        async def receive() -> None:
            nonlocal view
            while True:
                view = parse_view(await ws.receive_text(), view)

        async def send() -> None:
            while True:
                frame = await sub.queue.get()
                st = session.snapshot
                await ws.send_bytes(render_frame(frame, view, st.db_min, st.db_max))

        try:
            await _pump(receive(), send())
        finally:
            session.spectrum.unsubscribe(sub)

    @app.websocket("/ws/audio")
    async def ws_audio(ws: WebSocket) -> None:
        if not authorized(ws):
            await ws.close(code=4401)
            return
        await ws.accept()
        sub = session.audio.subscribe()

        async def receive() -> None:
            while True:
                await ws.receive_text()

        async def send() -> None:
            rate = None
            while True:
                chunk = await sub.queue.get()
                if chunk.rate != rate:
                    rate = chunk.rate
                    await ws.send_json({"type": "format", "rate": rate, "channels": 1, "format": "s16le"})
                await ws.send_bytes(chunk.pcm.astype("<i2").tobytes())

        try:
            await _pump(receive(), send())
        finally:
            session.audio.unsubscribe(sub)

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        if not authorized(ws):
            await ws.close(code=4401)
            return
        await ws.accept()
        sub = session.events.subscribe()

        async def receive() -> None:
            while True:
                await ws.receive_text()

        async def send() -> None:
            await ws.send_json({"type": "state", "state": session.snapshot.model_dump()})
            while True:
                await ws.send_json(await sub.queue.get())

        try:
            await _pump(receive(), send())
        finally:
            session.events.unsubscribe(sub)

    if static_dir is not None and (Path(static_dir) / "index.html").exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="web")
    else:
        @app.get("/", response_class=HTMLResponse)
        def placeholder() -> str:
            return PLACEHOLDER

    return app
