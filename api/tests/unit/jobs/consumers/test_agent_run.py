import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock

import pytest

sys.modules.setdefault("resource", ModuleType("resource"))

from src.jobs.consumers import agent_run  # noqa: E402
from src.jobs.consumers.agent_run import AgentRunConsumer  # noqa: E402


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
