import asyncio
import json
from pathlib import Path

import pytest

from src.services.package_manager import (
    PackageInfo,
    PackageNotFoundError,
    WorkspacePackageManager,
)


class _FakeProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False

    async def communicate(self):
        return self._stdout, self._stderr

    async def wait(self):
        return None

    def kill(self):
        self.killed = True


class _AsyncLineStream:
    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __aiter__(self):
        self._iter = iter(self._lines)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


@pytest.mark.asyncio
async def test_get_package_info_returns_pypi_metadata(monkeypatch, tmp_path: Path):
    manager = WorkspacePackageManager(tmp_path)

    class Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self):
            return {"info": {"name": "Requests", "version": "2.32.0", "summary": "HTTP"}}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, url: str):
            assert url == "https://pypi.org/pypi/requests/json"
            return Response()

    monkeypatch.setattr("src.services.package_manager.aiohttp.ClientSession", Session)

    info = await manager.get_package_info("requests")

    assert isinstance(info, PackageInfo)
    assert (info.name, info.version, info.summary) == ("Requests", "2.32.0", "HTTP")


@pytest.mark.asyncio
async def test_get_package_info_raises_for_404(monkeypatch, tmp_path: Path):
    manager = WorkspacePackageManager(tmp_path)

    class Response:
        status = 404

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def get(self, _url: str):
            return Response()

    monkeypatch.setattr("src.services.package_manager.aiohttp.ClientSession", Session)

    with pytest.raises(PackageNotFoundError, match="missing"):
        await manager.get_package_info("missing")


@pytest.mark.asyncio
async def test_list_installed_packages_parses_pip_json(monkeypatch, tmp_path: Path):
    manager = WorkspacePackageManager(tmp_path)
    payload = [{"name": "fastapi", "version": "1.0"}]

    async def fake_exec(*args, **kwargs):
        assert args[-2:] == ("--format", "json")
        return _FakeProcess(stdout=json.dumps(payload).encode())

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert await manager.list_installed_packages() == payload


@pytest.mark.asyncio
async def test_list_installed_packages_returns_empty_on_pip_failure(
    monkeypatch, tmp_path: Path
):
    manager = WorkspacePackageManager(tmp_path)

    async def fake_exec(*_args, **_kwargs):
        return _FakeProcess(returncode=1, stderr=b"broken")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    assert await manager.list_installed_packages() == []


@pytest.mark.asyncio
async def test_check_for_updates_reports_newer_versions(monkeypatch, tmp_path: Path):
    manager = WorkspacePackageManager(tmp_path)

    async def list_installed():
        return [
            {"name": "fresh", "version": "1.0"},
            {"name": "current", "version": "2.0"},
            {"name": "missing", "version": "3.0"},
        ]

    async def get_info(name: str):
        if name == "missing":
            raise PackageNotFoundError("missing")
        versions = {"fresh": "1.1", "current": "2.0"}
        return PackageInfo(name, versions[name], "")

    monkeypatch.setattr(manager, "list_installed_packages", list_installed)
    monkeypatch.setattr(manager, "get_package_info", get_info)

    assert await manager.check_for_updates() == [
        {"name": "fresh", "current_version": "1.0", "latest_version": "1.1"}
    ]


@pytest.mark.asyncio
async def test_install_package_returns_when_requested_version_already_installed(
    monkeypatch, tmp_path: Path
):
    manager = WorkspacePackageManager(tmp_path)
    logs: list[str] = []

    monkeypatch.setattr(
        manager,
        "get_package_info",
        lambda name: asyncio.sleep(0, PackageInfo(name, "1.0", "summary")),
    )
    monkeypatch.setattr(
        manager,
        "list_installed_packages",
        lambda: asyncio.sleep(0, [{"name": "Demo", "version": "1.0"}]),
    )

    await manager.install_package(
        "demo", version="1.0", log_callback=lambda msg: asyncio.sleep(0, logs.append(msg))
    )

    assert any("already installed" in message for message in logs)
    assert not manager.requirements_file.exists()


@pytest.mark.asyncio
async def test_install_package_streams_success_and_updates_requirements(
    monkeypatch, tmp_path: Path
):
    manager = WorkspacePackageManager(tmp_path)
    logs: list[str] = []
    calls: list[tuple] = []

    monkeypatch.setattr(
        manager,
        "get_package_info",
        lambda name: asyncio.sleep(0, PackageInfo(name, "1.0", "summary")),
    )
    monkeypatch.setattr(manager, "list_installed_packages", lambda: asyncio.sleep(0, []))

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        process = _FakeProcess()
        process.stdout = _AsyncLineStream([b"Collecting demo\n", b"\n", b"Installed\n"])
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    await manager.install_package(
        "demo", version="1.0", log_callback=lambda msg: asyncio.sleep(0, logs.append(msg))
    )

    assert any(call[-2:] == ("demo==1.0", "--upgrade") for call in calls)
    assert "demo==1.0\n" == manager.requirements_file.read_text()
    assert "Collecting demo" in logs
    assert "Installed" in logs


@pytest.mark.asyncio
async def test_install_requirements_streaming_requires_file(tmp_path: Path):
    manager = WorkspacePackageManager(tmp_path)
    logs: list[str] = []

    with pytest.raises(FileNotFoundError):
        await manager.install_requirements_streaming(
            log_callback=lambda msg: asyncio.sleep(0, logs.append(msg))
        )

    assert any("not found" in message for message in logs)


@pytest.mark.asyncio
async def test_install_requirements_streaming_raises_on_failed_pip(
    monkeypatch, tmp_path: Path
):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("demo\n")
    manager = WorkspacePackageManager(tmp_path)
    logs: list[str] = []

    async def fake_exec(*_args, **_kwargs):
        process = _FakeProcess(returncode=1)
        process.stdout = _AsyncLineStream([b"boom\n"])
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    with pytest.raises(Exception, match="Package installation failed"):
        await manager.install_requirements_streaming(
            log_callback=lambda msg: asyncio.sleep(0, logs.append(msg))
        )

    assert "boom" in logs
    assert any("Installation failed" in message for message in logs)


def test_append_to_requirements_creates_updates_and_preserves_comments(tmp_path: Path):
    manager = WorkspacePackageManager(tmp_path)

    asyncio.run(manager._append_to_requirements("demo", "1.0"))
    assert manager.requirements_file.read_text() == "demo==1.0\n"

    manager.requirements_file.write_text("# keep\nDemo>=0.9\nother~=2\n")
    asyncio.run(manager._append_to_requirements("demo", "1.1"))
    asyncio.run(manager._append_to_requirements("new-package"))

    assert manager.requirements_file.read_text() == (
        "# keep\n"
        "demo==1.1\n"
        "other~=2\n"
        "new-package\n"
    )
