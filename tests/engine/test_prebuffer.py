import time

import numpy as np
import pytest

from qrm_hunter.engine.prebuffer import MAX_PREBUFFER_S, PreBuffer
from qrm_hunter.engine.session import RadioSession
from qrm_hunter.engine.state import StatePatch
from qrm_hunter.sdr.sigmf_io import from_ci16, to_ci16
from qrm_hunter.sdr.synthetic import SyntheticDevice

RATE = 1000.0


def ramp(start, n):
    k = np.arange(start, start + n) % 1000
    return (k / 1000.0 - 1j * k / 2000.0).astype(np.complex64)


def q(x):
    return from_ci16(to_ci16(x))


def test_int16_round_trip_is_within_one_lsb_and_clips_at_full_scale():
    rng = np.random.default_rng(0)
    x = (rng.uniform(-1, 1, 1000) + 1j * rng.uniform(-1, 1, 1000)).astype(np.complex64)
    raw = to_ci16(x)
    assert raw.dtype == np.int16 and raw.size == 2000
    assert np.max(np.abs(from_ci16(raw) - x)) <= 1 / 32767
    assert from_ci16(to_ci16(np.array([1.5 - 2.0j], np.complex64)))[0] == pytest.approx(1 - 1j)


def test_snapshot_returns_the_latest_samples_after_wrap_around():
    pb = PreBuffer(2.0)  # 2000 samples at RATE
    for i in range(7):
        pb.write(ramp(i * 700, 700), 1, 7e6, RATE, i * 700)
    snap = pb.snapshot()
    assert snap.iq.size == 2000 and snap.iq.dtype == np.complex64
    assert np.array_equal(snap.iq, q(ramp(4900 - 2000, 2000)))
    assert (snap.end_index, snap.center_hz, snap.sample_rate, snap.gen) == (4900, 7e6, RATE, 1)
    assert snap.seconds == pytest.approx(2.0)


def test_snapshot_length_is_limited_to_what_is_held():
    pb = PreBuffer(2.0)
    pb.write(ramp(0, 500), 1, 7e6, RATE, 0)
    assert pb.snapshot(10.0).iq.size == 500
    tail = pb.snapshot(0.1)
    assert np.array_equal(tail.iq, q(ramp(400, 100))) and tail.end_index == 500


def test_a_block_larger_than_the_ring_keeps_its_tail():
    pb = PreBuffer(1.0)
    pb.write(ramp(0, 2500), 1, 7e6, RATE, 0)
    assert np.array_equal(pb.snapshot().iq, q(ramp(1500, 1000)))


def test_retune_clears_the_ring_and_rate_change_resizes_it():
    pb = PreBuffer(1.0)
    pb.write(ramp(0, 800), 1, 7e6, RATE, 0)
    pb.write(ramp(800, 300), 2, 7.2e6, RATE, 800)
    snap = pb.snapshot()
    assert (snap.iq.size, snap.gen, snap.center_hz) == (300, 2, 7.2e6)
    pb.write(ramp(0, 3000), 3, 7.2e6, 2000.0, 0)
    assert pb.snapshot().iq.size == 2000


def test_t0_is_the_wall_clock_time_of_the_first_sample():
    pb = PreBuffer(2.0)
    pb.write(ramp(0, 1000), 1, 7e6, RATE, 0, t_end=100.0)
    assert pb.snapshot(0.5).t0 == pytest.approx(99.5)


def test_disabled_empty_and_limits():
    off = PreBuffer(0)
    off.write(ramp(0, 10), 1, 7e6, RATE, 0)
    assert not off.enabled and off.snapshot() is None
    assert PreBuffer(1.0).snapshot() is None
    with pytest.raises(ValueError):
        PreBuffer(MAX_PREBUFFER_S + 1)
    with pytest.raises(ValueError):
        PreBuffer(-1)


def factory(_id):
    return SyntheticDevice(realtime=True)


def test_session_fills_the_prebuffer_and_keeps_it_after_close():
    s = RadioSession(factory, prebuffer_s=2.0)
    try:
        s.open("synthetic")
        deadline = time.monotonic() + 10.0
        while (snap := s.snapshot_iq()) is None or snap.seconds < 0.3:  # a loaded machine fills it slowly
            assert time.monotonic() < deadline, "prebuffer did not fill"
            time.sleep(0.05)
        assert 0.3 <= snap.seconds <= 2.0
        assert (snap.center_hz, snap.sample_rate) == (s.state.center_hz, s.state.sample_rate)
        gen, center, rate = s.current_generation
        assert (snap.gen, center, rate) == (gen, s.state.center_hz, s.state.sample_rate)
    finally:
        s.close()
    assert s.snapshot_iq() is not None


def test_session_retune_clears_the_prebuffer():
    s = RadioSession(factory, prebuffer_s=5.0)
    try:
        s.open("synthetic")
        deadline = time.monotonic() + 10.0
        while (snap := s.snapshot_iq()) is None or snap.seconds < 0.3:
            assert time.monotonic() < deadline, "prebuffer did not fill"
            time.sleep(0.05)
        retuned = time.monotonic()  # taken before update(): samples are kept from inside it too
        s.update(StatePatch(center_hz=8_000_000.0))
        snap = s.snapshot_iq()
        while snap.center_hz != 8_000_000.0 and time.monotonic() - retuned < 10.0:
            time.sleep(0.05)  # the first blocks after a retune can take a moment to render
            snap = s.snapshot_iq()
        assert snap.center_hz == 8_000_000.0
        assert snap.seconds <= time.monotonic() - retuned + 0.1  # nothing from before the retune
    finally:
        s.close()


def test_an_invalid_prebuffer_length_never_opens_the_device():
    opened = []

    class Tracked(SyntheticDevice):
        def open(self):
            opened.append(self)
            super().open()

    s = RadioSession(lambda _id: Tracked(realtime=True), prebuffer_s=90)
    with pytest.raises(ValueError):
        s.open("synthetic")
    assert opened == [] and not s.state.running


def test_session_without_prebuffer():
    s = RadioSession(factory, prebuffer_s=0)
    try:
        s.open("synthetic")
        time.sleep(0.3)
        assert s.snapshot_iq() is None
    finally:
        s.close()
