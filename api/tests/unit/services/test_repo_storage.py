from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.services.repo_storage import RepoStorage


def _repo_storage(bucket: str = "repo-bucket") -> RepoStorage:
    storage = RepoStorage.__new__(RepoStorage)
    storage._bucket = bucket
    return storage


class _Body:
    def __init__(self, content: bytes):
        self._content = content

    async def read(self) -> bytes:
        return self._content


@pytest.mark.asyncio
async def test_read_write_delete_use_repo_prefix_and_hash():
    storage = _repo_storage()
    client = AsyncMock()
    client.get_object.return_value = {"Body": _Body(b"payload")}

    assert await storage._read_from_s3(client, "/workflows/demo.py") == b"payload"
    client.get_object.assert_awaited_once_with(
        Bucket="repo-bucket",
        Key="_repo/workflows/demo.py",
    )

    digest = await storage._write_to_s3(client, "apps/demo/app.tsx", b"abc")

    assert digest == RepoStorage.compute_hash(b"abc")
    client.put_object.assert_awaited_once_with(
        Bucket="repo-bucket",
        Key="_repo/apps/demo/app.tsx",
        Body=b"abc",
    )

    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None
    storage._get_client = lambda: context

    await storage.delete("/obsolete.py")
    client.delete_object.assert_awaited_once_with(
        Bucket="repo-bucket",
        Key="_repo/obsolete.py",
    )


@pytest.mark.asyncio
async def test_list_and_metadata_follow_continuation_tokens_and_strip_repo_prefix():
    storage = _repo_storage()
    first_modified = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second_modified = datetime(2026, 1, 2, tzinfo=timezone.utc)
    client = AsyncMock()
    client.list_objects_v2.side_effect = [
        {
            "Contents": [{"Key": "_repo/workflows/a.py"}],
            "IsTruncated": True,
            "NextContinuationToken": "next-page",
        },
        {"Contents": [{"Key": "_repo/workflows/b.py"}], "IsTruncated": False},
        {
            "Contents": [
                {
                    "Key": "_repo/workflows/a.py",
                    "ETag": '"quoted-etag"',
                    "LastModified": first_modified,
                }
            ],
            "IsTruncated": True,
            "NextContinuationToken": "metadata-page",
        },
        {
            "Contents": [
                {
                    "Key": "_repo/workflows/b.py",
                    "ETag": "bare-etag",
                    "LastModified": second_modified,
                }
            ],
            "IsTruncated": False,
        },
    ]

    paths = await storage._list_from_s3(client, "workflows/")
    metadata = await storage._list_with_metadata_from_s3(client, "workflows/")

    assert paths == ["workflows/a.py", "workflows/b.py"]
    assert metadata["workflows/a.py"].etag == "quoted-etag"
    assert metadata["workflows/a.py"].last_modified == first_modified
    assert metadata["workflows/b.py"].etag == "bare-etag"
    assert metadata["workflows/b.py"].last_modified == second_modified
    assert client.list_objects_v2.await_args_list[1].kwargs == {
        "Bucket": "repo-bucket",
        "Prefix": "_repo/workflows/",
        "ContinuationToken": "next-page",
    }
    assert client.list_objects_v2.await_args_list[3].kwargs == {
        "Bucket": "repo-bucket",
        "Prefix": "_repo/workflows/",
        "ContinuationToken": "metadata-page",
    }


@pytest.mark.asyncio
async def test_list_directory_filters_excluded_paths_and_sorts(monkeypatch):
    storage = _repo_storage()
    raw_files = [
        "apps/demo/node_modules/pkg/index.js",
        "apps/demo/z.py",
        "apps/demo/a.py",
    ]
    raw_folders = [
        "apps/demo/build/",
        "apps/demo/components/",
        "apps/demo/api/",
    ]
    storage._list_directory_from_s3 = AsyncMock(return_value=(raw_files, raw_folders))
    client = AsyncMock()
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None
    storage._get_client = lambda: context

    files, folders = await storage.list_directory(
        "apps/demo/",
        exclude_fn=lambda path: "node_modules" in path or path.endswith("build"),
    )

    assert files == ["apps/demo/a.py", "apps/demo/z.py"]
    assert folders == ["apps/demo/api/", "apps/demo/components/"]
    storage._list_directory_from_s3.assert_awaited_once_with(
        client,
        "apps/demo/",
    )


@pytest.mark.asyncio
async def test_list_directory_from_s3_uses_delimiter_and_common_prefixes():
    storage = _repo_storage()
    client = AsyncMock()
    client.list_objects_v2.side_effect = [
        {
            "Contents": [{"Key": "_repo/apps/demo/app.py"}],
            "CommonPrefixes": [{"Prefix": "_repo/apps/demo/lib/"}],
            "IsTruncated": True,
            "NextContinuationToken": "page-2",
        },
        {
            "Contents": [{"Key": "_repo/apps/demo/config.py"}],
            "CommonPrefixes": [{"Prefix": "_repo/apps/demo/views/"}],
            "IsTruncated": False,
        },
    ]

    files, folders = await storage._list_directory_from_s3(client, "apps/demo/")

    assert files == ["apps/demo/app.py", "apps/demo/config.py"]
    assert folders == ["apps/demo/lib/", "apps/demo/views/"]
    assert client.list_objects_v2.await_args_list[0].kwargs == {
        "Bucket": "repo-bucket",
        "Prefix": "_repo/apps/demo/",
        "Delimiter": "/",
    }
    assert client.list_objects_v2.await_args_list[1].kwargs == {
        "Bucket": "repo-bucket",
        "Prefix": "_repo/apps/demo/",
        "Delimiter": "/",
        "ContinuationToken": "page-2",
    }


@pytest.mark.asyncio
async def test_exists_returns_false_when_head_object_raises():
    storage = _repo_storage()
    client = AsyncMock()
    client.head_object.side_effect = RuntimeError("missing")
    context = AsyncMock()
    context.__aenter__.return_value = client
    context.__aexit__.return_value = None
    storage._get_client = lambda: context

    assert await storage.exists("missing.py") is False
    client.head_object.assert_awaited_once_with(
        Bucket="repo-bucket",
        Key="_repo/missing.py",
    )
