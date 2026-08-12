import base64
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from src.models.contracts.workspace_repo_changesets import (
    WorkspaceRepoActivateRequest,
    WorkspaceRepoChangesetBegin,
    WorkspaceRepoFileMutationRequest,
)
from src.repositories.workspace_repo_changesets import RETRYABLE_GIT_FAILURE_STATES
from src.services.platform_commit_writer import (
    PlatformCommitError,
    PlatformCommitResult,
)
from src.services.workspace_repo_changesets import (
    ChangesetConflict,
    ChangesetInvalid,
    OrganizationScopeRequired,
    WorkspaceRepoChangesetService,
    require_organization_id,
)
from src.services.workspace_convergence import build_snapshot
from src.core.repo_dirty import RepoDirtyState
from src.core.workspace_writer import WorkspaceWriterLeaseLost


class MemoryRepo:
    def __init__(self, files=None):
        self.files = dict(files or {})

    async def list(self, prefix=""):
        return [path for path in self.files if path.startswith(prefix)]

    async def exists(self, path):
        return path in self.files

    async def read(self, path):
        return self.files[path]

    async def write(self, path, content):
        self.files[path] = content

    async def delete(self, path):
        self.files.pop(path, None)


class MemoryRows:
    def __init__(self):
        self.items = {}

    async def add(self, row):
        row.id = row.id or uuid4()
        now = datetime.now(timezone.utc)
        row.created_at = now
        row.updated_at = now
        self.items[row.id] = row
        return row

    async def get(self, row_id, organization_id, for_update=False):
        row = self.items.get(row_id)
        if row is None or row.organization_id != organization_id:
            return None
        return row

    async def count_open(self, scope, organization_id):
        return sum(
            row.scope == scope
            and row.organization_id == organization_id
            and row.status in {"open", "staged", "validated", "activating"}
            for row in self.items.values()
        )

    async def list_retryable_git_failures(self, organization_id, *, scope=None):
        return [
            row
            for row in self.items.values()
            if row.organization_id == organization_id
            and (scope is None or row.scope == scope)
            and row.failure_detail
            and row.failure_detail.get("state")
            in RETRYABLE_GIT_FAILURE_STATES
            and (row.status, row.failure_detail.get("phase"))
            in WorkspaceRepoChangesetService.RETRYABLE_GIT_FAILURES
        ]

    async def list_by_statuses(self, organization_id, statuses, *, limit=200):
        return [
            row
            for row in self.items.values()
            if row.organization_id == organization_id and row.status in statuses
        ][:limit]


class FakeDB:
    def __init__(self):
        self.commits = 0

    async def flush(self):
        return None

    async def execute(self, _statement):
        return None

    async def commit(self):
        self.commits += 1
        return None

    async def rollback(self):
        return None


class RecordingWriter:
    def __init__(self, result=None, error=None):
        self.result = result or PlatformCommitResult(
            commit_sha="a" * 40,
            tree_sha="b" * 40,
            signature_state="VALID",
        )
        self.error = error
        self.requests = []

    async def write(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.result


def service(files=None, organization_id=None):
    value = WorkspaceRepoChangesetService(
        FakeDB(), organization_id or uuid4(), repo=MemoryRepo(files)
    )
    value.rows = MemoryRows()
    return value


@pytest.fixture(autouse=True)
def generation_fenced_dirty_state(monkeypatch):
    state = RepoDirtyState(
        "1" * 32,
        "2026-08-11T12:00:00+00:00",
        "2026-08-11T12:00:00+00:00",
        "changeset:test",
    )
    monkeypatch.setattr(
        "src.core.repo_dirty.get_repo_dirty_state", AsyncMock(return_value=state)
    )
    monkeypatch.setattr(
        "src.core.repo_dirty.reconcile_repo_dirty", AsyncMock(return_value=True)
    )


def test_repo_changesets_require_an_organization_scope():
    with pytest.raises(OrganizationScopeRequired, match="organization-scoped"):
        require_organization_id(None)


@pytest.mark.asyncio
async def test_begin_uses_canonical_revision_and_rejects_stale_revision():
    svc = service(
        {"features/a.py": b"one", "features/b.py": b"two", "other/x": b"ignored"}
    )
    state = await svc.state("features")
    assert state.storage_root == "_repo"
    assert state.file_hashes == {
        "features/a.py": "7692c3ad3540bb803c020b3aee66cd8887123234ea0c6e7143c0add73ff431ed",
        "features/b.py": "3fc4ccfe745870e2c0d99f71f30ff0656c8dedd41cc1d7d3d376b0dbe685e2f3",
    }
    row = await svc.begin(
        WorkspaceRepoChangesetBegin(
            scope="features", base_revision=state.revision, worker_id="worker-1"
        ),
        uuid4(),
    )
    assert row.base_revision == state.revision
    assert row.worker_id == "worker-1"

    with pytest.raises(ChangesetConflict) as exc:
        await svc.begin(
            WorkspaceRepoChangesetBegin(scope="features", base_revision="0" * 64),
            uuid4(),
        )
    assert exc.value.detail["reason"] == "revision_mismatch"


@pytest.mark.asyncio
async def test_stage_is_scope_bounded_and_diff_does_not_mutate_workspace():
    svc = service({"features/a.py": b"old\n"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    changed = await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path="features/a.py",
            operation="write",
            content_base64=base64.b64encode(b"new\n").decode(),
        ),
    )
    assert changed.status == "staged"
    assert svc.repo.files["features/a.py"] == b"old\n"
    diff = await svc.diff(row.id)
    assert "-old" in diff.files[0].unified_diff
    assert "+new" in diff.files[0].unified_diff

    with pytest.raises(ChangesetInvalid):
        await svc.stage(
            row.id,
            WorkspaceRepoFileMutationRequest(path="apps/a.ts", operation="delete"),
        )


@pytest.mark.asyncio
async def test_diff_rejects_content_that_no_longer_matches_captured_base():
    svc = service({"features/a.py": b"old\n"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path="features/a.py",
            operation="write",
            content_base64=base64.b64encode(b"new\n").decode(),
        ),
    )
    svc.repo.files["features/a.py"] = b"concurrent\n"

    with pytest.raises(ChangesetConflict) as exc:
        await svc.diff(row.id)
    assert exc.value.detail["reason"] == "file_revision_mismatch"


@pytest.mark.asyncio
async def test_changesets_are_hidden_from_other_organizations():
    first_org = uuid4()
    second_org = uuid4()
    rows = MemoryRows()
    first = service({"features/a.py": b"one"}, first_org)
    second = service({"features/a.py": b"one"}, second_org)
    first.rows = rows
    second.rows = rows

    row = await first.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    with pytest.raises(KeyError):
        await second.get(row.id)
    assert (await first.state("features")).open_changesets == 1
    assert (await second.state("features")).open_changesets == 0


@pytest.mark.asyncio
async def test_path_level_cas_allows_disjoint_change_and_rejects_touched_change(
    monkeypatch,
):
    barriers = []

    @asynccontextmanager
    async def source_update(**kwargs):
        barriers.append(("enter", kwargs))
        try:
            yield
        finally:
            barriers.append(("exit", kwargs))

    monkeypatch.setattr("src.core.module_cache.workspace_source_update", source_update)
    svc = service({"features/a.py": b"a", "features/b.py": b"b"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path="features/a.py",
            operation="write",
            content_base64=base64.b64encode(b"A").decode(),
            force_deactivation=True,
        ),
    )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"
    svc.repo.files["features/b.py"] = b"B"  # unrelated concurrent edit

    class Storage:
        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **_kwargs):
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

        async def delete_file(self, path):
            await svc.repo.delete(path)

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", Storage
    )
    activated = await svc.activate(row.id, WorkspaceRepoActivateRequest(), "tester")
    assert activated.status == "activated"
    assert svc.repo.files == {"features/a.py": b"A", "features/b.py": b"B"}
    assert barriers == [
        (
            "enter",
            {
                "reason": "workspace_changeset_activated",
                "changed_paths": ["features/a.py"],
                "broadcast": True,
            },
        ),
        (
            "exit",
            {
                "reason": "workspace_changeset_activated",
                "changed_paths": ["features/a.py"],
                "broadcast": True,
            },
        ),
    ]

    second = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        second.id,
        WorkspaceRepoFileMutationRequest(path="features/a.py", operation="delete"),
    )
    current = svc.rows.items[second.id]
    current.validation = {"valid": True}
    current.status = "validated"
    svc.repo.files["features/a.py"] = b"someone else"
    with pytest.raises(ChangesetConflict) as exc:
        await svc.activate(second.id, WorkspaceRepoActivateRequest(), "tester")
    assert exc.value.detail["conflicting_paths"] == ["features/a.py"]

    aborted = await svc.abort(second.id)
    assert aborted.status == "aborted"


@pytest.mark.asyncio
async def test_abort_activation_snapshot_needs_no_backup_but_conflicted_resume_is_fenced(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.core.workspace_writer.assert_workspace_writer_access", AsyncMock()
    )
    svc = service({"features/a.py": b"a"})
    snapshot = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    snapshot_row = svc.rows.items[snapshot.id]
    snapshot_row.status = "activating"
    snapshot_row.failure_detail = {
        "phase": "activation_snapshot",
        "state": "pending",
    }

    aborted = await svc.abort(snapshot.id)
    assert aborted.status == "aborted"
    assert svc.repo.files["features/a.py"] == b"a"

    conflicted = await svc.begin(
        WorkspaceRepoChangesetBegin(scope="features"), uuid4()
    )
    conflicted_row = svc.rows.items[conflicted.id]
    conflicted_row.status = "conflicted"
    conflicted_row.activation_backup = {
        "features/a.py": base64.b64encode(b"a").decode()
    }
    svc.repo.files["features/a.py"] = b"newer-authoritative-write"

    with pytest.raises(ChangesetConflict) as exc_info:
        await svc.abort(conflicted.id)
    assert exc_info.value.detail == {
        "reason": "abort_revision_mismatch",
        "conflicting_paths": ["features/a.py"],
    }
    assert conflicted_row.status == "conflicted"
    assert svc.repo.files["features/a.py"] == b"newer-authoritative-write"


@pytest.mark.asyncio
async def test_activation_compensates_storage_on_partial_failure(monkeypatch):
    svc = service({"features/a.txt": b"a", "features/b.txt": b"b"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    for path, content in (("features/a.txt", b"A"), ("features/b.txt", b"B")):
        await svc.stage(
            row.id,
            WorkspaceRepoFileMutationRequest(
                path=path,
                operation="write",
                content_base64=base64.b64encode(content).decode(),
            ),
        )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class FailingStorage:
        calls = 0

        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **_kwargs):
            self.calls += 1
            await svc.repo.write(path, content)
            if self.calls == 2:
                raise RuntimeError("storage failed")
            return SimpleNamespace(pending_deactivations=[])

        async def delete_file(self, path):
            await svc.repo.delete(path)

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", FailingStorage
    )
    with pytest.raises(RuntimeError, match="storage failed"):
        await svc.activate(row.id, WorkspaceRepoActivateRequest(), "tester")
    assert svc.repo.files == {"features/a.txt": b"a", "features/b.txt": b"b"}
    assert stored.status == "failed"


@pytest.mark.asyncio
async def test_stale_activation_runner_leaves_backup_without_compensation(monkeypatch):
    svc = service({"features/a.txt": b"a", "features/b.txt": b"b"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    for path, content in (("features/a.txt", b"A"), ("features/b.txt", b"B")):
        await svc.stage(
            row.id,
            WorkspaceRepoFileMutationRequest(
                path=path,
                operation="write",
                content_base64=base64.b64encode(content).decode(),
            ),
        )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class StaleStorage:
        calls = 0

        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                raise WorkspaceWriterLeaseLost("lease replaced")
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", StaleStorage
    )

    with pytest.raises(WorkspaceWriterLeaseLost, match="lease replaced"):
        await svc.activate(row.id, WorkspaceRepoActivateRequest(), "tester")

    assert stored.status == "activating"
    assert stored.activation_backup == {
        "features/a.txt": base64.b64encode(b"a").decode(),
        "features/b.txt": base64.b64encode(b"b").decode(),
    }
    assert svc.repo.files == {"features/a.txt": b"A", "features/b.txt": b"b"}


@pytest.mark.asyncio
async def test_activation_persists_failure_when_compensation_also_fails(monkeypatch):
    svc = service({"features/a.txt": b"a", "features/b.txt": b"b"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    for path, content in (("features/a.txt", b"A"), ("features/b.txt", b"B")):
        await svc.stage(
            row.id,
            WorkspaceRepoFileMutationRequest(
                path=path,
                operation="write",
                content_base64=base64.b64encode(content).decode(),
            ),
        )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class BrokenRollbackStorage:
        calls = 0

        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("activation failed")
            if (
                kwargs.get("updated_by") == "changeset-rollback"
                and path == "features/a.txt"
            ):
                raise RuntimeError("rollback failed")
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

        async def delete_file(self, path):
            await svc.repo.delete(path)

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService",
        BrokenRollbackStorage,
    )
    with pytest.raises(RuntimeError, match="rollback failed"):
        await svc.activate(row.id, WorkspaceRepoActivateRequest(), "tester")
    assert stored.status == "recovery_required"
    assert "activation failed" in stored.error
    assert "rollback failed" in stored.error
    assert stored.failure_detail["rollback"]["state"] == "failed"
    assert stored.failure_detail["rollback"]["errors"]


@pytest.mark.asyncio
async def test_activation_closes_with_verified_writer_and_provenance(monkeypatch):
    writer = RecordingWriter()
    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({"features/a.txt": b"a"}),
        commit_writer=writer,
    )
    svc.rows = MemoryRows()
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path="features/a.txt",
            operation="write",
            content_base64=base64.b64encode(b"A").decode(),
        ),
    )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class Storage:
        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **_kwargs):
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", Storage
    )
    closed = await svc.activate(
        row.id,
        WorkspaceRepoActivateRequest(
            commit_message="agent change\noperator context",
            push=True,
            plan_id="plan-42",
            protected_main_source_sha="c" * 40,
        ),
        "operator@example.com",
    )
    assert closed.status == "committed"
    assert closed.commit_sha == "a" * 40
    assert svc.db.commits == 6
    assert len(writer.requests) == 1
    request = writer.requests[0]
    assert request.commit_message == "agent change\noperator context"
    assert request.operator == "operator@example.com"
    assert request.changeset_id == row.id
    assert request.plan_id == "plan-42"
    assert request.protected_main_source_sha == "c" * 40
    assert request.files[0].path == "features/a.txt"
    assert request.files[0].content_base64 == base64.b64encode(b"A").decode()
    assert request.files[0].expected_before_sha256 == hashlib.sha256(b"a").hexdigest()
    assert request.files[0].expected_sha256 == hashlib.sha256(b"A").hexdigest()


@pytest.mark.asyncio
async def test_git_commit_failure_preserves_activation_with_recovery_evidence(
    monkeypatch,
):
    writer = RecordingWriter(error=RuntimeError("git commit failed"))
    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({"features/a.txt": b"a"}),
        commit_writer=writer,
    )
    svc.rows = MemoryRows()
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path="features/a.txt",
            operation="write",
            content_base64=base64.b64encode(b"A").decode(),
        ),
    )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class Storage:
        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **_kwargs):
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", Storage
    )
    result = await svc.activate(
        row.id,
        WorkspaceRepoActivateRequest(commit_message="agent change", push=True),
        "tester",
    )

    assert result.status == "activated"
    assert svc.repo.files["features/a.txt"] == b"A"
    assert result.failure_detail["phase"] == "git_closure"
    assert result.failure_detail["state"] == "failed"
    assert result.failure_detail["message"] == "git commit failed"
    assert result.failure_detail["activation_preserved"] is True
    assert result.failure_detail["provenance"] == {
        "operator": "tester",
        "changeset_id": str(row.id),
        "commit_message": "agent change",
        "plan_id": None,
        "protected_main_source_sha": None,
    }
    assert result.failure_detail["dirty_generation"] == "1" * 32


@pytest.mark.asyncio
async def test_retry_git_closure_after_writer_configuration_skips_activation(
    monkeypatch,
):
    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({"features/a.txt": b"a"}),
        commit_writer=None,
    )
    svc.rows = MemoryRows()
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path="features/a.txt",
            operation="write",
            content_base64=base64.b64encode(b"A").decode(),
        ),
    )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class CountingStorage:
        writes = 0

        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **_kwargs):
            type(self).writes += 1
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", CountingStorage
    )
    failed = await svc.activate(
        row.id,
        WorkspaceRepoActivateRequest(commit_message="release source", push=True),
        "tester",
    )
    assert failed.status == "activated"
    assert failed.failure_detail["state"] == "not_configured"
    assert failed.failure_detail["provenance"]["commit_message"] == "release source"
    assert CountingStorage.writes == 1
    recorded_writer_job_id = uuid4()
    stored.writer_job_id = recorded_writer_job_id

    retry_writer = RecordingWriter(
        result=PlatformCommitResult(
            commit_sha="b" * 40,
            tree_sha="c" * 40,
            signature_state="VALID",
        )
    )
    svc.commit_writer = retry_writer
    closed = await svc.retry_git_closure(
        row.id,
        WorkspaceRepoActivateRequest(push=True),
        "retrying-operator",
    )

    assert closed.status == "committed"
    assert closed.commit_sha == "b" * 40
    assert closed.failure_detail is None
    assert closed.writer_job_id == recorded_writer_job_id
    assert CountingStorage.writes == 1
    assert len(retry_writer.requests) == 1
    assert retry_writer.requests[0].operator == "tester"


@pytest.mark.asyncio
async def test_retry_git_closure_rejects_non_retryable_state():
    svc = service({"features/a.txt": b"a"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())

    with pytest.raises(ChangesetInvalid, match="does not have a retryable"):
        await svc.retry_git_closure(
            row.id,
            WorkspaceRepoActivateRequest(commit_message="release source", push=True),
            "tester",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_state", RETRYABLE_GIT_FAILURE_STATES)
async def test_retry_git_closure_accepts_each_durable_failure_state(failure_state):
    svc = service({"features/a.txt": b"a"})
    svc.commit_writer = RecordingWriter()
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    stored = svc.rows.items[row.id]
    stored.status = "activated"
    stored.failure_detail = {
        "phase": "git_closure",
        "state": failure_state,
        "provenance": {
            "operator": "tester",
            "commit_message": "release source",
        },
    }
    snapshot = build_snapshot(
        {"features/a.txt": hashlib.sha256(b"a").hexdigest()}
    )
    stored.authoritative_revision = snapshot.revision
    stored.authoritative_files = snapshot.file_hashes
    stored.authoritative_base_files = snapshot.file_hashes

    result = await svc.retry_git_closure(
        row.id,
        WorkspaceRepoActivateRequest(push=True),
        "retrying-operator",
    )

    assert result.status == "committed"


@pytest.mark.asyncio
async def test_retry_committed_unpushed_requires_push():
    svc = service({"features/a.txt": b"a"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    stored = svc.rows.items[row.id]
    stored.status = "committed_unpushed"
    stored.failure_detail = {"phase": "git_push", "state": "failed"}

    with pytest.raises(ChangesetInvalid, match="requires push=true"):
        await svc.retry_git_closure(
            row.id,
            WorkspaceRepoActivateRequest(commit_message="release source"),
            "tester",
        )


@pytest.mark.asyncio
async def test_recoverable_git_closures_are_scope_bounded():
    svc = service({"features/a.txt": b"a", "features/b.txt": b"b"})
    first = await svc.begin(
        WorkspaceRepoChangesetBegin(scope="features/a.txt"), uuid4()
    )
    second = await svc.begin(
        WorkspaceRepoChangesetBegin(scope="features/b.txt"), uuid4()
    )
    first_stored = svc.rows.items[first.id]
    first_stored.status = "activated"
    first_stored.failure_detail = {
        "phase": "git_closure",
        "state": "not_configured",
    }
    second_stored = svc.rows.items[second.id]
    second_stored.status = "activated"
    second_stored.failure_detail = {"phase": "git_closure", "state": "failed"}

    result = await svc.recoverable_git_closures(scope="features/a.txt")

    assert [row.id for row in result] == [first.id]


@pytest.mark.asyncio
async def test_verification_failure_persists_candidate_sha_for_retry(monkeypatch):
    writer = RecordingWriter(
        error=PlatformCommitError(
            "GitHub commit signature is not verified: MISSING",
            commit_sha="c" * 40,
        )
    )
    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({"features/a.txt": b"a"}),
        commit_writer=writer,
    )
    svc.rows = MemoryRows()
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path="features/a.txt",
            operation="write",
            content_base64=base64.b64encode(b"A").decode(),
        ),
    )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class Storage:
        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **_kwargs):
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", Storage
    )
    failed = await svc.activate(
        row.id,
        WorkspaceRepoActivateRequest(commit_message="agent change", push=True),
        "tester",
    )
    assert failed.status == "committed_unpushed"
    assert failed.commit_sha == "c" * 40
    assert failed.failure_detail["commit_sha"] == "c" * 40
    assert svc.repo.files["features/a.txt"] == b"A"

    retry_writer = RecordingWriter()
    svc.commit_writer = retry_writer
    with pytest.raises(ChangesetInvalid, match="must match the original"):
        await svc.retry_git_closure(
            row.id,
            WorkspaceRepoActivateRequest(commit_message="different", push=True),
            "retrying-operator",
        )
    closed = await svc.retry_git_closure(
        row.id,
        WorkspaceRepoActivateRequest(push=True),
        "retrying-operator",
    )
    assert closed.status == "committed"
    retry_request = retry_writer.requests[0]
    assert retry_request.candidate_commit_sha == "c" * 40
    assert retry_request.commit_message == "agent change"
    assert retry_request.operator == "tester"


@pytest.mark.asyncio
async def test_commit_message_without_push_is_rejected_before_activation(monkeypatch):
    svc = service({"features/a.txt": b"a"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path="features/a.txt",
            operation="write",
            content_base64=base64.b64encode(b"A").decode(),
        ),
    )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    with pytest.raises(ChangesetInvalid, match="requires push=true"):
        await svc.activate(
            row.id,
            WorkspaceRepoActivateRequest(commit_message="agent change"),
            "tester",
        )

    assert stored.status == "validated"
    assert svc.repo.files["features/a.txt"] == b"a"


@pytest.mark.asyncio
@pytest.mark.parametrize("reconciled", [True, False])
async def test_successful_pushed_closure_reconciles_only_its_dirty_generation(
    monkeypatch, reconciled
):
    reconcile = AsyncMock(return_value=reconciled)
    monkeypatch.setattr("src.core.repo_dirty.reconcile_repo_dirty", reconcile)
    writer = RecordingWriter()
    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({"features/a.txt": b"a"}),
        commit_writer=writer,
    )
    svc.rows = MemoryRows()
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path="features/a.txt",
            operation="write",
            content_base64=base64.b64encode(b"A").decode(),
        ),
    )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class Storage:
        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **_kwargs):
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", Storage
    )
    closed = await svc.activate(
        row.id,
        WorkspaceRepoActivateRequest(commit_message="release", push=True),
        "tester",
    )

    reconcile.assert_awaited_once_with("1" * 32)
    assert closed.status == "committed"
    if reconciled:
        assert closed.failure_detail is None
    else:
        assert closed.failure_detail["state"] == "preserved_newer_generation"
