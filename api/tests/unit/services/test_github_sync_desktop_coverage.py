"""Additional desktop GitHub sync orchestration coverage using fakes only."""

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.models.contracts.github import PullResult, PushResult
from src.services.github_sync import GitHubSyncService, SyncError


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
        self.synced = []

    def lock(self) -> _AsyncPathContext:
        return _AsyncPathContext(self.work_dir)

    def checkout(self) -> _AsyncPathContext:
        return _AsyncPathContext(self.work_dir)

    async def sync_up(self, work_dir: Path) -> None:
        self.synced.append(work_dir)


class _Db:
    def __init__(self) -> None:
        self.commits = 0

    def begin_nested(self) -> _NestedTx:
        return _NestedTx()

    async def commit(self) -> None:
        self.commits += 1


class _Head:
    def __init__(self, valid: bool = True, hexsha: str = "abc123") -> None:
        self._valid = valid
        self.commit = type("Commit", (), {"hexsha": hexsha})()

    def is_valid(self) -> bool:
        return self._valid


def _service(tmp_path: Path, repo: object) -> GitHubSyncService:
    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    service.db = _Db()
    service.repo_manager = _RepoManager(tmp_path)
    service._resolver = type("Resolver", (), {})()
    service._open_or_init = lambda work_dir: repo
    return service


@pytest.mark.asyncio
async def test_desktop_status_returns_empty_status_when_open_fails(tmp_path: Path) -> None:
    service = object.__new__(GitHubSyncService)
    service.repo_manager = _RepoManager(tmp_path)
    service._open_or_init = lambda work_dir: (_ for _ in ()).throw(
        RuntimeError("bad checkout")
    )

    result = await service.desktop_status()

    assert result.changed_files == []
    assert result.total_changes == 0
    assert result.commits_ahead == 0
    assert result.commits_behind == 0


@pytest.mark.asyncio
async def test_desktop_commit_delegates_locked_repo_and_message(tmp_path: Path) -> None:
    repo = object()
    service = _service(tmp_path, repo)
    service._do_commit = AsyncMock(return_value=type("Result", (), {"success": True})())

    result = await service.desktop_commit("publish local edits")

    assert result.success is True
    service._do_commit.assert_awaited_once_with(tmp_path, repo, "publish local edits")


@pytest.mark.asyncio
async def test_desktop_sync_push_failure_stops_before_storage_and_import(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, object())
    service._do_pull = AsyncMock(return_value=PullResult(success=True, pulled=1))
    service._do_push = lambda work_dir, repo: PushResult(
        success=False,
        error="non-fast-forward",
    )
    service._import_all_entities = AsyncMock()
    service._update_file_index = AsyncMock()

    result = await service.desktop_sync()

    assert result.success is False
    assert result.pull_success is True
    assert result.push_success is False
    assert result.error == "non-fast-forward"
    assert service.repo_manager.synced == []
    service._import_all_entities.assert_not_awaited()
    service._update_file_index.assert_not_awaited()


@pytest.mark.asyncio
async def test_desktop_sync_reports_progress_through_successful_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = AsyncMock()
    refresh = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "src.core.pubsub",
        types.SimpleNamespace(publish_git_progress=progress),
    )
    monkeypatch.setitem(
        sys.modules,
        "src.core.module_cache",
        types.SimpleNamespace(refresh_modules_from_directory=refresh),
    )
    monkeypatch.setattr("src.services.github_sync._deleted_paths_in_head", lambda repo: set())

    service = _service(tmp_path, object())
    service._do_pull = AsyncMock(return_value=PullResult(success=True, pulled=2))
    service._do_push = lambda work_dir, repo: PushResult(
        success=True,
        pushed_commits=3,
        commit_sha="feedface",
    )
    service._import_all_entities = AsyncMock(return_value=(4, [], {}))
    service._update_file_index = AsyncMock()
    service._resolver._resolve_deletions = AsyncMock(return_value=[])
    service._sync_app_previews = AsyncMock()

    result = await service.desktop_sync(job_id="job-42", confirm_deletes=True)

    assert result.success is True
    assert result.pulled == 2
    assert result.pushed_commits == 3
    assert result.commit_sha == "feedface"
    assert result.entities_imported == 4
    assert [call.args[1] for call in progress.await_args_list] == [
        "Pushing to remote...",
        "Syncing to storage...",
        "Importing entities...",
        "Updating file index...",
        "Checking for removed entities...",
        "Syncing app previews...",
    ]
    assert service.repo_manager.synced == [tmp_path]
    refresh.assert_awaited_once_with(tmp_path)
    service._sync_app_previews.assert_awaited_once_with(tmp_path)


@pytest.mark.asyncio
async def test_desktop_abort_merge_returns_error_result_when_git_abort_fails(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "MERGE_HEAD").write_text("merge")

    class Git:
        def merge(self, *args):
            raise RuntimeError("abort failed")

    class Repo:
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_abort_merge()

    assert result.success is False
    assert result.error == "abort failed"


@pytest.mark.asyncio
async def test_desktop_diff_returns_empty_contents_when_repo_open_fails(
    tmp_path: Path,
) -> None:
    service = object.__new__(GitHubSyncService)
    service.repo_manager = _RepoManager(tmp_path)
    service._open_or_init = lambda work_dir: (_ for _ in ()).throw(
        RuntimeError("missing repo")
    )

    result = await service.desktop_diff("workflows/current.py")

    assert result.path == "workflows/current.py"
    assert result.head_content is None
    assert result.working_content is None


@pytest.mark.asyncio
async def test_desktop_discard_continues_after_one_path_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh = AsyncMock()
    monkeypatch.setitem(
        sys.modules,
        "src.core.module_cache",
        types.SimpleNamespace(refresh_modules_from_directory=refresh),
    )
    (tmp_path / "workflows").mkdir()
    kept = tmp_path / "workflows" / "kept.py"
    kept.mkdir()
    deleted = tmp_path / "workflows" / "deleted.py"
    deleted.write_text("delete me")

    class Git:
        def checkout(self, *args):
            if args[-1] == "workflows/kept.py":
                raise OSError("checkout failed")
            raise RuntimeError("not tracked")

    class Repo:
        head = _Head(valid=True)
        git = Git()

    service = _service(tmp_path, Repo())

    result = await service.desktop_discard(
        ["workflows/kept.py", "workflows/deleted.py"]
    )

    assert result.success is True
    assert result.discarded == ["workflows/deleted.py"]
    assert kept.exists()
    assert not deleted.exists()
    assert service.repo_manager.synced == [tmp_path]
    refresh.assert_awaited_once_with(tmp_path)


def test_open_or_init_existing_repo_updates_remote_and_missing_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import github_sync

    (tmp_path / ".git").mkdir()

    class Origin:
        name = "origin"

        def __init__(self) -> None:
            self.urls = []

        def set_url(self, url: str) -> None:
            self.urls.append(url)

    class Remotes:
        def __init__(self) -> None:
            self.origin = Origin()

        def __iter__(self):
            return iter([self.origin])

    class ConfigWriter:
        def __init__(self) -> None:
            self.values = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get_value(self, section: str, key: str) -> str:
            if key == "name":
                return "Existing User"
            raise KeyError(key)

        def set_value(self, section: str, key: str, value: str) -> None:
            self.values.append((section, key, value))

    class FakeRepo:
        created_remote = None

        def __init__(self, path: str) -> None:
            assert path == str(tmp_path)
            self.remotes = Remotes()
            self.writer = ConfigWriter()

        def create_remote(self, name: str, url: str) -> None:
            self.created_remote = (name, url)

        def config_writer(self) -> ConfigWriter:
            return self.writer

    monkeypatch.setattr(github_sync, "GitRepo", FakeRepo)
    service = object.__new__(GitHubSyncService)
    service.repo_url = "https://example.invalid/org/repo.git"

    repo = service._open_or_init(tmp_path)

    assert repo.remotes.origin.urls == ["https://example.invalid/org/repo.git"]
    assert repo.created_remote is None
    assert repo.writer.values == [("user", "email", "bifrost@localhost")]


def test_open_or_init_existing_repo_creates_origin_when_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import github_sync

    (tmp_path / ".git").mkdir()

    class ConfigWriter:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def get_value(self, section: str, key: str) -> str:
            return "configured"

    class FakeRepo:
        def __init__(self, path: str) -> None:
            self.remotes = []
            self.created = []

        def create_remote(self, name: str, url: str) -> None:
            self.created.append((name, url))

        def config_writer(self) -> ConfigWriter:
            return ConfigWriter()

    monkeypatch.setattr(github_sync, "GitRepo", FakeRepo)
    service = object.__new__(GitHubSyncService)
    service.repo_url = "https://example.invalid/org/repo.git"

    repo = service._open_or_init(tmp_path)

    assert repo.created == [("origin", "https://example.invalid/org/repo.git")]


def test_clone_or_init_treats_missing_remote_branch_as_new_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import github_sync

    created = []

    class FakeGitRepo:
        @staticmethod
        def clone_from(*args, **kwargs):
            raise RuntimeError("could not find remote branch main")

        @staticmethod
        def init(path: str):
            assert path == str(tmp_path)
            return FakeRepo()

    class FakeRepo:
        def create_remote(self, name: str, url: str) -> None:
            created.append((name, url))

    monkeypatch.setattr(github_sync, "GitRepo", FakeGitRepo)
    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    service.repo_url = "https://example.invalid/org/repo.git"

    assert isinstance(service._clone_or_init(tmp_path), FakeRepo)
    assert created == [("origin", "https://example.invalid/org/repo.git")]


def test_clone_or_init_wraps_unexpected_clone_error_and_cleans_tempdir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import shutil

    from src.services import github_sync

    removed = []

    class FakeGitRepo:
        @staticmethod
        def clone_from(*args, **kwargs):
            raise RuntimeError("permission denied")

    monkeypatch.setattr(github_sync, "GitRepo", FakeGitRepo)
    monkeypatch.setattr(shutil, "rmtree", lambda path, ignore_errors: removed.append(path))
    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    service.repo_url = "https://example.invalid/org/repo.git"

    with pytest.raises(SyncError, match="Failed to clone"):
        service._clone_or_init(tmp_path)

    assert removed
