"""RadioSession: owns the device, the reader and DSP threads, and the outgoing broadcasters."""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
from typing import Callable

from pydantic import ValidationError

from ..dsp.demod import DEFAULT_BW, Demodulator
from ..dsp.spectrum import SpectrumProcessor
from ..sdr.base import DeviceConfig, DeviceInfo, SdrDevice
from ..sdr.discovery import create_device
from .broadcast import Broadcaster
from .state import AudioChunk, RadioState, SpectrumFrame, StatePatch, Stats

log = logging.getLogger(__name__)

SPAN_USABLE = 0.45  # fraction of the sample rate either side of centre usable for tuning
RING_SECONDS = 0.5


class StateError(ValueError):
    """A requested state change is invalid. The message is shown to the user."""


def block_size_for(rate: float) -> int:
    return max(4096, 1 << int(round(math.log2(rate / 50))))


def _fmt_validation(exc: ValidationError) -> str:
    return "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors())


class RadioSession:
    def __init__(self, device_factory: Callable[[str], SdrDevice] = create_device) -> None:
        self._factory = device_factory
        self._lock = threading.RLock()
        self._dev_lock = threading.Lock()
        self._device: SdrDevice | None = None
        self._state = RadioState()
        self._stats = Stats()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._ring: queue.Queue = queue.Queue(maxsize=1)
        self._spectrum: SpectrumProcessor | None = None
        self._demod: Demodulator | None = None
        self._block = 32768
        self._gen = 0
        self._seq = 0
        self.spectrum: Broadcaster[SpectrumFrame] = Broadcaster(maxsize=2)
        self.audio: Broadcaster[AudioChunk] = Broadcaster(maxsize=8)
        self.events: Broadcaster[dict] = Broadcaster(maxsize=64)

    # --- public API -------------------------------------------------------------------------

    @property
    def state(self) -> RadioState:
        with self._lock:
            return self._state.model_copy()

    @property
    def stats(self) -> Stats:
        with self._lock:
            return self._stats.model_copy(update={"clients": self.spectrum.subscriber_count})

    def open(self, device_id: str, patch: StatePatch | None = None) -> RadioState:
        self.close()
        device = self._factory(device_id)
        try:
            device.open()
            base = self._state.model_copy(update={"device_id": device_id, "running": False})
            merged = self._merge(base, patch)
            explicit = patch.model_dump(exclude_unset=True) if patch else {}
            if "center_hz" not in explicit:
                merged = merged.model_copy(update={"center_hz": merged.tune_hz + merged.sample_rate / 4})
            merged = self._recentre(merged, device.info, center_explicit=False)
            # Build the DSP before touching the device so a rejected value leaves nothing half-applied.
            spectrum, demod = self._build_dsp(merged)
            applied = device.configure(self._device_config(merged))
        except BaseException:
            device.close()
            raise
        merged = self._with_applied(merged, applied).model_copy(update={"running": True})
        with self._lock:
            self._device = device
            self._state = merged
            self._stats = Stats()
            self._spectrum = spectrum
            self._demod = demod
            self._block = block_size_for(merged.sample_rate)
            self._ring = queue.Queue(maxsize=max(4, math.ceil(RING_SECONDS * merged.sample_rate / self._block)))
            self._gen += 1
            self._stop.clear()
        self._threads = [
            threading.Thread(target=self._reader_loop, args=(device,), name="sdr-reader", daemon=True),
            threading.Thread(target=self._dsp_loop, args=(device,), name="sdr-dsp", daemon=True),
        ]
        for t in self._threads:
            t.start()
        self._publish_state()
        return self.state

    def close(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)
        self._threads = []
        with self._lock:
            device, self._device = self._device, None
            was_running = self._state.running
            self._state = self._state.model_copy(update={"running": False})
        if device is not None:
            try:
                device.close()
            except Exception:
                log.exception("error closing device")
        if was_running:
            self._publish_state()

    def update(self, patch: StatePatch) -> RadioState:
        explicit = patch.model_dump(exclude_unset=True)
        with self._lock:
            old = self._state
            new = self._merge(old, patch)
            device = self._device
            if device is not None and old.running:
                new = self._recentre(new, device.info,
                                     center_explicit="center_hz" in explicit and "tune_hz" not in explicit)
                plan = self._plan_dsp(old, new)  # validates everything before any side effect
                if self._device_fields(new) != self._device_fields(old):
                    with self._dev_lock:
                        applied = device.configure(self._device_config(new))
                        self._gen += 1
                    new = self._with_applied(new, applied)
                self._apply_dsp(old, new, plan)
            self._state = new
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

    @staticmethod
    def _build_dsp(s: RadioState) -> tuple[SpectrumProcessor, Demodulator]:
        try:
            return (SpectrumProcessor(s.fft_size, s.avg_alpha),
                    Demodulator(s.sample_rate, s.mode, s.bw_hz, s.tune_hz - s.center_hz))
        except ValueError as exc:
            raise StateError(str(exc)) from exc

    def _plan_dsp(self, old: RadioState, new: RadioState):
        """Construct/validate the DSP changes without applying them. Returns (spectrum, demod), either may be None."""
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
        else:
            if (new.mode, new.bw_hz) != (old.mode, old.bw_hz):
                self._demod.set_mode(new.mode, new.bw_hz)
            self._demod.set_offset(new.tune_hz - new.center_hz)
        if new.sample_rate != old.sample_rate:
            self._block = block_size_for(new.sample_rate)

    def _publish_state(self) -> None:
        self.events.publish({"type": "state", "state": self.state.model_dump()})

    # --- threads ----------------------------------------------------------------------------

    def _reader_loop(self, device: SdrDevice) -> None:
        while not self._stop.is_set():
            try:
                with self._dev_lock:
                    gen = self._gen
                    block = device.read(self._block)
            except Exception as exc:
                if not self._stop.is_set():
                    log.warning("device read failed: %s", exc)
                    self._fail(device, exc)
                return
            ring = self._ring
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

    def _fail(self, device: SdrDevice, exc: Exception) -> None:
        self.events.publish({"type": "error", "code": "device_lost", "message": str(exc)})
        self._stop.set()
        with self._lock:
            if self._device is device:
                self._device = None
            self._state = self._state.model_copy(update={"running": False})
        try:
            device.close()
        except Exception:
            pass
        self._publish_state()

    def _dsp_loop(self, device: SdrDevice) -> None:
        last_pub = 0.0
        last_stats = time.monotonic()
        frames = 0
        pending = None
        while not self._stop.is_set():
            try:
                gen, block = self._ring.get(timeout=0.2)
            except queue.Empty:
                continue
            with self._lock:
                if gen != self._gen:
                    continue
                st = self._state
                db = self._spectrum.process(block)
                pcm = self._demod.process(block) if st.audio else None
                rate = self._demod.out_rate
            now = time.monotonic()
            if db is not None:
                pending = db
            if pending is not None and now - last_pub >= 1.0 / st.fps:
                self._seq += 1
                self.spectrum.publish(SpectrumFrame(self._seq, st.center_hz, st.sample_rate, pending))
                pending = None
                last_pub = now
                frames += 1
            if pcm is not None and pcm.size:
                self.audio.publish(AudioChunk(self._seq, rate, pcm))
            if now - last_stats >= 1.0:
                with self._lock:
                    self._stats.frame_rate = frames / (now - last_stats)
                    self._stats.overflows = int(getattr(device, "overflows", 0))
                self.events.publish({"type": "stats", "stats": self.stats.model_dump()})
                frames, last_stats = 0, now
