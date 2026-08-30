from uuid import uuid4

import pytest

from src.services.artifacts import ArtifactService


class _FakeDB:
    def __init__(self) -> None:
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        return None


class _FakeStorage:
    def __init__(self) -> None:
        self.raw_writes = []
        self.chunk_writes = []

    async def write_raw_to_s3(self, path: str, content: bytes) -> None:
        self.raw_writes.append((path, content))

    async def write_raw_chunks_to_s3(
        self,
        path: str,
        chunks,
        *,
        content_type: str | None = None,
    ):
        payload = b"".join([chunk async for chunk in chunks])
        self.chunk_writes.append((path, payload, content_type))
        return "unused", len(payload)

    async def delete_raw_from_s3(self, path: str) -> None:
        return None


@pytest.mark.asyncio
async def test_html_artifacts_are_stored_with_inert_object_content_type(monkeypatch):
    """Direct object-storage URLs must not render HTML as active browser content."""
    storage = _FakeStorage()
    monkeypatch.setattr(
        "src.services.artifacts.get_file_storage_service",
        lambda _db: storage,
    )

    artifact = await ArtifactService(_FakeDB()).store(
        filename="Unsafe.html",
        content_type="text/html; charset=utf-8",
        content=b"<script>alert(document.domain)</script>",
        created_by_user_id=uuid4(),
        organization_id=None,
    )

    assert artifact.content_type == "text/html; charset=utf-8"
    assert storage.raw_writes == []
    assert len(storage.chunk_writes) == 1
    _path, payload, object_content_type = storage.chunk_writes[0]
    assert payload == b"<script>alert(document.domain)</script>"
    assert object_content_type == "application/octet-stream"


@pytest.mark.asyncio
async def test_non_html_artifacts_keep_existing_storage_path(monkeypatch):
    """The HTML hardening must not change ordinary artifact persistence."""
    storage = _FakeStorage()
    monkeypatch.setattr(
        "src.services.artifacts.get_file_storage_service",
        lambda _db: storage,
    )

    await ArtifactService(_FakeDB()).store(
        filename="Notes.md",
        content_type="text/markdown",
        content=b"# Safe",
        created_by_user_id=uuid4(),
        organization_id=None,
    )

    assert len(storage.raw_writes) == 1
    assert storage.raw_writes[0][1] == b"# Safe"
    assert storage.chunk_writes == []
