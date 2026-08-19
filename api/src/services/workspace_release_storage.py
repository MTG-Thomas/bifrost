"""Read-only access to immutable Workspace release runtime trees."""

from __future__ import annotations

import asyncio

from src.config import Settings, get_settings

WORKSPACE_RELEASES_ROOT = "_workspace_releases"


def normalize_workspace_release_prefix(prefix: str) -> str:
    """Validate an object-storage prefix for one immutable runtime tree."""
    value = prefix.replace("\\", "/").strip("/")
    parts = value.split("/")
    if (
        len(parts) < 4
        or parts[0] != WORKSPACE_RELEASES_ROOT
        or parts[-1] != "files"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("invalid Workspace release runtime prefix")
    return value + "/"


class WorkspaceReleaseStorage:
    """Object reads scoped to one create-only Workspace release tree."""

    def __init__(self, runtime_prefix: str, settings: Settings | None = None):
        self.runtime_prefix = normalize_workspace_release_prefix(runtime_prefix)
        self._settings = settings or get_settings()
        if self._settings.object_storage_provider == "azure_blob":
            from src.services.file_storage.azure_blob_client import (
                AzureBlobStorageClient,
            )

            self._storage = AzureBlobStorageClient(self._settings)
            self._bucket = self._settings.azure_blob_container or ""
        else:
            from src.services.file_storage.s3_client import S3StorageClient

            self._storage = S3StorageClient(self._settings)
            self._bucket = self._settings.s3_bucket or ""

    def _key(self, path: str) -> str:
        normalized = path.replace("\\", "/").lstrip("/")
        if not normalized or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        ):
            raise ValueError("invalid Workspace release file path")
        return f"{self.runtime_prefix}{normalized}"

    async def read(self, path: str) -> bytes:
        async with self._storage.get_client() as client:
            response = await client.get_object(Bucket=self._bucket, Key=self._key(path))
            return await response["Body"].read()

    async def read_many(
        self, paths: list[str], *, concurrency: int = 32
    ) -> dict[str, bytes]:
        """Read a bounded set of release files through one storage client."""
        semaphore = asyncio.Semaphore(concurrency)
        async with self._storage.get_client() as client:

            async def read_one(path: str) -> tuple[str, bytes]:
                async with semaphore:
                    response = await client.get_object(
                        Bucket=self._bucket, Key=self._key(path)
                    )
                    return path, await response["Body"].read()

            return dict(await asyncio.gather(*(read_one(path) for path in paths)))

    async def list(self, prefix: str = "") -> list[str]:
        normalized = prefix.replace("\\", "/").lstrip("/")
        if normalized and any(
            part in {"", ".", ".."} for part in normalized.split("/")
        ):
            raise ValueError("invalid Workspace release list prefix")
        full_prefix = f"{self.runtime_prefix}{normalized}"
        paths: list[str] = []
        continuation_token = None
        async with self._storage.get_client() as client:
            while True:
                kwargs = {"Bucket": self._bucket, "Prefix": full_prefix}
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token
                response = await client.list_objects_v2(**kwargs)
                paths.extend(
                    item["Key"][len(self.runtime_prefix) :]
                    for item in response.get("Contents", [])
                )
                if not response.get("IsTruncated"):
                    break
                continuation_token = response.get("NextContinuationToken")
        return paths


def split_workspace_release_storage_path(path: str) -> tuple[str, str]:
    """Split a fully rooted release object into runtime prefix and relative path."""
    normalized = path.replace("\\", "/").lstrip("/")
    marker = "/files/"
    prefix, separator, relative = normalized.partition(marker)
    if not separator or not relative:
        raise ValueError("invalid Workspace release storage path")
    runtime_prefix = normalize_workspace_release_prefix(f"{prefix}/files")
    return runtime_prefix, relative
