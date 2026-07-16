from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.services.github_sync import GitHubSyncService


@pytest.mark.asyncio
async def test_commit_workspace_changes_syncs_storage_and_does_not_push_by_default(tmp_path):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")
    events = []

    @asynccontextmanager
    async def checkout():
        events.append("sync-down")
        yield tmp_path
        events.append("sync-up")

    service.repo_manager = SimpleNamespace(checkout=checkout)
    repo = Mock()
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None))
    service._do_push = Mock()

    sha = await service.commit_workspace_changes("agent change")

    assert sha == "a" * 40
    assert events == ["sync-down", "sync-up"]
    service._do_commit.assert_awaited_once_with(tmp_path, repo, "agent change")
    service._do_push.assert_not_called()


@pytest.mark.asyncio
async def test_commit_workspace_changes_pushes_only_when_requested(tmp_path):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(checkout=checkout)
    repo = Mock()
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None))
    service._do_push = Mock(return_value=SimpleNamespace(success=True, commit_sha="b" * 40, error=None))

    sha = await service.commit_workspace_changes("agent change", push=True)

    assert sha == "b" * 40
    service._do_push.assert_called_once_with(tmp_path, repo)
