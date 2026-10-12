"""Small polling helpers shared by the engine and recording tests."""

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
