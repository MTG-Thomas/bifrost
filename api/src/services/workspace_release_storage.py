"""Read-only access to immutable Workspace release runtime trees."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Any, Callable

from src.config import Settings, get_settings
from src.services.solutions.deployment_storage import CreateOnlyArtifactStorage

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


class WorkspaceReleaseStorage(CreateOnlyArtifactStorage):
    """Create-only writes and reads scoped to one immutable release tree."""

    def __init__(
        self,
        runtime_prefix: str,
        settings: Settings | None = None,
        client_factory: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
    ):
        self.runtime_prefix = normalize_workspace_release_prefix(runtime_prefix)
        super().__init__(
            settings=settings or get_settings(), client_factory=client_factory
        )

    def _key(self, path: str) -> str:
        normalized = path.replace("\\", "/").lstrip("/")
        if not normalized or any(
            part in {"", ".", ".."} for part in normalized.split("/")
        ):
            raise ValueError("invalid Workspace release file path")
        return f"{self.runtime_prefix}{normalized}"

    def object_key(self, path: str) -> str:
        """Return the immutable object key for a verified relative file path."""
        return self._key(path)

    async def read(self, path: str) -> bytes:
        async with self._client_factory() as client:
            response = await client.get_object(Bucket=self._bucket, Key=self._key(path))
            return await response["Body"].read()

    async def write(self, path: str, content: bytes) -> str:
        """Create one release file, accepting only byte-identical retries."""
        key = self._key(path)
        await self._create(
            key,
            content,
            "text/x-python" if path.endswith(".py") else "application/octet-stream",
            idempotent=True,
        )
        return key

    async def write_many(
        self, files: dict[str, bytes], *, concurrency: int = 16
    ) -> dict[str, str]:
        semaphore = asyncio.Semaphore(concurrency)

        async def write_one(path: str, content: bytes) -> tuple[str, str]:
            async with semaphore:
                return path, await self.write(path, content)

        return dict(
            await asyncio.gather(
                *(write_one(path, content) for path, content in sorted(files.items()))
            )
        )

    async def read_many(
        self, paths: list[str], *, concurrency: int = 32
    ) -> dict[str, bytes]:
        """Read a bounded set of release files through one storage client."""
        semaphore = asyncio.Semaphore(concurrency)
        async with self._client_factory() as client:

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
        async with self._client_factory() as client:
            while True:
                kwargs = {"Bucket": self._bucket, "Prefix": full_prefix}
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token
                response = await client.list_objects_v2(**kwargs)
                for item in response.get("Contents", []):
                    key = item.get("Key")
                    if isinstance(key, str):
                        paths.append(key[len(self.runtime_prefix) :])
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
