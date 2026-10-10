import asyncio
import threading
import time

import pytest

from qrm_hunter.engine.session import RadioSession, StateError
from qrm_hunter.engine.state import StatePatch
from qrm_hunter.sdr.base import DeviceError
from qrm_hunter.sdr.synthetic import SyntheticDevice

RATE = 2_048_000.0


def factory(_device_id):
    return SyntheticDevice(realtime=True)


def sdr_threads():
    return [t for t in threading.enumerate() if t.name.startswith("sdr-") and t.is_alive()]


def wait_no_threads(timeout=2.0):
    deadline = time.monotonic() + timeout
    while sdr_threads() and time.monotonic() < deadline:
        time.sleep(0.02)
    return sdr_threads()


@pytest.fixture
def session():
    s = RadioSession(factory)
    yield s
    s.close()


async def next_of(sub, predicate, timeout=3.0):
    async def find():
        while True:
            item = await sub.queue.get()
            if predicate(item):
                return item

    return await asyncio.wait_for(find(), timeout)


def test_open_and_close_manage_threads(session):
    state = session.open("synthetic")
    assert state.running and state.device_id == "synthetic"
    assert len(sdr_threads()) == 2
    session.close()
    assert not session.state.running
    assert wait_no_threads() == []


def test_open_offsets_centre_from_tune_to_avoid_dc(session):
    state = session.open("synthetic", StatePatch(tune_hz=7_100_000.0))
    assert state.tune_hz == 7_100_000.0
    assert state.center_hz == 7_100_000.0 + RATE / 4


def test_tune_inside_span_keeps_centre_outside_recentres(session):
    session.open("synthetic", StatePatch(tune_hz=7_100_000.0))
    centre = session.state.center_hz
    assert session.update(StatePatch(tune_hz=7_200_000.0)).center_hz == centre
    s = session.update(StatePatch(tune_hz=14_200_000.0))
    assert s.center_hz == 14_200_000.0 + RATE / 4
    assert s.tune_hz == 14_200_000.0


def test_explicit_centre_move_drags_tune_along_when_needed(session):
    session.open("synthetic", StatePatch(tune_hz=7_100_000.0))
    s = session.update(StatePatch(center_hz=10_000_000.0))
    assert s.center_hz == 10_000_000.0 and s.tune_hz == 10_000_000.0


def test_tune_outside_device_range_is_rejected_and_state_kept(session):
    before = session.open("synthetic")
    with pytest.raises(StateError, match="outside"):
        session.update(StatePatch(tune_hz=5e9))
    assert session.state == before


@pytest.mark.parametrize("patch", [StatePatch(fft_size=1000), StatePatch(db_min=-20.0, db_max=-30.0),
                                   StatePatch(avg_alpha=0.0)])
def test_invalid_values_are_rejected(session, patch):
    before = session.open("synthetic")
    with pytest.raises(StateError):
        session.update(patch)
    assert session.state == before


def test_dsp_rejected_values_leave_state_untouched(session):
    before = session.open("synthetic")
    with pytest.raises(StateError):
        session.update(StatePatch(mode="CW", bw_hz=20000))
    assert session.state == before
    assert session.state.running
    assert len(sdr_threads()) == 2


def test_mode_change_applies_default_bandwidth(session):
    session.open("synthetic")
    assert session.update(StatePatch(mode="USB")).bw_hz == 2700.0
    assert session.update(StatePatch(mode="CW", bw_hz=250.0)).bw_hz == 250.0


def test_update_while_closed_just_stores(session):
    s = session.update(StatePatch(fft_size=4096))
    assert s.fft_size == 4096 and not s.running


def test_spectrum_frames_flow_and_survive_fft_and_rate_changes(session):
    async def main():
        sub = session.spectrum.subscribe()
        await asyncio.to_thread(session.open, "synthetic")
        first = await next_of(sub, lambda f: True)
        await asyncio.to_thread(session.update, StatePatch(fft_size=2048))
        resized = await next_of(sub, lambda f: f.spectrum_db.size == 2048)
        await asyncio.to_thread(session.update, StatePatch(sample_rate=1_024_000.0))
        rerated = await next_of(sub, lambda f: f.sample_rate == 1_024_000.0)
        return first, resized, rerated

    first, resized, rerated = asyncio.run(main())
    assert first.spectrum_db.size == 8192
    assert resized.spectrum_db.size == 2048
    assert rerated.spectrum_db.size == 2048


def test_audio_chunks_when_enabled(session):
    async def main():
        sub = session.audio.subscribe()
        await asyncio.to_thread(session.open, "synthetic", StatePatch(tune_hz=7_245_000.0, audio=True))
        return await next_of(sub, lambda c: c.pcm.size > 0)

    chunk = asyncio.run(main())
    assert chunk.pcm.dtype.name == "int16"
    assert 10_000 < chunk.rate < 14_000


def test_slow_subscriber_is_bounded_and_radio_keeps_running(session):
    async def main():
        sub = session.spectrum.subscribe()
        await asyncio.to_thread(session.open, "synthetic")
        await asyncio.sleep(0.8)  # never read the queue
        return sub.queue.qsize(), sub.dropped

    size, dropped = asyncio.run(main())
    assert size <= 2
    assert dropped > 0
    assert session.state.running


class FailingDevice(SyntheticDevice):
    def __init__(self):
        super().__init__(realtime=True)
        self.reads = 0

    def read(self, n):
        self.reads += 1
        if self.reads > 5:
            raise DeviceError("USB device unplugged")
        return super().read(n)


def test_device_loss_reports_error_and_stops_cleanly():
    session = RadioSession(lambda _id: FailingDevice())

    async def main():
        sub = session.events.subscribe()
        await asyncio.to_thread(session.open, "synthetic")
        err = await next_of(sub, lambda e: e["type"] == "error")
        stopped = await next_of(sub, lambda e: e["type"] == "state" and not e["state"]["running"])
        return err, stopped

    err, stopped = asyncio.run(main())
    assert err["code"] == "device_lost" and "unplugged" in err["message"]
    assert not session.state.running
    assert wait_no_threads() == []
    session.close()  # safe after a failure


def test_reopen_closes_previous_device():
    made = []

    def tracking_factory(_id):
        made.append(SyntheticDevice(realtime=True))
        return made[-1]

    s = RadioSession(tracking_factory)
    s.open("synthetic")
    s.open("synthetic")
    assert made[0]._open is False and made[1]._open is True
    s.close()
    assert wait_no_threads() == []


# --- fix round 1 ---------------------------------------------------------------------------------


def tone_factory(_id):
    from qrm_hunter.sdr.synthetic import Source

    return SyntheticDevice(sources=[Source("cw", 7_150_000.0, -30.0), Source("cw", 14_350_000.0, -30.0)],
                           noise_db=-90.0, realtime=True)


def test_published_frames_always_match_their_own_labels():
    """A frame held back by fps throttling must never be published with a newer centre/rate."""
    s = RadioSession(tone_factory)
    TUNES = (7_100_000.0, 14_200_000.0)
    TONES = (7_150_000.0, 14_350_000.0)  # offsets differ per tune, so a mislabelled frame is detectable

    async def main():
        sub = s.spectrum.subscribe()
        frames = []

        async def collect():
            while True:
                frames.append(await sub.queue.get())

        task = asyncio.create_task(collect())
        # 65536-point FFT => some blocks produce no spectrum, which keeps a pending frame around
        await asyncio.to_thread(s.open, "x", StatePatch(tune_hz=7_100_000.0, fft_size=65536, fps=60.0))
        for i in range(8):
            await asyncio.sleep(0.25)
            await asyncio.to_thread(s.update, StatePatch(tune_hz=TUNES[(i + 1) % 2]))
        await asyncio.sleep(0.4)
        task.cancel()
        return frames

    frames = asyncio.run(main())
    s.close()
    assert len(frames) > 10
    for f in frames:
        n = f.spectrum_db.size
        peak = f.center_hz + (int(f.spectrum_db.argmax()) - n // 2) * f.sample_rate / n
        assert min(abs(peak - t) for t in TONES) < 3 * f.sample_rate / n, (peak, f.center_hz)


def test_concurrent_open_leaves_one_run():
    made = []
    lock = threading.Lock()

    def tracking_factory(_id):
        d = SyntheticDevice(realtime=True)
        with lock:
            made.append(d)
        return d

    s = RadioSession(tracking_factory)
    ts = [threading.Thread(target=s.open, args=("synthetic",)) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(sdr_threads()) == 2
    assert sum(d._open for d in made) == 1
    s.close()
    assert wait_no_threads() == []
    assert not any(d._open for d in made)


class SlowOnceDevice(SyntheticDevice):
    """On its 3rd read, ignores everything for `stall` seconds, then fails like a closed device."""

    def __init__(self, stall):
        super().__init__(realtime=True)
        self.reads = 0
        self.stall = stall

    def read(self, n):
        self.reads += 1
        if self.reads == 3:
            time.sleep(self.stall)
            raise DeviceError("closed under us")
        return super().read(n)


def test_stuck_old_reader_cannot_kill_a_new_session(monkeypatch):
    from qrm_hunter.engine import session as session_mod

    monkeypatch.setattr(session_mod, "JOIN_TIMEOUT", 0.2)
    monkeypatch.setattr(session_mod, "DEV_LOCK_TIMEOUT", 0.2)
    devices = [SlowOnceDevice(1.0), SyntheticDevice(realtime=True)]
    s = RadioSession(lambda _id: devices.pop(0))
    s.open("synthetic")
    time.sleep(0.3)  # old reader is now stuck inside read()
    s.open("synthetic")
    time.sleep(1.5)  # old read finally fails after the new run started
    try:
        assert s.state.running
        assert len(sdr_threads()) == 2
    finally:
        s.close()
    assert wait_no_threads() == []


def test_update_with_unresponsive_device_raises_and_keeps_state(monkeypatch):
    from qrm_hunter.engine import session as session_mod

    monkeypatch.setattr(session_mod, "JOIN_TIMEOUT", 0.2)
    monkeypatch.setattr(session_mod, "DEV_LOCK_TIMEOUT", 0.2)
    s = RadioSession(lambda _id: SlowOnceDevice(1.5))
    s.open("synthetic")
    time.sleep(0.3)
    before = s.state
    with pytest.raises(DeviceError, match="not responding"):
        s.update(StatePatch(center_hz=8_000_000.0))
    assert s.state == before
    s.close()
    assert wait_no_threads(3.0) == []


def test_rejected_reopen_keeps_running_radio(session):
    before = session.open("synthetic")
    with pytest.raises(StateError):
        session.open("synthetic", StatePatch(mode="CW", bw_hz=20000))
    assert session.state == before
    assert session.state.running
    assert len(sdr_threads()) == 2


def test_snapshot_readable_while_lock_held_and_tracks_updates():
    s = RadioSession(factory)
    s.open("synthetic", StatePatch(tune_hz=7_074_000))
    try:
        assert s.snapshot.tune_hz == 7_074_000
        s.update(StatePatch(mode="USB"))
        assert s.snapshot.mode == "USB"
        held, release = threading.Event(), threading.Event()

        def hold():
            with s._lock:
                held.set()
                release.wait(5)

        t = threading.Thread(target=hold)
        t.start()
        assert held.wait(2)
        try:
            result = []
            r = threading.Thread(target=lambda: result.append(s.snapshot))
            r.start()
            r.join(0.5)
            assert not r.is_alive() and result[0].mode == "USB"
        finally:
            release.set()
            t.join()
    finally:
        s.close()
    assert s.snapshot.running is False


# --- spectrum pacing (FrameGate) ---------------------------------------------------------------

def _gate_fps(block_s, fps=25.0, seconds=2.0, quantum=0.0, jitter=0.0, seed=0):
    """Simulate blocks arriving every block_s (optionally jittered and read through a clock that
    ticks in `quantum` steps) and count the frames the gate lets through."""
    import random

    from qrm_hunter.engine.session import FrameGate

    rnd = random.Random(seed)
    gate, t, frames = FrameGate(), 100.0, 0
    end = t + seconds
    while t < end:
        t += block_s + rnd.uniform(-jitter, jitter)
        now = (t // quantum) * quantum if quantum else t
        frames += gate.ready(now, fps)
    return frames / seconds


@pytest.mark.parametrize("quantum", [0.0, 0.015625])  # ideal clock, Windows GetTickCount64 clock
def test_frame_gate_hits_target_fps_with_16ms_blocks(quantum):
    # 2.048 MS/s in 32768-sample blocks: one block every 16 ms. The old "now - last >= 1/fps"
    # gate rounded each 40 ms interval up to 48 ms (~21 fps).
    assert 24.5 <= _gate_fps(32768 / RATE, quantum=quantum) <= 25.5


def test_frame_gate_with_jittery_bursty_blocks():
    assert 24.0 <= _gate_fps(0.016, jitter=0.008, quantum=0.015625) <= 26.0


def test_frame_gate_does_not_burst_after_a_stall():
    from qrm_hunter.engine.session import FrameGate

    gate = FrameGate()
    assert gate.ready(0.0, 25.0)
    assert not gate.ready(0.02, 25.0)
    # a 1 s stall, then blocks every 1 ms: one frame, then normal cadence (no catch-up burst)
    stalled = [gate.ready(1.0 + i * 0.001, 25.0) for i in range(40)]
    assert sum(stalled) == 1


def test_frame_gate_never_exceeds_fps_with_fast_blocks():
    assert _gate_fps(0.001, fps=10.0) <= 10.5


@pytest.mark.performance
def test_real_time_synthetic_publishes_about_25_fps(session):
    async def run():
        sub = session.spectrum.subscribe()
        await asyncio.to_thread(session.open, "synthetic", StatePatch(tune_hz=7_100_000.0))
        try:
            await next_of(sub, lambda f: True)  # skip start-up
            t0, count = time.perf_counter(), 0
            while time.perf_counter() - t0 < 2.0:
                await next_of(sub, lambda f: True)
                count += 1
            return count / (time.perf_counter() - t0)
        finally:
            session.spectrum.unsubscribe(sub)

    fps = asyncio.run(run())
    assert 22.0 <= fps <= 27.0


# --- final review fixes ------------------------------------------------------------------------


def test_close_turns_audio_off_but_open_honours_explicit_audio(session):
    session.open("synthetic", StatePatch(audio=True))
    assert session.state.audio
    session.close()
    assert session.state.audio is False
    assert session.open("synthetic").audio is False  # not resurrected by the next Start
    session.close()
    assert session.open("synthetic", StatePatch(audio=True)).audio is True


def test_device_loss_turns_audio_off():
    session = RadioSession(lambda _id: FailingDevice())

    async def main():
        sub = session.events.subscribe()
        await asyncio.to_thread(session.open, "synthetic", StatePatch(audio=True))
        return await next_of(sub, lambda e: e["type"] == "state" and not e["state"]["running"])

    stopped = asyncio.run(main())
    assert stopped["state"]["audio"] is False
    assert session.state.audio is False
    session.close()

