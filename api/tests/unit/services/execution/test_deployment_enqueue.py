from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.execution.async_executor import enqueue_workflow_execution


@pytest.mark.asyncio
async def test_queue_failure_leaves_committed_durable_deployment_pin(monkeypatch):
    deployment_id, workflow_id, user_id = uuid4(), uuid4(), uuid4()
    evidence = {
        "solution_deployment_id": str(deployment_id),
        "workflow_name": "run",
    }
    pinned = SimpleNamespace(
        deployment_id=deployment_id,
        name="run",
        queue_evidence=lambda: evidence,
    )
    added = []
    db = SimpleNamespace(
        execute=AsyncMock(),
        get=AsyncMock(return_value=None),
        add=added.append,
        commit=AsyncMock(),
    )

    @asynccontextmanager
    async def db_context():
        yield db

    monkeypatch.setattr("src.core.database.get_db_context", db_context)
    monkeypatch.setattr(
        "src.services.solutions.deployment_runtime.pin_workflow_runtime",
        AsyncMock(return_value=pinned),
    )
    monkeypatch.setattr(
        "src.services.execution.async_executor._publish_scheduled_once",
        AsyncMock(side_effect=OSError("queue unavailable")),
    )
    context = SimpleNamespace(
        solution_deployment_id=None,
        event=None,
        org_id=None,
        user_id=str(user_id),
        name="User",
        email="user@example.com",
        startup=None,
        form_inputs={},
        embed=None,
        is_platform_admin=False,
    )

    workflow_id_value = str(workflow_id)
    parameters = {}
    with pytest.raises(OSError, match="queue unavailable"):
        await enqueue_workflow_execution(context, workflow_id_value, parameters)

    db.commit.assert_awaited_once()
    assert added[0].solution_deployment_id == deployment_id
    assert added[0].runtime_mode == "deployment-v1"
    assert added[0].runtime_evidence == evidence
