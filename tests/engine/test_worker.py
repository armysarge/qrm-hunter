import threading
import time

import pytest

from qrm_hunter.engine.worker import Busy, JobRunner, TimedOut


def test_runs_a_job_and_returns_its_result():
    r = JobRunner()
    assert r.run(lambda ev: 42, timeout=1.0) == 42
    assert r.run(lambda ev: 43, timeout=1.0) == 43
    r.shutdown()


def test_errors_propagate():
    r = JobRunner()
    with pytest.raises(ValueError):
        r.run(lambda ev: (_ for _ in ()).throw(ValueError("bad")), timeout=1.0)
    assert r.run(lambda ev: 1, timeout=1.0) == 1
    r.shutdown()


def test_second_request_while_busy_is_refused():
    r = JobRunner()
    started, release = threading.Event(), threading.Event()

    def slow(ev):
        started.set()
        release.wait(2)
        return "done"

    t = threading.Thread(target=lambda: r.run(slow, timeout=5))
    t.start()
    started.wait(1)
    with pytest.raises(Busy):
        r.run(lambda ev: 1, timeout=1.0)
    release.set()
    t.join()
    r.shutdown()


def test_timeout_sets_the_cancel_event_and_stays_busy_until_the_job_stops():
    r = JobRunner()
    seen, finish = threading.Event(), threading.Event()

    def stubborn(ev):
        ev.wait(5)
        seen.set()
        finish.wait(5)
        return None

    with pytest.raises(TimedOut):
        r.run(stubborn, timeout=0.1)
    assert seen.wait(2)
    with pytest.raises(Busy):
        r.run(lambda ev: 1, timeout=1.0)
    finish.set()
    deadline = time.monotonic() + 5
    while True:
        try:
            assert r.run(lambda ev: 1, timeout=1.0) == 1
            break
        except Busy:
            assert time.monotonic() < deadline
            time.sleep(0.01)
    r.shutdown()


def test_a_job_raising_timeout_error_is_not_reported_as_timed_out():
    r = JobRunner()
    with pytest.raises(TimeoutError) as info:
        r.run(lambda ev: (_ for _ in ()).throw(TimeoutError("inner")), timeout=1.0)
    assert not isinstance(info.value, TimedOut)
    r.shutdown()


def test_shutdown_cancels_the_running_job():
    r = JobRunner()
    started, stopped = threading.Event(), threading.Event()

    def job(ev):
        started.set()
        ev.wait(5)
        stopped.set()

    t = threading.Thread(target=lambda: r.run(job, timeout=10))
    t.start()
    assert started.wait(2)
    r.shutdown()
    assert stopped.wait(2)
    t.join(2)
