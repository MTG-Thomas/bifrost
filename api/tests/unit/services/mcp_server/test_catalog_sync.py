from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.mcp_server import catalog_sync


class FakeRedis:
    def __init__(self, revision: int = 0) -> None:
        self.revision = revision
        self.published: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        assert key == catalog_sync.WORKFLOW_CATALOG_REVISION_KEY
        return str(self.revision) if self.revision else None

    async def incr(self, key: str) -> int:
        assert key == catalog_sync.WORKFLOW_CATALOG_REVISION_KEY
        self.revision += 1
        return self.revision

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


def redis_context(redis: FakeRedis):
    @asynccontextmanager
    async def context():
        yield redis

    return context


@pytest.mark.asyncio
async def test_publish_advances_durable_revision_before_broadcast(monkeypatch) -> None:
    redis = FakeRedis(revision=4)
    monkeypatch.setattr(catalog_sync, "get_redis", redis_context(redis))

    revision = await catalog_sync.publish_workflow_catalog_changed()

    assert revision == 5
    assert redis.published == [
        (
            "bifrost:mcp:workflow-catalog",
            json.dumps({
                "type": "mcp_workflow_catalog_changed",
                "revision": 5,
            }),
        )
    ]


@pytest.mark.asyncio
async def test_listener_and_request_reconciliation_use_shared_revision(
    monkeypatch,
) -> None:
    from src.services.mcp_server import server

    refresh = AsyncMock(return_value=3)
    monkeypatch.setattr(server, "refresh_workflow_tools", refresh)
    monkeypatch.setattr(
        catalog_sync,
        "get_workflow_catalog_revision",
        AsyncMock(return_value=8),
    )

    await catalog_sync._handle_workflow_catalog_changed({"revision": 7})
    await catalog_sync.ensure_workflow_catalog_current()

    assert refresh.await_args_list[0].kwargs == {"target_revision": 7}
    assert refresh.await_args_list[1].kwargs == {"target_revision": 8}


@pytest.mark.asyncio
async def test_start_and_stop_use_shared_pubsub_manager(monkeypatch) -> None:
    from src.services.mcp_server import server

    mcp = object()
    subscribe = AsyncMock()
    refresh = AsyncMock(return_value=2)
    unsubscribe = MagicMock()
    monkeypatch.setattr(catalog_sync.pubsub_manager, "subscribe_internal", subscribe)
    monkeypatch.setattr(
        catalog_sync.pubsub_manager,
        "unsubscribe_internal",
        unsubscribe,
    )
    monkeypatch.setattr(server, "refresh_workflow_tools", refresh)

    assert await catalog_sync.start_workflow_catalog_sync(mcp) == 2
    catalog_sync.stop_workflow_catalog_sync()

    subscribe.assert_awaited_once_with(
        catalog_sync.WORKFLOW_CATALOG_CHANNEL,
        catalog_sync._handle_workflow_catalog_changed,
    )
    refresh.assert_awaited_once_with(mcp=mcp, force=True)
    unsubscribe.assert_called_once_with(
        catalog_sync.WORKFLOW_CATALOG_CHANNEL,
        catalog_sync._handle_workflow_catalog_changed,
    )


@pytest.mark.asyncio
async def test_start_unsubscribes_when_initial_catalog_refresh_fails(
    monkeypatch,
) -> None:
    from src.services.mcp_server import server

    subscribe = AsyncMock()
    unsubscribe = MagicMock()
    monkeypatch.setattr(catalog_sync.pubsub_manager, "subscribe_internal", subscribe)
    monkeypatch.setattr(
        catalog_sync.pubsub_manager,
        "unsubscribe_internal",
        unsubscribe,
    )
    monkeypatch.setattr(
        server,
        "refresh_workflow_tools",
        AsyncMock(side_effect=RuntimeError("database unavailable")),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await catalog_sync.start_workflow_catalog_sync(object())

    unsubscribe.assert_called_once_with(
        catalog_sync.WORKFLOW_CATALOG_CHANNEL,
        catalog_sync._handle_workflow_catalog_changed,
    )
