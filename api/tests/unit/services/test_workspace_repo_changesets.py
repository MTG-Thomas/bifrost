import base64
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.contracts.workspace_repo_changesets import (
    WorkspaceRepoActivateRequest,
    WorkspaceRepoChangesetBegin,
    WorkspaceRepoFileMutationRequest,
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


def service(files=None, organization_id=None):
    value = WorkspaceRepoChangesetService(
        FakeDB(), organization_id or uuid4(), repo=MemoryRepo(files)
    )
    value.rows = MemoryRows()
    return value


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
async def test_activation_closes_with_commit_without_push_by_default(monkeypatch):
    calls = []

    async def commit(message, push):
        assert svc.db.commits == 2  # activation, then durable Git-pending marker
        calls.append((message, push))
        return "a" * 40, None

    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({"features/a.txt": b"a"}),
        commit_callback=commit,
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
        row.id, WorkspaceRepoActivateRequest(commit_message="agent change"), "tester"
    )
    assert closed.status == "committed"
    assert closed.commit_sha == "a" * 40
    assert calls == [("agent change", False)]


@pytest.mark.asyncio
async def test_git_commit_failure_preserves_activation_with_recovery_evidence(monkeypatch):
    async def commit(_message, _push):
        raise RuntimeError("git commit failed")

    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({"features/a.txt": b"a"}),
        commit_callback=commit,
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
        row.id, WorkspaceRepoActivateRequest(commit_message="agent change"), "tester"
    )

    assert result.status == "activated"
    assert svc.repo.files["features/a.txt"] == b"A"
    assert result.failure_detail == {
        "phase": "git_closure",
        "state": "failed",
        "message": "git commit failed",
        "activation_preserved": True,
    }


@pytest.mark.asyncio
async def test_activation_records_committed_unpushed_without_rolling_back(monkeypatch):
    async def commit(_message, _push):
        return "a" * 40, "remote rejected push"

    svc = WorkspaceRepoChangesetService(
        FakeDB(),
        uuid4(),
        repo=MemoryRepo({"features/a.txt": b"a"}),
        commit_callback=commit,
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
        WorkspaceRepoActivateRequest(commit_message="agent change", push=True),
        "tester",
    )
    assert closed.status == "committed_unpushed"
    assert closed.commit_sha == "a" * 40
    assert closed.error == "remote rejected push"
    assert svc.repo.files["features/a.txt"] == b"A"
