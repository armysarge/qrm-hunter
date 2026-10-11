"""Runs one job at a time on a dedicated thread, with a timeout and a cooperative cancel event."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Callable, TypeVar

T = TypeVar("T")


class Busy(RuntimeError):
    """A job is already running."""


class TimedOut(RuntimeError):
    """The job did not finish in time; it has been asked to stop."""


class JobRunner:
    def __init__(self, name: str = "analysis") -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        self._busy = threading.Lock()
        self._cancel: threading.Event | None = None
        self._future: Future | None = None

    def run(self, job: Callable[[threading.Event], T], timeout: float) -> T:
        if not self._busy.acquire(blocking=False):
            raise Busy("an analysis is already running")
        cancel = threading.Event()
        self._cancel, self._future = cancel, None

        def wrapped() -> T:
            try:
                return job(cancel)
            finally:
                self._busy.release()  # only when the job itself has stopped

        try:
            future = self._pool.submit(wrapped)
        except BaseException:
            self._busy.release()
            raise
        self._future = future
        try:
            return future.result(timeout=timeout)
        except FutureTimeout:
            if future.done():  # the job itself raised TimeoutError
                return future.result()
            cancel.set()
            raise TimedOut(f"the analysis took longer than {timeout:g} s and was stopped") from None

    def shutdown(self) -> None:
        cancel, future = self._cancel, self._future
        if cancel is not None:
            cancel.set()  # a running job stops at its next check
        if future is not None and future.cancel():  # queued, never started: its own release will not run
            self._busy.release()
        self._pool.shutdown(wait=False, cancel_futures=True)
