import asyncio


from src.scripts.event_bus import EventBus


class TestSubscribe:
    def test_returns_queue(self):
        bus = EventBus()
        q = bus.subscribe()
        assert isinstance(q, asyncio.Queue)

    def test_each_subscribe_returns_distinct_queue(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        assert q1 is not q2

    def test_tracks_subscribers(self):
        bus = EventBus()
        bus.subscribe()
        bus.subscribe()
        assert len(bus._subscribers) == 2


class TestUnsubscribe:
    def test_removes_subscriber(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        assert q not in bus._subscribers

    def test_noop_when_queue_not_present(self):
        bus = EventBus()
        q = asyncio.Queue()
        bus.unsubscribe(q)
        assert bus._subscribers == []

    def test_only_removes_target_queue(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.unsubscribe(q1)
        assert q1 not in bus._subscribers
        assert q2 in bus._subscribers


class TestPublish:
    def test_publish_without_running_loop_puts_directly(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.publish("refresh")
        msg = q.get_nowait()
        assert msg == {"type": "refresh", "data": {}}

    def test_publish_includes_data(self):
        bus = EventBus()
        q = bus.subscribe()
        bus.publish("refresh", {"count": 3})
        msg = q.get_nowait()
        assert msg["type"] == "refresh"
        assert msg["data"] == {"count": 3}

    def test_publish_broadcasts_to_all_subscribers(self):
        bus = EventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.publish("refresh")
        assert q1.get_nowait() == {"type": "refresh", "data": {}}
        assert q2.get_nowait() == {"type": "refresh", "data": {}}

    def test_publish_with_no_subscribers_is_noop(self):
        bus = EventBus()
        bus.publish("refresh")

    def test_publish_swallows_per_subscriber_errors(self):
        bus = EventBus()
        bad_q = bus.subscribe()
        good_q = bus.subscribe()

        original = bad_q.put_nowait

        def boom(*args, **kwargs):
            raise RuntimeError("full")

        bad_q.put_nowait = boom
        try:
            bus.publish("refresh")
        finally:
            bad_q.put_nowait = original

        assert good_q.get_nowait() == {"type": "refresh", "data": {}}


class TestPublishWithRunningLoop:

    def test_publish_uses_call_soon_threadsafe_when_loop_running(self):
        bus = EventBus()
        q = bus.subscribe()

        received = []

        async def runner():
            loop = asyncio.get_running_loop()
            called = []
            orig = loop.call_soon_threadsafe

            def spy(callback, *args):
                called.append(True)
                return orig(callback, *args)

            loop.call_soon_threadsafe = spy
            try:
                bus.publish("refresh", {"n": 1})
                await asyncio.sleep(0.05)
                assert called
                received.append(q.get_nowait())
            finally:
                loop.call_soon_threadsafe = orig

        asyncio.run(runner())
        assert received == [{"type": "refresh", "data": {"n": 1}}]


class TestModuleBus:
    def test_module_level_bus_is_event_bus_instance(self):
        from src.scripts import event_bus

        assert isinstance(event_bus.bus, EventBus)
