"""Focused git-sync boundary tests."""

import pytest


def _import_github_sync_service(monkeypatch):
    """Import github_sync without requiring GitPython in host-only test runs."""
    import sys
    import types

    fake_git = types.ModuleType("git")
    fake_git.Repo = object
    monkeypatch.setitem(sys.modules, "git", fake_git)

    from src.services.github_sync import GitHubSyncService

    return GitHubSyncService


class _Rows:
    def all(self):
        return [
            ("workflows/old.py", "old-hash"),
            ("workflows/other-workspace.py", "other-hash"),
        ]


class _FakeDb:
    def __init__(self):
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Rows()


class _ExistingRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RecordingDb:
    def __init__(self, existing_rows):
        self.existing_rows = existing_rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if len(self.statements) == 1:
            return _ExistingRows(self.existing_rows)
        return _ExistingRows([])


@pytest.mark.asyncio
async def test_file_index_cleanup_requires_explicit_git_deleted_paths(tmp_path, monkeypatch):
    GitHubSyncService = _import_github_sync_service(monkeypatch)
    service = object.__new__(GitHubSyncService)
    service.db = _FakeDb()

    await GitHubSyncService._update_file_index(
        service,
        tmp_path,
        removed_paths=set(),
    )

    assert len(service.db.statements) == 1


@pytest.mark.asyncio
async def test_file_index_cleanup_deletes_only_explicit_git_deleted_paths(tmp_path, monkeypatch):
    GitHubSyncService = _import_github_sync_service(monkeypatch)
    service = object.__new__(GitHubSyncService)
    service.db = _FakeDb()

    await GitHubSyncService._update_file_index(
        service,
        tmp_path,
        removed_paths={"workflows/old.py"},
    )

    assert len(service.db.statements) == 2


@pytest.mark.asyncio
async def test_file_index_skips_unchanged_binary_and_non_utf8_then_batches_upserts(
    tmp_path, monkeypatch
):
    from src.services.github_sync import _content_hash

    GitHubSyncService = _import_github_sync_service(monkeypatch)

    workflows = tmp_path / "workflows"
    workflows.mkdir()
    unchanged = workflows / "unchanged.py"
    unchanged.write_text("print('same')\n")
    (workflows / "binary.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (workflows / "bad_utf8.py").write_bytes(b"\xff\xfe\xfd")
    for idx in range(101):
        (workflows / f"changed_{idx}.py").write_text(f"print({idx})\n")

    service = object.__new__(GitHubSyncService)
    service.db = _RecordingDb(
        [
            (
                "workflows/unchanged.py",
                _content_hash(unchanged.read_bytes()),
            )
        ]
    )

    await GitHubSyncService._update_file_index(
        service,
        tmp_path,
        removed_paths=set(),
    )

    assert len(service.db.statements) == 3
    upserted_paths = {
        value
        for statement in service.db.statements[1:]
        for key, value in statement.compile().params.items()
        if key.startswith("path_m")
    }
    assert "workflows/changed_0.py" in upserted_paths
    assert "workflows/changed_100.py" in upserted_paths
    assert "workflows/unchanged.py" not in upserted_paths
    assert "workflows/binary.png" not in upserted_paths
    assert "workflows/bad_utf8.py" not in upserted_paths


@pytest.mark.asyncio
async def test_execute_ops_runs_each_op_against_service_db(monkeypatch):
    GitHubSyncService = _import_github_sync_service(monkeypatch)
    service = object.__new__(GitHubSyncService)
    service.db = object()
    calls = []

    class Op:
        def __init__(self, name):
            self.name = name

        async def execute(self, db):
            calls.append((self.name, db))

    count = await service._execute_ops([Op("first"), Op("second")])

    assert count == 2
    assert calls == [("first", service.db), ("second", service.db)]


def test_ops_to_issues_is_empty_until_ops_emit_validation_issues(monkeypatch):
    GitHubSyncService = _import_github_sync_service(monkeypatch)

    assert GitHubSyncService._ops_to_issues([object()]) == []


@pytest.mark.asyncio
async def test_reindex_registered_workflows_indexes_existing_files_only(
    tmp_path, monkeypatch
):
    import sys
    import types

    GitHubSyncService = _import_github_sync_service(monkeypatch)

    indexed = []

    class FakeWorkflowIndexer:
        def __init__(self, db):
            self.db = db

        async def index_python_file(self, path, content):
            indexed.append((path, content))

    monkeypatch.setitem(
        sys.modules,
        "src.services.file_storage.indexers.workflow",
        types.SimpleNamespace(WorkflowIndexer=FakeWorkflowIndexer),
    )

    existing = tmp_path / "workflows" / "existing.py"
    existing.parent.mkdir()
    existing.write_bytes(b"from bifrost import workflow\n")

    class Rows:
        def all(self):
            return [
                ("workflows/existing.py",),
                ("workflows/missing.py",),
            ]

    class Db:
        async def execute(self, statement):
            return Rows()

    service = object.__new__(GitHubSyncService)
    service.db = Db()

    count = await service._reindex_registered_workflows(tmp_path)

    assert count == 1
    assert indexed == [("workflows/existing.py", b"from bifrost import workflow\n")]
