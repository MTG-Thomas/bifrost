import base64
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from src.models.contracts.workspace_repo_changesets import (
    WorkspaceRepoActivateRequest,
    WorkspaceRepoChangesetBegin,
    WorkspaceRepoFileMutationRequest,
    WorkspaceRepoGitConvergenceApplyRequest,
    WorkspaceRepoGitConvergencePreviewRequest,
)
from src.repositories.workspace_repo_changesets import RETRYABLE_GIT_FAILURE_STATES
from src.services.platform_commit_writer import (
    PlatformCommitError,
    PlatformCommitResult,
    PlatformCommitSnapshot,
)
from src.services.workspace_repo_changesets import (
    ChangesetConflict,
    ChangesetInvalid,
    OrganizationScopeRequired,
    WorkspaceRepoChangesetService,
    require_organization_id,
)


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
            and row.failure_detail.get("state") in RETRYABLE_GIT_FAILURE_STATES
            and (
                (row.status, row.failure_detail.get("phase"))
                in WorkspaceRepoChangesetService.RETRYABLE_GIT_FAILURES
                or (
                    row.status == "activated"
                    and row.failure_detail.get("phase") == "git_convergence"
                )
            )
        ]


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
    def __init__(self, result=None, error=None, *, history_hashes=None):
        self.result = result or PlatformCommitResult(
            commit_sha="a" * 40,
            tree_sha="b" * 40,
            signature_state="VALID",
        )
        self.error = error
        self.history_hashes = dict(history_hashes or {})
        self.requests = []
        self.inspections = []

    async def inspect(self, paths, *, ref=None, reachable_from=None):
        self.inspections.append((paths, ref, reachable_from))
        return PlatformCommitSnapshot(
            commit_sha="d" * 40,
            tree_sha="e" * 40,
            file_sha256={path: self.history_hashes.get(path) for path in paths},
            signature_state="VALID" if ref is None else None,
        )

    async def write(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.result


class ConvergenceWriter(RecordingWriter):
    def __init__(self, *, source_sha, source_hashes, history_sha, history_hashes):
        super().__init__()
        self.source_sha = source_sha
        self.source_hashes = source_hashes
        self.history_sha = history_sha
        self.history_hashes = history_hashes
        self.inspections = []

    async def inspect(self, paths, *, ref=None, reachable_from=None):
        self.inspections.append((paths, ref, reachable_from))
        if ref is not None:
            return PlatformCommitSnapshot(
                commit_sha=self.source_sha,
                tree_sha="1" * 40,
                file_sha256={path: self.source_hashes.get(path) for path in paths},
            )
        return PlatformCommitSnapshot(
            commit_sha=self.history_sha,
            tree_sha="2" * 40,
            file_sha256={path: self.history_hashes.get(path) for path in paths},
            signature_state="VALID",
        )


def activation_request(row, **kwargs):
    """Bind a unit-test activation to the exact candidate under review."""
    candidate_id = row.validation.get("candidate_id")
    if not candidate_id:
        candidate_id = "sha256:" + "c" * 64
        row.validation["candidate_id"] = candidate_id
    return WorkspaceRepoActivateRequest(candidate_id=candidate_id, **kwargs)


def service(files=None, organization_id=None):
    value = WorkspaceRepoChangesetService(
        FakeDB(), organization_id or uuid4(), repo=MemoryRepo(files)
    )
    value.rows = MemoryRows()
    return value


def test_repo_changesets_require_an_organization_scope():
    with pytest.raises(OrganizationScopeRequired, match="organization-scoped"):
        require_organization_id(None)


def test_verify_requires_an_exact_existing_hash_without_content():
    with pytest.raises(ValueError, match="expected_hash"):
        WorkspaceRepoFileMutationRequest(
            path="features/existing.py",
            operation="verify",
        )
    content_base64 = base64.b64encode(b"unchanged").decode()
    with pytest.raises(ValueError, match="content_base64"):
        WorkspaceRepoFileMutationRequest(
            path="features/existing.py",
            operation="verify",
            expected_hash="a" * 64,
            content_base64=content_base64,
        )


def test_candidate_id_is_deterministic_across_equivalent_changesets():
    mutation = {
        "path": "features/existing.py",
        "operation": "verify",
        "before_hash": "a" * 64,
        "after_hash": "a" * 64,
        "force_deactivation": False,
    }
    first = SimpleNamespace(
        id=uuid4(),
        scope="features",
        base_revision="b" * 64,
        mutations=[mutation],
    )
    second = SimpleNamespace(
        id=uuid4(),
        scope="features",
        base_revision="b" * 64,
        mutations=[mutation],
    )
    actions = [
        {
            "action": "create",
            "path": "features/existing.py",
            "function_name": "existing",
            "type": "workflow",
            "name": "Existing",
            "requested_id": None,
            "organization_id": str(uuid4()),
        },
        {
            "action": "preserve",
            "path": "features/second.py",
            "function_name": "second",
            "type": "tool",
            "name": "Second",
            "requested_id": None,
            "organization_id": str(uuid4()),
        },
    ]

    assert WorkspaceRepoChangesetService._candidate_id(
        first,
        validated_revision="c" * 64,
        registration_actions=actions,
    ) == WorkspaceRepoChangesetService._candidate_id(
        second,
        validated_revision="c" * 64,
        registration_actions=list(reversed(actions)),
    )


@pytest.mark.asyncio
async def test_validate_rechecks_non_python_verify_hash():
    path = "features/existing.txt"
    original_hash = hashlib.sha256(b"unchanged").hexdigest()
    svc = service({path: b"unchanged"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path=path,
            operation="verify",
            expected_hash=original_hash,
        ),
    )
    svc.repo.files[path] = b"changed"

    with pytest.raises(ChangesetConflict) as exc:
        await svc.validate(row.id)
    assert exc.value.detail["reason"] == "file_revision_mismatch"


@pytest.mark.asyncio
async def test_validate_empty_changeset_explains_no_op():
    svc = service({"features/existing.py": b"pass\n"})
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())

    result = await svc.validate(row.id)

    assert result.valid is False
    assert result.diagnostics == [
        {
            "severity": "error",
            "source": "no_op",
            "message": "changeset contains no source or registry mutation to activate",
        }
    ]


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
    candidate_id = "sha256:" + "c" * 64
    stored.validation = {"valid": True, "candidate_id": candidate_id}
    stored.status = "validated"
    svc.repo.files["features/b.py"] = b"B"  # unrelated concurrent edit

    write_kwargs = []

    class Storage:
        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **kwargs):
            write_kwargs.append(kwargs)
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

        async def delete_file(self, path):
            await svc.repo.delete(path)

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", Storage
    )
    mismatched_request = WorkspaceRepoActivateRequest(candidate_id="sha256:" + "d" * 64)
    with pytest.raises(ChangesetInvalid, match="candidate_id"):
        await svc.activate(row.id, mismatched_request, "tester")
    activated = await svc.activate(
        row.id, WorkspaceRepoActivateRequest(candidate_id=candidate_id), "tester"
    )
    assert activated.status == "activated"
    assert svc.repo.files == {"features/a.py": b"A", "features/b.py": b"B"}
    assert write_kwargs == [
        {
            "updated_by": "tester",
            "force_deactivation": True,
            "skip_dirty_flag": False,
        }
    ]
    assert activated.validation["activation_evidence"] == {
        "schema": "bifrost.workspace-candidate/v2",
        "candidate_id": candidate_id,
        "activated_revision": activated.activated_revision,
        "files": [
            {
                "path": "features/a.py",
                "operation": "write",
                "sha256": hashlib.sha256(b"A").hexdigest(),
            }
        ],
        "registration_actions": [],
    }
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
        await svc.activate(second.id, activation_request(current), "tester")
    assert exc.value.detail["conflicting_paths"] == ["features/a.py"]

    aborted = await svc.abort(second.id)
    assert aborted.status == "aborted"


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
        await svc.activate(row.id, activation_request(stored), "tester")
    assert svc.repo.files == {"features/a.txt": b"a", "features/b.txt": b"b"}
    assert stored.status == "failed"


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
        await svc.activate(row.id, activation_request(stored), "tester")
    assert stored.status == "recovery_required"
    assert "activation failed" in stored.error
    assert "rollback failed" in stored.error
    assert stored.failure_detail["rollback"]["state"] == "failed"
    assert stored.failure_detail["rollback"]["errors"]


@pytest.mark.asyncio
async def test_activation_closes_with_verified_writer_and_provenance(monkeypatch):
    writer = RecordingWriter(
        history_hashes={"features/a.txt": hashlib.sha256(b"a").hexdigest()}
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
    closed = await svc.activate(
        row.id,
        activation_request(
            stored,
            commit_message="agent change\noperator context",
            push=True,
            plan_id="plan-42",
            protected_main_source_sha="c" * 40,
        ),
        "operator@example.com",
    )
    assert closed.status == "committed"
    assert closed.commit_sha == "a" * 40
    assert svc.db.commits == 3  # activation, pending marker, verified closure
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
    assert request.expected_head_sha == "d" * 40


@pytest.mark.asyncio
async def test_activation_closure_accepts_history_already_at_target(monkeypatch):
    path = "shared/bifrost/customer_identity.py"
    stale_runtime = b"stale active runtime\n"
    reviewed = b"reviewed target\n"
    reviewed_hash = hashlib.sha256(reviewed).hexdigest()
    writer = RecordingWriter(history_hashes={path: reviewed_hash})
    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({path: stale_runtime}),
        commit_writer=writer,
    )
    svc.rows = MemoryRows()
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope=path), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path=path,
            operation="write",
            content_base64=base64.b64encode(reviewed).decode(),
        ),
    )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    write_kwargs = []

    class Storage:
        def __init__(self, _db):
            pass

        async def write_file(self, file_path, content, **kwargs):
            write_kwargs.append(kwargs)
            await svc.repo.write(file_path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", Storage
    )

    @asynccontextmanager
    async def source_update(**_kwargs):
        yield

    monkeypatch.setattr("src.core.module_cache.workspace_source_update", source_update)
    closed = await svc.activate(
        row.id,
        activation_request(
            stored,
            commit_message="Release reviewed customer identity",
            push=True,
        ),
        "operator@example.com",
    )

    assert closed.status == "committed"
    assert closed.commit_sha == "d" * 40
    assert closed.error is None
    assert closed.failure_detail is None
    assert closed.validation["git_closure"] == {
        "schema": "bifrost.workspace-git-closure/v1",
        "history_head_sha": "d" * 40,
        "history_tree_sha": "e" * 40,
        "signature_state": "VALID",
        "paths": [
            {
                "path": path,
                "disposition": "target",
                "before_sha256": hashlib.sha256(stale_runtime).hexdigest(),
                "target_sha256": reviewed_hash,
                "history_sha256": reviewed_hash,
            }
        ],
        "disposition": "superseded",
        "commit_sha": "d" * 40,
        "superseded_paths": [path],
        "committed_paths": [],
    }
    assert writer.requests == []
    assert write_kwargs == [
        {
            "updated_by": "operator@example.com",
            "force_deactivation": False,
            "skip_dirty_flag": True,
        }
    ]


@pytest.mark.asyncio
async def test_activation_closure_commits_only_before_paths_from_mixed_history(
    monkeypatch,
):
    preserved_path = "features/preserved.py"
    missing_path = "features/missing.py"
    before = b"before\n"
    preserved_target = b"already preserved\n"
    missing_target = b"still missing\n"
    writer = RecordingWriter(
        history_hashes={
            preserved_path: hashlib.sha256(preserved_target).hexdigest(),
            missing_path: hashlib.sha256(before).hexdigest(),
        }
    )
    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({preserved_path: before, missing_path: before}),
        commit_writer=writer,
    )
    svc.rows = MemoryRows()
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    for path, content in (
        (preserved_path, preserved_target),
        (missing_path, missing_target),
    ):
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

    class Storage:
        def __init__(self, _db):
            pass

        async def write_file(self, path, content, **_kwargs):
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", Storage
    )

    @asynccontextmanager
    async def source_update(**_kwargs):
        yield

    monkeypatch.setattr("src.core.module_cache.workspace_source_update", source_update)
    closed = await svc.activate(
        row.id,
        activation_request(stored, commit_message="Release mixed closure", push=True),
        "operator@example.com",
    )

    assert closed.status == "committed"
    assert len(writer.requests) == 1
    request = writer.requests[0]
    assert [item.path for item in request.files] == [missing_path]
    assert request.expected_head_sha == "d" * 40
    assert closed.validation["git_closure"]["disposition"] == ("partially_superseded")
    assert closed.validation["git_closure"]["superseded_paths"] == [preserved_path]
    assert closed.validation["git_closure"]["committed_paths"] == [missing_path]


@pytest.mark.asyncio
async def test_activation_closure_preserves_activation_for_other_history_bytes(
    monkeypatch,
):
    path = "features/diverged.py"
    writer = RecordingWriter(
        history_hashes={path: hashlib.sha256(b"unrelated\n").hexdigest()}
    )
    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({path: b"before\n"}),
        commit_writer=writer,
    )
    svc.rows = MemoryRows()
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope=path), uuid4())
    await svc.stage(
        row.id,
        WorkspaceRepoFileMutationRequest(
            path=path,
            operation="write",
            content_base64=base64.b64encode(b"target\n").decode(),
        ),
    )
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class Storage:
        def __init__(self, _db):
            pass

        async def write_file(self, file_path, content, **_kwargs):
            await svc.repo.write(file_path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr(
        "src.services.workspace_repo_changesets.FileStorageService", Storage
    )

    @asynccontextmanager
    async def source_update(**_kwargs):
        yield

    monkeypatch.setattr("src.core.module_cache.workspace_source_update", source_update)
    result = await svc.activate(
        row.id,
        activation_request(stored, commit_message="Release diverged path", push=True),
        "operator@example.com",
    )

    assert result.status == "activated"
    assert result.failure_detail["activation_preserved"] is True
    assert result.failure_detail["history"]["paths"][0]["disposition"] == "other"
    assert "outside the reviewed before/target states" in result.error
    assert writer.requests == []


@pytest.mark.asyncio
async def test_git_commit_failure_preserves_activation_with_recovery_evidence(
    monkeypatch,
):
    writer = RecordingWriter(
        error=RuntimeError("git commit failed"),
        history_hashes={"features/a.txt": hashlib.sha256(b"a").hexdigest()},
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
    result = await svc.activate(
        row.id,
        activation_request(stored, commit_message="agent change", push=True),
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
        activation_request(stored, commit_message="release source", push=True),
        "tester",
    )
    assert failed.status == "activated"
    assert failed.failure_detail["state"] == "not_configured"
    assert failed.failure_detail["provenance"]["commit_message"] == "release source"
    assert CountingStorage.writes == 1

    retry_writer = RecordingWriter(
        result=PlatformCommitResult(
            commit_sha="b" * 40,
            tree_sha="c" * 40,
            signature_state="VALID",
        ),
        history_hashes={"features/a.txt": hashlib.sha256(b"a").hexdigest()},
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
    svc.commit_writer = RecordingWriter(
        history_hashes={"features/a.txt": hashlib.sha256(b"a").hexdigest()}
    )
    row = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    stored = svc.rows.items[row.id]
    stored.mutations = [
        {
            "path": "features/a.txt",
            "operation": "write",
            "before_hash": hashlib.sha256(b"a").hexdigest(),
            "after_hash": hashlib.sha256(b"A").hexdigest(),
            "content_base64": base64.b64encode(b"A").decode(),
        }
    ]
    stored.status = "activated"
    stored.failure_detail = {
        "phase": "git_closure",
        "state": failure_state,
        "provenance": {
            "operator": "tester",
            "commit_message": "release source",
        },
    }

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
async def test_git_convergence_uses_history_parent_hash_not_workspace_before_hash():
    path = "features/readiness.py"
    workspace_before = b"workspace-before\n"
    history_before = b"different-history-before\n"
    live = b"reviewed-and-live\n"
    source_sha = "d" * 40
    live_hash = hashlib.sha256(live).hexdigest()
    history_hash = hashlib.sha256(history_before).hexdigest()
    writer = ConvergenceWriter(
        source_sha=source_sha,
        source_hashes={path: live_hash},
        history_sha="e" * 40,
        history_hashes={path: history_hash},
    )
    svc = WorkspaceRepoChangesetService(
        FakeDB(), uuid4(), repo=MemoryRepo({path: workspace_before}), commit_writer=writer
    )
    svc.rows = MemoryRows()
    started = await svc.begin(WorkspaceRepoChangesetBegin(scope=path), uuid4())
    await svc.stage(
        started.id,
        WorkspaceRepoFileMutationRequest(
            path=path,
            operation="write",
            content_base64=base64.b64encode(live).decode(),
        ),
    )
    row = svc.rows.items[started.id]
    await svc.repo.write(path, live)
    row.status = "activated"
    row.failure_detail = {
        "phase": "git_closure",
        "state": "failed",
        "activation_preserved": True,
    }

    preview = await svc.preview_git_convergence(
        WorkspaceRepoGitConvergencePreviewRequest(
            changeset_ids=[row.id], protected_main_source_sha=source_sha
        )
    )

    assert preview.ready_to_apply is True
    assert preview.paths[0].history_sha256 == history_hash
    assert preview.paths[0].live_sha256 == live_hash
    assert preview.paths[0].reviewed_sha256 == live_hash
    applied = await svc.apply_git_convergence(
        WorkspaceRepoGitConvergenceApplyRequest(
            changeset_ids=[row.id],
            protected_main_source_sha=source_sha,
            candidate_id=preview.candidate_id,
            commit_message="Converge selected production history",
        ),
        operator="operator@example.com",
    )

    assert applied.applied is True
    assert applied.signature_state == "VALID"
    assert row.status == "committed"
    assert row.failure_detail is None
    assert row.validation["history_convergence"]["disposition"] == "reconciled"
    request = writer.requests[0]
    assert request.expected_head_sha == "e" * 40
    assert request.files[0].expected_before_sha256 == history_hash
    assert request.files[0].expected_before_sha256 != hashlib.sha256(
        workspace_before
    ).hexdigest()
    assert request.files[0].expected_sha256 == live_hash
    assert request.convergence_candidate_id == preview.candidate_id
    assert request.reconciled_changeset_ids == (row.id,)
    assert svc.db.commits == 2


@pytest.mark.asyncio
async def test_git_convergence_resumes_its_durable_pending_plan():
    path = "features/readiness.py"
    live = b"reviewed-and-live\n"
    live_hash = hashlib.sha256(live).hexdigest()
    source_sha = "d" * 40
    writer = ConvergenceWriter(
        source_sha=source_sha,
        source_hashes={path: live_hash},
        history_sha="e" * 40,
        history_hashes={path: None},
    )
    svc = WorkspaceRepoChangesetService(
        FakeDB(), uuid4(), repo=MemoryRepo({path: live}), commit_writer=writer
    )
    svc.rows = MemoryRows()
    started = await svc.begin(WorkspaceRepoChangesetBegin(scope=path), uuid4())
    await svc.stage(
        started.id,
        WorkspaceRepoFileMutationRequest(
            path=path,
            operation="write",
            content_base64=base64.b64encode(live).decode(),
        ),
    )
    row = svc.rows.items[started.id]
    row.status = "activated"
    row.failure_detail = {"phase": "git_closure", "state": "failed"}
    request = WorkspaceRepoGitConvergencePreviewRequest(
        changeset_ids=[row.id], protected_main_source_sha=source_sha
    )
    preview = await svc.preview_git_convergence(request)
    row.failure_detail = {
        "phase": "git_convergence",
        "state": "pending",
        "candidate_id": preview.candidate_id,
        "commit_message": "Converge selected production history",
        "operator": "original@example.com",
        "primary_evidence_changeset_id": str(row.id),
        "plan": preview.model_dump(mode="json"),
        "files": [
            {
                "path": path,
                "content_base64": base64.b64encode(live).decode(),
                "expected_before_sha256": None,
                "expected_sha256": live_hash,
            }
        ],
    }
    writer.history_sha = "f" * 40
    writer.history_hashes[path] = live_hash

    resumed_preview = await svc.preview_git_convergence(request)
    assert resumed_preview.candidate_id == preview.candidate_id
    applied = await svc.apply_git_convergence(
        WorkspaceRepoGitConvergenceApplyRequest(
            **request.model_dump(),
            candidate_id=preview.candidate_id,
            commit_message="Converge selected production history",
        ),
        operator="retry@example.com",
    )

    assert applied.applied is True
    assert writer.requests[0].operator == "original@example.com"
    assert writer.requests[0].expected_head_sha == "e" * 40


@pytest.mark.asyncio
async def test_git_convergence_apply_rejects_history_drift_after_preview():
    path = "features/readiness.py"
    live = b"reviewed-and-live\n"
    live_hash = hashlib.sha256(live).hexdigest()
    source_sha = "d" * 40
    writer = ConvergenceWriter(
        source_sha=source_sha,
        source_hashes={path: live_hash},
        history_sha="e" * 40,
        history_hashes={path: None},
    )
    svc = WorkspaceRepoChangesetService(
        FakeDB(), uuid4(), repo=MemoryRepo({path: live}), commit_writer=writer
    )
    svc.rows = MemoryRows()
    started = await svc.begin(WorkspaceRepoChangesetBegin(scope=path), uuid4())
    await svc.stage(
        started.id,
        WorkspaceRepoFileMutationRequest(
            path=path,
            operation="write",
            content_base64=base64.b64encode(live).decode(),
        ),
    )
    row = svc.rows.items[started.id]
    row.status = "activated"
    row.failure_detail = {"phase": "git_closure", "state": "failed"}
    preview_request = WorkspaceRepoGitConvergencePreviewRequest(
        changeset_ids=[row.id], protected_main_source_sha=source_sha
    )
    preview = await svc.preview_git_convergence(preview_request)
    writer.history_sha = "f" * 40

    with pytest.raises(ChangesetConflict) as exc:
        await svc.apply_git_convergence(
            WorkspaceRepoGitConvergenceApplyRequest(
                **preview_request.model_dump(),
                candidate_id=preview.candidate_id,
                commit_message="Converge selected production history",
            ),
            operator="operator@example.com",
        )

    assert exc.value.detail["reason"] == "history_convergence_candidate_mismatch"
    assert writer.requests == []
    assert row.status == "activated"


@pytest.mark.asyncio
async def test_git_convergence_marks_older_selected_bytes_as_superseded():
    path = "features/shared.py"
    old = b"old selected bytes\n"
    live = b"latest selected bytes\n"
    live_hash = hashlib.sha256(live).hexdigest()
    source_sha = "d" * 40
    writer = ConvergenceWriter(
        source_sha=source_sha,
        source_hashes={path: live_hash},
        history_sha="e" * 40,
        history_hashes={path: None},
    )
    svc = WorkspaceRepoChangesetService(
        FakeDB(), uuid4(), repo=MemoryRepo({path: b"base\n"}), commit_writer=writer
    )
    svc.rows = MemoryRows()
    selected = []
    for content in (old, live):
        started = await svc.begin(WorkspaceRepoChangesetBegin(scope=path), uuid4())
        await svc.stage(
            started.id,
            WorkspaceRepoFileMutationRequest(
                path=path,
                operation="write",
                content_base64=base64.b64encode(content).decode(),
            ),
        )
        row = svc.rows.items[started.id]
        row.status = "activated"
        row.failure_detail = {"phase": "git_closure", "state": "failed"}
        selected.append(row)
    await svc.repo.write(path, live)

    preview = await svc.preview_git_convergence(
        WorkspaceRepoGitConvergencePreviewRequest(
            changeset_ids=[row.id for row in selected],
            protected_main_source_sha=source_sha,
        )
    )

    dispositions = {
        item.changeset_id: item.disposition for item in preview.changesets
    }
    assert preview.ready_to_apply is True
    assert dispositions[selected[0].id] == "superseded"
    assert dispositions[selected[1].id] == "reconciled"


@pytest.mark.asyncio
async def test_git_convergence_uses_current_reviewed_bytes_for_superseded_path():
    path = "features/shared.py"
    selected_bytes = b"bytes from selected release\n"
    current = b"newer reviewed and live bytes\n"
    current_hash = hashlib.sha256(current).hexdigest()
    source_sha = "d" * 40
    writer = ConvergenceWriter(
        source_sha=source_sha,
        source_hashes={path: current_hash},
        history_sha="e" * 40,
        history_hashes={path: None},
    )
    svc = WorkspaceRepoChangesetService(
        FakeDB(), uuid4(), repo=MemoryRepo({path: b"base\n"}), commit_writer=writer
    )
    svc.rows = MemoryRows()
    started = await svc.begin(WorkspaceRepoChangesetBegin(scope=path), uuid4())
    await svc.stage(
        started.id,
        WorkspaceRepoFileMutationRequest(
            path=path,
            operation="write",
            content_base64=base64.b64encode(selected_bytes).decode(),
        ),
    )
    row = svc.rows.items[started.id]
    row.status = "activated"
    row.failure_detail = {"phase": "git_closure", "state": "failed"}
    await svc.repo.write(path, current)

    preview = await svc.preview_git_convergence(
        WorkspaceRepoGitConvergencePreviewRequest(
            changeset_ids=[row.id], protected_main_source_sha=source_sha
        )
    )

    assert preview.ready_to_apply is True
    assert preview.paths[0].desired_sha256 == current_hash
    assert preview.paths[0].source_changeset_id is None
    assert preview.changesets[0].disposition == "superseded"
    applied = await svc.apply_git_convergence(
        WorkspaceRepoGitConvergenceApplyRequest(
            changeset_ids=[row.id],
            protected_main_source_sha=source_sha,
            candidate_id=preview.candidate_id,
            commit_message="Converge selected production history",
        ),
        operator="operator@example.com",
    )

    assert applied.applied is True
    assert writer.requests[0].files[0].content_base64 == base64.b64encode(
        current
    ).decode()


@pytest.mark.asyncio
async def test_git_convergence_accepts_superseded_path_already_preserved_in_history():
    superseded_path = "features/already_preserved.py"
    missing_path = "features/missing.py"
    superseded_live = b"older reviewed bytes already preserved\n"
    selected_bytes = b"selected release bytes\n"
    missing_live = b"missing selected release bytes\n"
    source_sha = "d" * 40
    writer = ConvergenceWriter(
        source_sha=source_sha,
        source_hashes={
            superseded_path: hashlib.sha256(selected_bytes).hexdigest(),
            missing_path: hashlib.sha256(missing_live).hexdigest(),
        },
        history_sha="e" * 40,
        history_hashes={
            superseded_path: hashlib.sha256(superseded_live).hexdigest(),
            missing_path: None,
        },
    )
    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo(
            {
                superseded_path: b"base\n",
                missing_path: b"base\n",
            }
        ),
        commit_writer=writer,
    )
    svc.rows = MemoryRows()
    started = await svc.begin(WorkspaceRepoChangesetBegin(scope="features"), uuid4())
    for path, content in (
        (superseded_path, selected_bytes),
        (missing_path, missing_live),
    ):
        await svc.stage(
            started.id,
            WorkspaceRepoFileMutationRequest(
                path=path,
                operation="write",
                content_base64=base64.b64encode(content).decode(),
            ),
        )
    row = svc.rows.items[started.id]
    row.status = "activated"
    row.failure_detail = {"phase": "git_closure", "state": "failed"}
    await svc.repo.write(superseded_path, superseded_live)
    await svc.repo.write(missing_path, missing_live)

    preview = await svc.preview_git_convergence(
        WorkspaceRepoGitConvergencePreviewRequest(
            changeset_ids=[row.id], protected_main_source_sha=source_sha
        )
    )

    assert preview.ready_to_apply is True
    assert preview.changesets[0].disposition == "partially_superseded"
    assert preview.changesets[0].superseded_paths == [superseded_path]
    assert preview.changesets[0].reconciled_paths == [missing_path]
    assert preview.diagnostics == [
        {
            "severity": "warning",
            "source": "live_vs_reviewed",
            "path": superseded_path,
            "live_sha256": hashlib.sha256(superseded_live).hexdigest(),
            "reviewed_sha256": hashlib.sha256(selected_bytes).hexdigest(),
            "history_sha256": hashlib.sha256(superseded_live).hexdigest(),
        }
    ]


@pytest.mark.asyncio
async def test_verification_failure_persists_candidate_sha_for_retry(monkeypatch):
    writer = RecordingWriter(
        error=PlatformCommitError(
            "GitHub commit signature is not verified: MISSING",
            commit_sha="c" * 40,
        ),
        history_hashes={"features/a.txt": hashlib.sha256(b"a").hexdigest()},
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
        activation_request(stored, commit_message="agent change", push=True),
        "tester",
    )
    assert failed.status == "activated"
    assert failed.commit_sha == "c" * 40
    assert failed.failure_detail["commit_sha"] == "c" * 40
    assert svc.repo.files["features/a.txt"] == b"A"

    retry_writer = RecordingWriter(
        history_hashes={"features/a.txt": hashlib.sha256(b"A").hexdigest()}
    )
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
    assert closed.commit_sha == "d" * 40
    assert closed.validation["git_closure"]["disposition"] == "superseded"
    assert retry_writer.requests == []


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
