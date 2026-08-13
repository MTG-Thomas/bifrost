from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.mcp_server import catalog_sync


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.published.append((channel, payload))


def redis_context(redis: FakeRedis):
    @asynccontextmanager
    async def context():
        yield redis

    return context


@pytest.mark.asyncio
async def test_publish_only_broadcasts_committed_revision(monkeypatch) -> None:
    from src.core.cache import redis_client

    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "get_redis", redis_context(redis))

    revision = await catalog_sync.publish_workflow_catalog_changed(5)

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
async def test_get_revision_reads_durable_database_singleton(monkeypatch) -> None:
    from src.core import database

    execute = AsyncMock(
        return_value=SimpleNamespace(scalar_one=lambda: 9)
    )

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(database, "get_db_context", db_context)

    assert await catalog_sync.get_workflow_catalog_revision() == 9
    execute.assert_awaited_once()


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
