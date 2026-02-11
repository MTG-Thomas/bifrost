"""Tests for GitRepoManager — S3-backed persistent git working tree."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.git_repo_manager import GitRepoManager


@pytest.fixture
def mock_settings():
    """Create mock settings for S3 configuration."""
    settings = MagicMock()
    settings.s3_bucket = "bifrost-local"
    settings.s3_endpoint_url = "http://minio:9000"
    settings.s3_access_key = "bifrost"
    settings.s3_secret_key = "bifrost123"
    settings.s3_region = "us-east-1"
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
            "aws", "s3", "sync",
            "s3://bifrost-local/_repo/",
            "/tmp/work",
            "--endpoint-url", "http://minio:9000",
            "--only-show-errors",
        ]

    def test_sync_up_with_delete(self, manager):
        cmd = manager._build_sync_cmd(
            source="/tmp/work",
            dest="s3://bifrost-local/_repo/",
            delete=True,
        )
        assert cmd == [
            "aws", "s3", "sync",
            "/tmp/work",
            "s3://bifrost-local/_repo/",
            "--delete",
            "--endpoint-url", "http://minio:9000",
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
            "aws", "s3", "sync",
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


class TestHasGitDir:
    """Tests for has_git_dir existence check."""

    @pytest.mark.asyncio
    async def test_returns_true_when_git_head_exists(self, manager):
        with patch("src.services.repo_storage.RepoStorage.exists", new_callable=AsyncMock, return_value=True) as mock_exists:
            result = await manager.has_git_dir()
            assert result is True
            mock_exists.assert_awaited_once_with(".git/HEAD")

    @pytest.mark.asyncio
    async def test_returns_false_when_no_git_dir(self, manager):
        with patch("src.services.repo_storage.RepoStorage.exists", new_callable=AsyncMock, return_value=False) as mock_exists:
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

        with patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec:
            await manager._run_aws_cli(["aws", "s3", "sync", "src", "dst"])
            mock_exec.assert_called_once()
            # Verify env includes AWS credentials
            call_kwargs = mock_exec.call_args
            env = call_kwargs.kwargs["env"]
            assert env["AWS_ACCESS_KEY_ID"] == "bifrost"

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


class TestCheckout:
    """Tests for checkout context manager lifecycle."""

    @pytest.mark.asyncio
    async def test_creates_temp_dir_syncs_down_yields_syncs_up_cleans_up(self, manager):
        call_order = []

        async def mock_sync_down(target):
            call_order.append(("sync_down", target))

        async def mock_sync_up(source):
            call_order.append(("sync_up", source))

        with patch.object(manager, "sync_down", side_effect=mock_sync_down):
            with patch.object(manager, "sync_up", side_effect=mock_sync_up):
                async with manager.checkout() as work_dir:
                    assert work_dir.exists()
                    assert work_dir.is_dir()
                    call_order.append(("yield", work_dir))
                    saved_dir = work_dir

        # Verify call order
        assert len(call_order) == 3
        assert call_order[0][0] == "sync_down"
        assert call_order[1][0] == "yield"
        assert call_order[2][0] == "sync_up"

        # sync_down and sync_up received the same path
        assert call_order[0][1] == call_order[1][1]
        assert call_order[2][1] == call_order[1][1]

        # Temp dir cleaned up
        assert not saved_dir.exists()

    @pytest.mark.asyncio
    async def test_cleans_up_on_exception(self, manager):
        with patch.object(manager, "sync_down", new_callable=AsyncMock):
            with patch.object(manager, "sync_up", new_callable=AsyncMock):
                saved_dir = None
                with pytest.raises(ValueError, match="test error"):
                    async with manager.checkout() as work_dir:
                        saved_dir = work_dir
                        raise ValueError("test error")
                # Temp dir still cleaned up
                assert saved_dir is not None
                assert not saved_dir.exists()

    @pytest.mark.asyncio
    async def test_sync_up_not_called_on_sync_down_failure(self, manager):
        """If sync_down fails, sync_up should not be called."""
        with patch.object(
            manager, "sync_down",
            side_effect=RuntimeError("download failed"),
        ):
            with patch.object(manager, "sync_up", new_callable=AsyncMock) as mock_up:
                with pytest.raises(RuntimeError, match="download failed"):
                    async with manager.checkout():
                        pass  # pragma: no cover
                mock_up.assert_not_awaited()
