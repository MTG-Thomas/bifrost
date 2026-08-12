from contextlib import asynccontextmanager
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from git import Repo as GitRepo

from src.core.workspace_writer import WorkspaceWriterLeaseLost
from src.services.github_sync import GitHubSyncService


def _init_repo(path):
    repo = GitRepo.init(path)
    repo.config_writer().set_value("user", "name", "Test").set_value(
        "user", "email", "test@example.com"
    ).release()
    return repo


def _prepare_mock_repo(repo, *, head_sha="a" * 40, remote_sha=None):
    repo.head.is_valid.return_value = True
    repo.head.commit.hexsha = head_sha
    repo.head.commit.tree.traverse.return_value = []
    if remote_sha is not None:
        remote_commit = Mock()
        remote_commit.hexsha = remote_sha
        remote_commit.tree.traverse.return_value = []
        repo.commit.return_value = remote_commit


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_syncs_storage_and_does_not_push_by_default(
    tmp_path,
):
    service = GitHubSyncService(AsyncMock(), "https://example.test/org/repo.git")
    events = []

    @asynccontextmanager
    async def checkout():
        events.append("sync-down")
        yield tmp_path
        events.append("sync-up")

    service.repo_manager = SimpleNamespace(isolated_checkout=checkout)
    repo = Mock()
    _prepare_mock_repo(repo)
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._do_push = Mock()

    sha, push_error = await service.commit_workspace_changes(
        "agent change", expected_file_hashes={}
    )

    assert sha == "a" * 40
    assert push_error is None
    assert events == ["sync-down", "sync-up"]
    service._do_commit.assert_awaited_once_with(tmp_path, repo, "agent change")
    service._do_push.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_stages_assume_unchanged_activated_file(
    tmp_path,
):
    repo = _init_repo(tmp_path)
    tracked = tmp_path / "features" / "activated.py"
    tracked.parent.mkdir()
    tracked.write_bytes(b"old\n")
    repo.index.add(["features/activated.py"])
    repo.index.commit("initial")
    repo.git.update_index("--assume-unchanged", "features/activated.py")
    activated = b"new activated content\n"
    tracked.write_bytes(activated)

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service = GitHubSyncService(AsyncMock(), "https://example.test/org/repo.git")
    service.repo_manager = SimpleNamespace(isolated_checkout=checkout)
    service._open_or_init = Mock(return_value=repo)
    service._regenerate_manifest_to_dir = AsyncMock()
    service._run_preflight = AsyncMock(return_value=SimpleNamespace(valid=True))

    sha, push_error = await service.commit_workspace_changes(
        "activated source",
        expected_file_hashes={
            "features/activated.py": hashlib.sha256(activated).hexdigest()
        },
    )

    assert push_error is None
    assert sha == repo.head.commit.hexsha
    blob = repo.head.commit.tree / "features/activated.py"
    assert blob.data_stream.read() == activated


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_rejects_stale_materialized_file_without_sync_up(
    tmp_path,
):
    repo = _init_repo(tmp_path)
    tracked = tmp_path / "features" / "activated.py"
    tracked.parent.mkdir()
    tracked.write_bytes(b"stale\n")
    repo.index.add(["features/activated.py"])
    repo.index.commit("initial")
    events = []

    @asynccontextmanager
    async def checkout():
        events.append("sync-down")
        yield tmp_path
        events.append("sync-up")

    service = GitHubSyncService(AsyncMock(), "https://example.test/org/repo.git")
    service.repo_manager = SimpleNamespace(isolated_checkout=checkout)
    service._open_or_init = Mock(return_value=repo)

    with pytest.raises(RuntimeError, match="does not match the authoritative snapshot"):
        await service.commit_workspace_changes(
            "activated source",
            expected_file_hashes={
                "features/activated.py": hashlib.sha256(b"activated\n").hexdigest()
            },
        )

    assert events == ["sync-down"]


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_pushes_only_when_requested(tmp_path):
    service = GitHubSyncService(AsyncMock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(isolated_checkout=checkout)
    repo = Mock()
    _prepare_mock_repo(repo, remote_sha="b" * 40)
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._do_push = Mock(
        return_value=SimpleNamespace(success=True, commit_sha="b" * 40, error=None)
    )
    service._reconcile_remote_before_workspace_push = Mock(return_value=None)

    sha, push_error = await service.commit_workspace_changes(
        "agent change", push=True, expected_file_hashes={}
    )

    assert sha == "b" * 40
    assert push_error is None
    service._reconcile_remote_before_workspace_push.assert_called_once_with(
        tmp_path, repo
    )
    service._do_push.assert_called_once_with(tmp_path, repo)


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_fences_stale_lease_before_push(
    tmp_path,
):
    service = GitHubSyncService(AsyncMock(), "https://example.test/org/repo.git")
    events = []

    @asynccontextmanager
    async def checkout():
        events.append("sync-down")
        yield tmp_path
        events.append("sync-up")

    service.repo_manager = SimpleNamespace(isolated_checkout=checkout)
    repo = Mock()
    _prepare_mock_repo(repo)
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._do_push = Mock()
    service._reconcile_remote_before_workspace_push = Mock(return_value=None)

    with patch(
        "src.core.workspace_writer.checkpoint_workspace_writer_lease",
        new_callable=AsyncMock,
        side_effect=[None, None, WorkspaceWriterLeaseLost("lease replaced")],
    ) as checkpoint:
        with pytest.raises(WorkspaceWriterLeaseLost, match="lease replaced"):
            await service.commit_workspace_changes(
                "agent change", push=True, expected_file_hashes={}
            )

    assert checkpoint.await_count == 3
    service._do_push.assert_not_called()
    assert events == ["sync-down"]


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_returns_commit_when_push_fails(tmp_path):
    service = GitHubSyncService(AsyncMock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(isolated_checkout=checkout)
    repo = Mock()
    _prepare_mock_repo(repo)
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._do_push = Mock(
        return_value=SimpleNamespace(success=False, commit_sha=None, error="rejected")
    )
    service._reconcile_remote_before_workspace_push = Mock(return_value=None)

    sha, push_error = await service.commit_workspace_changes(
        "agent change", push=True, expected_file_hashes={}
    )

    assert sha == "a" * 40
    assert push_error == "rejected"


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_returns_remote_reconciliation_error(
    tmp_path,
):
    service = GitHubSyncService(AsyncMock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(isolated_checkout=checkout)
    repo = Mock()
    _prepare_mock_repo(repo)
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._reconcile_remote_before_workspace_push = Mock(
        return_value="remote merge failed"
    )
    service._do_push = Mock()

    sha, push_error = await service.commit_workspace_changes(
        "agent change", push=True, expected_file_hashes={}
    )

    assert sha == "a" * 40
    assert push_error == "remote merge failed"
    repo.git.reset.assert_called_once_with("--hard", "a" * 40)
    service._do_push.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_does_not_persist_changed_merge_tree(
    tmp_path,
):
    from src.services.workspace_convergence import build_snapshot

    service = GitHubSyncService(AsyncMock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(isolated_checkout=checkout)
    repo = Mock()
    _prepare_mock_repo(repo)
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._reconcile_remote_before_workspace_push = Mock(return_value=None)
    service._do_push = Mock()
    intended = build_snapshot({"features/a.py": "1" * 64})
    merged = build_snapshot({"features/a.py": "2" * 64})

    with patch(
        "src.services.workspace_convergence.snapshot_git_tree",
        side_effect=[intended, merged],
    ):
        result = await service.commit_workspace_changes(
            "agent change", push=True, expected_file_hashes={}
        )

    assert result.push_error is not None
    assert result.mismatch_paths == ["features/a.py"]
    repo.git.reset.assert_called_once_with("--hard", "a" * 40)
    service._do_push.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_pushes_when_remote_branch_is_missing(
    tmp_path,
):
    service = GitHubSyncService(AsyncMock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(isolated_checkout=checkout)
    repo = Mock()
    _prepare_mock_repo(repo, remote_sha="b" * 40)
    repo.remotes.origin.fetch.side_effect = [
        RuntimeError("couldn't find remote ref production-live"),
        None,
    ]
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._do_push = Mock(
        return_value=SimpleNamespace(success=True, commit_sha="b" * 40, error=None)
    )

    sha, push_error = await service.commit_workspace_changes(
        "agent change", push=True, expected_file_hashes={}
    )

    assert sha == "b" * 40
    assert push_error is None
    service._do_push.assert_called_once_with(tmp_path, repo)


def test_workspace_repo_remote_reconciliation_merges_advanced_related_branch(tmp_path):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")
    service.branch = "production-live"
    repo = Mock()
    repo.git.rev_list.return_value = "2"
    repo.merge_base.return_value = [Mock()]

    error = service._reconcile_remote_before_workspace_push(tmp_path, repo)

    assert error is None
    repo.remotes.origin.fetch.assert_called_once_with("production-live")
    repo.git.rev_list.assert_called_once_with("--count", "HEAD..origin/production-live")
    repo.merge_base.assert_called_once_with("HEAD", "origin/production-live")
    repo.git.merge.assert_called_once_with("--no-edit", "origin/production-live")


def test_workspace_repo_remote_reconciliation_links_unrelated_histories_with_local_tree(
    tmp_path,
):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")
    service.branch = "production-live"
    repo = Mock()
    repo.git.rev_list.return_value = "2"
    repo.merge_base.return_value = []

    error = service._reconcile_remote_before_workspace_push(tmp_path, repo)

    assert error is None
    repo.git.merge.assert_called_once_with(
        "--no-edit",
        "--allow-unrelated-histories",
        "--strategy=ours",
        "origin/production-live",
    )


def test_workspace_repo_remote_reconciliation_preserves_real_local_tree_for_unrelated_histories(
    tmp_path,
):
    remote_dir = tmp_path / "remote.git"
    GitRepo.init(remote_dir, bare=True)

    remote_seed_dir = tmp_path / "remote-seed"
    remote_seed = GitRepo.init(remote_seed_dir)
    remote_seed.config_writer().set_value("user", "name", "Test").set_value(
        "user", "email", "test@example.com"
    ).release()
    (remote_seed_dir / "remote-only.txt").write_text("remote\n")
    remote_seed.index.add(["remote-only.txt"])
    remote_seed.index.commit("remote root")
    remote_seed.git.branch("-M", "production-live")
    remote_seed.create_remote("origin", str(remote_dir)).push(
        "production-live:production-live"
    )

    work_dir = tmp_path / "workspace"
    repo = GitRepo.init(work_dir)
    repo.config_writer().set_value("user", "name", "Test").set_value(
        "user", "email", "test@example.com"
    ).release()
    (work_dir / "workspace-only.txt").write_text("workspace\n")
    repo.index.add(["workspace-only.txt"])
    repo.index.commit("workspace root")
    repo.create_remote("origin", str(remote_dir))

    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")
    service.branch = "production-live"

    error = service._reconcile_remote_before_workspace_push(work_dir, repo)

    assert error is None
    assert len(repo.head.commit.parents) == 2
    assert (work_dir / "workspace-only.txt").read_text() == "workspace\n"
    assert not (work_dir / "remote-only.txt").exists()


def test_workspace_repo_remote_reconciliation_aborts_conflicted_merge(tmp_path):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")
    service.branch = "production-live"
    repo = Mock()
    repo.git.rev_list.return_value = "1"
    repo.git.merge.side_effect = [RuntimeError("conflict"), None]
    merge_head = tmp_path / ".git" / "MERGE_HEAD"
    merge_head.parent.mkdir()
    merge_head.write_text("remote-sha\n")

    error = service._reconcile_remote_before_workspace_push(tmp_path, repo)

    assert error == (
        "Failed to merge advanced remote branch before workspace push: conflict"
    )
    assert repo.git.merge.call_args_list[-1].args == ("--abort",)
