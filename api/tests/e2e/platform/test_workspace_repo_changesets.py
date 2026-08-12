"""HTTP contract coverage for authoritative workspace _repo changesets."""

import base64
import hashlib
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from src.models.orm.users import User
from src.models.orm.workspace_repo_changesets import WorkspaceRepoChangeset
from src.models.contracts.workspace_repo_changesets import (
    WorkspaceRepoActivateRequest,
    WorkspaceRepoChangesetBegin,
    WorkspaceRepoFileMutationRequest,
)
from src.core.repo_dirty import clear_repo_dirty, get_repo_dirty_state, mark_repo_dirty
from src.services.platform_commit_writer import (
    PlatformCommitError,
    PlatformCommitResult,
)
from src.services.repo_storage import RepoStorage
from src.services.workspace_repo_changesets import WorkspaceRepoChangesetService
from tests.e2e.conftest import execute_workflow_sync, write_and_register

def _wait_for_platform_job(e2e_client, headers, accepted, *, timeout=45.0):
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = e2e_client.get(
            f"/api/platform-jobs/{job_id}", headers=headers
        )
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.1)
    raise AssertionError(f"platform job {job_id} did not finish")


def _create_validated_changeset(e2e_client, headers, scope, path, content):
    state = e2e_client.get(
        "/api/workspace-repo-changesets/state",
        headers=headers,
        params={"scope": scope},
    )
    started = e2e_client.post(
        "/api/workspace-repo-changesets",
        headers=headers,
        json={"scope": scope, "base_revision": state.json()["revision"]},
    )
    changeset_id = started.json()["id"]
    staged = e2e_client.post(
        f"/api/workspace-repo-changesets/{changeset_id}/files",
        headers=headers,
        json={
            "path": path,
            "operation": "write",
            "content_base64": base64.b64encode(content).decode(),
        },
    )
    assert staged.status_code == 200, staged.text
    validated = e2e_client.post(
        f"/api/workspace-repo-changesets/{changeset_id}/validate",
        headers=headers,
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["valid"] is True
    return changeset_id


class _E2ECommitWriter:
    def __init__(self, *, error=None, mark_later=False):
        self.error = error
        self.mark_later = mark_later
        self.requests = []

    async def write(self, request):
        self.requests.append(request)
        if self.mark_later:
            await mark_repo_dirty(writer="later-editor-write")
        if self.error:
            raise self.error
        return PlatformCommitResult(
            commit_sha="b" * 40,
            tree_sha="c" * 40,
            signature_state="VALID",
        )


async def _create_service_changeset(db_session, admin, writer, path, content):
    service = WorkspaceRepoChangesetService(
        db_session,
        admin.organization_id,
        repo=RepoStorage(),
        commit_writer=writer,
    )
    begun = await service.begin(
        WorkspaceRepoChangesetBegin(scope=path.rsplit("/", 1)[0]), admin.id
    )
    await service.stage(
        begun.id,
        WorkspaceRepoFileMutationRequest(
            path=path,
            operation="write",
            content_base64=base64.b64encode(content).decode(),
        ),
    )
    validated = await service.validate(begun.id)
    assert validated.valid is True
    return service, begun.id



@pytest.mark.e2e
def test_workspace_repo_changeset_stages_validates_and_activates_atomically(
    e2e_client, platform_admin
):
    scope = f"test_changesets_{uuid4().hex}"
    path = f"{scope}/hello.txt"
    headers = platform_admin.headers
    content = b"hello from a changeset\n"
    try:
        state = e2e_client.get(
            "/api/workspace-repo-changesets/state",
            headers=headers,
            params={"scope": scope},
        )
        assert state.status_code == 200, state.text
        assert state.json()["storage_root"] == "_repo"

        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=headers,
            json={
                "scope": scope,
                "base_revision": state.json()["revision"],
                "worker_id": "e2e-worker",
            },
        )
        assert started.status_code == 201, started.text
        changeset_id = started.json()["id"]

        staged = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/files",
            headers=headers,
            json={
                "path": path,
                "operation": "write",
                "content_base64": base64.b64encode(content).decode(),
            },
        )
        assert staged.status_code == 200, staged.text
        assert staged.json()["status"] == "staged"

        # Staging never exposes content through the authoritative files API.
        absent = e2e_client.post(
            "/api/files/exists", headers=headers, json={"path": path}
        )
        assert absent.status_code == 200
        assert absent.json()["exists"] is False

        validated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/validate", headers=headers
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True

        activated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/activate",
            headers=headers,
            json={},
        )
        job = _wait_for_platform_job(e2e_client, headers, activated)
        assert job["status"] == "succeeded", job
        assert job["result"]["changeset"]["status"] == "activated"

        read = e2e_client.post("/api/files/read", headers=headers, json={"path": path})
        assert read.status_code == 200, read.text
        assert read.json()["content"] == content.decode()
        status_response = e2e_client.get(
            "/api/github/repo-status", headers=headers
        )
        assert status_response.status_code == 200, status_response.text
        assert status_response.json()["dirty"] is True
        assert status_response.json()["dirty_generation"] is not None
    finally:
        # The test owns this unique prefix and may clean it directly.
        import asyncio

        asyncio.run(RepoStorage().delete(path))


@pytest.mark.e2e
def test_python_activation_invalidates_worker_import_generation_immediately(
    e2e_client, platform_admin
):
    """A dependent workflow must see one coherent revision after activation."""
    suffix = uuid4().hex
    scope = f"test_generation_{suffix}"
    helper_path = f"{scope}/helper.py"
    workflow_path = f"{scope}/workflow.py"
    function_name = f"generation_probe_{suffix}"
    headers = platform_admin.headers
    helper_v1 = 'def revision():\n    return "revision-a"\n'
    helper_v2 = 'def revision():\n    return "revision-b"\n'
    workflow_source = f'''from bifrost import workflow
from {scope}.helper import revision

@workflow(name="{function_name}", execution_mode="async")
async def {function_name}() -> dict:
    return {{"revision": revision()}}
'''

    try:
        helper_created = e2e_client.put(
            "/api/files/editor/content",
            headers=headers,
            json={"path": helper_path, "content": helper_v1, "encoding": "utf-8"},
        )
        assert helper_created.status_code in {200, 201}, helper_created.text
        registered = write_and_register(
            e2e_client,
            headers,
            workflow_path,
            workflow_source,
            function_name,
        )

        first = execute_workflow_sync(
            e2e_client, headers, registered["id"], max_wait=30.0
        )
        assert first["status"] == "Success", first
        assert first["result"] == {"revision": "revision-a"}
        first_generation = (first.get("execution_context") or {}).get(
            "workspace_generation"
        )
        assert first_generation

        state = e2e_client.get(
            "/api/workspace-repo-changesets/state",
            headers=headers,
            params={"scope": scope},
        )
        assert state.status_code == 200, state.text
        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=headers,
            json={"scope": scope, "base_revision": state.json()["revision"]},
        )
        assert started.status_code == 201, started.text
        changeset_id = started.json()["id"]
        staged = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/files",
            headers=headers,
            json={
                "path": helper_path,
                "operation": "write",
                "content_base64": base64.b64encode(helper_v2.encode()).decode(),
            },
        )
        assert staged.status_code == 200, staged.text
        validated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/validate",
            headers=headers,
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True, validated.text
        activated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/activate",
            headers=headers,
            json={},
        )
        job = _wait_for_platform_job(e2e_client, headers, activated)
        assert job["status"] == "succeeded", job

        second = execute_workflow_sync(
            e2e_client, headers, registered["id"], max_wait=30.0
        )
        assert second["status"] == "Success", second
        assert second["result"] == {"revision": "revision-b"}
        second_generation = (second.get("execution_context") or {}).get(
            "workspace_generation"
        )
        assert second_generation
        assert second_generation != first_generation
    finally:
        e2e_client.delete(f"/api/files/editor?path={workflow_path}", headers=headers)
        e2e_client.delete(f"/api/files/editor?path={helper_path}", headers=headers)


@pytest.mark.e2e
def test_multi_root_changesets_use_one_ordered_writer_ledger(
    e2e_client, platform_admin
):
    suffix = uuid4().hex
    headers = platform_admin.headers
    paths = [
        (f"features/{suffix}", f"features/{suffix}/one.txt", b"one\n"),
        (f"shared/{suffix}", f"shared/{suffix}/two.txt", b"two\n"),
    ]
    ids = []
    try:
        for scope, path, content in paths:
            ids.append(
                _create_validated_changeset(
                    e2e_client, headers, scope, path, content
                )
            )
        accepted = [
            e2e_client.post(
                f"/api/workspace-repo-changesets/{changeset_id}/activate",
                headers=headers,
                json={},
            )
            for changeset_id in ids
        ]
        jobs = [
            _wait_for_platform_job(e2e_client, headers, response)
            for response in accepted
        ]
        assert [job["status"] for job in jobs] == ["succeeded", "succeeded"]
        assert jobs[1]["started_at"] >= jobs[0]["completed_at"]

        status = e2e_client.get(
            "/api/workspace-repo-changesets/operational-status",
            headers=headers,
        )
        assert status.status_code == 200, status.text
        ledger_ids = [item["id"] for item in status.json()["closure_ledger"]]
        assert ledger_ids.index(ids[0]) < ledger_ids.index(ids[1])
    finally:
        import asyncio

        for _scope, path, _content in paths:
            asyncio.run(RepoStorage().delete(path))


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_abandoned_activating_changeset_is_visible_and_exact_abort_restores_backup(
    e2e_client, platform_admin, db_session
):
    admin = (
        await db_session.execute(
            select(User).where(User.id == platform_admin.user_id)
        )
    ).scalar_one()
    assert admin.organization_id is not None
    path = f"test_changesets_{uuid4().hex}/abandoned.txt"
    storage = RepoStorage()
    await storage.write(path, b"partially activated\n")
    row = WorkspaceRepoChangeset(
        organization_id=admin.organization_id,
        scope=path.rsplit("/", 1)[0],
        base_revision="0" * 64,
        base_files={path: hashlib.sha256(b"original\n").hexdigest()},
        mutations=[
            {
                "path": path,
                "operation": "write",
                "content_base64": base64.b64encode(b"partially activated\n").decode(),
                "before_hash": hashlib.sha256(b"original\n").hexdigest(),
                "after_hash": hashlib.sha256(b"partially activated\n").hexdigest(),
                "force_deactivation": False,
            }
        ],
        status="activating",
        created_by=admin.id,
        activation_backup={path: base64.b64encode(b"original\n").decode()},
        failure_detail={"phase": "activation", "state": "pending"},
    )
    db_session.add(row)
    await db_session.commit()
    try:
        status = e2e_client.get(
            "/api/workspace-repo-changesets/operational-status",
            headers=platform_admin.headers,
        )
        assert status.status_code == 200, status.text
        assert str(row.id) in {
            item["id"] for item in status.json()["active_changesets"]
        }

        aborted = e2e_client.post(
            f"/api/workspace-repo-changesets/{row.id}/abort",
            headers=platform_admin.headers,
        )
        assert aborted.status_code == 200, aborted.text
        assert aborted.json()["status"] == "aborted"
        assert await storage.read(path) == b"original\n"
    finally:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(WorkspaceRepoChangeset.id == row.id)
        )
        await db_session.commit()
        await storage.delete(path)


@pytest.mark.e2e
def test_workspace_repo_git_closure_retry_requires_authentication(e2e_client):
    response = e2e_client.post(
        f"/api/workspace-repo-changesets/{uuid4()}/retry-git-closure",
        json={"commit_message": "e2e retry", "push": True},
    )

    assert response.status_code in {401, 403}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_workspace_repo_git_closure_retry_is_org_scoped_and_state_guarded(
    e2e_client, platform_admin, org1, db_session
):
    admin = (
        await db_session.execute(select(User).where(User.id == platform_admin.user_id))
    ).scalar_one()
    assert admin.organization_id is not None
    cross_org = WorkspaceRepoChangeset(
        organization_id=UUID(org1["id"]),
        scope=f"test_changesets_{uuid4().hex}",
        base_revision="0" * 64,
        base_files={},
        mutations=[],
        status="activated",
        created_by=admin.id,
        failure_detail={"phase": "git_closure", "state": "failed"},
    )
    db_session.add(cross_org)
    await db_session.commit()
    try:
        scoped = e2e_client.post(
            f"/api/workspace-repo-changesets/{cross_org.id}/retry-git-closure",
            headers=platform_admin.headers,
            json={"commit_message": "e2e retry", "push": True},
        )
        assert scoped.status_code == 404, scoped.text

        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=platform_admin.headers,
            json={"scope": f"test_changesets_{uuid4().hex}"},
        )
        assert started.status_code == 201, started.text
        invalid = e2e_client.post(
            f"/api/workspace-repo-changesets/{started.json()['id']}/retry-git-closure",
            headers=platform_admin.headers,
            json={"commit_message": "e2e retry", "push": True},
        )
        assert invalid.status_code == 422, invalid.text
    finally:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(
                WorkspaceRepoChangeset.id.in_([cross_org.id, started.json()["id"]])
            )
        )
        await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_recoverable_git_closures_include_durable_retry_states(
    e2e_client, platform_admin, db_session
):
    admin = (
        await db_session.execute(select(User).where(User.id == platform_admin.user_id))
    ).scalar_one()
    assert admin.organization_id is not None
    scope = f"test_changesets_{uuid4().hex}"
    rows = [
        WorkspaceRepoChangeset(
            organization_id=admin.organization_id,
            scope=scope,
            base_revision="0" * 64,
            base_files={},
            mutations=[],
            status="activated",
            created_by=admin.id,
            failure_detail={"phase": "git_closure", "state": state},
        )
        for state in ("failed", "not_configured", "pending")
    ]
    db_session.add_all(rows)
    await db_session.commit()
    try:
        response = e2e_client.get(
            "/api/workspace-repo-changesets/recoverable-git-closures",
            headers=platform_admin.headers,
            params={"scope": scope},
        )

        assert response.status_code == 200, response.text
        assert {item["id"] for item in response.json()} == {
            str(row.id) for row in rows
        }
    finally:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(
                WorkspaceRepoChangeset.id.in_([row.id for row in rows])
            )
        )
        await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_workspace_repo_git_closure_retry_requires_remote_push_without_reactivation(
    e2e_client, platform_admin, db_session
):
    admin = (
        await db_session.execute(select(User).where(User.id == platform_admin.user_id))
    ).scalar_one()
    assert admin.organization_id is not None
    row = WorkspaceRepoChangeset(
        organization_id=admin.organization_id,
        scope=f"test_changesets_{uuid4().hex}",
        base_revision="0" * 64,
        base_files={},
        mutations=[],
        status="activated",
        created_by=admin.id,
        activated_revision="a" * 64,
        failure_detail={"phase": "git_closure", "state": "failed"},
        dirty_generation="1" * 32,
        commit_message="e2e retry",
    )
    db_session.add(row)
    await db_session.commit()
    try:
        retried = e2e_client.post(
            f"/api/workspace-repo-changesets/{row.id}/retry-git-closure",
            headers=platform_admin.headers,
            json={"commit_message": "e2e retry", "push": False},
        )

        assert retried.status_code == 422, retried.text
        shown = e2e_client.get(
            f"/api/workspace-repo-changesets/{row.id}",
            headers=platform_admin.headers,
        )
        assert shown.status_code == 200, shown.text
        assert shown.json()["status"] == "activated"
        assert shown.json()["activated_revision"] == "a" * 64
        assert shown.json()["failure_detail"] == {
            "phase": "git_closure",
            "state": "failed",
        }
    finally:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(WorkspaceRepoChangeset.id == row.id)
        )
        await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_failed_commit_preserves_source_and_retry_closes_its_generation(
    platform_admin, db_session
):
    admin = (
        await db_session.execute(select(User).where(User.id == platform_admin.user_id))
    ).scalar_one()
    assert admin.organization_id is not None
    path = f"test_changesets_{uuid4().hex}/failed-commit.txt"
    storage = RepoStorage()
    await clear_repo_dirty()
    writer = _E2ECommitWriter(error=RuntimeError("commit failed"))
    service, changeset_id = await _create_service_changeset(
        db_session, admin, writer, path, b"activated source\n"
    )
    try:
        failed = await service.activate(
            changeset_id,
            WorkspaceRepoActivateRequest(commit_message="release", push=True),
            platform_admin.email,
        )

        assert failed.status == "activated"
        assert failed.failure_detail["phase"] == "git_closure"
        assert failed.failure_detail["state"] == "failed"
        assert await storage.read(path) == b"activated source\n"
        assert await get_repo_dirty_state() is not None

        service.commit_writer = _E2ECommitWriter()
        closed = await service.retry_git_closure(
            changeset_id,
            WorkspaceRepoActivateRequest(push=True),
            platform_admin.email,
        )

        assert closed.status == "committed"
        assert closed.failure_detail is None
        assert await storage.read(path) == b"activated source\n"
        assert await get_repo_dirty_state() is None
    finally:
        await storage.delete(path)
        await clear_repo_dirty()
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(
                WorkspaceRepoChangeset.id == changeset_id
            )
        )
        await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_uncertain_push_is_retryable_and_newer_generation_survives_closure(
    platform_admin, db_session
):
    admin = (
        await db_session.execute(select(User).where(User.id == platform_admin.user_id))
    ).scalar_one()
    assert admin.organization_id is not None
    path = f"test_changesets_{uuid4().hex}/failed-push.txt"
    storage = RepoStorage()
    await clear_repo_dirty()
    writer = _E2ECommitWriter(
        error=PlatformCommitError("remote verification failed", commit_sha="a" * 40)
    )
    service, changeset_id = await _create_service_changeset(
        db_session, admin, writer, path, b"activated source\n"
    )
    try:
        failed = await service.activate(
            changeset_id,
            WorkspaceRepoActivateRequest(commit_message="release", push=True),
            platform_admin.email,
        )

        assert failed.status == "committed_unpushed"
        assert failed.commit_sha == "a" * 40
        assert failed.failure_detail["phase"] == "remote_verification"
        assert await get_repo_dirty_state() is not None

        retry_writer = _E2ECommitWriter(mark_later=True)
        service.commit_writer = retry_writer
        closed = await service.retry_git_closure(
            changeset_id,
            WorkspaceRepoActivateRequest(push=True),
            platform_admin.email,
        )

        assert retry_writer.requests[0].candidate_commit_sha == "a" * 40
        assert closed.status == "committed"
        assert closed.failure_detail["state"] == "preserved_newer_generation"
        dirty = await get_repo_dirty_state()
        assert dirty is not None
        assert dirty.writer == "later-editor-write"
    finally:
        await storage.delete(path)
        await clear_repo_dirty()
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(
                WorkspaceRepoChangeset.id == changeset_id
            )
        )
        await db_session.commit()
