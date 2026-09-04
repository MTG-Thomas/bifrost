from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.models.orm.solution_deploy_jobs import SolutionDeployJob
from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.solutions import Solution
from src.routers.solutions import (
    _enqueue_solution_deploy_job,
    _run_deploy_job,
    _source_accountability_organization_id,
    _solution_candidate_id,
)


def test_solution_candidate_id_hashes_exact_staged_bytes(tmp_path):
    path = tmp_path / "candidate.zip"
    path.write_bytes(b"exact deploy bytes")
    assert _solution_candidate_id(path) == (
        "sha256:e308919039cd35ee393feae0740fd24b356d6279fff339e18411316b7eb846e1"
    )


def test_solution_accountability_uses_the_producer_organization(monkeypatch):
    producer_organization_id = uuid4()
    settings = SimpleNamespace(
        workspace_source_release_oidc_organization_id=str(producer_organization_id)
    )
    monkeypatch.setattr("src.routers.solutions.get_settings", lambda: settings)

    assert _source_accountability_organization_id() == str(producer_organization_id)


@pytest.mark.asyncio
async def test_deploy_job_is_staged_as_encrypted_central_job(
    db_session,
    tmp_path,
    monkeypatch,
):
    sol = Solution(slug="demo-memory-profile", name="Demo memory profile")
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
    monkeypatch.setattr("src.routers.solutions.SolutionDeployJobStorage.delete", delete)

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reconcile_error", "rollback_error"),
    [
        (None, None),
        (RuntimeError("readback failed"), None),
        (RuntimeError("readback failed"), RuntimeError("rollback failed")),
    ],
)
async def test_deploy_accountability_runs_after_storage_finalize(
    tmp_path, monkeypatch, reconcile_error, rollback_error
):
    events: list[str] = []
    job = SolutionDeployJob(id=uuid4(), install_id=uuid4(), status="queued")
    solution = Solution(
        id=job.install_id,
        slug="reviewed-solution",
        name="Reviewed Solution",
        organization_id=uuid4(),
    )

    class FakeDB:
        async def get(self, model, row_id):  # noqa: ANN001, ANN201
            if model is SolutionDeployJob and row_id == job.id:
                return job
            if model is Solution and row_id == solution.id:
                return solution
            return None

        async def commit(self):
            events.append("commit")

        async def rollback(self):
            events.append("rollback")
            if rollback_error is not None:
                raise rollback_error

    database = FakeDB()

    @asynccontextmanager
    async def fake_db_context():
        yield database

    @asynccontextmanager
    async def fake_write_lock(_solution_id):
        yield

    async def finalize_s3():
        events.append("finalize")

    deploy_result = SimpleNamespace(
        finalize_s3=finalize_s3,
        workflows_upserted=1,
        workflows_deleted=0,
        tables_upserted=0,
        tables_deleted=0,
        apps_upserted=0,
        apps_deleted=0,
        forms_upserted=0,
        forms_deleted=0,
        agents_upserted=0,
        agents_deleted=0,
        claims_upserted=0,
        claims_deleted=0,
        integrations_shell_created=0,
        roles_created=[],
    )

    async def read_artifact():
        events.append("artifact_read")
        return b"reviewed artifact"

    async def reconcile(*_args, **_kwargs):
        events.append("reconcile")
        if reconcile_error is not None:
            raise reconcile_error
        return {"state": "released", "obligation_id": "obligation-1"}

    from src.core import database as database_module
    from src.services import solution_deploy_obligations
    from src.services.solutions import source_artifact, write_lock, zip_install

    monkeypatch.setattr(database_module, "get_db_context", fake_db_context)
    monkeypatch.setattr(write_lock, "solution_write_lock", fake_write_lock)
    monkeypatch.setattr(
        zip_install,
        "deploy_zip_to_solution_path",
        AsyncMock(return_value=deploy_result),
    )
    monkeypatch.setattr(
        source_artifact,
        "SolutionSourceArtifactStorage",
        lambda _solution_id: SimpleNamespace(read=read_artifact),
    )
    monkeypatch.setattr(
        solution_deploy_obligations,
        "reconcile_solution_deploy_obligation",
        reconcile,
    )
    zip_path = tmp_path / "deploy.zip"
    zip_path.write_bytes(b"reviewed artifact")

    await _run_deploy_job(
        job.id,
        solution.id,
        zip_path,
        force=False,
        candidate_id="sha256:" + "a" * 64,
        accountability_organization_id=solution.organization_id,
    )

    assert events[:4] == ["commit", "finalize", "artifact_read", "reconcile"]
    assert job.status == "succeeded"
    accountability = job.result["source_release_accountability"]
    if reconcile_error is None:
        assert accountability["state"] == "released"
        assert events.count("commit") == 2
        assert "rollback" not in events
    else:
        assert accountability == {
            "state": "attention_required",
            "reason": "post-deploy accountability reconciliation failed",
            "error_type": "RuntimeError",
        }
        assert events.count("commit") == 1
        assert events.count("rollback") == 1
        assert events.index("rollback") > events.index("reconcile")


@pytest.mark.asyncio
async def test_deploy_snapshots_slug_before_commit_expires_solution(tmp_path, monkeypatch):
    events: list[str] = []
    job = SolutionDeployJob(id=uuid4(), install_id=uuid4(), status="queued")

    class ExpiringSolution:
        id = job.install_id
        organization_id = uuid4()
        _expired = False

        @property
        def slug(self):
            if self._expired:
                raise RuntimeError("expired ORM attribute was accessed")
            return "snapshot-before-commit"

    solution = ExpiringSolution()

    class FakeDB:
        async def get(self, model, row_id):
            if model is SolutionDeployJob and row_id == job.id:
                return job
            if model is Solution and row_id == solution.id:
                return solution
            return None

        async def commit(self):
            solution._expired = True

    database = FakeDB()

    @asynccontextmanager
    async def fake_db_context():
        yield database

    @asynccontextmanager
    async def fake_write_lock(_solution_id):
        yield

    async def finalize_s3():
        events.append("finalize")

    result = SimpleNamespace(
        finalize_s3=finalize_s3,
        workflows_upserted=1, workflows_deleted=0,
        tables_upserted=0, tables_deleted=0,
        apps_upserted=0, apps_deleted=0,
        forms_upserted=0, forms_deleted=0,
        agents_upserted=0, agents_deleted=0,
        claims_upserted=0, claims_deleted=0,
        integrations_shell_created=0, roles_created=[],
    )

    async def reconcile(*_args, **kwargs):
        assert kwargs["solution_slug"] == "snapshot-before-commit"
        return {"state": "released"}

    from src.core import database as database_module
    from src.services import solution_deploy_obligations
    from src.services.solutions import source_artifact, write_lock, zip_install

    monkeypatch.setattr(database_module, "get_db_context", fake_db_context)
    monkeypatch.setattr(write_lock, "solution_write_lock", fake_write_lock)
    monkeypatch.setattr(zip_install, "deploy_zip_to_solution_path", AsyncMock(return_value=result))
    monkeypatch.setattr(
        source_artifact,
        "SolutionSourceArtifactStorage",
        lambda _solution_id: SimpleNamespace(read=AsyncMock(return_value=b"artifact")),
    )
    monkeypatch.setattr(solution_deploy_obligations, "reconcile_solution_deploy_obligation", reconcile)
    zip_path = tmp_path / "deploy.zip"
    zip_path.write_bytes(b"artifact")

    await _run_deploy_job(
        job.id,
        solution.id,
        zip_path,
        force=False,
        accountability_organization_id=solution.organization_id,
    )

    assert job.status == "succeeded"
