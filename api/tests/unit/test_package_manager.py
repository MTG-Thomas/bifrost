"""
Unit tests for WorkspacePackageManager
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.package_manager import PackageInfo, PackageNotFoundError, WorkspacePackageManager


@pytest.fixture
def workspace_path(tmp_path):
    """Create a temporary workspace directory"""
    return tmp_path / "workspace"


@pytest.fixture
def pkg_manager(workspace_path):
    """Create a WorkspacePackageManager instance"""
    workspace_path.mkdir(parents=True, exist_ok=True)
    return WorkspacePackageManager(workspace_path)


class TestWorkspacePackageManager:
    """Tests for WorkspacePackageManager"""

    def test_init(self, workspace_path):
        """Test package manager initialization"""
        workspace_path.mkdir(parents=True, exist_ok=True)
        pkg_manager = WorkspacePackageManager(workspace_path)

        assert pkg_manager.workspace_path == workspace_path
        assert pkg_manager.requirements_file == workspace_path / "requirements.txt"

    @pytest.mark.asyncio
    async def test_get_package_info_success(self, pkg_manager):
        """Test getting package info from PyPI"""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={
            "info": {
                "name": "requests",
                "version": "2.31.0",
                "summary": "Python HTTP for Humans."
            }
        })

        with patch('src.services.package_manager.aiohttp.ClientSession') as mock_session_class:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.get = MagicMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            info = await pkg_manager.get_package_info("requests")

            assert isinstance(info, PackageInfo)
            assert info.name == "requests"
            assert info.version == "2.31.0"
            assert info.summary == "Python HTTP for Humans."

    @pytest.mark.asyncio
    async def test_get_package_info_not_found(self, pkg_manager):
        """Test getting package info for non-existent package"""
        mock_response = MagicMock()
        mock_response.status = 404

        with patch('src.services.package_manager.aiohttp.ClientSession') as mock_session_class:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.get = MagicMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_session_class.return_value = mock_session

            with pytest.raises(PackageNotFoundError, match="Package 'nonexistent' not found on PyPI"):
                await pkg_manager.get_package_info("nonexistent")

    @pytest.mark.asyncio
    async def test_list_installed_packages(self, pkg_manager):
        """Test listing installed packages"""
        mock_process = AsyncMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(
            json.dumps([
                {"name": "requests", "version": "2.31.0"},
                {"name": "pandas", "version": "2.0.3"}
            ]).encode(),
            b""
        ))

        with patch('asyncio.create_subprocess_exec', return_value=mock_process):
            packages = await pkg_manager.list_installed_packages()

            assert len(packages) == 2
            assert packages[0]["name"] == "requests"
            assert packages[0]["version"] == "2.31.0"
            assert packages[1]["name"] == "pandas"
            assert packages[1]["version"] == "2.0.3"

    @pytest.mark.asyncio
    async def test_list_installed_packages_returns_empty_on_pip_failure(self, pkg_manager):
        """Failed pip list should be treated as an empty package list."""
        mock_process = AsyncMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(b"", b"pip failed"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            assert await pkg_manager.list_installed_packages() == []

    @pytest.mark.asyncio
    async def test_check_for_updates_reports_version_mismatches(self, pkg_manager):
        """Update checks should ignore provider failures and return changed versions."""
        pkg_manager.list_installed_packages = AsyncMock(return_value=[
            {"name": "requests", "version": "2.30.0"},
            {"name": "missing", "version": "1.0.0"},
            {"name": "current", "version": "1.0.0"},
        ])

        async def get_info(name):
            if name == "missing":
                raise PackageNotFoundError("missing")
            if name == "current":
                return PackageInfo("current", "1.0.0", "Current")
            return PackageInfo("requests", "2.31.0", "Requests")

        pkg_manager.get_package_info = get_info

        assert await pkg_manager.check_for_updates() == [
            {
                "name": "requests",
                "current_version": "2.30.0",
                "latest_version": "2.31.0",
            }
        ]

    @pytest.mark.asyncio
    async def test_append_to_requirements_new_file(self, pkg_manager):
        """Test appending to requirements.txt when file doesn't exist"""
        await pkg_manager._append_to_requirements("requests", "2.31.0")

        assert pkg_manager.requirements_file.exists()
        content = pkg_manager.requirements_file.read_text()
        assert content.strip() == "requests==2.31.0"

    @pytest.mark.asyncio
    async def test_append_to_requirements_existing_file(self, pkg_manager):
        """Test appending to existing requirements.txt"""
        # Create existing requirements.txt
        pkg_manager.requirements_file.parent.mkdir(parents=True, exist_ok=True)
        pkg_manager.requirements_file.write_text("pandas==2.0.3\n")

        await pkg_manager._append_to_requirements("requests", "2.31.0")

        content = pkg_manager.requirements_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert "pandas==2.0.3" in lines
        assert "requests==2.31.0" in lines

    @pytest.mark.asyncio
    async def test_append_to_requirements_update_existing(self, pkg_manager):
        """Test updating existing package in requirements.txt"""
        # Create existing requirements.txt with old version
        pkg_manager.requirements_file.parent.mkdir(parents=True, exist_ok=True)
        pkg_manager.requirements_file.write_text("requests==2.30.0\npandas==2.0.3\n")

        await pkg_manager._append_to_requirements("requests", "2.31.0")

        content = pkg_manager.requirements_file.read_text()
        lines = content.strip().split("\n")
        assert len(lines) == 2
        assert "requests==2.31.0" in lines
        assert "pandas==2.0.3" in lines
        # Old version should not be present
        assert "requests==2.30.0" not in lines

    @pytest.mark.asyncio
    async def test_append_to_requirements_preserves_comments_and_updates_specifier(
        self,
        pkg_manager,
    ):
        """Existing package lines should update across common version operators."""
        pkg_manager.requirements_file.parent.mkdir(parents=True, exist_ok=True)
        pkg_manager.requirements_file.write_text(
            "# base deps\n\nrequests>=2.30.0\npandas~=2.0\n"
        )

        await pkg_manager._append_to_requirements("requests", "2.31.0")

        assert pkg_manager.requirements_file.read_text().splitlines() == [
            "# base deps",
            "",
            "requests==2.31.0",
            "pandas~=2.0",
        ]

    @pytest.mark.asyncio
    async def test_install_package_returns_when_requested_version_already_installed(
        self,
        pkg_manager,
    ):
        """Exact installed version should short-circuit before invoking pip."""
        logs = []

        async def capture_log(message: str) -> None:
            logs.append(message)

        pkg_manager.get_package_info = AsyncMock(
            return_value=PackageInfo("requests", "2.31.0", "Requests")
        )
        pkg_manager.list_installed_packages = AsyncMock(
            return_value=[{"name": "Requests", "version": "2.31.0"}]
        )

        await pkg_manager.install_package(
            "requests",
            version="2.31.0",
            log_callback=capture_log,
        )

        assert any("already installed" in item for item in logs)

    @pytest.mark.asyncio
    async def test_install_package_streams_pip_output_and_updates_requirements(
        self,
        pkg_manager,
    ):
        """Successful installs should stream output and append changed specs."""
        logs = []

        async def capture_log(message: str) -> None:
            logs.append(message)

        pkg_manager.get_package_info = AsyncMock(
            return_value=PackageInfo("requests", "2.31.0", "Requests")
        )
        pkg_manager.list_installed_packages = AsyncMock(return_value=[])
        pkg_manager._append_to_requirements = AsyncMock()

        class FakeStdout:
            def __aiter__(self):
                self._lines = iter([b"Collecting requests\n", b"\n", b"Installed\n"])
                return self

            async def __anext__(self):
                try:
                    return next(self._lines)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        fake_process = SimpleNamespace(
            stdout=FakeStdout(),
            returncode=0,
            wait=AsyncMock(),
        )

        with patch("asyncio.create_subprocess_exec", return_value=fake_process) as create:
            await pkg_manager.install_package(
                "requests",
                version="2.31.0",
                log_callback=capture_log,
            )

        assert create.call_args.args[:4] == (__import__("sys").executable, "-m", "pip", "install")
        assert "requests==2.31.0" in create.call_args.args
        assert "Collecting requests" in logs
        assert "Installed" in logs
        pkg_manager._append_to_requirements.assert_awaited_once_with("requests", "2.31.0")

    @pytest.mark.asyncio
    async def test_install_package_raises_on_pip_failure(self, pkg_manager):
        """Non-zero pip exit should raise after logging failure."""
        logs = []

        async def capture_log(message: str) -> None:
            logs.append(message)

        pkg_manager.get_package_info = AsyncMock(
            return_value=PackageInfo("broken", "1.0.0", "Broken")
        )
        pkg_manager.list_installed_packages = AsyncMock(return_value=[])

        fake_process = SimpleNamespace(
            stdout=None,
            returncode=1,
            wait=AsyncMock(),
        )

        with patch("asyncio.create_subprocess_exec", return_value=fake_process):
            with pytest.raises(Exception, match="Package installation failed"):
                await pkg_manager.install_package("broken", log_callback=capture_log)

        assert "✗ Installation failed" in logs

    @pytest.mark.asyncio
    async def test_install_requirements_streaming_missing_file(self, pkg_manager):
        """Missing requirements files should fail before invoking pip."""
        logs = []

        async def capture_log(message: str) -> None:
            logs.append(message)

        with pytest.raises(FileNotFoundError):
            await pkg_manager.install_requirements_streaming(log_callback=capture_log)

        assert logs[-1].endswith("requirements.txt not found")

    @pytest.mark.asyncio
    async def test_install_requirements_streaming_success(self, pkg_manager):
        """Requirements installs should stream pip output and report success."""
        logs = []

        async def capture_log(message: str) -> None:
            logs.append(message)

        pkg_manager.requirements_file.parent.mkdir(parents=True, exist_ok=True)
        pkg_manager.requirements_file.write_text("requests==2.31.0\n")

        class FakeStdout:
            def __aiter__(self):
                self._lines = iter([b"Installing deps\n"])
                return self

            async def __anext__(self):
                try:
                    return next(self._lines)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        fake_process = SimpleNamespace(
            stdout=FakeStdout(),
            returncode=0,
            wait=AsyncMock(),
        )

        with patch("asyncio.create_subprocess_exec", return_value=fake_process):
            await pkg_manager.install_requirements_streaming(log_callback=capture_log)

        assert logs == [
            "Reading requirements from requirements.txt...",
            "Installing deps",
            "✓ Packages installed successfully",
        ]

    @pytest.mark.asyncio
    async def test_install_requirements_streaming_raises_on_pip_failure(
        self,
        pkg_manager,
    ):
        """Requirements install should raise on non-zero pip exit."""
        logs = []

        async def capture_log(message: str) -> None:
            logs.append(message)

        pkg_manager.requirements_file.parent.mkdir(parents=True, exist_ok=True)
        pkg_manager.requirements_file.write_text("broken==1.0.0\n")
        fake_process = SimpleNamespace(
            stdout=None,
            returncode=1,
            wait=AsyncMock(),
        )

        with patch("asyncio.create_subprocess_exec", return_value=fake_process):
            with pytest.raises(Exception, match="Package installation failed"):
                await pkg_manager.install_requirements_streaming(log_callback=capture_log)

        assert "✗ Installation failed" in logs
