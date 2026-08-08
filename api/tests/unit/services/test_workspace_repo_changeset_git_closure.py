from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from git import Repo as GitRepo

from src.services.github_sync import GitHubSyncService


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_syncs_storage_and_does_not_push_by_default(
    tmp_path,
):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")
    events = []

    @asynccontextmanager
    async def checkout():
        events.append("sync-down")
        yield tmp_path
        events.append("sync-up")

    service.repo_manager = SimpleNamespace(checkout=checkout)
    repo = Mock()
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._do_push = Mock()

    sha, push_error = await service.commit_workspace_changes("agent change")

    assert sha == "a" * 40
    assert push_error is None
    assert events == ["sync-down", "sync-up"]
    service._do_commit.assert_awaited_once_with(tmp_path, repo, "agent change")
    service._do_push.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_pushes_only_when_requested(tmp_path):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(checkout=checkout)
    repo = Mock()
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._do_push = Mock(
        return_value=SimpleNamespace(success=True, commit_sha="b" * 40, error=None)
    )
    service._reconcile_remote_before_workspace_push = Mock(return_value=None)

    sha, push_error = await service.commit_workspace_changes("agent change", push=True)

    assert sha == "b" * 40
    assert push_error is None
    service._reconcile_remote_before_workspace_push.assert_called_once_with(
        tmp_path, repo
    )
    service._do_push.assert_called_once_with(tmp_path, repo)


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_returns_commit_when_push_fails(tmp_path):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(checkout=checkout)
    repo = Mock()
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._do_push = Mock(
        return_value=SimpleNamespace(success=False, commit_sha=None, error="rejected")
    )
    service._reconcile_remote_before_workspace_push = Mock(return_value=None)

    sha, push_error = await service.commit_workspace_changes("agent change", push=True)

    assert sha == "a" * 40
    assert push_error == "rejected"


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_returns_remote_reconciliation_error(
    tmp_path,
):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(checkout=checkout)
    repo = Mock()
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._reconcile_remote_before_workspace_push = Mock(
        return_value="remote merge failed"
    )
    service._do_push = Mock()

    sha, push_error = await service.commit_workspace_changes("agent change", push=True)

    assert sha == "a" * 40
    assert push_error == "remote merge failed"
    service._do_push.assert_not_called()


@pytest.mark.asyncio
async def test_workspace_repo_commit_closure_pushes_when_remote_branch_is_missing(
    tmp_path,
):
    service = GitHubSyncService(Mock(), "https://example.test/org/repo.git")

    @asynccontextmanager
    async def checkout():
        yield tmp_path

    service.repo_manager = SimpleNamespace(checkout=checkout)
    repo = Mock()
    repo.remotes.origin.fetch.side_effect = RuntimeError(
        "couldn't find remote ref production-live"
    )
    service._open_or_init = Mock(return_value=repo)
    service._do_commit = AsyncMock(
        return_value=SimpleNamespace(success=True, commit_sha="a" * 40, error=None)
    )
    service._do_push = Mock(
        return_value=SimpleNamespace(success=True, commit_sha="b" * 40, error=None)
    )

    sha, push_error = await service.commit_workspace_changes("agent change", push=True)

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
