from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solutions import Solution
from src.routers.solutions import (
    _enqueue_solution_deploy_job,
    _run_deploy_job,
    _solution_candidate_id,
)


def test_solution_candidate_id_hashes_exact_staged_bytes(tmp_path):
    path = tmp_path / "candidate.zip"
    path.write_bytes(b"exact deploy bytes")
    assert _solution_candidate_id(path) == (
        "sha256:e308919039cd35ee393feae0740fd24b356d6279fff339e18411316b7eb846e1"
    )


@pytest.mark.asyncio
async def test_deploy_job_is_staged_as_encrypted_central_job(
    db_session,
    tmp_path,
    monkeypatch,
):
    sol = Solution(slug="demo", name="Demo")
    db_session.add(sol)
    await db_session.flush()
    path = tmp_path / "deploy.zip"
    path.write_bytes(b"validated")
    monkeypatch.setattr(
        "src.routers.solutions.SolutionDeployJobStorage.write_path",
        AsyncMock(return_value=("a" * 64, len(b"validated"))),
    )
    monkeypatch.setattr(
        "src.routers.solutions.publish_platform_job_update", AsyncMock()
    )

    projection = await _enqueue_solution_deploy_job(
        db_session,
        kind="deploy",
        install_id=sol.id,
        organization_id=None,
        options={"force": True, "password": "not-plaintext"},
        requested_by_user_id=uuid4(),
        requested_by_email="admin@example.com",
        requested_by_name="Admin",
        input_path=path,
    )
    central = await db_session.get(PlatformJob, projection.id)
    assert central is not None
    assert central.id == projection.id
    assert central.job_type == "solution.deploy"
    assert central.payload == {"protected": True}
    assert central.encrypted_payload is not None
    assert "not-plaintext" not in central.encrypted_payload
    assert central.resource_lock_key == f"solution:{sol.id}"


@pytest.mark.asyncio
async def test_deploy_job_rejects_candidate_changed_during_staging(
    db_session, tmp_path, monkeypatch
):
    path = tmp_path / "deploy.zip"
    path.write_bytes(b"candidate")
    delete = AsyncMock()
    monkeypatch.setattr(
        "src.routers.solutions.SolutionDeployJobStorage.write_path",
        AsyncMock(return_value=("b" * 64, len(b"candidate"))),
    )
    monkeypatch.setattr(
        "src.routers.solutions.SolutionDeployJobStorage.delete", delete
    )

    with pytest.raises(ValueError, match="candidate hash changed"):
        await _enqueue_solution_deploy_job(
            db_session,
            kind="deploy",
            install_id=None,
            organization_id=None,
            options={"candidate_id": "sha256:" + "a" * 64},
            requested_by_user_id=uuid4(),
            requested_by_email="admin@example.com",
            requested_by_name="Admin",
            input_path=path,
        )
    delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_deploy_job_does_not_start_after_job_is_terminal(
    tmp_path, monkeypatch
):
    job = SolutionDeployJob(id=uuid4(), install_id=None, status="failed")

    class FakeDB:
        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            assert model is SolutionDeployJob
            assert row_id == job.id
            return job

    @asynccontextmanager
    async def fake_db_context():
        yield FakeDB()

    from src.core import database
    from src.services.solutions import zip_install

    deploy = AsyncMock()
    monkeypatch.setattr(database, "get_db_context", fake_db_context)
    monkeypatch.setattr(zip_install, "deploy_zip_to_solution_path", deploy)
    zip_path = tmp_path / "deploy.zip"
    zip_path.write_bytes(b"not used")

    await _run_deploy_job(job.id, uuid4(), zip_path, force=False)

    deploy.assert_not_awaited()
    assert not zip_path.exists()
