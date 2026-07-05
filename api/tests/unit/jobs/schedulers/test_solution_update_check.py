from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.schedulers import solution_update_check


class _SolutionResult:
    def __init__(self, solutions):
        self._solutions = solutions

    def scalars(self):
        return self

    def all(self):
        return self._solutions


@pytest.mark.asyncio
async def test_check_solution_updates_emits_only_first_new_update(monkeypatch):
    first_new_update = SimpleNamespace(
        id=uuid4(),
        slug="first",
        organization_id=uuid4(),
        version="1.0.0",
        update_available_version=None,
        git_repo_url="https://example.test/repo.git",
        repo_subpath="solutions/first",
        git_ref="main",
    )
    still_outdated = SimpleNamespace(
        id=uuid4(),
        slug="second",
        organization_id=uuid4(),
        version="1.0.0",
        update_available_version="1.1.0",
        git_repo_url="https://example.test/repo.git",
        repo_subpath="solutions/second",
        git_ref="main",
    )
    no_update = SimpleNamespace(
        id=uuid4(),
        slug="third",
        organization_id=uuid4(),
        version="2.0.0",
        update_available_version="2.1.0",
        git_repo_url="https://example.test/repo.git",
        repo_subpath="solutions/third",
        git_ref="main",
    )
    db = AsyncMock()
    db.execute.return_value = _SolutionResult([first_new_update, still_outdated, no_update])

    @asynccontextmanager
    async def fake_db_context():
        yield db

    async def fake_fetch_remote_version(**kwargs):
        return {
            "solutions/first": "1.2.0",
            "solutions/second": "1.1.0",
            "solutions/third": "2.0.0",
        }[kwargs["repo_subpath"]]

    def fake_compute_update_available(*, installed, remote):
        return remote if remote != installed else None

    emit = AsyncMock()
    monkeypatch.setattr(solution_update_check, "get_db_context", fake_db_context)
    monkeypatch.setattr(
        solution_update_check, "fetch_remote_version", fake_fetch_remote_version
    )
    monkeypatch.setattr(
        solution_update_check, "compute_update_available", fake_compute_update_available
    )
    monkeypatch.setattr(solution_update_check, "emit_solution_update_available", emit)

    result = await solution_update_check.check_solution_updates()

    assert result == {"checked": 3, "updates_found": 2}
    assert first_new_update.update_available_version == "1.2.0"
    assert still_outdated.update_available_version == "1.1.0"
    assert no_update.update_available_version is None
    emit.assert_awaited_once_with(
        solution_id=first_new_update.id,
        slug="first",
        organization_id=first_new_update.organization_id,
        installed_version="1.0.0",
        available_version="1.2.0",
    )
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_solution_updates_continues_after_per_solution_failure(monkeypatch):
    broken = SimpleNamespace(
        id=uuid4(),
        slug="broken",
        organization_id=uuid4(),
        version="1.0.0",
        update_available_version=None,
        git_repo_url="https://example.test/repo.git",
        repo_subpath="broken",
        git_ref="main",
    )
    healthy = SimpleNamespace(
        id=uuid4(),
        slug="healthy",
        organization_id=uuid4(),
        version="1.0.0",
        update_available_version=None,
        git_repo_url="https://example.test/repo.git",
        repo_subpath="healthy",
        git_ref="main",
    )
    db = AsyncMock()
    db.execute.return_value = _SolutionResult([broken, healthy])

    @asynccontextmanager
    async def fake_db_context():
        yield db

    async def fake_fetch_remote_version(**kwargs):
        if kwargs["repo_subpath"] == "broken":
            raise RuntimeError("remote failed")
        return "1.3.0"

    monkeypatch.setattr(solution_update_check, "get_db_context", fake_db_context)
    monkeypatch.setattr(
        solution_update_check, "fetch_remote_version", fake_fetch_remote_version
    )
    monkeypatch.setattr(
        solution_update_check,
        "compute_update_available",
        lambda *, installed, remote: remote,
    )
    monkeypatch.setattr(
        solution_update_check, "emit_solution_update_available", AsyncMock()
    )

    result = await solution_update_check.check_solution_updates()

    assert result == {"checked": 2, "updates_found": 1}
    assert broken.update_available_version is None
    assert healthy.update_available_version == "1.3.0"
    db.commit.assert_awaited_once()
