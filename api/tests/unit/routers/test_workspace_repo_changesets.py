from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import SecretStr
from src.routers import workspace_repo_changesets


@pytest.mark.asyncio
async def test_service_uses_github_app_writer_without_saved_token(monkeypatch):
    organization_id = uuid4()
    config = SimpleNamespace(
        repo_url="https://github.com/example/repo",
        token=None,
        branch="production-live",
    )
    get_config = AsyncMock(return_value=config)
    settings = SimpleNamespace(
        github_app_commit_writer_configured=True,
        github_app_id=123,
        github_app_installation_id=456,
        github_app_private_key=SecretStr("private-key"),
    )
    constructor_args = []
    writer = Mock()

    def build_writer(**kwargs):
        constructor_args.append(kwargs)
        return writer

    # _service imports dependencies lazily so test settings remain patchable.
    monkeypatch.setattr("src.services.github_config.get_github_config", get_config)
    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    monkeypatch.setattr(
        "src.services.platform_commit_writer.GitHubAppCommitWriter", build_writer
    )
    db = Mock()

    service = await workspace_repo_changesets._service(db, organization_id)

    assert service.commit_writer is writer
    assert constructor_args == [
        {
            "repo_url": "https://github.com/example/repo",
            "branch": "production-live",
            "app_id": 123,
            "installation_id": 456,
            "private_key": "private-key",
        }
    ]


@pytest.mark.asyncio
async def test_service_does_not_fall_back_to_saved_personal_token(monkeypatch):
    organization_id = uuid4()
    config = SimpleNamespace(
        repo_url="https://github.com/example/repo",
        token="saved-personal-token",
        branch="production-live",
    )
    settings = SimpleNamespace(github_app_commit_writer_configured=False)
    # _service imports dependencies lazily so test settings remain patchable.
    monkeypatch.setattr(
        "src.services.github_config.get_github_config", AsyncMock(return_value=config)
    )
    monkeypatch.setattr("src.config.get_settings", lambda: settings)

    service = await workspace_repo_changesets._service(Mock(), organization_id)

    assert service.commit_writer is None
