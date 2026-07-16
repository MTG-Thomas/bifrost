import base64
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.models.contracts.workspace_changesets import (
    WorkspaceActivateRequest,
    WorkspaceChangesetBegin,
    WorkspaceFileMutationRequest,
)
from src.services.workspace_changesets import ChangesetConflict, ChangesetInvalid, WorkspaceChangesetService


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

    async def get(self, row_id, for_update=False):
        return self.items.get(row_id)

    async def count_open(self, scope):
        return sum(row.scope == scope and row.status in {"open", "staged", "validated", "activating"} for row in self.items.values())


class FakeDB:
    async def flush(self):
        return None

    async def execute(self, _statement):
        return None

    async def commit(self):
        return None


def service(files=None):
    value = WorkspaceChangesetService(FakeDB(), repo=MemoryRepo(files))
    value.rows = MemoryRows()
    return value


@pytest.mark.asyncio
async def test_begin_uses_canonical_revision_and_rejects_stale_revision():
    svc = service({"features/a.py": b"one", "features/b.py": b"two", "other/x": b"ignored"})
    state = await svc.state("features")
    row = await svc.begin(WorkspaceChangesetBegin(scope="features", base_revision=state.revision, worker_id="worker-1"), uuid4())
    assert row.base_revision == state.revision
    assert row.worker_id == "worker-1"

    with pytest.raises(ChangesetConflict) as exc:
        await svc.begin(WorkspaceChangesetBegin(scope="features", base_revision="0" * 64), uuid4())
    assert exc.value.detail["reason"] == "revision_mismatch"


@pytest.mark.asyncio
async def test_stage_is_scope_bounded_and_diff_does_not_mutate_workspace():
    svc = service({"features/a.py": b"old\n"})
    row = await svc.begin(WorkspaceChangesetBegin(scope="features"), uuid4())
    changed = await svc.stage(row.id, WorkspaceFileMutationRequest(path="features/a.py", operation="write", content_base64=base64.b64encode(b"new\n").decode()))
    assert changed.status == "staged"
    assert svc.repo.files["features/a.py"] == b"old\n"
    diff = await svc.diff(row.id)
    assert "-old" in diff.files[0].unified_diff
    assert "+new" in diff.files[0].unified_diff

    with pytest.raises(ChangesetInvalid):
        await svc.stage(row.id, WorkspaceFileMutationRequest(path="apps/a.ts", operation="delete"))


@pytest.mark.asyncio
async def test_path_level_cas_allows_disjoint_change_and_rejects_touched_change(monkeypatch):
    svc = service({"features/a.py": b"a", "features/b.py": b"b"})
    row = await svc.begin(WorkspaceChangesetBegin(scope="features"), uuid4())
    await svc.stage(row.id, WorkspaceFileMutationRequest(path="features/a.py", operation="write", content_base64=base64.b64encode(b"A").decode(), force_deactivation=True))
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

    monkeypatch.setattr("src.services.workspace_changesets.FileStorageService", Storage)
    activated = await svc.activate(row.id, WorkspaceActivateRequest(), "tester")
    assert activated.status == "activated"
    assert svc.repo.files == {"features/a.py": b"A", "features/b.py": b"B"}

    second = await svc.begin(WorkspaceChangesetBegin(scope="features"), uuid4())
    await svc.stage(second.id, WorkspaceFileMutationRequest(path="features/a.py", operation="delete"))
    current = svc.rows.items[second.id]
    current.validation = {"valid": True}
    current.status = "validated"
    svc.repo.files["features/a.py"] = b"someone else"
    with pytest.raises(ChangesetConflict) as exc:
        await svc.activate(second.id, WorkspaceActivateRequest(), "tester")
    assert exc.value.detail["conflicting_paths"] == ["features/a.py"]


@pytest.mark.asyncio
async def test_activation_compensates_storage_on_partial_failure(monkeypatch):
    svc = service({"features/a.txt": b"a", "features/b.txt": b"b"})
    row = await svc.begin(WorkspaceChangesetBegin(scope="features"), uuid4())
    for path, content in (("features/a.txt", b"A"), ("features/b.txt", b"B")):
        await svc.stage(row.id, WorkspaceFileMutationRequest(path=path, operation="write", content_base64=base64.b64encode(content).decode()))
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

    monkeypatch.setattr("src.services.workspace_changesets.FileStorageService", FailingStorage)
    with pytest.raises(RuntimeError, match="storage failed"):
        await svc.activate(row.id, WorkspaceActivateRequest(), "tester")
    assert svc.repo.files == {"features/a.txt": b"a", "features/b.txt": b"b"}
    assert stored.status == "failed"


@pytest.mark.asyncio
async def test_activation_closes_with_commit_without_push_by_default(monkeypatch):
    calls = []

    async def commit(message, push):
        calls.append((message, push))
        return "a" * 40

    svc = WorkspaceChangesetService(FakeDB(), repo=MemoryRepo({"features/a.txt": b"a"}), commit_callback=commit)
    svc.rows = MemoryRows()
    row = await svc.begin(WorkspaceChangesetBegin(scope="features"), uuid4())
    await svc.stage(row.id, WorkspaceFileMutationRequest(path="features/a.txt", operation="write", content_base64=base64.b64encode(b"A").decode()))
    stored = svc.rows.items[row.id]
    stored.validation = {"valid": True}
    stored.status = "validated"

    class Storage:
        def __init__(self, _db):
            pass
        async def write_file(self, path, content, **_kwargs):
            await svc.repo.write(path, content)
            return SimpleNamespace(pending_deactivations=[])

    monkeypatch.setattr("src.services.workspace_changesets.FileStorageService", Storage)
    closed = await svc.activate(row.id, WorkspaceActivateRequest(commit_message="agent change"), "tester")
    assert closed.status == "committed"
    assert closed.commit_sha == "a" * 40
    assert calls == [("agent change", False)]
