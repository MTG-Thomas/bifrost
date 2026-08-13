import asyncio
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.modules.setdefault("resource", ModuleType("resource"))

from src.jobs.consumers import agent_run  # noqa: E402
from src.jobs.consumers.agent_run import AgentRunConsumer  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "claimable"),
    [
        ("scheduled", False),
        ("queued", True),
        ("running", False),
        ("completed", False),
    ],
)
async def test_durable_agent_run_claim_serializes_with_publisher(
    status: str,
    claimable: bool,
) -> None:
    row = SimpleNamespace(status=status)
    db = AsyncMock()
    db.get.return_value = row
    db_context = AsyncMock()
    db_context.__aenter__.return_value = db
    db_context.__aexit__.return_value = False

    with patch.object(AgentRunConsumer, "__init__", lambda self: None):
        consumer = AgentRunConsumer()
    consumer._session_factory = MagicMock(return_value=db_context)

    result = await consumer._claim_durable_run(str(uuid4()))

    assert result is claimable
    assert "bifrost:agent-run:" in str(db.execute.await_args.args[0])
    if claimable:
        assert row.status == "running"
        db.commit.assert_awaited_once()
    else:
        db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_watcher_cancels_task_when_redis_flag_exists(monkeypatch):
    monkeypatch.setattr(agent_run, "CANCEL_CHECK_INTERVAL", 0)
    redis_client = AsyncMock()
    redis_client.get.return_value = "1"

    task = asyncio.create_task(asyncio.sleep(60))

    await AgentRunConsumer._cancel_watcher("run-123", task, redis_client)
    try:
        await task
    except asyncio.CancelledError:
        # Expected: the watcher cancels the task after seeing the Redis flag.
        pass

    assert task.cancelled()
    redis_client.get.assert_awaited_once_with("bifrost:agent_run:run-123:cancel")


@pytest.mark.asyncio
async def test_cancel_watcher_ignores_transient_redis_errors(monkeypatch):
    monkeypatch.setattr(agent_run, "CANCEL_CHECK_INTERVAL", 0)
    redis_client = AsyncMock()
    redis_client.get.side_effect = [RuntimeError("redis unavailable"), None]

    task = asyncio.create_task(asyncio.sleep(0))

    await AgentRunConsumer._cancel_watcher("run-456", task, redis_client)

    assert task.done()
    assert not task.cancelled()
    assert redis_client.get.await_count >= 1
