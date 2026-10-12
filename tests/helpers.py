"""Small polling helpers shared by the engine and recording tests."""

import concurrent.futures
import contextlib
import sys
import threading
import time


def wait_until(condition, timeout=10.0, interval=0.02):
    """Poll `condition` until it is truthy or the deadline passes; returns its last value."""
    deadline = time.monotonic() + timeout
    while not (value := condition()) and time.monotonic() < deadline:
        time.sleep(interval)
    return value


def tap_threads():
    return [t for t in threading.enumerate() if t.name == "sdr-tap" and t.is_alive()]


def join_tap_threads(timeout=10.0):
    """Wait for every IQ tap thread to finish; returns the ones still alive."""
    for t in tap_threads():
        t.join(timeout)
    return tap_threads()


@contextlib.contextmanager
def ws_connect(client, url, **kwargs):
    """TestClient.websocket_connect, minus the exit-time CancelledError the client can raise when
    the handler has not finished unwinding yet on a busy machine."""
    session = client.websocket_connect(url, **kwargs)
    ws = session.__enter__()
    try:
        yield ws
    except BaseException:
        info = sys.exc_info()
        with contextlib.suppress(concurrent.futures.CancelledError):
            session.__exit__(*info)
        raise
    with contextlib.suppress(concurrent.futures.CancelledError):
        session.__exit__(None, None, None)
