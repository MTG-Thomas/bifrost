"""Tests for GitRepoManager object-storage-backed persistent git worktree."""

import importlib
import asyncio
import hashlib
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.git_repo_manager import GitRepoManager

git_repo_manager = importlib.import_module("src.services.git_repo_manager")


@pytest.fixture
def mock_settings():
    """Create mock settings for S3 configuration."""
    settings = MagicMock()
    settings.s3_bucket = "bifrost-local"
    settings.s3_endpoint_url = "http://seaweedfs:8333"
    settings.s3_access_key = "bifrost"
    settings.s3_secret_key = "bifrost123"
    settings.s3_region = "us-east-1"
    settings.object_storage_provider = "s3"
    settings.redis_url = ""  # Disable Redis lock in unit tests
    return settings


@pytest.fixture
def manager(mock_settings):
    """Create a GitRepoManager with mock settings."""
    return GitRepoManager(settings=mock_settings)


class TestBuildSyncCmd:
    """Tests for _build_sync_cmd command construction."""

    def test_basic_sync_down(self, manager):
        cmd = manager._build_sync_cmd(
            source="s3://bifrost-local/_repo/",
            dest="/tmp/work",
        )
        assert cmd == [
            "aws",
            "s3",
            "sync",
            "s3://bifrost-local/_repo/",
            "/tmp/work",
            "--endpoint-url",
            "http://seaweedfs:8333",
            "--only-show-errors",
        ]

    def test_sync_up_with_delete(self, manager):
        cmd = manager._build_sync_cmd(
            source="/tmp/work",
            dest="s3://bifrost-local/_repo/",
            delete=True,
        )
        assert cmd == [
            "aws",
            "s3",
            "sync",
            "/tmp/work",
            "s3://bifrost-local/_repo/",
            "--delete",
            "--endpoint-url",
            "http://seaweedfs:8333",
            "--only-show-errors",
        ]

    def test_no_endpoint_for_aws(self, mock_settings):
        """When endpoint_url is None (real AWS), omit --endpoint-url."""
        mock_settings.s3_endpoint_url = None
        mgr = GitRepoManager(settings=mock_settings)
        cmd = mgr._build_sync_cmd(
            source="s3://prod-bucket/_repo/",
            dest="/tmp/work",
        )
        assert "--endpoint-url" not in cmd
        assert cmd == [
            "aws",
            "s3",
            "sync",
            "s3://prod-bucket/_repo/",
            "/tmp/work",
            "--only-show-errors",
        ]


class TestBuildEnv:
    """Tests for _build_env environment variable construction."""

    def test_sets_aws_credentials(self, manager):
        env = manager._build_env()
        assert env["AWS_ACCESS_KEY_ID"] == "bifrost"
        assert env["AWS_SECRET_ACCESS_KEY"] == "bifrost123"
        assert env["AWS_DEFAULT_REGION"] == "us-east-1"

    def test_inherits_os_environ(self, manager):
        """Should include existing env vars (PATH, etc.)."""
        env = manager._build_env()
        assert "PATH" in env


class TestS3Uri:
    """Tests for _s3_uri construction."""

    def test_builds_uri_from_bucket(self, manager):
        assert manager._s3_uri() == "s3://bifrost-local/_repo/"


class TestPersistentWorkDir:
    """Tests for persistent worktree helpers."""

    def test_work_dir_creates_configured_persistent_path(
        self, manager, tmp_path, monkeypatch
    ):
        work_dir = tmp_path / "git-work"
        monkeypatch.setattr(git_repo_manager, "PERSISTENT_WORK_DIR", work_dir)

        assert manager.work_dir == work_dir
        assert work_dir.is_dir()

    def test_is_initialized_requires_git_directory(
        self, manager, tmp_path, monkeypatch
    ):
        work_dir = tmp_path / "git-work"
        monkeypatch.setattr(git_repo_manager, "PERSISTENT_WORK_DIR", work_dir)

        assert manager.is_initialized is False

        (work_dir / ".git").mkdir(parents=True)
        assert manager.is_initialized is True


class TestHasGitDir:
    """Tests for has_git_dir existence check."""

    @pytest.mark.asyncio
    async def test_returns_true_when_git_head_exists(self, manager):
        with patch(
            "src.services.repo_storage.RepoStorage.exists",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_exists:
            result = await manager.has_git_dir()
            assert result is True
            mock_exists.assert_awaited_once_with(".git/HEAD")

    @pytest.mark.asyncio
    async def test_returns_false_when_no_git_dir(self, manager):
        with patch(
            "src.services.repo_storage.RepoStorage.exists",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_exists:
            result = await manager.has_git_dir()
            assert result is False
            mock_exists.assert_awaited_once_with(".git/HEAD")


class TestRunAwsCli:
    """Tests for _run_aws_cli subprocess execution."""

    @pytest.mark.asyncio
    async def test_success(self, manager):
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b""))
        mock_process.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_process
        ) as mock_exec:
            await manager._run_aws_cli(["aws", "s3", "sync", "src", "dst"])
            mock_exec.assert_called_once()
            # Verify env includes AWS credentials
            call_kwargs = mock_exec.call_args
            env = call_kwargs.kwargs["env"]
            assert env["AWS_ACCESS_KEY_ID"] == "bifrost"

    @pytest.mark.asyncio
    async def test_success_logs_stderr_at_debug(self, manager, caplog):
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"", b"warning text\n"))
        mock_process.returncode = 0

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            caplog.at_level(logging.DEBUG, logger="src.services.git_repo_manager"),
        ):
            await manager._run_aws_cli(["aws", "s3", "sync", "src", "dst"])

        assert "aws s3 sync stderr: warning text" in caplog.text

    @pytest.mark.asyncio
    async def test_failure_raises_runtime_error(self, manager):
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(
            return_value=(b"", b"fatal: bucket not found")
        )
        mock_process.returncode = 1

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            with pytest.raises(RuntimeError, match="aws s3 sync failed"):
                await manager._run_aws_cli(["aws", "s3", "sync", "src", "dst"])


class TestSyncDown:
    """Tests for sync_down operation."""

    @pytest.mark.asyncio
    async def test_calls_aws_sync_with_correct_args(self, manager, tmp_path):
        with patch.object(manager, "_run_aws_cli", new_callable=AsyncMock) as mock_run:
            await manager.sync_down(tmp_path)
            mock_run.assert_awaited_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0:3] == ["aws", "s3", "sync"]
            assert cmd[3] == "s3://bifrost-local/_repo/"
            assert cmd[4] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_creates_target_dir(self, manager):
        import tempfile

        target = Path(tempfile.mkdtemp()) / "subdir"
        with patch.object(manager, "_run_aws_cli", new_callable=AsyncMock):
            await manager.sync_down(target)
            assert target.exists()
        # Cleanup
        import shutil

        shutil.rmtree(target.parent)


class TestSyncUp:
    """Tests for sync_up operation."""

    @pytest.mark.asyncio
    async def test_calls_aws_sync_with_delete(self, manager, tmp_path):
        with patch.object(manager, "_run_aws_cli", new_callable=AsyncMock) as mock_run:
            await manager.sync_up(tmp_path)
            mock_run.assert_awaited_once()
            cmd = mock_run.call_args[0][0]
            assert cmd[0:3] == ["aws", "s3", "sync"]
            assert cmd[3] == str(tmp_path)
            assert cmd[4] == "s3://bifrost-local/_repo/"
            assert "--delete" in cmd


class TestAzureBlobSync:
    """Azure Blob uses RepoStorage rather than the AWS CLI."""

    @pytest.fixture
    def azure_manager(self, mock_settings):
        mock_settings.object_storage_provider = "azure_blob"
        return GitRepoManager(settings=mock_settings)

    @pytest.mark.asyncio
    async def test_sync_down_materializes_repo_objects(self, azure_manager, tmp_path):
        storage = MagicMock()
        storage.list = AsyncMock(return_value=[".git/HEAD", "workflows/test.py"])
        storage.read = AsyncMock(
            side_effect=lambda path: {
                ".git/HEAD": b"ref: refs/heads/main\n",
                "workflows/test.py": b"print('ok')\n",
            }[path]
        )

        with (
            patch("src.services.repo_storage.RepoStorage", return_value=storage),
            patch.object(azure_manager, "_run_aws_cli", new_callable=AsyncMock) as aws,
        ):
            await azure_manager.sync_down(tmp_path)

        assert (tmp_path / ".git" / "HEAD").read_bytes() == b"ref: refs/heads/main\n"
        assert (tmp_path / "workflows" / "test.py").read_bytes() == b"print('ok')\n"
        aws.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_down_preserves_identical_readonly_git_object(
        self, azure_manager, tmp_path
    ):
        git_object = tmp_path / ".git" / "objects" / "00" / "readonly"
        git_object.parent.mkdir(parents=True)
        git_object.write_bytes(b"existing-object")
        git_object.chmod(0o444)
        storage = MagicMock()
        storage.list = AsyncMock(return_value=[".git/objects/00/readonly"])
        storage.read = AsyncMock(return_value=b"existing-object")

        with patch("src.services.repo_storage.RepoStorage", return_value=storage):
            await azure_manager.sync_down(tmp_path)

        assert git_object.read_bytes() == b"existing-object"
        assert git_object.stat().st_mode & 0o777 == 0o444

    @pytest.mark.asyncio
    async def test_sync_down_atomically_replaces_changed_readonly_git_object(
        self, azure_manager, tmp_path
    ):
        git_object = tmp_path / ".git" / "objects" / "00" / "readonly"
        git_object.parent.mkdir(parents=True)
        git_object.write_bytes(b"stale-object")
        git_object.chmod(0o444)
        storage = MagicMock()
        storage.list = AsyncMock(return_value=[".git/objects/00/readonly"])
        storage.read = AsyncMock(return_value=b"remote-object")

        with patch("src.services.repo_storage.RepoStorage", return_value=storage):
            await azure_manager.sync_down(tmp_path)

        assert git_object.read_bytes() == b"remote-object"
        assert not list(git_object.parent.glob(".readonly.*.tmp"))

    @pytest.mark.asyncio
    async def test_sync_up_writes_changes_and_deletes_removed_objects(
        self, azure_manager, tmp_path
    ):
        unchanged = tmp_path / ".git" / "HEAD"
        unchanged.parent.mkdir(parents=True)
        unchanged.write_bytes(b"main")
        changed = tmp_path / "workflows" / "test.py"
        changed.parent.mkdir(parents=True)
        changed.write_bytes(b"changed")
        azure_manager._downloaded_hashes = {
            ".git/HEAD": hashlib.sha256(b"main").hexdigest(),
            "workflows/test.py": hashlib.sha256(b"old").hexdigest(),
            "deleted.py": hashlib.sha256(b"deleted").hexdigest(),
        }
        storage = MagicMock()
        storage.list = AsyncMock(
            return_value=[".git/HEAD", "workflows/test.py", "deleted.py"]
        )
        storage.write = AsyncMock()
        storage.delete = AsyncMock()

        with patch("src.services.repo_storage.RepoStorage", return_value=storage):
            await azure_manager.sync_up(tmp_path)

        storage.write.assert_awaited_once_with("workflows/test.py", b"changed")
        storage.delete.assert_awaited_once_with("deleted.py")

    @pytest.mark.asyncio
    async def test_sync_down_rejects_path_traversal(self, azure_manager, tmp_path):
        storage = MagicMock()
        storage.list = AsyncMock(return_value=["../escape"])
        storage.read = AsyncMock(return_value=b"bad")

        with patch("src.services.repo_storage.RepoStorage", return_value=storage):
            with pytest.raises(ValueError, match="Unsafe repository object path"):
                await azure_manager.sync_down(tmp_path)

        storage.read.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_up_rejects_symlinks(self, azure_manager, tmp_path):
        outside = tmp_path.parent / "outside-secret"
        outside.write_bytes(b"secret")
        (tmp_path / "linked-secret").symlink_to(outside)
        storage = MagicMock()
        storage.list = AsyncMock(return_value=[])
        storage.write = AsyncMock()

        with patch("src.services.repo_storage.RepoStorage", return_value=storage):
            with pytest.raises(ValueError, match="Symlinks are not supported"):
                await azure_manager.sync_up(tmp_path)

        storage.write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_transfer_cancels_and_drains_siblings(self, azure_manager):
        sibling_cancelled = asyncio.Event()

        async def fail() -> None:
            await asyncio.sleep(0)
            raise RuntimeError("transfer failed")

        async def wait_forever() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                sibling_cancelled.set()
                raise

        with pytest.raises(RuntimeError, match="transfer failed"):
            await azure_manager._run_transfers([fail(), wait_forever()])

        assert sibling_cancelled.is_set()


class TestCheckout:
    """Tests for checkout context manager lifecycle (persistent working dir)."""

    @pytest.mark.asyncio
    async def test_syncs_down_yields_persistent_dir_syncs_up(self, manager):
        call_order = []

        async def mock_sync_down(target):
            call_order.append(("sync_down", target))

        async def mock_sync_up(source):
            call_order.append(("sync_up", source))

        with (
            patch.object(manager, "sync_down", side_effect=mock_sync_down),
            patch.object(manager, "sync_up", side_effect=mock_sync_up),
            patch.object(manager, "_acquire_lock") as mock_lock,
        ):
            # Mock the lock as an async context manager that does nothing
            mock_lock.return_value.__aenter__ = AsyncMock()
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            async with manager.checkout() as work_dir:
                assert work_dir.exists()
                assert work_dir.is_dir()
                call_order.append(("yield", work_dir))

        # Verify call order: sync_down -> yield -> sync_up
        assert len(call_order) == 3
        assert call_order[0][0] == "sync_down"
        assert call_order[1][0] == "yield"
        assert call_order[2][0] == "sync_up"

        # All received the same persistent path
        assert call_order[0][1] == call_order[1][1]
        assert call_order[2][1] == call_order[1][1]

        # Persistent dir is NOT cleaned up (by design)
        assert work_dir == manager.work_dir

    @pytest.mark.asyncio
    async def test_persistent_dir_survives_exception(self, manager):
        with (
            patch.object(manager, "sync_down", new_callable=AsyncMock),
            patch.object(manager, "sync_up", new_callable=AsyncMock),
            patch.object(manager, "_acquire_lock") as mock_lock,
        ):
            mock_lock.return_value.__aenter__ = AsyncMock()
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(ValueError, match="test error"):
                async with manager.checkout() as work_dir:
                    raise ValueError("test error")
            # Persistent dir still exists (not deleted on error)
            assert work_dir == manager.work_dir

    @pytest.mark.asyncio
    async def test_sync_up_not_called_on_sync_down_failure(self, manager):
        """If sync_down fails, sync_up should not be called."""
        with (
            patch.object(
                manager,
                "sync_down",
                side_effect=RuntimeError("download failed"),
            ),
            patch.object(manager, "sync_up", new_callable=AsyncMock) as mock_up,
            patch.object(manager, "_acquire_lock") as mock_lock,
        ):
            mock_lock.return_value.__aenter__ = AsyncMock()
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(RuntimeError, match="download failed"):
                async with manager.checkout():
                    pass  # pragma: no cover
            mock_up.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_readonly_checkout_skips_sync_up(
        self, manager, tmp_path, monkeypatch
    ):
        work_dir = tmp_path / "git-work"
        monkeypatch.setattr(git_repo_manager, "PERSISTENT_WORK_DIR", work_dir)

        with (
            patch.object(manager, "sync_down", new_callable=AsyncMock) as mock_down,
            patch.object(manager, "sync_up", new_callable=AsyncMock) as mock_up,
            patch.object(manager, "_acquire_lock") as mock_lock,
        ):
            mock_lock.return_value.__aenter__ = AsyncMock()
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            async with manager.checkout_readonly() as yielded:
                assert yielded == work_dir

        mock_down.assert_awaited_once_with(work_dir)
        mock_up.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lock_yields_work_dir_without_s3_sync(
        self, manager, tmp_path, monkeypatch
    ):
        work_dir = tmp_path / "git-work"
        monkeypatch.setattr(git_repo_manager, "PERSISTENT_WORK_DIR", work_dir)

        with (
            patch.object(manager, "sync_down", new_callable=AsyncMock) as mock_down,
            patch.object(manager, "sync_up", new_callable=AsyncMock) as mock_up,
            patch.object(manager, "_acquire_lock") as mock_lock,
        ):
            mock_lock.return_value.__aenter__ = AsyncMock()
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            async with manager.lock() as yielded:
                assert yielded == work_dir

        mock_down.assert_not_awaited()
        mock_up.assert_not_awaited()


class TestAcquireLock:
    """Tests for Redis lock behavior."""

    @pytest.mark.asyncio
    async def test_skips_lock_when_redis_url_is_absent(self, manager):
        with patch.object(git_repo_manager.redis, "from_url") as from_url:
            async with manager._acquire_lock():
                pass

        from_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_acquires_releases_and_closes_redis_lock(self, mock_settings):
        mock_settings.redis_url = "redis://redis:6379/0"
        manager = GitRepoManager(settings=mock_settings)
        lock = AsyncMock()
        lock.acquire = AsyncMock(return_value=True)
        lock.release = AsyncMock()
        client = MagicMock()
        client.lock.return_value = lock
        client.aclose = AsyncMock()

        with patch.object(git_repo_manager.redis, "from_url", return_value=client):
            async with manager._acquire_lock():
                pass

        client.lock.assert_called_once_with(
            git_repo_manager.GIT_LOCK_KEY,
            timeout=git_repo_manager.GIT_LOCK_TIMEOUT,
            blocking_timeout=git_repo_manager.GIT_LOCK_TIMEOUT,
        )
        lock.acquire.assert_awaited_once()
        lock.release.assert_awaited_once()
        client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_redis_lock_is_busy_and_still_closes_client(
        self, mock_settings
    ):
        mock_settings.redis_url = "redis://redis:6379/0"
        manager = GitRepoManager(settings=mock_settings)
        lock = AsyncMock()
        lock.acquire = AsyncMock(return_value=False)
        lock.release = AsyncMock()
        client = MagicMock()
        client.lock.return_value = lock
        client.aclose = AsyncMock()

        with patch.object(git_repo_manager.redis, "from_url", return_value=client):
            with pytest.raises(RuntimeError, match="another git operation"):
                async with manager._acquire_lock():
                    pass  # pragma: no cover

        lock.release.assert_awaited_once()
        client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_expired_lock_release_error_is_ignored(self, mock_settings):
        mock_settings.redis_url = "redis://redis:6379/0"
        manager = GitRepoManager(settings=mock_settings)
        lock = AsyncMock()
        lock.acquire = AsyncMock(return_value=True)
        lock.release = AsyncMock(side_effect=RuntimeError("expired"))
        client = MagicMock()
        client.lock.return_value = lock
        client.aclose = AsyncMock()

        with patch.object(git_repo_manager.redis, "from_url", return_value=client):
            async with manager._acquire_lock():
                pass

        client.aclose.assert_awaited_once()
