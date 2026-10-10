"""Thread-to-asyncio fan-out with bounded, drop-oldest queues per subscriber."""

from __future__ import annotations

import asyncio
import threading
from typing import Generic, TypeVar

T = TypeVar("T")


class Subscription(Generic[T]):
    def __init__(self, loop: asyncio.AbstractEventLoop, maxsize: int) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[T] = asyncio.Queue(maxsize)
        self.dropped = 0


def _offer(sub: Subscription, item) -> None:
    if sub.queue.full():
        sub.queue.get_nowait()
        sub.dropped += 1
    sub.queue.put_nowait(item)


class Broadcaster(Generic[T]):
    def __init__(self, maxsize: int = 2) -> None:
        self.maxsize = maxsize
        self._subs: set[Subscription[T]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> Subscription[T]:
        sub: Subscription[T] = Subscription(asyncio.get_running_loop(), self.maxsize)
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription[T]) -> None:
        with self._lock:
            self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, item: T) -> None:
        with self._lock:
            subs = list(self._subs)
        for sub in subs:
            try:
                sub.loop.call_soon_threadsafe(_offer, sub, item)
            except RuntimeError:  # the subscriber's loop has closed
                self.unsubscribe(sub)
