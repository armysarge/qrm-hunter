import asyncio
import threading

from qrm_hunter.engine.broadcast import Broadcaster


def test_drop_oldest_keeps_latest_items():
    async def main():
        b = Broadcaster(maxsize=2)
        sub = b.subscribe()
        t = threading.Thread(target=lambda: [b.publish(i) for i in range(10)])
        t.start()
        t.join()
        await asyncio.sleep(0.05)
        items = [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]
        return items, sub.dropped

    items, dropped = asyncio.run(main())
    assert items == [8, 9]
    assert dropped == 8


def test_unsubscribed_queue_receives_nothing():
    async def main():
        b = Broadcaster(maxsize=4)
        sub = b.subscribe()
        b.unsubscribe(sub)
        b.publish("x")
        await asyncio.sleep(0.01)
        return sub.queue.qsize(), b.subscriber_count

    assert asyncio.run(main()) == (0, 0)


def test_publish_with_no_subscribers_is_a_no_op():
    Broadcaster().publish(1)
