"""CLI sync should compare normalized content hashes, not storage ETags."""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone
from typing import Any

import pytest

from bifrost import cli


class _Response:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._body


class _SyncClient:
    def __init__(
        self,
        *,
        server_path: str,
        content_hash: str | None,
        storage_etag: str,
        last_modified: str | None = None,
    ) -> None:
        self.server_path = server_path
        self.content_hash = content_hash
        self.storage_etag = storage_etag
        self.last_modified = last_modified or datetime(2000, 1, 1, tzinfo=timezone.utc).isoformat()
        self.writes: list[dict[str, Any]] = []

    async def post(self, endpoint: str, json: dict[str, Any]) -> _Response:
        if endpoint == "/api/files/list":
            item = {
                "path": self.server_path,
                "etag": self.storage_etag,
                "last_modified": self.last_modified,
                "updated_by": "tester",
            }
            if self.content_hash is not None:
                item["content_hash"] = self.content_hash
            return _Response(200, {"files_metadata": [item]})
        if endpoint == "/api/files/write":
            self.writes.append(json)
            return _Response(204)
        raise AssertionError(f"unexpected endpoint: {endpoint}")


@pytest.fixture
def workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path


@pytest.mark.asyncio
async def test_sync_skips_unchanged_when_content_hash_matches(workspace: pathlib.Path) -> None:
    rel = "example.py"
    local_bytes = b"print('same')\n"
    (workspace / rel).write_bytes(local_bytes)
    content_hash = cli._hash_for_cache(local_bytes)

    client = _SyncClient(
        server_path=rel,
        content_hash=content_hash,
        storage_etag="azure-opaque-etag",
    )

    rc = await cli._sync_files(str(workspace), force=True, client=client)

    assert rc == 0
    assert client.writes == []


@pytest.mark.asyncio
async def test_sync_pushes_when_content_hash_differs_even_if_storage_etag_matches(
    workspace: pathlib.Path,
) -> None:
    rel = "example.py"
    local_bytes = b"print('local')\n"
    (workspace / rel).write_bytes(local_bytes)
    stale_hash = cli._hash_for_cache(b"print('server')\n")

    client = _SyncClient(
        server_path=rel,
        content_hash=stale_hash,
        storage_etag=cli._hash_for_cache(local_bytes),
    )

    rc = await cli._sync_files(str(workspace), force=True, client=client)

    assert rc == 0
    assert [write["path"] for write in client.writes] == [rel]


@pytest.mark.asyncio
async def test_sync_pushes_prefixed_path_when_content_hash_differs(
    workspace: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rel = "example.py"
    repo_rel = f"tmp/{rel}"
    local_bytes = b"print('local v2')\n"
    (workspace / rel).write_bytes(local_bytes)
    stale_hash = cli._hash_for_cache(b"print('server v1')\n")

    client = _SyncClient(
        server_path=repo_rel,
        content_hash=stale_hash,
        storage_etag=cli._hash_for_cache(local_bytes),
    )
    monkeypatch.setattr(cli, "_detect_repo_prefix", lambda path: "tmp")

    rc = await cli._sync_files(str(workspace), force=True, client=client)

    assert rc == 0
    assert [write["path"] for write in client.writes] == [repo_rel]
