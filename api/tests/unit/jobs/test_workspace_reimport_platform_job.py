from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.workspace_writer import current_workspace_writer_label
from src.jobs.platform.reimport import (
    WorkspaceReimportPayload,
    run_workspace_reimport,
)


@pytest.mark.asyncio
async def test_workspace_reimport_runs_under_durable_writer_identity(
    monkeypatch,
) -> None:
    context = SimpleNamespace(
        job_id=uuid4(),
        lease_token=uuid4(),
        report=AsyncMock(),
        log=AsyncMock(),
    )

    @asynccontextmanager
    async def fake_db_context():
        yield AsyncMock()

    class FakeSyncService:
        def __init__(self, **_kwargs):
            pass

        async def reimport_from_repo(self) -> int:
            assert current_workspace_writer_label() == "workspace-reimport"
            return 7

    monkeypatch.setattr("src.core.database.get_db_context", fake_db_context)
    monkeypatch.setattr(
        "src.services.github_sync.GitHubSyncService", FakeSyncService
    )

    result = await run_workspace_reimport(context, WorkspaceReimportPayload())

    assert result["entities_imported"] == 7
    assert current_workspace_writer_label() is None
