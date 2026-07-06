from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core import redis_reconnect
from src.core.redis_reconnect import ResilientPubSubListener


class _FakePubSub:
    def __init__(self, messages: list[dict] | None = None) -> None:
        self.subscribed: list[str] = []
        self.psubscribed: list[str] = []
        self.closed = False
        self._messages = list(messages or [])

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def psubscribe(self, pattern: str) -> None:
        self.psubscribed.append(pattern)

    async def close(self) -> None:
        self.closed = True

    async def get_message(self, *, ignore_subscribe_messages: bool, timeout: float):
        assert ignore_subscribe_messages is True
        assert timeout == 0.5
        if self._messages:
            return self._messages.pop(0)
        return None


class _FakeRedis:
    def __init__(self, pubsub: _FakePubSub) -> None:
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self) -> _FakePubSub:
        return self._pubsub

    async def close(self) -> None:
        self.closed = True


async def _record_message(
    sink: list[tuple[str, dict]], channel: str, payload: dict
) -> None:
    sink.append((channel, payload))


@pytest.mark.asyncio
async def test_connect_cleans_existing_connection_and_subscribes_channels(monkeypatch) -> None:
    old_pubsub = _FakePubSub()
    old_redis = _FakeRedis(old_pubsub)
    new_pubsub = _FakePubSub()
    new_redis = _FakeRedis(new_pubsub)
    created_urls: list[str] = []

    def fake_from_url(url: str) -> _FakeRedis:
        created_urls.append(url)
        return new_redis

    monkeypatch.setattr(redis_reconnect.Redis, "from_url", fake_from_url)
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        channels=["jobs", "events"],
        patterns=["bifrost:*"],
        on_message=AsyncMock(),
    )
    listener._pubsub = old_pubsub
    listener._redis = old_redis

    assert await listener._connect() is True

    assert created_urls == ["redis://example"]
    assert old_pubsub.closed is True
    assert old_redis.closed is True
    assert new_pubsub.subscribed == ["jobs", "events"]
    assert new_pubsub.psubscribed == ["bifrost:*"]
    assert listener._redis is new_redis
    assert listener._pubsub is new_pubsub


@pytest.mark.asyncio
async def test_connect_returns_false_when_subscription_setup_fails(monkeypatch) -> None:
    class BrokenPubSub(_FakePubSub):
        async def subscribe(self, channel: str) -> None:
            raise RuntimeError(f"cannot subscribe {channel}")

    monkeypatch.setattr(
        redis_reconnect.Redis,
        "from_url",
        lambda _url: _FakeRedis(BrokenPubSub()),
    )
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        channels=["jobs"],
        on_message=AsyncMock(),
    )

    assert await listener._connect() is False


@pytest.mark.asyncio
async def test_cleanup_best_effort_closes_and_clears_connections() -> None:
    class BrokenClose:
        async def close(self) -> None:
            raise RuntimeError("already closed")

    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=AsyncMock(),
    )
    listener._pubsub = BrokenClose()  # type: ignore[assignment]
    listener._redis = BrokenClose()  # type: ignore[assignment]

    await listener._cleanup()

    assert listener._pubsub is None
    assert listener._redis is None


@pytest.mark.asyncio
async def test_handle_message_decodes_channel_messages_and_patterns() -> None:
    seen: list[tuple[str, dict]] = []
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=lambda channel, payload: _record_message(seen, channel, payload),
    )

    await listener._handle_message({
        "type": "message",
        "channel": b"jobs",
        "data": json.dumps({"id": 1}),
    })
    await listener._handle_message({
        "type": "pmessage",
        "channel": "bifrost:events",
        "data": json.dumps({"event": "ready"}),
    })

    assert seen == [
        ("jobs", {"id": 1}),
        ("bifrost:events", {"event": "ready"}),
    ]


@pytest.mark.asyncio
async def test_handle_message_decodes_byte_pattern_channels() -> None:
    seen: list[tuple[str, dict]] = []
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=lambda channel, payload: _record_message(seen, channel, payload),
    )

    await listener._handle_message({
        "type": "pmessage",
        "channel": b"bifrost:events",
        "data": json.dumps({"event": "ready"}),
    })

    assert seen == [("bifrost:events", {"event": "ready"})]


@pytest.mark.asyncio
async def test_listen_dispatches_messages_until_stopped(monkeypatch) -> None:
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=AsyncMock(),
    )
    listener._running = True
    listener._pubsub = _FakePubSub([
        {"type": "message", "channel": "jobs", "data": json.dumps({"id": 1})}
    ])  # type: ignore[assignment]
    handled: list[dict] = []

    async def handle_once(message: dict) -> None:
        handled.append(message)
        listener._running = False

    monkeypatch.setattr(listener, "_handle_message", handle_once)

    await listener._listen()

    assert handled == [{"type": "message", "channel": "jobs", "data": '{"id": 1}'}]


@pytest.mark.asyncio
async def test_listen_returns_when_no_pubsub() -> None:
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=AsyncMock(),
    )
    listener._running = True

    await listener._listen()

    assert listener._running is True


@pytest.mark.asyncio
async def test_handle_message_logs_and_swallows_bad_payloads(caplog) -> None:
    failing_callback = AsyncMock(side_effect=RuntimeError("handler failed"))
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=failing_callback,
    )

    await listener._handle_message({
        "type": "message",
        "channel": "jobs",
        "data": "{not-json",
    })
    await listener._handle_message({
        "type": "message",
        "channel": "jobs",
        "data": json.dumps({"id": 1}),
    })

    assert "Invalid JSON in pub/sub message" in caplog.text
    assert "Error handling pub/sub message" in caplog.text


@pytest.mark.asyncio
async def test_start_rejects_duplicate_start_and_stop_cancels_task(monkeypatch) -> None:
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=AsyncMock(),
    )

    async def wait_until_cancelled() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(listener, "_listener_loop", wait_until_cancelled)
    task = await listener.start()

    with pytest.raises(RuntimeError, match="already running"):
        await listener.start()

    assert listener.is_healthy() is True

    await listener.stop()

    assert task.cancelled()
    assert listener.is_healthy() is False


@pytest.mark.asyncio
async def test_listener_loop_backs_off_and_recovers_after_failed_connects(monkeypatch) -> None:
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=AsyncMock(),
        initial_backoff=0.25,
        max_backoff=0.5,
        backoff_multiplier=2,
    )
    listener._running = True
    listener._consecutive_failures = 1
    connect = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(listener, "_connect", connect)
    monkeypatch.setattr(listener, "_listen", AsyncMock(side_effect=asyncio.CancelledError))
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(redis_reconnect.asyncio, "sleep", fake_sleep)

    await listener._listener_loop()

    assert connect.await_count == 2
    assert sleeps == [0.25]
    assert listener._consecutive_failures == 0


@pytest.mark.asyncio
async def test_listener_loop_records_lost_connection_and_stops_after_sleep(monkeypatch) -> None:
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=AsyncMock(),
        initial_backoff=1,
        extended_failure_threshold=1,
    )
    listener._running = True
    monkeypatch.setattr(listener, "_connect", AsyncMock(return_value=True))
    monkeypatch.setattr(listener, "_listen", AsyncMock(side_effect=RuntimeError("redis lost")))
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        listener._running = False

    monkeypatch.setattr(redis_reconnect.asyncio, "sleep", fake_sleep)

    await listener._listener_loop()

    assert sleeps == [1]
    assert listener._consecutive_failures == 1


def test_is_healthy_requires_running_task_below_failure_threshold() -> None:
    listener = ResilientPubSubListener(
        redis_url="redis://example",
        on_message=AsyncMock(),
        extended_failure_threshold=2,
    )

    assert listener.is_healthy() is False

    listener._running = True
    listener._listener_task = SimpleNamespace(done=lambda: False)  # type: ignore[assignment]
    listener._consecutive_failures = 1
    assert listener.is_healthy() is True

    listener._consecutive_failures = 2
    assert listener.is_healthy() is False
