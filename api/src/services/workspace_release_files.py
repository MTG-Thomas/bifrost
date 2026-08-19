"""Release-aware reads and legacy-mutation guards for Workspace source paths."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)

from src.services.workspace_release_runtime import (
    WorkspaceReleaseDescriptor,
    WorkspaceReleaseRuntimeError,
)
from src.services.workspace_release_storage import WorkspaceReleaseStorage


class WorkspaceReleasePathGoverned(RuntimeError):
    """A legacy mutation targeted source owned by immutable Live."""

    def __init__(self, path: str, release_id: str):
        self.path = path
        self.release_id = release_id
        super().__init__(
            f"path {path!r} is governed by active workspace-release-v1 "
            f"{release_id}; use `bifrost promote` to change reviewed Live source"
        )


def normalize_release_path(path: str) -> str:
    value = path.replace("\\", "/").strip("/")
    parts = PurePosixPath(value).parts
    if not value or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Workspace path must be relative and cannot contain '..'")
    return value


@dataclass(frozen=True)
class WorkspaceReleaseFileView:
    """Verified immutable file view for the platform-global active release."""

    release: WorkspaceReleaseDescriptor
    storage: WorkspaceReleaseStorage

    @classmethod
    def from_release(
        cls,
        release: WorkspaceReleaseDescriptor,
        *,
        storage: WorkspaceReleaseStorage | None = None,
    ) -> "WorkspaceReleaseFileView":
        return cls(
            release=release,
            storage=storage or WorkspaceReleaseStorage(release.runtime_storage_prefix),
        )

    def governs(self, path: str) -> bool:
        return normalize_release_path(path) in self.release.source_hashes

    async def read(self, path: str) -> bytes:
        normalized = normalize_release_path(path)
        expected = self.release.source_hashes.get(normalized)
        if expected is None:
            raise FileNotFoundError(normalized)
        raw = await self.storage.read(normalized)
        observed = hashlib.sha256(raw).hexdigest()
        if observed != expected:
            raise WorkspaceReleaseRuntimeError(
                f"immutable Workspace release bytes do not match {normalized}"
            )
        return raw

    async def read_many(
        self, paths: list[str], *, concurrency: int = 32
    ) -> dict[str, bytes]:
        normalized = [normalize_release_path(path) for path in paths]
        missing = [
            path for path in normalized if path not in self.release.source_hashes
        ]
        if missing:
            raise FileNotFoundError(missing[0])
        rows = await self.storage.read_many(normalized, concurrency=concurrency)
        for path in normalized:
            raw = rows.get(path)
            expected = self.release.source_hashes[path]
            if raw is None or hashlib.sha256(raw).hexdigest() != expected:
                raise WorkspaceReleaseRuntimeError(
                    f"immutable Workspace release bytes do not match {path}"
                )
        return rows

    async def list(self, prefix: str = "") -> list[str]:
        normalized = prefix.replace("\\", "/").lstrip("/")
        if normalized and ".." in PurePosixPath(normalized).parts:
            raise ValueError("Workspace prefix cannot contain '..'")
        return [
            path
            for path in sorted(self.release.source_hashes)
            if path.startswith(normalized)
        ]


async def active_workspace_release_file_view(
    session: AsyncSession,
    organization_id: UUID | None,
) -> WorkspaceReleaseFileView | None:
    """Resolve the one platform-global immutable Live Workspace tree.

    ``organization_id`` remains in the signature for call-site compatibility,
    but shared Workspace source is global.  Authorization may be org-scoped;
    source authority must not be.
    """
    del organization_id
    release = await global_active_workspace_release_descriptor(session)
    return (
        WorkspaceReleaseFileView.from_release(release) if release is not None else None
    )


async def global_active_workspace_release_descriptor(
    session: AsyncSession,
) -> WorkspaceReleaseDescriptor | None:
    """Resolve the single platform-global Live pointer used by shared projections."""
    rows = (
        await session.execute(
            select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
            .join(
                WorkspacePromotionArtifact,
                WorkspacePromotionArtifact.id == WorkspacePromotionRelease.artifact_id,
            )
            .where(WorkspacePromotionRelease.activation_state == "live")
            .limit(2)
        )
    ).all()
    if not rows:
        return None
    if len(rows) != 1:
        raise WorkspaceReleaseRuntimeError(
            "platform has more than one global Live Workspace release"
        )
    return WorkspaceReleaseDescriptor.from_rows(*rows[0])


async def governed_workspace_release_file_view(
    session: AsyncSession,
    organization_id: UUID | None,
    path: str,
) -> WorkspaceReleaseFileView | None:
    view = await active_workspace_release_file_view(session, organization_id)
    return view if view is not None and view.governs(path) else None


async def reject_release_governed_paths(
    session: AsyncSession,
    organization_id: UUID | None,
    paths: Iterable[str],
) -> None:
    from src.services.workspace_release_projection import (
        acquire_workspace_release_lock,
    )

    await acquire_workspace_release_lock(session, organization_id)
    release = await global_active_workspace_release_descriptor(session)
    if release is None:
        return
    for path in paths:
        normalized = normalize_release_path(path)
        if normalized in release.source_hashes:
            raise WorkspaceReleasePathGoverned(normalized, release.release_id)


async def reject_release_governed_prefixes(
    session: AsyncSession,
    organization_id: UUID | None,
    prefixes: Iterable[str],
) -> None:
    """Reject a legacy recursive mutation intersecting immutable Live source."""
    from src.services.workspace_release_projection import (
        acquire_workspace_release_lock,
    )

    await acquire_workspace_release_lock(session, organization_id)
    release = await global_active_workspace_release_descriptor(session)
    if release is None:
        return
    for prefix in prefixes:
        normalized = normalize_release_path(prefix)
        marker = normalized + "/"
        for governed_path in release.source_hashes:
            if governed_path == normalized or governed_path.startswith(marker):
                raise WorkspaceReleasePathGoverned(governed_path, release.release_id)


__all__ = [
    "WorkspaceReleaseFileView",
    "WorkspaceReleasePathGoverned",
    "active_workspace_release_file_view",
    "governed_workspace_release_file_view",
    "global_active_workspace_release_descriptor",
    "normalize_release_path",
    "reject_release_governed_paths",
    "reject_release_governed_prefixes",
]
