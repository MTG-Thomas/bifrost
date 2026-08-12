"""E2E tests for repo dirty flag and repo-status endpoint."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.core.workspace_writer import WORKSPACE_WRITER_RESOURCE_LOCK
from src.models.orm.platform_jobs import PlatformJob
from tests.e2e.file_policy_helpers import grant_file_policy


def test_repo_status_default(e2e_client, platform_admin):
    """Repo status endpoint should return expected shape."""
    resp = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "dirty" in data
    assert "dirty_since" in data


def test_repo_status_dirty_after_editor_write(e2e_client, platform_admin):
    """Writing via the editor endpoint should mark repo dirty."""
    e2e_client.put("/api/files/editor/content", headers=platform_admin.headers, json={
        "path": "test-dirty-flag.py",
        "content": "# test dirty flag",
    })
    resp = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["dirty"] is True
    assert data["dirty_since"] is not None
    assert data["dirty_generation"] is not None
    assert data["dirty_writer"] == platform_admin.email

    second_write = e2e_client.put(
        "/api/files/editor/content",
        headers=platform_admin.headers,
        json={"path": "test-dirty-flag.py", "content": "# second generation"},
    )
    assert second_write.status_code == 200, second_write.text
    advanced = e2e_client.get(
        "/api/github/repo-status", headers=platform_admin.headers
    ).json()
    assert advanced["dirty_generation"] != data["dirty_generation"]
    assert advanced["dirty_since"] == data["dirty_since"]
    assert advanced["dirty_updated_at"] != data["dirty_updated_at"]


def test_cli_push_does_not_set_dirty(e2e_client, platform_admin):
    """CLI per-file write should not mark repo as dirty."""
    grant_file_policy(e2e_client, platform_admin.headers, location="workspace")

    # Get current dirty state
    before = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers).json()

    # Push a file via per-file write endpoint
    resp = e2e_client.post("/api/files/write", headers=platform_admin.headers, json={
        "path": "test-push-no-dirty.py",
        "content": "# test push",
        "mode": "cloud",
        "location": "workspace",
    })
    assert resp.status_code == 204

    # Per-file CLI staging intentionally does not advance dirty state.
    after = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers).json()
    assert after["dirty_generation"] == before["dirty_generation"]
    assert after["dirty_updated_at"] == before["dirty_updated_at"]


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_editor_write_is_rejected_while_durable_workspace_writer_is_active(
    e2e_client, platform_admin, db_session
):
    now = datetime.now(timezone.utc)
    job = PlatformJob(
        job_type="workspace.repo-closure",
        payload_version=1,
        payload={"protected": False},
        resource_lock_key=WORKSPACE_WRITER_RESOURCE_LOCK,
        organization_id=None,
        requested_by_user_id=str(platform_admin.user_id),
        requested_by_email=platform_admin.email,
        requested_by_name=platform_admin.email,
        resource_type="workspace_repo_changeset",
        resource_id=str(uuid4()),
        title="Writer fence test",
        status="running",
        phase="Remote verification",
        lease_owner="e2e",
        lease_token=uuid4(),
        lease_expires_at=now + timedelta(minutes=5),
        heartbeat_at=now,
    )
    db_session.add(job)
    await db_session.commit()
    try:
        response = e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={"path": f"writer-fence-{uuid4().hex}.txt", "content": "blocked"},
        )
        assert response.status_code == 409, response.text
        assert response.json()["error"] == "workspace_writer_busy"
        assert response.json()["job_id"] == str(job.id)
    finally:
        await db_session.execute(delete(PlatformJob).where(PlatformJob.id == job.id))
        await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_operational_status_exposes_expired_workspace_writer_lease(
    e2e_client, platform_admin, db_session
):
    now = datetime.now(timezone.utc)
    queued = PlatformJob(
        job_type="workspace.delete-path",
        payload_version=1,
        payload={"path": "queued-behind-abandoned-writer"},
        resource_lock_key=WORKSPACE_WRITER_RESOURCE_LOCK,
        organization_id=None,
        requested_by_user_id=str(platform_admin.user_id),
        requested_by_email=platform_admin.email,
        requested_by_name=platform_admin.email,
        resource_type="workspace_path",
        resource_id="queued-behind-abandoned-writer",
        title="Queued writer visibility test",
        status="queued",
        phase="Queued",
        available_at=now + timedelta(hours=1),
    )
    job = PlatformJob(
        job_type="workspace.repo-closure",
        payload_version=1,
        payload={"protected": False},
        resource_lock_key=WORKSPACE_WRITER_RESOURCE_LOCK,
        organization_id=None,
        requested_by_user_id=str(platform_admin.user_id),
        requested_by_email=platform_admin.email,
        requested_by_name=platform_admin.email,
        resource_type="workspace_repo_changeset",
        resource_id=str(uuid4()),
        title="Expired writer visibility test",
        status="running",
        phase="Authoritative snapshot",
        lease_owner="lost-runner",
        lease_token=uuid4(),
        lease_expires_at=now - timedelta(minutes=1),
        heartbeat_at=now - timedelta(minutes=2),
    )
    db_session.add_all([queued, job])
    await db_session.commit()
    try:
        response = e2e_client.get(
            "/api/workspace-repo-changesets/operational-status",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, response.text
        writer = response.json()["active_writer"]
        assert writer["job_id"] == str(job.id)
        assert writer["lease_owner"] == "lost-runner"
        assert writer["lease_expired"] is True
    finally:
        await db_session.execute(
            delete(PlatformJob).where(PlatformJob.id.in_((queued.id, job.id)))
        )
        await db_session.commit()
