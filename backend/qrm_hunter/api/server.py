"""FastAPI app: REST control plus spectrum/audio/event WebSockets, with optional token auth."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket as StarletteWebSocket

from .. import __version__
from ..analysis.features import AnalysisContext, Cancelled, ShortInput
from ..analysis.rules import user_signatures_path
from ..analysis.service import ANALYSIS_TIMEOUT_S, Analyser, AnalysisResult, make_check
from ..config.bandplan import band_at, bands_for_region
from ..config.memories import (
    MAX_IMPORT_BYTES,
    MAX_MEMORIES,
    ImportRequest,
    MemoriesNotWritable,
    Memory,
    MemoryFields,
    MemoryPatch,
    MemoryStore,
    export_csv,
    export_json,
    memories_path_for,
    merge_import,
    parse_import,
    sorted_memories,
)
from ..config.settings import Settings, SettingsPatch, SettingsStore
from ..dsp.extract import extract_span
from ..engine.session import RadioSession, StateError
from ..engine.state import StatePatch
from ..engine.worker import Busy, JobRunner, TimedOut
from ..recording.files import NS
from ..recording.recorder import (
    MIN_SNAPSHOT_S,
    AlreadyRecording,
    LowDisk,
    NotRecording,
    NotRunning,
    PreBufferOff,
    Recorder,
    RecorderError,
    RecordingContext,
    ShortBuffer,
)
from ..recording.store import (
    RecordingNotFound,
    RecordingStore,
    TrashError,
    default_recordings_dir,
    resolve_recordings_dir,
    reveal,
)
from ..sdr.base import DeviceError, DeviceInfo
from ..sdr.discovery import discover
from ..sdr.sigmf_io import from_ci16, to_ci16
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


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seconds: float = Field(10.0, gt=0, le=60)
    lo_hz: float | None = None
    hi_hz: float | None = None
    analysis_id: str | None = None


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lo_hz: float | None = None
    hi_hz: float | None = None
    lead_in_s: float | None = Field(None, ge=0, le=60)


class RenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class AnalyseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["live", "recording"]
    seconds: float | None = Field(None, gt=0, le=60)
    lo_hz: float | None = None
    hi_hz: float | None = None
    id: str | None = None


RECORDER_STATUS: tuple[tuple[type[RecorderError], int], ...] = (
    (NotRunning, 409), (AlreadyRecording, 409), (NotRecording, 409),
    (LowDisk, 507), (ShortBuffer, 422), (PreBufferOff, 422))


class LiveClip:
    """The IQ behind the latest live analysis, kept so it can be saved as a recording."""

    def __init__(self, iq16, rate, center_hz, t0, lo_hz, hi_hz, radio_center_hz, result: AnalysisResult) -> None:
        self.iq16, self.rate, self.center_hz, self.t0 = iq16, rate, center_hz, t0
        self.lo_hz, self.hi_hz, self.radio_center_hz, self.result = lo_hz, hi_hz, radio_center_hz, result


def dc_inside(radio_center_hz: float | None, lo_hz: float, hi_hz: float) -> float | None:
    return radio_center_hz if radio_center_hz is not None and lo_hz <= radio_center_hz <= hi_hz else None


LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def host_name(host: str) -> str | None:
    """The name part of a Host header ("[::1]:7070" -> "::1"), or None if it is malformed."""
    h = host.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        if end < 0:
            return None
        name, rest = h[1:end], h[end + 1:]
        if rest and not (rest[0] == ":" and rest[1:].isdigit()):
            return None
        return name
    name, sep, port = h.partition(":")
    if sep and not port.isdigit():
        return None
    return name or None


def same_origin(origin: str, host: str) -> bool:
    """True if an Origin header names the same host:port the request was sent to."""
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc) and         parts.netloc.lower() == host.strip().lower()


class RequestGuard:
    """Cross-site protections for an API that may run without a token on loopback.

    - Without a token, only Host names 127.0.0.1, localhost and [::1] (any port, so the Vite
      dev proxy on :5173 works) are served. This defeats DNS rebinding.
    - A WebSocket carrying an Origin header must come from the page's own origin (Origin
      host:port == Host). Browsers always send Origin and do not apply CORS to WebSockets;
      non-browser clients that send no Origin are allowed.
    - State-changing /api requests must be Content-Type: application/json. A cross-site page
      can only send that after a CORS preflight, which this server never grants, so "simple"
      cross-site POSTs (CSRF) are refused.
    """

    def __init__(self, app: ASGIApp, *, loopback_only: bool) -> None:
        self.app = app
        self.loopback_only = loopback_only

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        host = headers.get("host", "")
        if self.loopback_only and host_name(host) not in LOOPBACK_NAMES:
            await self._reject(scope, receive, send, 403, "host not allowed")
            return
        if kind == "websocket":
            origin = headers.get("origin")
            if origin is not None and not same_origin(origin, host):
                await self._reject(scope, receive, send, 403, "cross-origin WebSocket refused")
                return
        elif scope["method"] in UNSAFE_METHODS and scope["path"].startswith("/api/"):
            ctype = headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype != "application/json":
                await self._reject(scope, receive, send, 415, "Content-Type must be application/json")
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await StarletteWebSocket(scope, receive, send).close(code=4403, reason=detail)
        else:
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


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
               discover_fn: Callable[[], list[DeviceInfo]] = discover,
               memory_store: MemoryStore | None = None,
               desktop: bool = False,
               analyser: Analyser | None = None,
               free_bytes: Callable[[object], int] | None = None) -> FastAPI:

    settings_lock = threading.Lock()
    memories_lock = threading.Lock()
    if memory_store is None:
        memory_store = MemoryStore(memories_path_for(store.path))
    signatures_warning: str | None = None
    if analyser is None:
        analyser, signatures_warning = Analyser.create(user_signatures_path(store.path))
        if signatures_warning:
            log.warning("%s", signatures_warning)
    runner = JobRunner("analysis")
    last_live: dict[str, LiveClip] = {}

    def save_settings(**changes) -> Settings:
        with settings_lock:
            app.state.settings = Settings.model_validate({**app.state.settings.model_dump(), **changes})
            store.save(app.state.settings)
            return app.state.settings

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        try:
            await run_in_threadpool(session.close)  # finalises a running recording
        finally:
            runner.shutdown()
            await run_in_threadpool(lambda: save_settings(last_tune_hz=int(session.snapshot.tune_hz)))

    app = FastAPI(title="QRM Hunter", lifespan=lifespan)
    app.add_middleware(RequestGuard, loopback_only=token is None)
    app.state.settings = store.load()
    session.set_region(app.state.settings.region)
    session.prebuffer_s = app.state.settings.prebuffer_s
    app.state.memories = memory_store.load()
    _restore_last_session(session, app.state.settings)

    def recordings() -> RecordingStore:
        return RecordingStore(resolve_recordings_dir(app.state.settings.recordings_dir))

    # band_at() also knows the "FM BC" broadcast band (group "BC"); mode may be "WFM"
    def recording_context() -> RecordingContext:
        st, s = session.state, app.state.settings
        band = band_at(st.tune_hz, s.region)
        meta = {"band": band.name if band else None, "mode": st.mode, "device": st.device_id,
                "gain_db": st.gain_db, "direct_sampling": st.direct_sampling}
        if s.callsign:
            meta["callsign"] = s.callsign
        return RecordingContext(meta, s.colormap)

    recorder = Recorder(session, recordings, recording_context, session.events.publish, free_bytes=free_bytes)

    def recording_call(fn, *args, **kwargs):
        """Run a recording operation, turning its errors into readable HTTP errors."""
        try:
            return fn(*args, **kwargs)
        except RecorderError as exc:
            status = next((code for kind, code in RECORDER_STATUS if isinstance(exc, kind)), 500)
            raise HTTPException(status, str(exc)) from exc
        except RecordingNotFound as exc:  # also a recording whose metadata cannot be read
            raise HTTPException(404, "no such recording") from exc
        except TrashError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:  # RegionError, a bad name, an unreadable recording
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            root = resolve_recordings_dir(app.state.settings.recordings_dir)
            raise HTTPException(422, f"cannot use the recordings folder {root}: {exc.strerror or exc}") from exc

    def run_analysis(job) -> AnalysisResult:
        try:
            return runner.run(job, ANALYSIS_TIMEOUT_S)
        except Busy as exc:
            raise HTTPException(409, "an analysis is already running") from exc
        except (TimedOut, Cancelled) as exc:
            raise HTTPException(504, str(exc)) from exc
        except ShortInput as exc:
            raise HTTPException(422, str(exc)) from exc

    def save_memories(memories: list[Memory]) -> None:
        """Persist, then commit and announce a new memory list (call with memories_lock held).
        If the file cannot be written, the in-memory list is left unchanged and nothing is published."""
        try:
            memory_store.save(memories)
        except MemoriesNotWritable as exc:
            log.warning("%s", exc)
            raise HTTPException(503, "settings folder not writable") from exc
        except OSError as exc:
            log.warning("cannot save memories to %s (%s)", memory_store.path, exc)
            raise HTTPException(500, f"could not save memories: {exc.strerror or exc}") from exc
        app.state.memories = memories
        # Other open UIs refetch on this event.
        session.events.publish({"type": "memories"})

    def find_memory(memory_id: str) -> int:
        for i, m in enumerate(app.state.memories):
            if m.id == memory_id:
                return i
        raise HTTPException(404, "no such memory")

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

    @api.get("/rds")
    def get_rds() -> dict | None:
        return session.rds

    @api.get("/bandplan")
    def bandplan() -> dict:
        region = app.state.settings.region
        return {"region": region, "bands": [
            {"name": b.name, "lo_hz": b.lo_hz, "hi_hz": b.hi_hz, "default_hz": b.default_hz, "group": b.group}
            for b in bands_for_region(region)]}

    @api.get("/settings")
    def get_settings() -> dict:
        return app.state.settings.model_dump(exclude={"token"})

    @api.patch("/settings")
    def patch_settings(patch: SettingsPatch) -> dict:
        settings = save_settings(**patch.model_dump(exclude_unset=True, exclude_none=True))
        session.set_region(settings.region)  # WFM de-emphasis and the RDS programme type table
        session.prebuffer_s = settings.prebuffer_s
        return settings.model_dump(exclude={"token"})

    @api.get("/memories")
    def list_memories() -> list[dict]:
        return [m.model_dump() for m in sorted_memories(app.state.memories)]

    @api.post("/memories", status_code=201)
    def create_memory(fields: MemoryFields) -> dict:
        with memories_lock:
            if len(app.state.memories) >= MAX_MEMORIES:
                raise HTTPException(409, f"at most {MAX_MEMORIES} memories can be stored")
            memory = Memory.new(fields)
            save_memories([*app.state.memories, memory])
        return memory.model_dump()

    @api.get("/memories/export")
    def export_memories(format: str = "json") -> Response:
        if format == "json":
            body, media = export_json(app.state.memories), "application/json"
        elif format == "csv":
            body, media = export_csv(app.state.memories), "text/csv"
        else:
            raise HTTPException(422, "format must be json or csv")
        return Response(body, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="qrm-hunter-memories.{format}"'})

    @api.post("/memories/import")
    def import_memories(req: ImportRequest) -> dict:
        if len(req.data.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise HTTPException(413, f"import data is larger than {MAX_IMPORT_BYTES // 1_000_000} MB")
        try:
            incoming, invalid = parse_import(req.format, req.data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if req.replace and not incoming:
            # Refuse rather than replace everything with nothing (wrong file, all rows invalid).
            raise HTTPException(422, "no valid memories in the import; nothing was replaced")
        with memories_lock:
            merged, added, skipped = merge_import(app.state.memories, incoming, req.replace)
            if added or req.replace:
                save_memories(merged)
        return {"added": added, "skipped": skipped + invalid}

    @api.patch("/memories/{memory_id}")
    def patch_memory(memory_id: str, patch: MemoryPatch) -> dict:
        with memories_lock:
            i = find_memory(memory_id)
            old = app.state.memories[i]
            try:
                new = Memory.model_validate({**old.model_dump(), **patch.model_dump(exclude_unset=True)})
            except ValidationError as exc:
                raise HTTPException(422, "; ".join(
                    f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())) from exc
            memories = list(app.state.memories)
            memories[i] = new
            save_memories(memories)
        return new.model_dump()

    @api.delete("/memories/{memory_id}", status_code=204)
    def delete_memory(memory_id: str) -> Response:
        with memories_lock:
            i = find_memory(memory_id)
            save_memories([m for j, m in enumerate(app.state.memories) if j != i])
        return Response(status_code=204)

    # The handlers below are plain functions, so FastAPI runs them in its thread pool: starting a
    # recording (lead-in), saving, stopping and analysing do file I/O and DSP off the event loop.
    @api.post("/record/snapshot")
    def record_snapshot(req: SnapshotRequest) -> dict:
        if req.analysis_id is None:
            return recording_call(recorder.snapshot, req.seconds, req.lo_hz, req.hi_hz).model_dump()
        clip = last_live.get(req.analysis_id)
        if clip is None:
            raise HTTPException(404, "that analysis is no longer available; run Analyse again")
        info = recording_call(recorder.save_iq, from_ci16(clip.iq16), clip.rate, clip.center_hz, clip.t0,
                              lo_hz=clip.lo_hz, hi_hz=clip.hi_hz, radio_center_hz=clip.radio_center_hz)
        source = clip.result.source.model_copy(update={"kind": "recording", "recording_id": info.id})
        result = clip.result.model_copy(update={"source": source})
        store_ = recordings()
        recording_call(store_.save_analysis, info.id, result.model_dump_json())
        session.events.publish({"type": "recordings"})
        return recording_call(store_.info, info.id).model_dump()

    @api.post("/record/start")
    def record_start(req: StartRequest) -> dict:
        return recording_call(recorder.start, req.lo_hz, req.hi_hz, req.lead_in_s)

    @api.post("/record/stop")
    def record_stop() -> dict:
        return recording_call(recorder.stop).model_dump()

    @api.get("/record/status")
    def record_status() -> dict:
        return recorder.status()

    @api.get("/recordings")
    def list_recordings() -> list[dict]:
        return [i.model_dump() for i in recording_call(recordings().list)]

    @api.get("/recordings/{rec_id}/waterfall.png")
    def recording_waterfall(rec_id: str) -> FileResponse:
        path = recording_call(recordings().path, rec_id) / "waterfall.png"
        if not path.is_file():
            raise HTTPException(404, "this recording has no waterfall image")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})

    @api.patch("/recordings/{rec_id}")
    def rename_recording(rec_id: str, req: RenameRequest) -> dict:
        info = recording_call(recordings().rename, rec_id, req.name)
        session.events.publish({"type": "recordings"})
        return info.model_dump()

    @api.delete("/recordings/{rec_id}", status_code=204)
    def delete_recording(rec_id: str) -> Response:
        recording_call(recordings().trash, rec_id)
        session.events.publish({"type": "recordings"})
        return Response(status_code=204)

    @api.post("/recordings/{rec_id}/open-folder", status_code=204)
    def open_recording_folder(rec_id: str) -> Response:
        if not desktop:
            raise HTTPException(403, "Open folder is only available in the desktop app")
        folder = recording_call(recordings().path, rec_id)
        try:
            reveal(folder)
        except OSError as exc:
            raise HTTPException(500, f"could not open {folder}: {exc.strerror or exc}") from exc
        return Response(status_code=204)

    @api.post("/analyse")
    def analyse_route(req: AnalyseRequest) -> dict:
        mains = app.state.settings.mains_hz
        if req.source == "live":
            gen = session.current_generation[0]
            snap = session.snapshot_iq(req.seconds)
            if snap is None and session.prebuffer_s <= 0:
                raise HTTPException(422, "the pre-buffer is off; set Pre-buffer seconds in Settings")
            if snap is None or snap.gen != gen or snap.seconds < MIN_SNAPSHOT_S:
                raise HTTPException(422, "need at least 2 s of data at this frequency")
            ex = recording_call(extract_span, snap.iq, snap.sample_rate, snap.center_hz, req.lo_hz, req.hi_hz)
            ctx = AnalysisContext(mains_hz=mains, dc_hz=dc_inside(snap.center_hz, ex.lo_hz, ex.hi_hz))
            result = run_analysis(lambda ev: analyser.analyse(
                ex.iq, ex.rate, ex.center_hz, ctx, kind="live", lo_hz=ex.lo_hz, hi_hz=ex.hi_hz,
                check=make_check(ev)))
            last_live.clear()
            last_live[result.id] = LiveClip(to_ci16(ex.iq), ex.rate, ex.center_hz, snap.t0, ex.lo_hz, ex.hi_hz,
                                            snap.center_hz, result)
            return result.model_dump()
        if not req.id:
            raise HTTPException(422, "id is required when analysing a recording")
        store_ = recordings()
        iq, rate, center, meta = recording_call(store_.load_iq, req.id)
        lo = float(meta.get(NS + "lo_hz", center - rate / 2))
        hi = float(meta.get(NS + "hi_hz", center + rate / 2))
        ctx = AnalysisContext(mains_hz=mains, dc_hz=dc_inside(meta.get(NS + "radio_center_hz"), lo, hi))
        result = run_analysis(lambda ev: analyser.analyse(
            iq, rate, center, ctx, kind="recording", lo_hz=lo, hi_hz=hi, recording_id=req.id,
            check=make_check(ev)))
        try:
            store_.save_analysis(req.id, result.model_dump_json())
            session.events.publish({"type": "recordings"})
        except OSError as exc:
            log.warning("could not save the analysis of %s: %s", req.id, exc)
        return result.model_dump()

    @api.get("/app")
    def app_info() -> dict:
        return {"version": __version__, "desktop": desktop, "recordings_dir": str(recordings().root),
                "default_recordings_dir": str(default_recordings_dir()), "ranker": analyser.ranker_status,
                "signatures_warning": signatures_warning}

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
            if signatures_warning:
                await ws.send_json({"type": "error", "code": "signatures", "message": signatures_warning})
            rds = session.rds
            if rds is not None:
                await ws.send_json({"type": "rds", "rds": rds})
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
