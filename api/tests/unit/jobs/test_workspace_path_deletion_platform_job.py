from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.platform.workspace_path_deletion import (
    WORKSPACE_PATH_DELETION_DEFINITION,
    WorkspacePathDeletionPayload,
    run_workspace_path_deletion,
)


@pytest.mark.asyncio
async def test_workspace_path_deletion_runs_under_durable_writer(monkeypatch) -> None:
    context = SimpleNamespace(
        job_id=uuid4(),
        lease_token=uuid4(),
        report=AsyncMock(),
    )
    db = AsyncMock()
    checkpoint = AsyncMock()
    mark_dirty = AsyncMock()
    delete = AsyncMock(return_value=3)

    @asynccontextmanager
    async def fake_db_context():
        yield db

    monkeypatch.setattr("src.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.core.workspace_writer.checkpoint_workspace_writer_lease", checkpoint
    )
    monkeypatch.setattr("src.core.repo_dirty.mark_repo_dirty", mark_dirty)
    monkeypatch.setattr(
        "src.services.workspace_path_deletion.delete_workspace_path_recursively",
        delete,
    )

    result = await run_workspace_path_deletion(
        context, WorkspacePathDeletionPayload(path="apps/example")
    )

    assert result == {"path": "apps/example", "deleted_entries": 3}
    checkpoint.assert_awaited_once_with(db)
    mark_dirty.assert_awaited_once_with(writer="delete:apps/example")
    delete.assert_awaited_once()
    assert context.report.await_count == 2


def test_workspace_path_deletion_is_registered_as_global_serial_job() -> None:
    registered = get_platform_job_definition("workspace.delete-path")

    assert registered is WORKSPACE_PATH_DELETION_DEFINITION
    assert registered.policy.max_concurrency == 1
    assert registered.policy.max_attempts == 3
    assert registered.policy.retry_on_runner_loss is True
