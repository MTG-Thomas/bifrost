from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.services.file_storage.s3_client import S3StorageClient


def _settings(**overrides):
    values = {
        "s3_bucket": "files",
        "s3_endpoint_url": "http://minio:9000",
        "s3_access_key": "access",
        "s3_secret_key": "secret",
        "s3_region": "us-east-1",
        "s3_public_endpoint_url": None,
        "s3_configured": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_hash_content_type_and_public_presigned_url_rewrite() -> None:
    client = S3StorageClient(_settings(s3_public_endpoint_url="/s3"))

    assert (
        client.compute_hash(b"hello")
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    assert client.guess_content_type("report.csv") == "text/csv"
    assert client.guess_content_type("blob.unknownext") == "application/octet-stream"
    assert (
        client._rewrite_presigned_url(
            "http://minio:9000/files/uploads/report.csv?X-Amz-Signature=abc"
        )
        == "/s3/files/uploads/report.csv?X-Amz-Signature=abc"
    )


@pytest.mark.asyncio
async def test_generate_presigned_upload_url_uses_bucket_key_and_content_type() -> None:
    calls = []
    client = S3StorageClient(_settings(s3_public_endpoint_url="/s3"))

    class FakeS3:
        async def generate_presigned_url(self, operation, Params, ExpiresIn):
            calls.append((operation, Params, ExpiresIn))
            return "http://minio:9000/files/uploads/a.txt?sig=1"

    @asynccontextmanager
    async def fake_get_client():
        yield FakeS3()

    client.get_client = fake_get_client

    url = await client.generate_presigned_upload_url(
        "uploads/a.txt",
        "text/plain",
        expires_in=60,
    )

    assert url == "/s3/files/uploads/a.txt?sig=1"
    assert calls == [
        (
            "put_object",
            {"Bucket": "files", "Key": "uploads/a.txt", "ContentType": "text/plain"},
            60,
        )
    ]
    assert client.presigned_upload_headers("text/plain") == {"Content-Type": "text/plain"}


@pytest.mark.asyncio
async def test_put_object_from_chunks_uses_single_put_for_small_stream() -> None:
    client = S3StorageClient(_settings())
    calls = []

    class FakeS3:
        async def put_object(self, **kwargs):
            calls.append(kwargs)

    @asynccontextmanager
    async def fake_get_client():
        yield FakeS3()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"hello"
        yield b""
        yield b" world"

    client.get_client = fake_get_client

    digest, size = await client.put_object_from_chunks(
        "uploads/greeting.txt",
        chunks(),
        content_type="text/plain",
        part_size=100,
    )

    assert size == 11
    assert digest == client.compute_hash(b"hello world")
    assert calls == [
        {
            "Bucket": "files",
            "Key": "uploads/greeting.txt",
            "Body": b"hello world",
            "ContentType": "text/plain",
        }
    ]
