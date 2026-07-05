"""Focused branch coverage for GitHub sync helpers without real remotes."""

from pathlib import Path

import pytest

from src.services.github_sync import (
    GitHubSyncService,
    SyncError,
    _auto_resolve_manifest_conflicts,
    _classify_conflict_type,
    _deleted_paths_in_head,
    _three_way_merge_dicts,
    _walk_tree,
)


class _Head:
    def __init__(self, valid: bool = True) -> None:
        self._valid = valid

    def is_valid(self) -> bool:
        return self._valid


def _service() -> GitHubSyncService:
    service = object.__new__(GitHubSyncService)
    service.branch = "main"
    return service


def test_walk_tree_returns_files_and_skips_git_internals(tmp_path: Path) -> None:
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "ignored").write_bytes(b"git")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "sync.py").write_bytes(b"print('sync')\n")
    (tmp_path / "README.md").write_bytes(b"# docs\n")

    assert _walk_tree(tmp_path) == {
        "README.md": b"# docs\n",
        "workflows/sync.py": b"print('sync')\n",
    }


def test_deleted_paths_in_head_returns_deleted_paths_and_handles_git_errors() -> None:
    class Git:
        def diff_tree(self, *args):
            assert args == ("--no-commit-id", "--name-status", "-r", "HEAD")
            return "\n".join(
                [
                    "D\tworkflows/old.py",
                    "M\tworkflows/kept.py",
                    "D\tapps/old/page.tsx",
                ]
            )

    class Repo:
        git = Git()

    assert _deleted_paths_in_head(Repo()) == {
        "workflows/old.py",
        "apps/old/page.tsx",
    }

    class BrokenGit:
        def diff_tree(self, *args):
            raise RuntimeError("not a git repo")

    class BrokenRepo:
        git = BrokenGit()

    assert _deleted_paths_in_head(BrokenRepo()) == set()


def test_three_way_merge_preserves_independent_changes_and_honors_deletions() -> None:
    base = {
        "kept_by_ours": "same",
        "modified_by_ours": "old",
        "modified_by_theirs": "old",
        "nested": {"ours": 1, "theirs": 1, "both": "base"},
        "deleted_by_ours": "gone",
        "deleted_by_theirs": "gone",
    }
    ours = {
        "kept_by_ours": "same",
        "modified_by_ours": "ours",
        "modified_by_theirs": "old",
        "nested": {"ours": 2, "theirs": 1, "both": "ours"},
        "deleted_by_theirs": "gone",
        "ours_added": "ours",
    }
    theirs = {
        "kept_by_ours": "same",
        "modified_by_ours": "old",
        "modified_by_theirs": "theirs",
        "nested": {"ours": 1, "theirs": 2, "both": "theirs"},
        "deleted_by_ours": "gone",
        "theirs_added": "theirs",
    }

    merged = _three_way_merge_dicts(base, ours, theirs)

    assert merged["modified_by_ours"] == "old"
    assert merged["modified_by_theirs"] == "theirs"
    assert merged["nested"] == {"ours": 1, "theirs": 2, "both": "theirs"}
    assert merged["ours_added"] == "ours"
    assert merged["theirs_added"] == "theirs"
    assert "deleted_by_ours" not in merged
    assert "deleted_by_theirs" not in merged


@pytest.mark.parametrize(
    ("stages", "expected"),
    [
        ({1, 2, 3}, "both_modified"),
        ({2, 3}, "both_added"),
        ({1, 3}, "deleted_by_us"),
        ({1, 2}, "deleted_by_them"),
        ({2}, "both_modified"),
    ],
)
def test_classify_conflict_type_from_unmerged_blob_stages(stages, expected) -> None:
    unmerged = {"workflows/conflict.py": [(stage, object()) for stage in stages]}

    assert _classify_conflict_type(unmerged, "workflows/conflict.py") == expected
    assert _classify_conflict_type({}, "missing.py") == "both_modified"


def test_auto_resolve_manifest_conflicts_merges_yaml_and_stages_result(
    tmp_path: Path,
) -> None:
    class Git:
        def __init__(self) -> None:
            self.added = []

        def show(self, ref):
            return {
                ":1:.bifrost/workflows.yaml": "workflows:\n  old:\n    name: Old\n",
                ":2:.bifrost/workflows.yaml": (
                    "workflows:\n"
                    "  old:\n"
                    "    name: Old Local\n"
                    "  local:\n"
                    "    name: Local\n"
                ),
                ":3:.bifrost/workflows.yaml": (
                    "workflows:\n"
                    "  old:\n"
                    "    name: Old Remote\n"
                    "  remote:\n"
                    "    name: Remote\n"
                ),
            }[ref]

        def add(self, path):
            self.added.append(path)

    class Repo:
        git = Git()

    resolved = _auto_resolve_manifest_conflicts(
        Repo(),
        tmp_path,
        {".bifrost/workflows.yaml": [(1, object()), (2, object()), (3, object())]},
    )

    merged = (tmp_path / ".bifrost" / "workflows.yaml").read_text()
    assert resolved == {".bifrost/workflows.yaml"}
    assert Repo.git.added == [".bifrost/workflows.yaml"]
    assert "name: Old Remote" in merged
    assert "name: Local" in merged
    assert "name: Remote" in merged


def test_auto_resolve_manifest_conflicts_accepts_theirs_when_merge_fails(
    tmp_path: Path,
) -> None:
    class Git:
        def __init__(self) -> None:
            self.added = []

        def show(self, ref):
            if ref == ":2:.bifrost/forms.yaml":
                return "- not a dict"
            if ref == ":3:.bifrost/forms.yaml":
                return "forms:\n  remote:\n    name: Remote\n"
            return "forms: {}\n"

        def add(self, path):
            self.added.append(path)

    class Repo:
        git = Git()

    resolved = _auto_resolve_manifest_conflicts(
        Repo(),
        tmp_path,
        {".bifrost/forms.yaml": [(1, object()), (2, object()), (3, object())]},
    )

    assert resolved == {".bifrost/forms.yaml"}
    assert (tmp_path / ".bifrost" / "forms.yaml").read_text() == (
        "forms:\n  remote:\n    name: Remote\n"
    )
    assert Repo.git.added == [".bifrost/forms.yaml"]


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
