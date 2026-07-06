"""Tests for the DLQ operational CLI helpers."""

import pytest
from unittest.mock import AsyncMock

from src.jobs import dlq_cli
from src.jobs.dlq_cli import (
    decode_message,
    discard,
    _describe,
    _fetch_poison_messages,
    _requeue_messages,
    replay,
)


class FakePoisonMessage:
    body = b'{"execution_id":"abc"}'
    message_id = "abc"
    correlation_id = "corr"
    headers = {
        "x-idempotency-key": "abc",
        "x-retry-count": 3,
        "x-replayed-count": 1,
        "x-origin-queue": "workflow-executions",
    }


def test_decode_message_handles_valid_json():
    assert decode_message(b'{"ok": true}') == {"ok": True}


def test_decode_message_handles_malformed_body():
    assert decode_message(b"{not-json") == "{not-json"


def test_describe_includes_operational_metadata():
    row = _describe("workflow-executions", FakePoisonMessage())

    assert row["poison_queue"] == "workflow-executions-poison"
    assert row["idempotency_key"] == "abc"
    assert row["retry_count"] == 3
    assert row["replay_count"] == 1
    assert row["body"] == {"execution_id": "abc"}


class FakePoisonQueue:
    def __init__(self, messages):
        self._messages = list(messages)

    async def get(self, *, fail: bool, no_ack: bool):
        del fail, no_ack
        if not self._messages:
            return None
        return self._messages.pop(0)


class FakeMessage:
    def __init__(self, message_id: str, body: bytes = b'{"ok":true}'):
        self.message_id = message_id
        self.body = body
        self.correlation_id = f"corr-{message_id}"
        self.headers = {"x-replayed-count": 2, "x-idempotency-key": message_id}
        self.nacked = False
        self.acked = False

    async def nack(self, *, requeue: bool):
        assert requeue is True
        self.nacked = True

    async def ack(self):
        self.acked = True


@pytest.mark.asyncio
async def test_fetch_poison_messages_stops_at_limit_and_empty_queue():
    queue = FakePoisonQueue([FakeMessage("one"), FakeMessage("two")])

    messages = await _fetch_poison_messages(queue, limit=3)

    assert [message.message_id for message in messages] == ["one", "two"]
    assert await _fetch_poison_messages(queue, limit=1) == []


@pytest.mark.asyncio
async def test_requeue_messages_nacks_each_message_once():
    messages = [FakeMessage("one"), FakeMessage("two")]

    await _requeue_messages(messages)

    assert all(message.nacked for message in messages)


class FakeDefaultExchange:
    def __init__(self):
        self.published = []

    async def publish(self, message, *, routing_key: str):
        self.published.append((message, routing_key))


class FakeChannel:
    def __init__(self, messages):
        self.messages = messages
        self.default_exchange = FakeDefaultExchange()
        self.closed = False
        self.declared = []

    async def declare_queue(self, name, **kwargs):
        self.declared.append((name, kwargs))
        return FakePoisonQueue(self.messages)

    async def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, channel):
        self.channel_obj = channel

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def channel(self):
        return self.channel_obj


@pytest.mark.asyncio
async def test_replay_dry_run_describes_and_requeues_without_publish(monkeypatch):
    messages = [FakeMessage("one")]
    channel = FakeChannel(messages)
    monkeypatch.setattr(dlq_cli, "_connect", AsyncMock(return_value=FakeConnection(channel)))

    rows = await replay("workflow-executions", limit=1, dry_run=True)

    assert rows[0]["message_id"] == "one"
    assert messages[0].nacked is True
    assert messages[0].acked is False
    assert channel.default_exchange.published == []
    assert channel.closed is True


@pytest.mark.asyncio
async def test_replay_publishes_with_incremented_replay_headers(monkeypatch):
    messages = [FakeMessage("one", body=b'{"execution_id":"one"}')]
    channel = FakeChannel(messages)
    monkeypatch.setattr(dlq_cli, "_connect", AsyncMock(return_value=FakeConnection(channel)))

    rows = await replay("workflow-executions", limit=1, dry_run=False)

    assert rows[0]["message_id"] == "one"
    assert messages[0].acked is True
    published, routing_key = channel.default_exchange.published[0]
    assert routing_key == "workflow-executions"
    assert published.headers["x-replayed-count"] == 3
    assert published.headers["x-retry-count"] == 0
    assert published.headers["x-original-message-id"] == "one"


@pytest.mark.asyncio
async def test_discard_dry_run_requeues_without_ack(monkeypatch):
    messages = [FakeMessage("one"), FakeMessage("two")]
    channel = FakeChannel(messages)
    monkeypatch.setattr(dlq_cli, "_connect", AsyncMock(return_value=FakeConnection(channel)))

    rows = await discard("workflow-executions", limit=2, reason="bad payload", dry_run=True)

    assert [row["message_id"] for row in rows] == ["one", "two"]
    assert all(message.nacked for message in messages)
    assert not any(message.acked for message in messages)
