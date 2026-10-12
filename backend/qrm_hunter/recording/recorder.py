"""Save the last N seconds, or record Start/Stop, of the visible span or a selected region."""

from __future__ import annotations

import logging
import math
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import numpy as np

from ..dsp.extract import SpanExtractor, extract_span, validate_region
from ..engine.session import TAP_SECONDS, RadioSession, StateError, Tap, block_size_for
from .files import SigmfWriter
from .store import RecordingInfo, RecordingStore
from .waterfall import write_products

log = logging.getLogger(__name__)

MAX_RECORDING_S = 30 * 60.0
MIN_FREE_BYTES = 500 * 1024 ** 2
DISK_CHECK_S = 5.0
PROGRESS_S = 1.0
DEFAULT_LEAD_IN_S = 10.0
MIN_SNAPSHOT_S = 2.0
TRUNCATING = frozenset({"device_lost", "low_disk", "closed", "error"})
_LEAD_IN_CHUNK = 1 << 18  # samples per extractor call while processing the lead-in


class RecorderError(RuntimeError):
    """A recording request cannot be carried out. The message is shown to the user."""


class NotRunning(RecorderError): ...
class AlreadyRecording(RecorderError): ...
class NotRecording(RecorderError): ...
class LowDisk(RecorderError): ...
class ShortBuffer(RecorderError): ...
class PreBufferOff(RecorderError): ...


@dataclass(frozen=True)
class RecordingContext:
    meta: dict  # band, mode, device, gain_db, direct_sampling, callsign
    colormap: str


@dataclass
class _Active:
    gen: int
    extractor: SpanExtractor
    writer: SigmfWriter
    store: RecordingStore
    colormap: str
    next_index: int | None
    started: float
    next_disk: float
    next_progress: float
    tap: Tap
    limit: int  # output samples allowed in the file (max_s, lead-in included)
    gaps: int = 0
    info: RecordingInfo | None = None
    error: str | None = None


class _Slot:
    """Ties one tap's callbacks to the recording it feeds, so a late callback from an earlier
    tap can never touch a newer recording."""

    def __init__(self) -> None:
        self.active: _Active | None = None


class Recorder:
    def __init__(self, session: RadioSession, store: Callable[[], RecordingStore],
                 context: Callable[[], RecordingContext], publish: Callable[[dict], None], *,
                 free_bytes: Callable[[Path], int] | None = None, clock: Callable[[], float] = time.monotonic,
                 max_s: float = MAX_RECORDING_S) -> None:
        self._session = session
        self._store = store
        self._context = context
        self._publish = publish
        self._free = free_bytes or (lambda p: shutil.disk_usage(p).free)
        self._clock = clock
        self._max_s = max_s
        self._lock = threading.Lock()
        self._active: _Active | None = None
        self.last_first_index: int | None = None  # run-relative index of the last recording's first sample

    # --- helpers ---------------------------------------------------------------------------------

    def _check_disk(self, root: Path) -> None:
        free = self._free(root)
        if free < MIN_FREE_BYTES:
            raise LowDisk(f"only {free / 1024 ** 2:.0f} MB free in {root}; at least 500 MB is needed to record")

    def _finish_files(self, store: RecordingStore, writer: SigmfWriter, colormap: str, **final) -> RecordingInfo:
        writer.close(**final)
        write_products(writer.folder, writer.sample_rate, writer.center_hz, colormap)
        return store.info(writer.folder.name)

    @staticmethod
    def _remove_if_empty(folder: Path) -> None:
        try:
            folder.rmdir()  # only succeeds on an empty folder: nothing recorded is ever removed
        except OSError:
            pass

    # --- Save last N s ---------------------------------------------------------------------------

    def save_iq(self, iq: np.ndarray, rate: float, center_hz: float, t0: float, *, lo_hz: float, hi_hz: float,
                radio_center_hz: float, end_reason: str = "snapshot") -> RecordingInfo:
        store = self._store()
        self._check_disk(store.ensure_root())
        ctx = self._context()
        folder = store.new_folder(center_hz, datetime.fromtimestamp(t0, UTC))
        try:
            writer = SigmfWriter(folder, rate, center_hz, t0, {**ctx.meta, "lo_hz": lo_hz, "hi_hz": hi_hz,
                                                               "radio_center_hz": radio_center_hz})
        except BaseException:
            self._remove_if_empty(folder)
            raise
        try:
            writer.write(iq)
        except BaseException:
            self._abandon(store, writer, ctx.colormap)
            raise
        info = self._finish_files(store, writer, ctx.colormap, truncated=False, end_reason=end_reason, gaps=0)
        self._publish({"type": "recordings"})
        return info

    def snapshot(self, seconds: float, lo_hz: float | None = None, hi_hz: float | None = None) -> RecordingInfo:
        # Checks that need no samples come first: a bad region or a full disk is reported as such,
        # not as a buffer that is still filling.
        st = self._session.snapshot
        validate_region(st.center_hz, st.sample_rate, lo_hz, hi_hz)
        self._check_disk(self._store().ensure_root())
        gen = self._session.current_generation[0]
        snap = self._session.snapshot_iq(seconds)
        if snap is None and self._session.prebuffer_s <= 0:
            raise PreBufferOff("the pre-buffer is off; set Pre-buffer seconds in Settings")
        # Right after a retune the buffer can still hold the previous tuning until the first new
        # block lands; that is not data "at this frequency" either.
        if snap is None or snap.gen != gen or snap.seconds < MIN_SNAPSHOT_S:
            raise ShortBuffer("need at least 2 s of data at this frequency")
        ex = extract_span(snap.iq, snap.sample_rate, snap.center_hz, lo_hz, hi_hz)
        return self.save_iq(ex.iq, ex.rate, ex.center_hz, snap.t0, lo_hz=ex.lo_hz, hi_hz=ex.hi_hz,
                            radio_center_hz=snap.center_hz)

    # --- Start / Stop ----------------------------------------------------------------------------

    def status(self) -> dict:
        a = self._active
        if a is None:
            return {"recording": False, "elapsed_s": 0.0, "bytes": 0, "id": None}
        return {"recording": True, "elapsed_s": self._clock() - a.started, "bytes": a.writer.bytes,
                "id": a.writer.folder.name}

    def start(self, lo_hz: float | None = None, hi_hz: float | None = None,
              lead_in_s: float | None = None) -> dict:
        with self._lock:
            if self._active is not None:
                raise AlreadyRecording("a recording is already running")
            if not self._session.state.running:
                raise NotRunning("start the radio before recording")
            store = self._store()
            self._check_disk(store.ensure_root())
            _, center, rate = self._session.current_generation
            validate_region(center, rate, lo_hz, hi_hz)
            lead = DEFAULT_LEAD_IN_S if lead_in_s is None else max(0.0, float(lead_in_s))
            lead = min(lead, self._max_s)  # the lead-in counts toward the cap
            # The tap opens before the lead-in snapshot so that no block falls between the two. Its
            # consumer waits on self._lock, so live blocks queue up until the lead-in is written;
            # the queue is sized to hold them meanwhile.
            slot = _Slot()
            max_blocks = max(8, math.ceil((lead + TAP_SECONDS) * rate / block_size_for(rate)))
            try:
                tap = self._session.open_tap(partial(self._consume, slot), partial(self._on_end, slot), max_blocks)
            except StateError as exc:
                # The radio is running but a previous tap is still finishing its file.
                if self._session.state.running:
                    raise AlreadyRecording("a recording is still being finished") from None
                raise NotRunning(str(exc)) from None
            writer = None
            ctx = None
            try:
                # The tap records the generation it opened on; a retune since the check above is
                # caught here rather than mixing two tunings.
                extractor = SpanExtractor(tap.sample_rate, tap.center_hz, lo_hz, hi_hz)
                snap = self._session.snapshot_iq(lead) if lead > 0 else None
                if snap is not None and (snap.gen != tap.gen or snap.iq.size == 0):
                    snap = None  # the buffer still holds another tuning: start without a lead-in
                limit = max(1, math.floor(self._max_s * extractor.out_rate))
                lead_iq = None
                if snap is not None:
                    parts = [extractor.process(snap.iq[i:i + _LEAD_IN_CHUNK])
                             for i in range(0, snap.iq.size, _LEAD_IN_CHUNK)]
                    lead_iq = np.concatenate(parts)
                t0 = snap.t0 if snap is not None else time.time()
                ctx = self._context()
                folder = store.new_folder(extractor.out_center_hz, datetime.fromtimestamp(t0, UTC))
                try:
                    writer = SigmfWriter(folder, extractor.out_rate, extractor.out_center_hz, t0,
                                         {**ctx.meta, "lo_hz": extractor.lo_hz, "hi_hz": extractor.hi_hz,
                                          "radio_center_hz": tap.center_hz})
                except BaseException:
                    self._remove_if_empty(folder)
                    raise
                next_index = None
                self.last_first_index = None
                if lead_iq is not None:
                    first = snap.end_index - snap.iq.size
                    if lead_iq.size > limit:  # decimation rounding: keep the newest samples
                        drop = lead_iq.size - limit
                        lead_iq = lead_iq[drop:]
                        first += round(drop * extractor.in_rate / extractor.out_rate)
                    writer.write(lead_iq)
                    next_index = snap.end_index
                    self.last_first_index = first
                now = self._clock()
                active = _Active(tap.gen, extractor, writer, store, ctx.colormap, next_index, started=now,
                                 next_disk=now + DISK_CHECK_S, next_progress=now + PROGRESS_S, tap=tap, limit=limit)
                slot.active = self._active = active
            except BaseException:
                tap.end("error")  # no wait: the consumer is blocked on self._lock until we return
                if writer is not None:
                    self._abandon(store, writer, ctx.colormap)
                raise
        return self.status()

    def _abandon(self, store: RecordingStore, writer: SigmfWriter, colormap: str) -> None:
        """A start or save that failed after its data file was created: keep what was written as a
        truncated recording rather than leaving a data file without metadata."""
        try:
            self._finish_files(store, writer, colormap, truncated=True, end_reason="error", gaps=0)
        except Exception:
            log.exception("could not finish the abandoned recording in %s", writer.folder)
        self._publish({"type": "recordings"})

    def stop(self) -> RecordingInfo:
        with self._lock:
            active = self._active
        if active is None:
            raise NotRecording("no recording is running")
        self._session.close_tap(active.tap, "stopped")
        if not active.tap.done.is_set():
            raise RecorderError("the recording did not finish in time")
        if active.error or active.info is None:
            raise RecorderError(active.error or "the recording could not be finished")
        return active.info

    # --- tap thread ------------------------------------------------------------------------------

    def _consume(self, slot: _Slot, gen: int, center: float, rate: float, index: int,
                 block: np.ndarray) -> str | None:
        with self._lock:  # waits while start() writes the lead-in
            a = slot.active
        if a is None:
            return "stopped"  # start() failed
        if gen < a.gen:
            return None  # read before the tap's tuning took effect
        if gen != a.gen:
            return "retuned"
        if a.next_index is not None:
            skip = a.next_index - index
            if skip >= block.size:
                return None  # already in the lead-in
            if skip > 0:
                block, index = block[skip:], index + skip
            elif skip < 0:
                a.gaps += -skip  # samples the tap queue had to drop
        else:
            self.last_first_index = index
        a.next_index = index + block.size
        out = a.extractor.process(block)
        room = a.limit - a.writer.samples
        if out.size >= room:  # the cap is counted in samples, so the file never runs over max_s
            a.writer.write(out[:room])
            return "cap"
        a.writer.write(out)
        now = self._clock()
        if now >= a.next_disk:
            a.next_disk = now + DISK_CHECK_S
            if self._free(a.store.root) < MIN_FREE_BYTES:
                return "low_disk"
        if now >= a.next_progress:
            # keep a steady cadence, but after a stall restart from now instead of catching up
            a.next_progress = (a.next_progress if now - a.next_progress < PROGRESS_S else now) + PROGRESS_S
            self._publish({"type": "recording", "elapsed_s": round(now - a.started, 1), "bytes": a.writer.bytes})
        return None

    def _on_end(self, slot: _Slot, reason: str) -> None:
        a = slot.active
        if a is None:
            return
        try:
            a.info = self._finish_files(a.store, a.writer, a.colormap, truncated=reason in TRUNCATING,
                                        end_reason=reason, gaps=a.gaps)
        except Exception as exc:  # disk full, folder removed, ...
            log.exception("could not finish the recording")
            a.error = f"could not finish the recording: {exc}"
        finally:
            with self._lock:
                if self._active is a:
                    self._active = None
        self._publish({"type": "recording_stopped", "reason": reason,
                       "recording": a.info.model_dump() if a.info else None, "error": a.error})
        self._publish({"type": "recordings"})
