"""Desktop git-sync operation coverage without network or real git remotes."""

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.models.contracts.github import (
    EntityChange,
    FetchResult,
    MergeConflict,
    PullResult,
    PushResult,
)
from src.services.github_sync import GitHubSyncService


class _AsyncPathContext:
    def __init__(self, path: Path) -> None:
        self.path = path

    async def __aenter__(self) -> Path:
        return self.path

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _NestedTx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _RepoManager:
    def __init__(self, work_dir: Path, initialized: bool = True) -> None:
        self.work_dir = work_dir
        self.is_initialized = initialized
        self.synced_up = False

    def lock(self) -> _AsyncPathContext:
        return _AsyncPathContext(self.work_dir)

    def checkout(self) -> _AsyncPathContext:
        return _AsyncPathContext(self.work_dir)

    async def sync_up(self, work_dir: Path) -> None:
        assert work_dir == self.work_dir
        self.synced_up = True


class _Db:
    def __init__(self) -> None:
        self.commits = 0

    def begin_nested(self) -> _NestedTx:
        return _NestedTx()

    async def commit(self) -> None:
        self.commits += 1


class _Head:
    def __init__(self, valid: bool = True, hexsha: str = "abc123def456") -> None:
        self._valid = valid
        self.commit = type("Commit", (), {"hexsha": hexsha})()

    def is_valid(self) -> bool:
        return self._valid


def _service(tmp_path: Path, repo) -> GitHubSyncService:
    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    service.db = _Db()
    service.repo_manager = _RepoManager(tmp_path)
    service._resolver = type("Resolver", (), {})()
    service._open_or_init = lambda work_dir: repo
    return service


@pytest.mark.asyncio
async def test_desktop_fetch_returns_error_result_when_checkout_fails(tmp_path):
    service = object.__new__(GitHubSyncService)
    service.repo_manager = type(
        "FailingManager",
        (),
        {"checkout": lambda self: (_ for _ in ()).throw(RuntimeError("storage down"))},
    )()

    result = await service.desktop_fetch(job_id="job-1")

    assert result.success is False
    assert result.error == "storage down"


@pytest.mark.asyncio
async def test_desktop_status_returns_empty_status_when_repo_uninitialized(tmp_path):
    service = object.__new__(GitHubSyncService)
    service.repo_manager = _RepoManager(tmp_path, initialized=False)

    result = await service.desktop_status()

    assert result.changed_files == []
    assert result.total_changes == 0
    assert result.conflicts == []


@pytest.mark.asyncio
async def test_desktop_fetch_regenerates_manifest_and_publishes_progress(
    tmp_path, monkeypatch
):
    progress = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "src.core.pubsub",
        types.SimpleNamespace(publish_git_progress=progress),
    )

    service = _service(tmp_path, object())
    service._regenerate_manifest_to_dir = AsyncMock()
    service._do_fetch = lambda work_dir, repo: FetchResult(
        success=True,
        commits_ahead=1,
        commits_behind=2,
    )

    result = await service.desktop_fetch(job_id="job-123")

    assert result.success is True
    assert result.commits_ahead == 1
    assert result.commits_behind == 2
    service._regenerate_manifest_to_dir.assert_awaited_once_with(service.db, tmp_path)
    assert [call.args for call in progress.await_args_list] == [
        ("job-123", "Syncing from storage...", 0, 0),
        ("job-123", "Generating manifest...", 0, 0),
        ("job-123", "Fetching remote...", 0, 0),
    ]


@pytest.mark.asyncio
async def test_desktop_commit_returns_error_result_when_core_commit_raises(tmp_path):
    service = _service(tmp_path, object())
    service._do_commit = AsyncMock(side_effect=RuntimeError("preflight crashed"))

    result = await service.desktop_commit("sync")

    assert result.success is False
    assert result.error == "preflight crashed"


@pytest.mark.asyncio
async def test_do_pull_returns_structured_conflicts_with_missing_stage_content(
    tmp_path, monkeypatch
):
    from src.services import github_sync

    monkeypatch.setattr(
        github_sync, "_auto_resolve_manifest_conflicts", lambda *_: set()
    )
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "MERGE_HEAD").write_text("merge")

    class Origin:
        def fetch(self, branch):
            assert branch == "main"

    class Git:
        def merge(self, ref):
            assert ref == "origin/main"
            raise RuntimeError("conflict")

        def show(self, ref):
            if ref == ":2:workflows/conflict.py":
                return "ours"
            raise RuntimeError("stage missing")

    class Index:
        def unmerged_blobs(self):
            return {"workflows/conflict.py": [(1, object()), (2, object())]}

    class Repo:
        remotes = type("Remotes", (), {"origin": Origin()})()
        git = Git()
        index = Index()

    service = object.__new__(GitHubSyncService)
    service.branch = "main"

    result = await service._do_pull(tmp_path, Repo())

    assert result.success is False
    assert result.error == "Merge conflicts detected"
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.path == "workflows/conflict.py"
    assert conflict.ours_content == "ours"
    assert conflict.theirs_content is None
    assert conflict.display_name == "conflict.py"
    assert conflict.entity_type == "workflow"
    assert conflict.conflict_type == "deleted_by_them"


@pytest.mark.asyncio
async def test_do_pull_commits_when_all_conflicts_auto_resolve(tmp_path, monkeypatch):
    from src.services import github_sync

    calls: list[str] = []

    def auto_resolve(repo, work_dir, unmerged):
        calls.append("auto")
        repo.index._unmerged = {}
        return {".bifrost/workflows.yaml"}

    monkeypatch.setattr(github_sync, "_auto_resolve_manifest_conflicts", auto_resolve)
    (tmp_path / ".git").mkdir()
    merge_head = tmp_path / ".git" / "MERGE_HEAD"
    merge_head.write_text("merge")

    class Origin:
        def fetch(self, branch):
            return None

    class Git:
        def merge(self, ref):
            raise RuntimeError("conflict")

    class Index:
        def __init__(self):
            self._unmerged = {".bifrost/workflows.yaml": [(1, object()), (2, object())]}
            self.commits = []

        def unmerged_blobs(self):
            return self._unmerged

        def commit(self, message, parent_commits):
            self.commits.append((message, parent_commits))

    class Repo:
        remotes = type("Remotes", (), {"origin": Origin()})()
        git = Git()
        index = Index()
        head = _Head(valid=True, hexsha="feedface")

        def commit(self, ref):
            assert ref == "MERGE_HEAD"
            return "merge-head-commit"

    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    service._sync_app_previews = AsyncMock()

    result = await service._do_pull(tmp_path, Repo())

    assert result.success is True
    assert result.commit_sha == "feedface"
    assert calls == ["auto"]
    assert Repo.index.commits == [
        (
            "Merge remote-tracking branch (auto-resolved)",
            [Repo.head.commit, "merge-head-commit"],
        )
    ]
    assert not merge_head.exists()
    service._sync_app_previews.assert_awaited_once_with(tmp_path)


@pytest.mark.asyncio
async def test_do_pull_returns_success_when_remote_branch_is_absent(tmp_path):
    class Origin:
        def fetch(self, branch):
            raise RuntimeError("couldn't find remote ref main")

    class Git:
        def merge(self, ref):
            raise AssertionError("missing remote branch should not merge")

    class Repo:
        remotes = type("Remotes", (), {"origin": Origin()})()
        git = Git()

    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    service._sync_app_previews = AsyncMock()

    result = await service._do_pull(tmp_path, Repo())

    assert result.success is True
    assert result.pulled == 0
    assert result.commit_sha is None
    service._sync_app_previews.assert_not_called()


@pytest.mark.asyncio
async def test_desktop_sync_returns_delete_confirmation_and_keeps_deletions_dry(
    tmp_path, monkeypatch
):
    refresh = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "src.core.module_cache",
        types.SimpleNamespace(refresh_modules_from_directory=refresh),
    )
    monkeypatch.setattr(
        "src.services.github_sync._deleted_paths_in_head",
        lambda repo: {"workflows/removed.py"},
    )

    class Repo:
        pass

    removed = EntityChange(
        action="removed",
        entity_type="workflow",
        name="Removed Workflow",
        path="workflows/removed.py",
    )
    kept = EntityChange(action="keep", entity_type="table", name="Audit Log")

    service = _service(tmp_path, Repo())
    service._do_pull = AsyncMock(return_value=PullResult(success=True, pulled=2))
    service._do_push = lambda work_dir, repo: PushResult(
        success=True,
        pushed_commits=1,
        commit_sha="abc123",
    )
    updated = EntityChange(
        action="updated", entity_type="workflow", name="Updated Workflow"
    )
    service._import_all_entities = AsyncMock(
        return_value=(3, [updated], {"workflow": {"old"}})
    )
    service._update_file_index = AsyncMock()
    service._resolver._resolve_deletions = AsyncMock(return_value=[kept, removed])
    service._sync_app_previews = AsyncMock()

    result = await service.desktop_sync(confirm_deletes=False)

    assert result.success is True
    assert result.needs_delete_confirmation is True
    assert result.pending_deletes == [removed]
    assert result.entity_changes == [updated]
    assert result.pulled == 2
    assert result.pushed_commits == 1
    assert result.entities_imported == 3
    service._resolver._resolve_deletions.assert_awaited_once_with(
        work_dir=tmp_path,
        dry_run=True,
        removed_entity_ids={"workflow": {"old"}},
        removed_paths={"workflows/removed.py"},
    )
    service._sync_app_previews.assert_not_called()
    assert service.db.commits == 1


@pytest.mark.asyncio
async def test_desktop_sync_applies_confirmed_deletions_and_shapes_success(
    tmp_path, monkeypatch
):
    monkeypatch.setitem(
        sys.modules,
        "src.core.module_cache",
        types.SimpleNamespace(refresh_modules_from_directory=AsyncMock()),
    )
    monkeypatch.setattr(
        "src.services.github_sync._deleted_paths_in_head", lambda repo: set()
    )

    removed = EntityChange(action="removed", entity_type="agent", name="Old Agent")
    deletion_change = EntityChange(
        action="removed", entity_type="agent", name="Old Agent"
    )

    service = _service(tmp_path, object())
    service._do_pull = AsyncMock(return_value=PullResult(success=True, pulled=0))
    service._do_push = lambda work_dir, repo: PushResult(
        success=True,
        pushed_commits=2,
        commit_sha="def456",
    )
    service._import_all_entities = AsyncMock(return_value=(1, [], {"agent": {"old"}}))
    service._update_file_index = AsyncMock()
    service._resolver._resolve_deletions = AsyncMock(
        side_effect=[[removed], [deletion_change]]
    )
    service._sync_app_previews = AsyncMock()

    result = await service.desktop_sync(confirm_deletes=True)

    assert result.success is True
    assert result.needs_delete_confirmation is False
    assert result.entity_changes == [deletion_change]
    assert result.commit_sha == "def456"
    assert service.db.commits == 2
    service._sync_app_previews.assert_awaited_once_with(tmp_path)


@pytest.mark.asyncio
async def test_desktop_sync_returns_push_error_without_importing(tmp_path):
    conflict = MergeConflict(path="workflows/conflict.py")
    service = _service(tmp_path, object())
    service._do_pull = AsyncMock(
        return_value=PullResult(success=False, conflicts=[conflict], error="conflict")
    )
    service._do_push = lambda *_: (_ for _ in ()).throw(AssertionError("no push"))
    service._import_all_entities = AsyncMock()

    result = await service.desktop_sync()

    assert result.success is False
    assert result.pull_success is False
    assert result.conflicts == [conflict]
    assert result.error == "Merge conflicts detected"
    service._import_all_entities.assert_not_called()


def test_do_resolve_returns_error_when_no_merge_or_unmerged_entries(tmp_path):
    class Index:
        def unmerged_blobs(self):
            return {}

    class Repo:
        index = Index()

    service = object.__new__(GitHubSyncService)

    result = service._do_resolve(tmp_path, Repo(), {"workflows/conflict.py": "ours"})

    assert result.success is False
    assert result.error == "No conflicts to resolve"


def test_do_resolve_uses_rm_fallback_for_delete_conflicts(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "MERGE_HEAD").write_text("merge")

    class Git:
        def __init__(self):
            self.calls = []

        def checkout(self, *args):
            self.calls.append(("checkout", args))
            raise RuntimeError("deleted on one side")

        def rm(self, path):
            self.calls.append(("rm", path))

        def add(self, path):
            self.calls.append(("add", path))

    class Index:
        def __init__(self):
            self.commits = []

        def unmerged_blobs(self):
            return {"workflows/conflict.py": [(1, object()), (2, object())]}

        def commit(self, message):
            self.commits.append(message)

    class Repo:
        git = Git()
        index = Index()

    service = object.__new__(GitHubSyncService)

    result = service._do_resolve(tmp_path, Repo(), {"workflows/conflict.py": "theirs"})

    assert result.success is True
    assert Repo.git.calls == [
        ("checkout", ("--theirs", "workflows/conflict.py")),
        ("rm", "workflows/conflict.py"),
    ]
    assert Repo.index.commits == ["Merge with conflict resolution"]


@pytest.mark.asyncio
async def test_desktop_abort_merge_reports_no_merge_in_progress(tmp_path):
    service = _service(tmp_path, object())

    result = await service.desktop_abort_merge()

    assert result.success is False
    assert result.error == "No merge in progress"


@pytest.mark.asyncio
async def test_desktop_abort_merge_runs_git_abort_when_merge_head_exists(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "MERGE_HEAD").write_text("merge")

    class Git:
        def __init__(self):
            self.calls = []

        def merge(self, *args):
            self.calls.append(args)

    class Repo:
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_abort_merge()

    assert result.success is True
    assert Repo.git.calls == [("--abort",)]


@pytest.mark.asyncio
async def test_desktop_diff_handles_new_file_and_missing_working_file(tmp_path):
    class Git:
        def show(self, ref):
            raise RuntimeError("missing in head")

    class Repo:
        head = _Head(valid=True)
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_diff("workflows/new.py")

    assert result.path == "workflows/new.py"
    assert result.head_content is None
    assert result.working_content is None


@pytest.mark.asyncio
async def test_desktop_diff_returns_head_and_replacement_decoded_content(tmp_path):
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "changed.py").write_bytes(b"working\xff\n")

    class Git:
        def show(self, ref):
            assert ref == "HEAD:workflows/changed.py"
            return "head\n"

    class Repo:
        head = _Head(valid=True)
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_diff("workflows/changed.py")

    assert result.head_content == "head\n"
    assert result.working_content in {"working\ufffd\n", "working\xff\n"}


@pytest.mark.asyncio
async def test_desktop_discard_restores_tracked_and_deletes_untracked_paths(
    tmp_path, monkeypatch
):
    refresh = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "src.core.module_cache",
        types.SimpleNamespace(refresh_modules_from_directory=refresh),
    )
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "new.py").write_text("new")

    class Git:
        def __init__(self):
            self.checkouts = []

        def checkout(self, *args):
            self.checkouts.append(args)
            if args[-1] in {"workflows/new.py", "workflows/missing.py"}:
                raise RuntimeError("not tracked")

    class Repo:
        head = _Head(valid=True)
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_discard(
        ["workflows/tracked.py", "workflows/new.py", "workflows/missing.py"]
    )

    assert result.success is True
    assert result.discarded == ["workflows/tracked.py", "workflows/new.py"]
    assert not (tmp_path / "workflows" / "new.py").exists()
    assert service.repo_manager.synced_up is True
    refresh.assert_awaited_once_with(tmp_path)
