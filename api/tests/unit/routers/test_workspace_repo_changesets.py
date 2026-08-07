from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from src.routers import workspace_repo_changesets


@pytest.mark.asyncio
async def test_service_uses_authenticated_github_remote(monkeypatch):
    organization_id = uuid4()
    config = SimpleNamespace(
        repo_url="https://github.com/example/repo",
        token="token:/@ value",
        branch="production-live",
    )
    get_config = AsyncMock(return_value=config)
    commit_workspace_changes = AsyncMock(return_value=("a" * 40, None))
    constructor_args = []

    class GitService:
        def __init__(self, db, repo_url, branch):
            constructor_args.append((db, repo_url, branch))
            self.commit_workspace_changes = commit_workspace_changes

    monkeypatch.setattr("src.services.github_config.get_github_config", get_config)
    monkeypatch.setattr("src.services.github_sync.GitHubSyncService", GitService)
    db = Mock()

    service = await workspace_repo_changesets._service(db, organization_id)
    assert service.commit_callback is not None
    result = await service.commit_callback("release source", True)

    assert constructor_args == [
        (
            db,
            "https://x-access-token:token%3A%2F%40%20value@github.com/example/repo.git",
            "production-live",
        )
    ]
    assert result == ("a" * 40, None)
    commit_workspace_changes.assert_awaited_once_with("release source", push=True)
