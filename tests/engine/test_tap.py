import threading

import pytest
from helpers import join_tap_threads, wait_until
from qrm_hunter.engine.session import RadioSession, StateError
from qrm_hunter.engine.state import StatePatch
from qrm_hunter.sdr.base import DeviceError
from qrm_hunter.sdr.synthetic import SyntheticDevice


def factory(_id):
    return SyntheticDevice(realtime=True)


@pytest.fixture(autouse=True)
def _close_every_session(monkeypatch):
    """Every RadioSession a test creates is closed afterwards, even if the test fails midway."""
    made = []

    class TrackedSession(RadioSession):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setitem(globals(), "RadioSession", TrackedSession)
    yield
    for s in made:
        s.close()
    assert join_tap_threads() == []


class Collector:
    def __init__(self, stop_after=None):
        self.blocks, self.ended = [], threading.Event()
        self.reason = None
        self.stop_after = stop_after

    def consume(self, gen, center, rate, index, block):
        self.blocks.append((gen, center, rate, index, block.size))
        if self.stop_after and len(self.blocks) >= self.stop_after:
            return "enough"
        return None

    def on_end(self, reason):
        self.reason = reason
        self.ended.set()


def test_tap_delivers_contiguous_blocks_and_stops_on_request():
    s = RadioSession(factory)
    s.open("synthetic")
    c = Collector()
    tap = s.open_tap(c.consume, c.on_end)
    assert wait_until(lambda: len(c.blocks) > 3)
    s.close_tap(tap)
    assert c.reason == "stopped" and tap.done.is_set() and len(c.blocks) > 3
    for (_, _, _, i0, n0), (_, _, _, i1, _) in zip(c.blocks, c.blocks[1:]):
        assert i1 == i0 + n0
    assert c.blocks[0][1:3] == (s.state.center_hz, s.state.sample_rate)
    assert (tap.center_hz, tap.sample_rate) == (s.state.center_hz, s.state.sample_rate)


def test_consumer_can_end_the_tap_and_only_one_tap_at_a_time():
    s = RadioSession(factory)
    s.open("synthetic")
    c = Collector(stop_after=2)
    s.open_tap(c.consume, c.on_end)
    with pytest.raises(StateError):
        s.open_tap(Collector().consume, lambda r: None)
    assert c.ended.wait(10) and c.reason == "enough"
    c2 = Collector()
    tap2 = s.open_tap(c2.consume, c2.on_end)  # the first tap is gone
    s.close_tap(tap2)
    assert c2.reason == "stopped"


def test_tap_needs_a_running_radio():
    with pytest.raises(StateError):
        RadioSession(factory).open_tap(lambda *a: None, lambda r: None)


def test_close_and_device_loss_end_the_tap():
    s = RadioSession(factory)
    s.open("synthetic")
    c = Collector()
    s.open_tap(c.consume, c.on_end)
    s.close()
    assert c.ended.wait(10) and c.reason == "closed"

    unplug = threading.Event()  # set once the tap is open, so the loss cannot beat open_tap()

    class Failing(SyntheticDevice):
        def read(self, n):
            if unplug.is_set():
                raise DeviceError("unplugged")
            return super().read(n)

    s2 = RadioSession(lambda _id: Failing(realtime=True))
    s2.open("synthetic")
    c2 = Collector()
    s2.open_tap(c2.consume, c2.on_end)
    unplug.set()
    assert c2.ended.wait(10) and c2.reason == "device_lost"


def test_a_full_queue_still_ends_and_counts_drops():
    s = RadioSession(factory)
    s.open("synthetic")
    release = threading.Event()
    c = Collector()

    def slow(*block):
        release.wait(10)
        return c.consume(*block)

    tap = s.open_tap(slow, c.on_end, max_blocks=2)
    assert wait_until(lambda: tap.dropped > 0)
    tap.end("stopped")
    release.set()
    assert c.ended.wait(10) and c.reason == "stopped"
    # the block in hand plus the queued ones, and at most one a reader offered just before the end
    assert 1 <= len(c.blocks) <= 4


def test_a_failing_consumer_ends_the_tap_with_error():
    s = RadioSession(factory)
    s.open("synthetic")
    c = Collector()

    def broken(*block):
        raise RuntimeError("boom")

    s.open_tap(broken, c.on_end)
    assert c.ended.wait(10) and c.reason == "error"
    assert s.state.running


def test_retune_changes_the_generation_seen_by_the_tap():
    s = RadioSession(factory)
    s.open("synthetic")
    c = Collector()
    tap = s.open_tap(c.consume, c.on_end)
    assert wait_until(lambda: len(c.blocks) > 2)
    s.update(StatePatch(center_hz=9_000_000.0))
    assert wait_until(lambda: any(b[1] == 9_000_000.0 for b in c.blocks[-3:]))
    s.close_tap(tap)
    gens = {b[0] for b in c.blocks}
    assert len(gens) == 2 and any(b[1] == 9_000_000.0 for b in c.blocks)
