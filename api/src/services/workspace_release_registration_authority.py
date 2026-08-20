"""Authority guard for mutable Workspace workflow registrations."""

from __future__ import annotations

from enum import StrEnum
from typing import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from src.services.workspace_release_files import (
    global_active_workspace_release_descriptor,
    normalize_release_path,
)
from src.services.workspace_release_projection import acquire_workspace_release_lock


class WorkspaceRegistrationMutationAuthority(StrEnum):
    """Explicit authorities that may mutate Workspace registration rows."""

    EXTERNAL = "external"
    RELEASE_ACTIVATION = "release_activation"


class WorkspaceReleaseRegistrationGoverned(RuntimeError):
    """An external mutation targeted registration state owned by Live."""

    def __init__(self, reference: str, release_id: str, operation: str):
        self.reference = reference
        self.release_id = release_id
        self.operation = operation
        super().__init__(
            f"workflow registration {reference!r} is governed by active "
            f"workspace-release-v1 {release_id}; {operation} must use a reviewed "
            "Workspace release"
        )


async def guard_workspace_registration_mutation(
    db: AsyncSession,
    *,
    operation: str,
    paths: Iterable[str] = (),
    workflows: Iterable[object] = (),
    authority: WorkspaceRegistrationMutationAuthority = (
        WorkspaceRegistrationMutationAuthority.EXTERNAL
    ),
) -> None:
    """Serialize with Live changes and reject external governed mutations.

    Release activation already owns the same global transaction lock and is the
    sole internal writer allowed to apply the immutable registration manifest.
    Every other caller is checked against both governed paths and effective
    registration identities so renaming/repointing cannot escape authority.
    """
    if authority is WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION:
        return
    if authority is not WorkspaceRegistrationMutationAuthority.EXTERNAL:
        raise ValueError(f"unsupported Workspace registration authority: {authority}")

    await acquire_workspace_release_lock(db, None)
    release = await global_active_workspace_release_descriptor(db)
    if release is None:
        return

    governed_paths = set(release.governed_paths)
    effective_ids = {
        str(registration["workflow_id"])
        for registration in release.effective_registrations.values()
    }
    effective_keys = set(release.effective_registrations)

    for path in paths:
        normalized = normalize_release_path(path)
        if normalized in governed_paths:
            raise WorkspaceReleaseRegistrationGoverned(
                normalized, release.release_id, operation
            )

    for workflow in workflows:
        raw_path = getattr(workflow, "path", None)
        path = normalize_release_path(raw_path) if raw_path else None
        function_name = getattr(workflow, "function_name", None)
        workflow_id = getattr(workflow, "id", None)
        reference = (
            f"{path}::{function_name}"
            if path and function_name
            else str(workflow_id or path or "unknown")
        )
        if (
            path in governed_paths
            or str(workflow_id) in effective_ids
            or reference in effective_keys
        ):
            raise WorkspaceReleaseRegistrationGoverned(
                reference, release.release_id, operation
            )


__all__ = [
    "WorkspaceRegistrationMutationAuthority",
    "WorkspaceReleaseRegistrationGoverned",
    "guard_workspace_registration_mutation",
]
