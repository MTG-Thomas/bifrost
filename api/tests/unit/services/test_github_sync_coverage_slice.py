"""Focused branch coverage for GitHub sync helpers without real remotes."""

from pathlib import Path

import pytest

from src.services.github_sync import GitHubSyncService, SyncError


class _Head:
    def __init__(self, valid: bool = True) -> None:
        self._valid = valid

    def is_valid(self) -> bool:
        return self._valid


def _service() -> GitHubSyncService:
    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    return service


def test_do_status_returns_conflicts_when_manifest_auto_resolve_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import github_sync

    monkeypatch.setattr(
        github_sync,
        "_auto_resolve_manifest_conflicts",
        lambda *_: (_ for _ in ()).throw(RuntimeError("resolver failed")),
    )
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "MERGE_HEAD").write_text("merge")

    class Git:
        def rev_list(self, *args):
            if args[-1] == "origin/main..HEAD":
                return "4"
            raise RuntimeError("missing behind ref")

        def show(self, ref):
            if ref == ":2:workflows/conflict.py":
                return "ours"
            raise RuntimeError("stage missing")

    class Index:
        def unmerged_blobs(self):
            return {"workflows/conflict.py": [(1, object()), (2, object())]}

    class Repo:
        git = Git()
        head = _Head(valid=True)
        index = Index()

    result = _service()._do_status(tmp_path, Repo())

    assert result.changed_files == []
    assert result.total_changes == 0
    assert result.commits_ahead == 4
    assert result.commits_behind == 0
    assert result.merging is True
    assert len(result.conflicts) == 1
    conflict = result.conflicts[0]
    assert conflict.path == "workflows/conflict.py"
    assert conflict.ours_content == "ours"
    assert conflict.theirs_content is None
    assert conflict.conflict_type == "deleted_by_them"


def test_do_status_parses_porcelain_changes_and_resets_staging(tmp_path: Path) -> None:
    class Git:
        def __init__(self) -> None:
            self.calls = []

        def add(self, **kwargs):
            self.calls.append(("add", kwargs))

        def status(self, *args):
            self.calls.append(("status", args))
            return "\n".join(
                [
                    ' M  "workflows/changed.py"',
                    "D  forms/old.yaml",
                    "R  apps/old.tsx -> apps/new.tsx",
                    "?? agents/new.yaml",
                    "",
                ]
            )

        def rev_list(self, *args):
            if args[-1] == "origin/main..HEAD":
                return "1"
            if args[-1] == "HEAD..origin/main":
                return "2"
            raise AssertionError(args)

        def reset(self, *args):
            self.calls.append(("reset", args))

    class Index:
        def unmerged_blobs(self):
            return {}

    class Repo:
        git = Git()
        head = _Head(valid=True)
        index = Index()

    result = _service()._do_status(tmp_path, Repo())

    assert [(file.path, file.change_type) for file in result.changed_files] == [
        ("workflows/changed.py", "modified"),
        ("forms/old.yaml", "deleted"),
        ("apps/new.tsx", "renamed"),
        ("agents/new.yaml", "added"),
    ]
    assert result.total_changes == 4
    assert result.commits_ahead == 1
    assert result.commits_behind == 2
    assert Repo.git.calls[0] == ("add", {"A": True})
    assert Repo.git.calls[-1] == ("reset", ("HEAD",))


def test_do_status_reports_untracked_files_for_initial_repo(tmp_path: Path) -> None:
    class Git:
        def __init__(self) -> None:
            self.calls = []

        def add(self, **kwargs):
            self.calls.append(("add", kwargs))

        def reset(self, *args):
            raise AssertionError("initial repo status should not reset HEAD")

    class Index:
        def unmerged_blobs(self):
            return {}

    class Repo:
        git = Git()
        head = _Head(valid=False)
        index = Index()
        untracked_files = ["workflows/first.py", "forms/first.yaml"]

    result = _service()._do_status(tmp_path, Repo())

    assert [(file.path, file.change_type) for file in result.changed_files] == [
        ("workflows/first.py", "added"),
        ("forms/first.yaml", "added"),
    ]
    assert result.total_changes == 2
    assert Repo.git.calls == [("add", {"A": True})]


def test_clone_or_init_initializes_origin_when_remote_branch_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import github_sync

    created_remotes = []

    class FakeGitRepo:
        @staticmethod
        def clone_from(*args, **kwargs):
            raise RuntimeError("remote repository empty")

        @staticmethod
        def init(path):
            assert path == str(tmp_path)
            return FakeRepo()

    class FakeRepo:
        def create_remote(self, name, url):
            created_remotes.append((name, url))

    monkeypatch.setattr(github_sync, "GitRepo", FakeGitRepo)
    service = _service()
    service.repo_url = "https://example.invalid/org/repo.git"

    repo = service._clone_or_init(tmp_path)

    assert isinstance(repo, FakeRepo)
    assert created_remotes == [("origin", "https://example.invalid/org/repo.git")]


def test_clone_or_init_wraps_unexpected_clone_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import github_sync

    class FakeGitRepo:
        @staticmethod
        def clone_from(*args, **kwargs):
            raise RuntimeError("permission denied")

    monkeypatch.setattr(github_sync, "GitRepo", FakeGitRepo)
    service = _service()
    service.repo_url = "https://example.invalid/org/repo.git"

    with pytest.raises(SyncError, match="Failed to clone"):
        service._clone_or_init(tmp_path)
