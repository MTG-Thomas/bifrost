from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from src.services.solution_files import (
    enumerate_solution_files,
    iter_solution_file_chunks,
    read_solution_file,
    write_solution_file,
    write_solution_file_from_chunks,
)


class _ExecuteResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._scalar


@pytest.mark.asyncio
async def test_enumerate_solution_files_maps_metadata_rows_without_s3_calls():
    install_id = uuid4()
    rows = [
        SimpleNamespace(
            location="workflows",
            path="sync.py",
            sha256="abc123",
            size_bytes=42,
            s3_key="solutions/install/workflows/sync.py",
        ),
        SimpleNamespace(
            location="apps",
            path="demo/app.tsx",
            sha256=None,
            size_bytes=None,
            s3_key=None,
        ),
    ]
    db = AsyncMock()
    db.execute.return_value = _ExecuteResult(rows=rows)

    entries = await enumerate_solution_files(db, install_id)

    assert [(entry.location, entry.path, entry.sha256, entry.size) for entry in entries] == [
        ("workflows", "sync.py", "abc123", 42),
        ("apps", "demo/app.tsx", None, None),
    ]
    assert entries[0].s3_key == "solutions/install/workflows/sync.py"
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_read_solution_file_requires_matching_metadata_before_s3_read():
    install_id = uuid4()
    db = AsyncMock()
    db.execute.return_value = _ExecuteResult(scalar="stored/key.txt")
    storage = AsyncMock()
    storage.read_uploaded_file.return_value = b"contents"

    with patch("src.services.solution_files.FileStorageService", return_value=storage):
        content = await read_solution_file(db, install_id, "files", "key.txt")

    assert content == b"contents"
    storage.read_uploaded_file.assert_awaited_once_with("stored/key.txt")


@pytest.mark.asyncio
async def test_read_solution_file_missing_metadata_raises_without_s3_read():
    db = AsyncMock()
    db.execute.return_value = _ExecuteResult(scalar=None)

    with patch("src.services.solution_files.FileStorageService") as storage_cls:
        with pytest.raises(FileNotFoundError, match="No file for install"):
            await read_solution_file(db, uuid4(), "files", "missing.txt")

    storage_cls.assert_not_called()


@pytest.mark.asyncio
async def test_iter_solution_file_chunks_streams_configured_chunk_size():
    db = AsyncMock()
    db.execute.return_value = _ExecuteResult(scalar="stored/big.bin")
    storage = MagicMock()

    async def chunks(_key, *, chunk_size):
        yield f"chunk-size={chunk_size}".encode()
        yield b"tail"

    storage.iter_raw_s3_chunks = chunks

    with patch("src.services.solution_files.FileStorageService", return_value=storage):
        received = [
            chunk
            async for chunk in iter_solution_file_chunks(
                db,
                uuid4(),
                "files",
                "big.bin",
                chunk_size=16,
            )
        ]

    assert received == [b"chunk-size=16", b"tail"]


@pytest.mark.asyncio
async def test_write_solution_file_skip_preserves_existing_metadata_and_storage():
    install_id = uuid4()
    db = AsyncMock()
    db.execute.return_value = _ExecuteResult(scalar=uuid4())

    with patch("src.services.solution_files.FileStorageService") as storage_cls:
        written = await write_solution_file(
            db,
            install_id,
            "files",
            "keep.txt",
            b"new bytes",
            mode="skip",
        )

    assert written is False
    storage_cls.assert_not_called()
    db.commit.assert_not_awaited()
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_solution_file_insert_writes_s3_and_commits_metadata(monkeypatch):
    install_id = UUID("11111111-1111-1111-1111-111111111111")
    db = AsyncMock()
    db.execute.side_effect = [_ExecuteResult(scalar=None), _ExecuteResult()]
    storage = AsyncMock()
    monkeypatch.setattr(
        "src.services.solution_files.resolve_s3_key",
        lambda location, solution_id, path: f"{location}/{solution_id}/{path}",
    )

    with patch("src.services.solution_files.FileStorageService", return_value=storage):
        written = await write_solution_file(
            db,
            install_id,
            "files",
            "new.txt",
            b"hello",
        )

    assert written is True
    storage.write_raw_to_s3.assert_awaited_once_with(
        "files/11111111-1111-1111-1111-111111111111/new.txt",
        b"hello",
    )
    assert db.execute.await_count == 2
    inserted_values = db.execute.await_args_list[1].args[0].compile().params
    assert inserted_values["solution_id"] == install_id
    assert inserted_values["location"] == "files"
    assert inserted_values["path"] == "new.txt"
    assert inserted_values["sha256"]
    assert inserted_values["size_bytes"] == 5
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_solution_file_from_chunks_updates_existing_metadata(monkeypatch):
    install_id = UUID("22222222-2222-2222-2222-222222222222")
    existing_id = uuid4()
    db = AsyncMock()
    db.execute.side_effect = [_ExecuteResult(scalar=existing_id), _ExecuteResult()]
    storage = AsyncMock()
    storage.write_raw_chunks_to_s3.return_value = ("chunk-hash", 12)
    monkeypatch.setattr(
        "src.services.solution_files.resolve_s3_key",
        lambda location, solution_id, path: f"{location}/{solution_id}/{path}",
    )

    async def chunks():
        yield b"hello "
        yield b"world!"

    with patch("src.services.solution_files.FileStorageService", return_value=storage):
        written = await write_solution_file_from_chunks(
            db,
            install_id,
            "assets",
            "payload.bin",
            chunks(),
        )

    assert written is True
    storage.write_raw_chunks_to_s3.assert_awaited_once()
    assert storage.write_raw_chunks_to_s3.await_args.args[0] == (
        "assets/22222222-2222-2222-2222-222222222222/payload.bin"
    )
    assert db.execute.await_count == 2
    updated_values = db.execute.await_args_list[1].args[0].compile().params
    assert updated_values["s3_key"] == (
        "assets/22222222-2222-2222-2222-222222222222/payload.bin"
    )
    assert updated_values["sha256"] == "chunk-hash"
    assert updated_values["size_bytes"] == 12
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_write_solution_file_rejects_unknown_mode_before_db_or_storage():
    db = AsyncMock()

    with pytest.raises(ValueError, match="mode must be 'replace' or 'skip'"):
        await write_solution_file(db, uuid4(), "files", "bad.txt", b"", mode="append")

    db.execute.assert_not_called()
