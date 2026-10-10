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
