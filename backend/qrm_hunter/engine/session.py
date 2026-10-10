"""RadioSession: owns the device, the reader and DSP threads, and the outgoing broadcasters."""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from typing import Callable

from pydantic import ValidationError

from ..dsp.demod import DEFAULT_BW, Demodulator, deemphasis_for
from ..dsp.rds import RdsDecoder
from ..dsp.spectrum import SpectrumProcessor
from ..sdr.base import DeviceConfig, DeviceError, DeviceInfo, SdrDevice
from ..sdr.discovery import create_device
from .broadcast import Broadcaster
from .state import AudioChunk, RadioState, SpectrumFrame, StatePatch, Stats

log = logging.getLogger(__name__)

SPAN_USABLE = 0.45  # fraction of the sample rate either side of centre usable for tuning
RING_SECONDS = 0.5
JOIN_TIMEOUT = 2.0  # seconds to wait for a run's threads on close
DEV_LOCK_TIMEOUT = 3.0  # seconds to wait for an in-progress device read/configure
RDS_MIN_INTERVAL = 0.2  # seconds between published RDS updates (at most 5 per second)


class StateError(ValueError):
    """A requested state change is invalid. The message is shown to the user."""


def block_size_for(rate: float) -> int:
    return max(4096, 1 << int(round(math.log2(rate / 50))))


def _fmt_validation(exc: ValidationError) -> str:
    return "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())


class FrameGate:
    """Paces spectrum publishing against a deadline rather than "time since the last frame".

    Frames can only go out when a block arrives, so a "since last frame" gate rounds every
    interval up to the next block (40 ms becomes 48 ms with 16 ms blocks: ~21 fps, not 25).
    Advancing a deadline by one period keeps the long-run cadence exact. After a stall (more
    than a period late) the deadline restarts from `now`, so there is no catch-up burst.
    """

    def __init__(self) -> None:
        self._next: float | None = None

    def ready(self, now: float, fps: float) -> bool:
        if self._next is None:
            self._next = now
        if now < self._next:
            return False
        period = 1.0 / fps
        late = now - self._next
        self._next = (self._next if late < period else now) + period
        return True


class _Run:
    """One open/close cycle: its device, its own stop event, ring and threads."""

    def __init__(self, device: SdrDevice, ring_size: int) -> None:
        self.device = device
        self.stop = threading.Event()
        self.ring: queue.Queue = queue.Queue(maxsize=ring_size)
        self.threads: list[threading.Thread] = []


class RadioSession:
    def __init__(self, device_factory: Callable[[str], SdrDevice] = create_device) -> None:
        self._factory = device_factory
        self._life = threading.RLock()  # serialises open()/close()
        self._lock = threading.RLock()  # guards state and DSP
        self._dev_lock = threading.Lock()  # held by the reader during read, by update during configure
        self._run: _Run | None = None
        self._snapshot = RadioState()
        self._state = RadioState()
        self._stats = Stats()
        self._spectrum: SpectrumProcessor | None = None
        self._demod: Demodulator | None = None
        self._rds: RdsDecoder | None = None  # runs only while the mode is WFM
        self._rds_snapshot: dict | None = None
        self._region = 1
        self._block = 32768
        self._gen = 0
        self._seq = 0
        self.spectrum: Broadcaster[SpectrumFrame] = Broadcaster(maxsize=2)
        self.audio: Broadcaster[AudioChunk] = Broadcaster(maxsize=8)
        self.events: Broadcaster[dict] = Broadcaster(maxsize=64)

    # --- public API -------------------------------------------------------------------------

    @property
    def _state(self) -> RadioState:
        return self._state_v

    @_state.setter
    def _state(self, value: RadioState) -> None:
        self._state_v = value
        self._snapshot = value.model_copy()  # single attribute assignment: readable without the lock

    @property
    def snapshot(self) -> RadioState:
        """Lock-free copy of the latest state; safe to call from the event loop."""
        return self._snapshot.model_copy()

    @property
    def state(self) -> RadioState:
        with self._lock:
            return self._state.model_copy()

    @property
    def rds(self) -> dict | None:
        """The latest published RDS state (None when not in WFM or after a retune); lock-free."""
        return self._rds_snapshot

    @property
    def region(self) -> int:
        return self._region

    def set_region(self, region: int) -> None:
        """IARU region: picks the WFM de-emphasis (75 us in Region 2, else 50 us) and the RDS
        programme type table (RBDS in Region 2)."""
        with self._lock:
            self._region = region
            if self._demod is not None:
                self._demod.set_deemphasis(deemphasis_for(region))
            if self._rds is not None:
                self._rds.set_region(region)

    @property
    def stats(self) -> Stats:
        with self._lock:
            return self._stats.model_copy(update={"clients": self.spectrum.subscriber_count})

    def open(self, device_id: str, patch: StatePatch | None = None) -> RadioState:
        with self._life:
            device = self._factory(device_id)
            # Validate everything that does not need the hardware first (device.info is available
            # before open), so a rejected request leaves the running radio untouched.
            try:
                base = self.state.model_copy(update={"device_id": device_id, "running": False})
                merged = self._merge(base, patch)
                explicit = patch.model_dump(exclude_unset=True) if patch else {}
                if "center_hz" not in explicit:
                    merged = merged.model_copy(update={"center_hz": merged.tune_hz + merged.sample_rate / 4})
                merged = self._recentre(merged, device.info, center_explicit=False)
                spectrum, demod = self._build_dsp(merged)
            except BaseException:
                self._safe_close(device)
                raise
            self.close()  # release the previous device (it may be the same hardware)
            try:
                device.open()
                applied = device.configure(self._device_config(merged))
            except BaseException:
                self._safe_close(device)
                raise
            merged = self._with_applied(merged, applied).model_copy(update={"running": True})
            demod.set_offset(merged.tune_hz - merged.center_hz)
            block = block_size_for(merged.sample_rate)
            run = _Run(device, max(4, math.ceil(RING_SECONDS * merged.sample_rate / block)))
            with self._lock:
                self._run = run
                self._state = merged
                self._stats = Stats()
                self._spectrum = spectrum
                self._demod = demod
                self._block = block
                self._gen += 1
                self._reset_rds()
            run.threads = [
                threading.Thread(target=self._reader_loop, args=(run,), name="sdr-reader", daemon=True),
                threading.Thread(target=self._dsp_loop, args=(run,), name="sdr-dsp", daemon=True),
            ]
            for t in run.threads:
                t.start()
            self._publish_state()
            return self.state

    def close(self) -> None:
        with self._life:
            with self._lock:
                run, self._run = self._run, None
                was_running = self._state.running
                # audio goes off with the radio (the UI tears its player down on stop); an
                # explicit audio=true on the next open() is still honoured
                self._state = self._state.model_copy(update={"running": False, "audio": False})
                self._reset_rds()
            if run is not None:
                run.stop.set()
                for t in run.threads:
                    t.join(timeout=JOIN_TIMEOUT)
                # Avoid closing the device mid-read when we can; stop waiting after a timeout.
                got = self._dev_lock.acquire(timeout=DEV_LOCK_TIMEOUT)
                try:
                    self._safe_close(run.device)
                finally:
                    if got:
                        self._dev_lock.release()
            if was_running:
                self._publish_state()

    @staticmethod
    def _safe_close(device: SdrDevice) -> None:
        try:
            device.close()
        except Exception:
            log.exception("error closing device")

    def update(self, patch: StatePatch) -> RadioState:
        explicit = patch.model_dump(exclude_unset=True)
        with self._lock:
            old = self._state
            new = self._merge(old, patch)
            run = self._run
            if run is not None and old.running:
                device = run.device
                new = self._recentre(new, device.info,
                                     center_explicit="center_hz" in explicit and "tune_hz" not in explicit)
                plan = self._plan_dsp(old, new)  # validates everything before any side effect
                if self._device_fields(new) != self._device_fields(old):
                    if not self._dev_lock.acquire(timeout=DEV_LOCK_TIMEOUT):
                        raise DeviceError("device not responding")
                    try:
                        applied = device.configure(self._device_config(new))
                    finally:
                        self._dev_lock.release()
                    new = self._with_applied(new, applied)
                    # Blocks are labelled with centre and rate; only a change to either makes
                    # in-flight blocks and the pending frame stale. Gain/ppm changes keep them.
                    if (new.center_hz, new.sample_rate) != (old.center_hz, old.sample_rate):
                        self._gen += 1
                self._apply_dsp(old, new, plan)
            self._state = new
            if (new.mode, new.tune_hz, new.sample_rate) != (old.mode, old.tune_hz, old.sample_rate):
                self._reset_rds()  # another station or mode: start decoding afresh
        self._publish_state()
        return self.state

    # --- state helpers ----------------------------------------------------------------------

    @staticmethod
    def _merge(base: RadioState, patch: StatePatch | None) -> RadioState:
        data = base.model_dump()
        if patch is not None:
            upd = patch.model_dump(exclude_unset=True)
            if "mode" in upd and "bw_hz" not in upd:
                upd["bw_hz"] = DEFAULT_BW[upd["mode"]]
            data.update(upd)
        try:
            return RadioState.model_validate(data)
        except ValidationError as exc:
            raise StateError(_fmt_validation(exc)) from exc

    @staticmethod
    def _recentre(s: RadioState, info: DeviceInfo, center_explicit: bool) -> RadioState:
        lo, hi = info.freq_range
        rate = float(min(info.sample_rates, key=lambda r: abs(r - s.sample_rate)))
        limit = SPAN_USABLE * rate
        tune, center = s.tune_hz, s.center_hz
        if center_explicit and abs(tune - center) > limit:
            tune = center
        if not lo <= tune <= hi:
            raise StateError(f"{tune / 1e6:.6f} MHz is outside {info.label}'s range "
                             f"{lo / 1e6:.3f}–{hi / 1e6:.3f} MHz")
        if abs(tune - center) > limit:
            center = tune + rate / 4
        center = min(max(center, lo), hi)
        if abs(tune - center) > limit:
            raise StateError(f"cannot place {tune / 1e6:.6f} MHz inside the receiver's span")
        return s.model_copy(update={"tune_hz": tune, "center_hz": center, "sample_rate": rate})

    @staticmethod
    def _device_fields(s: RadioState) -> tuple:
        return (s.center_hz, s.sample_rate, s.gain_db, s.ppm, s.direct_sampling)

    @staticmethod
    def _device_config(s: RadioState) -> DeviceConfig:
        return DeviceConfig(center_hz=s.center_hz, sample_rate=s.sample_rate, gain_db=s.gain_db,
                            ppm=s.ppm, direct_sampling=s.direct_sampling)

    @staticmethod
    def _with_applied(s: RadioState, cfg: DeviceConfig) -> RadioState:
        return s.model_copy(update={"center_hz": cfg.center_hz, "sample_rate": cfg.sample_rate,
                                    "gain_db": cfg.gain_db, "ppm": cfg.ppm,
                                    "direct_sampling": cfg.direct_sampling})

    def _build_dsp(self, s: RadioState) -> tuple[SpectrumProcessor, Demodulator]:
        try:
            return (SpectrumProcessor(s.fft_size, s.avg_alpha),
                    Demodulator(s.sample_rate, s.mode, s.bw_hz, s.tune_hz - s.center_hz,
                                deemphasis_s=deemphasis_for(self._region)))
        except ValueError as exc:
            raise StateError(str(exc)) from exc

    def _plan_dsp(self, old: RadioState, new: RadioState):
        """Construct/validate the DSP changes without applying them: (spectrum, demod), either may be None."""
        spectrum = demod = None
        if new.sample_rate != old.sample_rate:
            spectrum, demod = self._build_dsp(new)
        else:
            if new.fft_size != old.fft_size:
                try:
                    spectrum = SpectrumProcessor(new.fft_size, new.avg_alpha)
                except ValueError as exc:
                    raise StateError(str(exc)) from exc
            if (new.mode, new.bw_hz) != (old.mode, old.bw_hz):
                try:  # throwaway instance: only validates the passband for this rate
                    Demodulator(new.sample_rate, new.mode, new.bw_hz, 0.0)
                except ValueError as exc:
                    raise StateError(str(exc)) from exc
        return spectrum, demod

    def _apply_dsp(self, old: RadioState, new: RadioState, plan) -> None:
        spectrum, demod = plan
        if spectrum is not None:
            self._spectrum = spectrum
        else:
            self._spectrum.avg_alpha = new.avg_alpha
            if new.center_hz != old.center_hz:
                self._spectrum.reset()
        if demod is not None:
            self._demod = demod
        elif (new.mode, new.bw_hz) != (old.mode, old.bw_hz):
            self._demod.set_mode(new.mode, new.bw_hz)
        # always use the centre the device actually applied
        self._demod.set_offset(new.tune_hz - new.center_hz)
        if new.sample_rate != old.sample_rate:
            self._block = block_size_for(new.sample_rate)

    def _publish_state(self) -> None:
        self.events.publish({"type": "state", "state": self.state.model_dump()})

    def _reset_rds(self) -> None:
        """Call with the lock held. A fresh RDS decoder while running in WFM, otherwise none; the
        published RDS state is forgotten (and its absence announced). Under the lock, so a DSP
        thread update from the old decoder cannot arrive after the announcement."""
        demod = self._demod
        if self._state.running and self._state.mode == "WFM" and demod is not None and demod.mode == "WFM":
            self._rds = RdsDecoder(demod.mpx_rate, self._region)
        else:
            self._rds = None
        if self._rds_snapshot is not None:
            self._rds_snapshot = None
            self.events.publish({"type": "rds", "rds": None})

    # --- threads ----------------------------------------------------------------------------

    def _reader_loop(self, run: _Run) -> None:
        device, ring = run.device, run.ring
        while not run.stop.is_set():
            try:
                with self._dev_lock:
                    gen = self._gen
                    block = device.read(self._block)
            except Exception as exc:
                if not run.stop.is_set():
                    log.warning("device read failed: %s", exc)
                    self._fail(run, exc)
                return
            try:
                ring.put_nowait((gen, block))
            except queue.Full:
                try:
                    ring.get_nowait()
                except queue.Empty:
                    pass
                with self._lock:
                    self._stats.dropped += 1
                try:
                    ring.put_nowait((gen, block))
                except queue.Full:
                    pass

    def _fail(self, run: _Run, exc: Exception) -> None:
        run.stop.set()
        with self._lock:
            current = self._run is run
            if current:
                self._run = None
                self._state = self._state.model_copy(update={"running": False, "audio": False})
                self._reset_rds()
        if not current:  # a newer open()/close() already superseded this run
            return
        self.events.publish({"type": "error", "code": "device_lost", "message": str(exc)})
        self._safe_close(run.device)
        self._publish_state()

    def _dsp_loop(self, run: _Run) -> None:
        device = run.device
        clock = time.perf_counter  # high resolution: time.monotonic ticks in 15.6 ms on Windows
        gate = FrameGate()
        last_stats = clock()
        frames = 0
        pending = None  # (gen, center_hz, sample_rate, spectrum_db) captured when produced
        last_rds = -math.inf
        while not run.stop.is_set():
            try:
                gen, block = run.ring.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                if self._run is not run:
                    return
                if gen != self._gen:
                    pending = None
                    continue
                st = self._state
                db = self._spectrum.process(block)
                rds = self._rds
                # WFM demodulates even without audio: the RDS decoder needs the MPX.
                pcm = self._demod.process(block) if st.audio or rds is not None else None
                rate = self._demod.out_rate
                if rds is not None:
                    rds.process(self._demod.mpx)
                    if rds.changed and clock() - last_rds >= RDS_MIN_INTERVAL:
                        rds.changed = False
                        last_rds = clock()
                        self._rds_snapshot = rds.state.to_dict()
                        self.events.publish({"type": "rds", "rds": self._rds_snapshot})
                if not st.audio:
                    pcm = None
            now = clock()
            if db is not None:
                pending = (gen, st.center_hz, st.sample_rate, db)
            if pending is not None and pending[0] != gen:
                pending = None
            if pending is not None and gate.ready(now, st.fps):
                self._seq += 1
                self.spectrum.publish(SpectrumFrame(self._seq, pending[1], pending[2], pending[3]))
                pending = None
                frames += 1
            if pcm is not None and pcm.size:
                self.audio.publish(AudioChunk(self._seq, rate, pcm))
            if now - last_stats >= 1.0:
                with self._lock:
                    self._stats.frame_rate = frames / (now - last_stats)
                    self._stats.overflows = int(getattr(device, "overflows", 0))
                self.events.publish({"type": "stats", "stats": self.stats.model_dump()})
                frames, last_stats = 0, now
