from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi import HTTPException, status

from src.models import CommitRequest, GitOpRequest, ValidateTokenRequest
from src.routers import github
from src.services.github_api import GitHubAPIError


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(org_id=UUID("11111111-1111-1111-1111-111111111111"))


def _user() -> SimpleNamespace:
    return SimpleNamespace(email="admin@example.com", user_id="user-1")


def _config(
    *,
    token: str | None = "ghp_test",
    repo_url: str | None = "https://github.com/acme/repo.git",
    branch: str = "main",
) -> SimpleNamespace:
    return SimpleNamespace(
        token=token,
        repo_url=repo_url,
        branch=branch,
        last_synced_at="2026-07-05T00:00:00Z",
    )


@pytest.mark.asyncio
async def test_validate_github_token_requires_token() -> None:
    with pytest.raises(HTTPException) as exc:
        await github.validate_github_token(
            ValidateTokenRequest(token=""),
            _ctx(),
            _user(),
            AsyncMock(),
        )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "GitHub token required"


@pytest.mark.asyncio
async def test_validate_github_token_saves_token_and_maps_repositories() -> None:
    client = SimpleNamespace(
        list_repositories=AsyncMock(
            return_value=[
                {
                    "name": "repo",
                    "full_name": "acme/repo",
                    "description": "Test repo",
                    "url": "https://github.com/acme/repo",
                    "private": True,
                }
            ]
        )
    )

    with (
        patch.object(github, "GitHubAPIClient", return_value=client),
        patch.object(github, "save_github_config", AsyncMock()) as save_config,
    ):
        result = await github.validate_github_token(
            ValidateTokenRequest(token="ghp_test"),
            _ctx(),
            _user(),
            AsyncMock(),
        )

    assert result.repositories[0].full_name == "acme/repo"
    save_config.assert_awaited_once()
    assert save_config.await_args.kwargs["repo_url"] is None
    assert save_config.await_args.kwargs["branch"] == "main"


@pytest.mark.asyncio
async def test_list_github_repos_translates_github_api_error() -> None:
    client = SimpleNamespace(
        list_repositories=AsyncMock(
            side_effect=GitHubAPIError("rate limited", status_code=403)
        )
    )

    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "GitHubAPIClient", return_value=client),
    ):
        with pytest.raises(HTTPException) as exc:
            await github.list_github_repos(_ctx(), _user(), AsyncMock())

    assert exc.value.status_code == status.HTTP_502_BAD_GATEWAY
    assert exc.value.detail == "GitHub API error: rate limited"


@pytest.mark.asyncio
async def test_get_commits_extracts_repo_paginates_and_truncates_message() -> None:
    commit = SimpleNamespace(
        sha="abc123",
        commit=SimpleNamespace(
            message="first line\nbody",
            author=SimpleNamespace(name="Ada", date="2026-07-05T01:02:03Z"),
        ),
    )
    client = SimpleNamespace(list_commits=AsyncMock(return_value=[commit]))

    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "GitHubAPIClient", return_value=client),
    ):
        result = await github.get_commits(
            _ctx(),
            _user(),
            AsyncMock(),
            limit=20,
            offset=40,
        )

    client.list_commits.assert_awaited_once_with(
        repo="acme/repo",
        sha="main",
        per_page=20,
        page=3,
    )
    assert result.commits[0].message == "first line"
    assert result.has_more is False


@pytest.mark.asyncio
async def test_git_fetch_requires_complete_configuration() -> None:
    with patch.object(github, "get_github_config", AsyncMock(return_value=_config(repo_url=None))):
        with pytest.raises(HTTPException) as exc:
            await github.git_fetch(_ctx(), _user(), AsyncMock(), GitOpRequest())

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert exc.value.detail == "GitHub not configured"


@pytest.mark.asyncio
async def test_git_commit_publishes_requested_job_id_and_message() -> None:
    with (
        patch.object(github, "get_github_config", AsyncMock(return_value=_config())),
        patch.object(github, "publish_git_operation", AsyncMock()) as publish,
    ):
        result = await github.git_commit(
            CommitRequest(message="ship it", job_id="job-1"),
            _ctx(),
            _user(),
            AsyncMock(),
        )

    assert result.job_id == "job-1"
    publish.assert_awaited_once_with(
        job_id="job-1",
        org_id="11111111-1111-1111-1111-111111111111",
        user_id="user-1",
        user_email="admin@example.com",
        op_type="git_commit",
        message="ship it",
    )
