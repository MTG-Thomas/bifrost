"""Additional desktop GitHub sync orchestration coverage using fakes only."""

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.models.contracts.github import PullResult, PushResult
from src.services.github_sync import (
    GitHubSyncService,
    SyncError,
    _run_ruff_check,
    _auto_resolve_manifest_conflicts,
    _classify_conflict_type,
    _deleted_paths_in_head,
    _three_way_merge_dicts,
    _walk_tree,
)


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


class _Blob:
    pass


def _service(tmp_path: Path, repo: object) -> GitHubSyncService:
    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    service.db = _Db()
    service.repo_manager = _RepoManager(tmp_path)
    service._resolver = type("Resolver", (), {})()
    service._open_or_init = lambda work_dir: repo
    return service


def test_walk_tree_returns_repo_relative_files_and_skips_git_dir(tmp_path: Path) -> None:
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "ignored").write_text("git internals")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "demo.py").write_text("print('ok')")
    (tmp_path / "README.md").write_text("docs")

    result = _walk_tree(tmp_path)

    assert result == {
        "README.md": b"docs",
        "workflows/demo.py": b"print('ok')",
    }


def test_deleted_paths_in_head_returns_only_deleted_entries() -> None:
    class Git:
        def diff_tree(self, *args):
            return "D\told.py\nM\tkept.py\nA\tnew.py\nD\tforms/removed.yaml"

    assert _deleted_paths_in_head(SimpleNamespace(git=Git())) == {
        "old.py",
        "forms/removed.yaml",
    }


def test_deleted_paths_in_head_treats_git_errors_as_no_deletes() -> None:
    class Git:
        def diff_tree(self, *args):
            raise RuntimeError("no commits yet")

    assert _deleted_paths_in_head(SimpleNamespace(git=Git())) == set()


def test_three_way_merge_preserves_independent_edits_and_deletions() -> None:
    merged = _three_way_merge_dicts(
        base={
            "kept": {"owner": "base", "enabled": True},
            "theirs_deleted": "same",
            "ours_deleted": "same",
            "ours_changed": "base",
        },
        ours={
            "kept": {"owner": "ours", "enabled": True},
            "theirs_deleted": "same",
            "ours_changed": "ours",
            "ours_added": 1,
        },
        theirs={
            "kept": {"owner": "theirs", "enabled": False},
            "ours_deleted": "same",
            "ours_changed": "base",
            "theirs_added": 2,
        },
    )

    assert merged == {
        "kept": {"owner": "theirs", "enabled": False},
        "ours_changed": "base",
        "ours_added": 1,
        "theirs_added": 2,
    }


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ([(1, _Blob()), (2, _Blob()), (3, _Blob())], "both_modified"),
        ([(2, _Blob()), (3, _Blob())], "both_added"),
        ([(1, _Blob()), (3, _Blob())], "deleted_by_us"),
        ([(1, _Blob()), (2, _Blob())], "deleted_by_them"),
        ([], "both_modified"),
    ],
)
def test_classify_conflict_type_from_git_stages(entries, expected) -> None:
    assert _classify_conflict_type({"workflows/demo.py": entries}, "workflows/demo.py") == expected


def test_auto_resolve_manifest_conflicts_merges_yaml_and_adds_file(
    tmp_path: Path,
) -> None:
    shown = {
        ":1:.bifrost/workflows.yaml": "demo:\n  name: Demo\n  enabled: true\n",
        ":2:.bifrost/workflows.yaml": (
            "demo:\n  name: Demo Local\n  enabled: true\nlocal_only: 1\n"
        ),
        ":3:.bifrost/workflows.yaml": (
            "demo:\n  name: Demo Remote\n  enabled: false\nremote_only: 2\n"
        ),
    }

    class Git:
        def __init__(self) -> None:
            self.added = []

        def show(self, ref: str) -> str:
            return shown[ref]

        def add(self, path: str) -> None:
            self.added.append(path)

    git = Git()
    repo = SimpleNamespace(git=git)

    resolved = _auto_resolve_manifest_conflicts(
        repo,
        tmp_path,
        {".bifrost/workflows.yaml": [(1, _Blob()), (2, _Blob()), (3, _Blob())]},
    )

    assert resolved == {".bifrost/workflows.yaml"}
    assert git.added == [".bifrost/workflows.yaml"]
    merged = (tmp_path / ".bifrost" / "workflows.yaml").read_text()
    assert "Demo Remote" in merged
    assert "enabled: false" in merged
    assert "local_only: 1" in merged
    assert "remote_only: 2" in merged


def test_auto_resolve_manifest_conflicts_accepts_theirs_when_merge_fails(
    tmp_path: Path,
) -> None:
    class Git:
        def __init__(self) -> None:
            self.added = []

        def show(self, ref: str) -> str:
            if ref.startswith(":3:"):
                return "- remote\n- list\n"
            return "- local\n- list\n"

        def add(self, path: str) -> None:
            self.added.append(path)

    git = Git()
    repo = SimpleNamespace(git=git)

    resolved = _auto_resolve_manifest_conflicts(
        repo,
        tmp_path,
        {".bifrost/forms.yaml": [(1, _Blob()), (2, _Blob()), (3, _Blob())]},
    )

    assert resolved == {".bifrost/forms.yaml"}
    assert (tmp_path / ".bifrost" / "forms.yaml").read_text() == "- remote\n- list\n"
    assert git.added == [".bifrost/forms.yaml"]


@pytest.mark.asyncio
async def test_run_preflight_reports_invalid_manifest_syntax_and_lint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import github_sync

    (tmp_path / ".bifrost").mkdir()
    (tmp_path / ".bifrost" / "workflows.yaml").write_text("[not valid")
    workflow = tmp_path / "workflows" / "broken.py"
    workflow.parent.mkdir()
    workflow.write_text("def broken(:\n")

    async def fake_run(*args, **kwargs):
        return SimpleNamespace(
            stdout=(
                '[{"filename":"'
                + str(workflow)
                + '","location":{"row":7},"code":"F401","message":"unused import"}]'
            )
        )

    monkeypatch.setattr(github_sync, "_run_ruff_check", fake_run)
    service = object.__new__(GitHubSyncService)

    result = await service._run_preflight(tmp_path)

    assert result.valid is False
    categories = [issue.category for issue in result.issues]
    assert "manifest" in categories
    assert "syntax" in categories
    assert "lint" in categories
    assert any("Invalid manifest" in issue.message for issue in result.issues)
    assert any(issue.line == 1 and "Syntax error" in issue.message for issue in result.issues)
    assert any(issue.line == 7 and issue.severity == "warning" for issue in result.issues)


@pytest.mark.asyncio
async def test_run_preflight_allows_missing_ruff(tmp_path: Path, monkeypatch) -> None:
    from src.services import github_sync

    (tmp_path / "workflow.py").write_text("VALUE = 1\n")

    async def missing_ruff(*args, **kwargs):
        raise FileNotFoundError("ruff")

    monkeypatch.setattr(github_sync, "_run_ruff_check", missing_ruff)
    service = object.__new__(GitHubSyncService)

    result = await service._run_preflight(tmp_path)

    assert result.valid is True
    assert result.issues == []


@pytest.mark.asyncio
async def test_ruff_process_is_reaped_when_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    communicate_started = asyncio.Event()
    wait_finished = asyncio.Event()

    class Process:
        returncode = None
        terminated = False

        async def communicate(self):
            communicate_started.set()
            await asyncio.Future()

        def terminate(self) -> None:
            self.terminated = True

        async def wait(self) -> int:
            self.returncode = -15
            wait_finished.set()
            return self.returncode

    process = Process()
    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        AsyncMock(return_value=process),
    )

    task = asyncio.create_task(_run_ruff_check(tmp_path, ["workflow.py"]))
    await communicate_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.terminated is True
    assert wait_finished.is_set()


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


def test_do_fetch_counts_ahead_behind_and_handles_missing_remote(tmp_path: Path) -> None:
    class Origin:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail

        def fetch(self, branch: str) -> None:
            if self.fail:
                raise RuntimeError("couldn't find remote ref main")

    class Git:
        def __init__(self) -> None:
            self.counts = {
                "origin/main..HEAD": "2",
                "HEAD..origin/main": "3",
            }

        def rev_list(self, *args):
            return self.counts[args[-1]]

    service = _service(tmp_path, object())
    service.branch = "main"
    repo = SimpleNamespace(
        remotes=SimpleNamespace(origin=Origin()),
        head=_Head(valid=True),
        git=Git(),
    )

    result = service._do_fetch(tmp_path, repo)

    assert result.success is True
    assert result.commits_ahead == 2
    assert result.commits_behind == 3
    assert result.remote_branch_exists is True

    repo.remotes.origin = Origin(fail=True)
    missing = service._do_fetch(tmp_path, repo)
    assert missing.remote_branch_exists is False
    assert missing.commits_ahead == 0
    assert missing.commits_behind == 0


def test_do_status_reports_conflicts_before_staging(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "MERGE_HEAD").write_text("merge")

    class Index:
        def unmerged_blobs(self):
            return {"workflows/demo.py": [(2, _Blob()), (3, _Blob())]}

    class Git:
        def show(self, ref: str) -> str:
            if ref.startswith(":2:"):
                return "ours"
            return "theirs"

        def rev_list(self, *args):
            return "0"

        def add(self, *args, **kwargs):
            raise AssertionError("conflict status should not stage changes")

    service = _service(tmp_path, object())
    repo = SimpleNamespace(index=Index(), git=Git(), head=_Head(valid=True))

    result = service._do_status(tmp_path, repo)

    assert result.total_changes == 0
    assert result.merging is True
    assert len(result.conflicts) == 1
    assert result.conflicts[0].path == "workflows/demo.py"
    assert result.conflicts[0].ours_content == "ours"
    assert result.conflicts[0].theirs_content == "theirs"
    assert result.conflicts[0].conflict_type == "both_added"


def test_do_status_classifies_porcelain_changes_and_renames(tmp_path: Path) -> None:
    class Index:
        def unmerged_blobs(self):
            return {}

    class Git:
        def __init__(self) -> None:
            self.reset_called = False

        def rev_list(self, *args):
            if args[-1] == "origin/main..HEAD":
                return "1"
            return "2"

        def add(self, *args, **kwargs):
            return None

        def status(self, *args):
            return (
                "A  workflows/new.py\n"
                " M workflows/changed.py\n"
                "D  workflows/deleted.py\n"
                "R  workflows/old.py -> workflows/renamed.py\n"
                '?? "forms/quoted form.yaml"\n'
            )

        def reset(self, *args):
            self.reset_called = True

    git = Git()
    repo = SimpleNamespace(index=Index(), git=git, head=_Head(valid=True))
    service = _service(tmp_path, repo)
    service.branch = "main"

    result = service._do_status(tmp_path, repo)

    assert result.commits_ahead == 1
    assert result.commits_behind == 2
    assert result.total_changes == 5
    assert [changed.change_type for changed in result.changed_files] == [
        "added",
        "modified",
        "deleted",
        "renamed",
        "added",
    ]
    assert result.changed_files[3].path == "workflows/renamed.py"
    assert result.changed_files[4].path == "forms/quoted form.yaml"
    assert git.reset_called is True


def test_do_status_reports_untracked_files_when_repo_has_no_head(tmp_path: Path) -> None:
    class Index:
        def unmerged_blobs(self):
            return {}

    class Git:
        def add(self, *args, **kwargs):
            return None

    repo = SimpleNamespace(
        index=Index(),
        git=Git(),
        head=_Head(valid=False),
        untracked_files=["workflows/first.py", "forms/demo.yaml"],
    )
    service = _service(tmp_path, repo)

    result = service._do_status(tmp_path, repo)

    assert result.total_changes == 2
    assert all(changed.change_type == "added" for changed in result.changed_files)


def test_do_push_returns_noop_without_valid_head(tmp_path: Path) -> None:
    service = _service(tmp_path, object())
    repo = SimpleNamespace(head=_Head(valid=False))

    result = service._do_push(tmp_path, repo)

    assert result.success is True
    assert result.pushed_commits == 0


def test_do_push_uses_local_head_count_when_fetch_count_fails(tmp_path: Path) -> None:
    class Origin:
        def __init__(self) -> None:
            self.pushed = []

        def fetch(self, branch: str) -> None:
            raise RuntimeError("network unavailable")

        def push(self, refspec: str):
            self.pushed.append(refspec)
            return []

    class Git:
        def rev_list(self, *args):
            assert args[-1] == "HEAD"
            return "4"

    repo = SimpleNamespace(
        remotes=SimpleNamespace(origin=Origin()),
        head=_Head(valid=True, hexsha="abc123456789"),
        git=Git(),
    )
    service = _service(tmp_path, repo)
    service.branch = "main"

    result = service._do_push(tmp_path, repo)

    assert result.success is True
    assert result.pushed_commits == 4
    assert result.commit_sha == "abc123456789"
    assert repo.remotes.origin.pushed == ["HEAD:main"]


def test_do_push_surfaces_remote_error_flags(tmp_path: Path) -> None:
    from git.remote import PushInfo

    class Origin:
        def fetch(self, branch: str) -> None:
            return None

        def push(self, refspec: str):
            return [SimpleNamespace(flags=PushInfo.REJECTED, summary="stale branch")]

    class Git:
        def rev_list(self, *args):
            return "1"

    repo = SimpleNamespace(
        remotes=SimpleNamespace(origin=Origin()),
        head=_Head(valid=True),
        git=Git(),
    )
    service = _service(tmp_path, repo)
    service.branch = "main"

    result = service._do_push(tmp_path, repo)

    assert result.success is False
    assert result.error == "Push rejected (non-fast-forward): stale branch"


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
