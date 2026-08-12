import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from git import Repo as GitRepo

from src.services.workspace_convergence import (
    build_snapshot,
    mismatch_paths,
    snapshot_git_tree,
    snapshot_repo_storage,
    snapshot_worktree,
)
from src.services import github_sync
from src.services.github_sync import GitHubSyncService


def _commit(root: Path) -> GitRepo:
    repo = GitRepo.init(root)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Test")
        config.set_value("user", "email", "test@example.test")
    repo.git.add(A=True)
    repo.index.commit("snapshot")
    return repo


def test_snapshot_normalizes_text_newlines_and_preserves_binary_bytes(tmp_path):
    text = tmp_path / "features" / "sample.py"
    text.parent.mkdir()
    text.write_bytes(b"one\r\ntwo\r\n")
    binary = tmp_path / "apps" / "asset.bin"
    binary.parent.mkdir()
    binary.write_bytes(b"\x00one\r\ntwo")
    repo = _commit(tmp_path)

    # Both snapshots normalize text CRLF to LF, so the worktree rewrite to LF
    # must not register as a mismatch. Binary bytes are hashed exactly.
    text.write_bytes(b"one\ntwo\n")
    worktree = snapshot_worktree(tmp_path)
    committed = snapshot_git_tree(repo.head.commit.tree)

    assert mismatch_paths(worktree, committed) == []
    binary.write_bytes(b"\x00one\ntwo")
    changed = snapshot_worktree(tmp_path)
    assert mismatch_paths(changed, committed) == ["apps/asset.bin"]


def test_snapshot_ignores_non_authored_tool_caches():
    snapshot = build_snapshot(
        {
            "features/example.py": "source-hash",
            ".ruff_cache/CACHEDIR.TAG": "cache-hash",
            "features/__pycache__/example.pyc": "bytecode-hash",
        }
    )
    assert snapshot.file_hashes == {"features/example.py": "source-hash"}


@pytest.mark.asyncio
async def test_repo_storage_snapshot_uses_bounded_concurrency() -> None:
    class RecordingRepo:
        def __init__(self) -> None:
            self.active = 0
            self.maximum = 0

        async def list(self):
            return [f"features/{index}.py" for index in range(32)]

        async def read(self, path: str) -> bytes:
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0.001)
            self.active -= 1
            return path.encode()

    repo = RecordingRepo()
    result = await snapshot_repo_storage(repo)  # type: ignore[arg-type]

    assert len(result.file_hashes) == 32
    assert 1 < repo.maximum <= 16


@pytest.mark.asyncio
async def test_authoritative_convergence_reuses_short_lived_generation_cache(
    monkeypatch,
) -> None:
    github_sync._convergence_cache.clear()
    service = GitHubSyncService(AsyncMock(), "https://token@github.com/acme/repo.git")
    expected = SimpleNamespace(authoritative_converged=True)
    uncached = AsyncMock(return_value=expected)
    monkeypatch.setattr(service, "_authoritative_convergence_uncached", uncached)
    monkeypatch.setattr(
        "src.core.repo_dirty.get_repo_dirty_state",
        AsyncMock(return_value=SimpleNamespace(generation="generation-one")),
    )

    assert await service.authoritative_convergence() is expected
    assert await service.authoritative_convergence() is expected
    uncached.assert_awaited_once()


def test_git_error_redaction_removes_embedded_http_credentials() -> None:
    message = (
        "fatal: unable to access "
        "'https://x-access-token:secret-value@github.com/acme/repo.git/'"
    )

    redacted = github_sync._redact_git_error(message)

    assert "secret-value" not in redacted
    assert "https://***@github.com/acme/repo.git/" in redacted

@pytest.mark.asyncio
async def test_status_distinguishes_clean_generated_checkout_from_authoritative_drift(
    tmp_path,
):
    remote_dir = tmp_path / "remote.git"
    seed_dir = tmp_path / "seed"
    generated_dir = tmp_path / "generated"
    authoritative_dir = tmp_path / "authoritative"
    remote = GitRepo.init(remote_dir, bare=True)
    seed_dir.mkdir()
    (seed_dir / "README.md").write_text("seed\n")
    seed = _commit(seed_dir)
    (seed_dir / "features" / "sample.py").parent.mkdir()
    (seed_dir / "features" / "sample.py").write_text("remote\n")
    seed.git.add(A=True)
    seed.index.commit("remote source")
    seed.git.branch("-M", "main")
    seed.create_remote("origin", str(remote_dir))
    seed.git.push("-u", "origin", "main")
    remote.git.symbolic_ref("HEAD", "refs/heads/main")
    GitRepo.clone_from(str(remote_dir), generated_dir)
    GitRepo.clone_from(str(remote_dir), authoritative_dir)
    (authoritative_dir / "features" / "sample.py").write_text("object store\n")

    @asynccontextmanager
    async def lock():
        yield generated_dir

    @asynccontextmanager
    async def snapshot_checkout():
        yield authoritative_dir

    service = GitHubSyncService(AsyncMock(), str(remote_dir), "main")
    service.repo_manager = SimpleNamespace(
        lock=lock,
        snapshot_checkout=snapshot_checkout,
    )

    result = await service.authoritative_convergence()

    assert result.generated_checkout_clean is True
    assert result.authoritative_converged is False
    assert result.remote_sha is not None
    assert result.mismatch_paths == ["features/sample.py"]
