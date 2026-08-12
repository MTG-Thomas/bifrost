from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.platform.base import PlatformJobFailure
from src.jobs.platform.registry import get_platform_job_definition
from src.jobs.platform.workspace_repo_closure import (
    WORKSPACE_REPO_CLOSURE_DEFINITION,
    WorkspaceRepoClosurePayload,
    run_workspace_repo_closure,
)


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        job_id=uuid4(),
        lease_token=uuid4(),
        report=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_workspace_closure_job_runs_under_durable_writer_identity(
    monkeypatch,
) -> None:
    context = _context()
    result = SimpleNamespace(
        failure_detail=None,
        error=None,
        model_dump=lambda **_kwargs: {"status": "committed"},
    )
    service = SimpleNamespace(activate=AsyncMock(return_value=result))

    @asynccontextmanager
    async def fake_db_context():
        yield AsyncMock()

    monkeypatch.setattr("src.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.build_workspace_repo_changeset_service",
        AsyncMock(return_value=service),
    )
    payload = WorkspaceRepoClosurePayload(
        changeset_id=uuid4(),
        organization_id=uuid4(),
        operation="activate",
        commit_message="close workspace",
        push=True,
        updated_by="operator@example.com",
    )

    response = await run_workspace_repo_closure(context, payload)

    assert response == {"changeset": {"status": "committed"}}
    service.activate.assert_awaited_once()
    call = service.activate.await_args
    assert call.kwargs["writer_job_id"] == context.job_id
    assert context.report.await_count == 3


@pytest.mark.asyncio
async def test_workspace_closure_job_exposes_recoverable_git_failure(
    monkeypatch,
) -> None:
    context = _context()
    result = SimpleNamespace(
        failure_detail={"phase": "git_push", "state": "failed"},
        error="push rejected",
    )
    service = SimpleNamespace(retry_git_closure=AsyncMock(return_value=result))

    @asynccontextmanager
    async def fake_db_context():
        yield AsyncMock()

    monkeypatch.setattr("src.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.build_workspace_repo_changeset_service",
        AsyncMock(return_value=service),
    )
    payload = WorkspaceRepoClosurePayload(
        changeset_id=uuid4(),
        organization_id=uuid4(),
        operation="retry",
        commit_message="close workspace",
        push=True,
        updated_by="operator@example.com",
    )

    with pytest.raises(PlatformJobFailure, match="push rejected") as exc_info:
        await run_workspace_repo_closure(context, payload)

    assert exc_info.value.retryable is True
    service.retry_git_closure.assert_awaited_once()


def test_workspace_closure_job_has_single_writer_runner_loss_policy() -> None:
    registered = get_platform_job_definition("workspace.repo-closure")

    assert registered is WORKSPACE_REPO_CLOSURE_DEFINITION
    assert registered.policy.max_concurrency == 1
    assert registered.policy.max_attempts == 1
    assert registered.policy.retry_on_runner_loss is False
