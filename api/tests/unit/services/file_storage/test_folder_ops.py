from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.services.file_storage.folder_ops import FolderOperationsService


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _Db:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self.rows)


def _file(path: str, content: str = "content"):
    return SimpleNamespace(
        path=path,
        content=content,
        content_hash=f"hash-{path}",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _service(rows, write_file=None):
    return FolderOperationsService(
        db=_Db(rows),
        settings=SimpleNamespace(),
        s3_client=None,
        remove_metadata_fn=None,
        write_file_fn=write_file,
    )


@pytest.mark.asyncio
async def test_list_files_synthesizes_direct_child_folders_and_files(monkeypatch):
    monkeypatch.setattr(
        "src.services.editor.file_filter.is_excluded_path",
        lambda path: path.startswith(".git/"),
    )
    service = _service(
        [
            _file("apps/app/pages/index.tsx", "source"),
            _file("apps/app/package.json", "{}"),
            _file("docs/readme.md", "hello"),
            _file(".git/config", "ignored"),
        ]
    )

    entries = await service.list_files()

    assert [entry.path for entry in entries] == ["apps/", "docs/"]
    apps_entry = entries[0]
    assert apps_entry.content_type == "inode/directory"
    assert apps_entry.size_bytes == 0


@pytest.mark.asyncio
async def test_list_files_for_subdirectory_returns_files_and_nested_folders(monkeypatch):
    monkeypatch.setattr(
        "src.services.editor.file_filter.is_excluded_path",
        lambda path: path.endswith(".pyc"),
    )
    service = _service(
        [
            _file("apps/app/pages/index.tsx", "source"),
            _file("apps/app/pages/cache.pyc", "ignored"),
            _file("apps/app/src/main.tsx", "main"),
            _file("apps/app/package.json", "{}"),
        ]
    )

    entries = await service.list_files("apps/app")

    assert [entry.path for entry in entries] == [
        "apps/app/package.json",
        "apps/app/pages/",
        "apps/app/src/",
    ]
    package_entry = entries[0]
    assert package_entry.content_hash == "hash-apps/app/package.json"
    assert package_entry.size_bytes == 2
    assert package_entry.content_type == "text/plain"


@pytest.mark.asyncio
async def test_list_files_recursive_filters_excluded_folders_and_folder_markers(monkeypatch):
    monkeypatch.setattr(
        "src.services.editor.file_filter.is_excluded_path",
        lambda path: path.startswith("node_modules/"),
    )
    service = _service(
        [
            _file("src/app.py", "print('ok')"),
            _file("src/generated/", ""),
            _file("node_modules/pkg/index.js", "ignored"),
        ]
    )

    entries = await service.list_files(recursive=True)

    assert [entry.path for entry in entries] == ["src/app.py"]


@pytest.mark.asyncio
async def test_create_folder_writes_gitkeep_placeholder(monkeypatch):
    from unittest.mock import AsyncMock

    writes = []

    class RepoStorage:
        async def write(self, path, content):
            writes.append((path, content))

    monkeypatch.setattr("src.services.repo_storage.RepoStorage", RepoStorage)
    monkeypatch.setattr(
        "src.core.workspace_writer.assert_workspace_writer_access", AsyncMock()
    )
    monkeypatch.setattr("src.core.repo_dirty.mark_repo_dirty", AsyncMock())
    service = _service([])

    await service.create_folder("/nested/path/")

    assert writes == [("nested/path/.gitkeep", b"")]


@pytest.mark.asyncio
async def test_upload_from_directory_skips_git_metadata_and_counts_files(tmp_path: Path):
    written = []
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("ignore")
    (tmp_path / "workflows").mkdir()
    (tmp_path / "workflows" / "hello.py").write_bytes(b"print('hello')")
    (tmp_path / "README.md").write_bytes(b"# readme")

    async def write_file(path, content, updated_by):
        written.append((path, content, updated_by))

    service = _service([], write_file=write_file)

    count = await service.upload_from_directory(tmp_path, updated_by="alice")

    assert count == 2
    assert sorted(written) == [
        ("README.md", b"# readme", "alice"),
        ("workflows/hello.py", b"print('hello')", "alice"),
    ]
