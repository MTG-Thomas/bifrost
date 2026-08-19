"""Idempotent compatibility and signed-history projection for immutable Live."""

from __future__ import annotations

import base64
import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.workspace_release import canonical_digest, workspace_manifest_id
from src.core.module_cache import inspect_module_coherence, workspace_source_update
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.services.file_storage import FileStorageService
from src.services.platform_commit_writer import (
    PlatformCommitError,
    PlatformCommitFile,
    PlatformCommitRequest,
    PlatformCommitWriter,
)
from src.services.repo_storage import RepoStorage
from src.services.workspace_release_runtime import (
    WorkspaceReleaseDescriptor,
    WorkspaceReleaseRuntimeError,
)
from src.services.workspace_release_storage import WorkspaceReleaseStorage

WORKSPACE_RELEASE_LOCK_EVIDENCE_SCHEMA = "bifrost.workspace-release-lock/v1"
PROJECTION_PATHS_SCHEMA = "bifrost.workspace-release-projection-paths/v1"
ProgressReporter = Callable[[str, int, int | None, float | None], Awaitable[None]]


class ProjectionFileStorage(Protocol):
    async def write_file(
        self,
        path: str,
        content: bytes,
        *,
        updated_by: str,
        skip_dirty_flag: bool,
    ) -> Any:
        raise NotImplementedError


@dataclass(frozen=True)
class WorkspaceReleaseProjectionPath:
    path: str
    base_sha256: str | None
    target_sha256: str


@dataclass(frozen=True)
class WorkspaceReleasePathState:
    path: str
    base_sha256: str | None
    target_sha256: str
    observed_sha256: str | None
    disposition: str


class WorkspaceReleaseProjectionError(RuntimeError):
    """A compatibility projection could not be proven safe."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        evidence: dict[str, Any] | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.evidence = evidence or {}
        self.retryable = retryable


class _ReleaseSuperseded(RuntimeError):
    def __init__(self, observed_state: str):
        super().__init__(observed_state)
        self.observed_state = observed_state


async def acquire_workspace_release_lock(
    db: AsyncSession, organization_id: UUID
) -> None:
    """Serialize activation and projection for the one global Workspace Live."""
    del organization_id  # The compatibility _repo and production-live are global.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('bifrost:workspace-release'))"),
    )


def classify_workspace_release_path(
    path: WorkspaceReleaseProjectionPath, observed_sha256: str | None
) -> WorkspaceReleasePathState:
    if observed_sha256 == path.target_sha256:
        disposition = "target"
    elif observed_sha256 == path.base_sha256:
        disposition = "base"
    else:
        disposition = "other"
    return WorkspaceReleasePathState(
        path=path.path,
        base_sha256=path.base_sha256,
        target_sha256=path.target_sha256,
        observed_sha256=observed_sha256,
        disposition=disposition,
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _projection_paths(
    release: WorkspacePromotionRelease,
    artifact: WorkspacePromotionArtifact,
    descriptor: WorkspaceReleaseDescriptor,
) -> tuple[WorkspaceReleaseProjectionPath, ...]:
    prepared = release.prepared_evidence
    activation = release.activation_evidence
    if not isinstance(prepared, dict) or not isinstance(activation, dict):
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release is missing prepared or activation evidence.",
        )
    prepared_without_id = dict(prepared)
    prepared_evidence_id = prepared_without_id.pop("evidence_id", None)
    activation_without_id = dict(activation)
    activation_evidence_id = activation_without_id.pop("evidence_id", None)
    if (
        not isinstance(prepared_evidence_id, str)
        or canonical_digest(prepared_without_id) != prepared_evidence_id
        or not isinstance(activation_evidence_id, str)
        or canonical_digest(activation_without_id) != activation_evidence_id
    ):
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release prepared or activation evidence digest is invalid.",
        )
    activation_projection = activation.get("projection_paths")
    if activation.get("prepared_evidence_id") != prepared_evidence_id or not isinstance(
        activation_projection, dict
    ):
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release activation does not reference its prepared evidence.",
        )
    raw_paths = activation_projection.get("paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release prepared evidence is missing projection paths.",
        )
    closure = artifact.manifest.get("closure")
    if not isinstance(closure, list):
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release artifact closure is invalid.",
        )
    closure_targets: dict[str, str] = {}
    for item in closure:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not isinstance(item.get("sha256"), str)
            or item["path"] in closure_targets
        ):
            raise WorkspaceReleaseProjectionError(
                "workspace_release_projection_invalid",
                "Workspace release artifact closure is invalid.",
            )
        closure_targets[item["path"]] = item["sha256"]
    parsed: list[WorkspaceReleaseProjectionPath] = []
    for item in raw_paths:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "base_sha256",
            "target_sha256",
        }:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_projection_invalid",
                "Workspace release projection path evidence is invalid.",
            )
        path = item["path"]
        base_sha256 = item["base_sha256"]
        target_sha256 = item["target_sha256"]
        if (
            not isinstance(path, str)
            or path not in closure_targets
            or not isinstance(target_sha256, str)
            or target_sha256 != closure_targets.get(path)
            or descriptor.source_hashes.get(path) != target_sha256
            or (base_sha256 is not None and not isinstance(base_sha256, str))
            or any(
                digest is not None
                and (
                    len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)
                )
                for digest in (base_sha256, target_sha256)
            )
        ):
            raise WorkspaceReleaseProjectionError(
                "workspace_release_projection_invalid",
                "Workspace release projection hashes are invalid.",
            )
        parsed.append(
            WorkspaceReleaseProjectionPath(
                path=path,
                base_sha256=base_sha256,
                target_sha256=target_sha256,
            )
        )
    if (
        len(parsed) != len(closure_targets)
        or [item.path for item in parsed] != sorted(closure_targets)
        or activation_projection.get("projection_paths_id")
        != canonical_digest({"schema": PROJECTION_PATHS_SCHEMA, "paths": raw_paths})
        or prepared.get("projection_paths") != raw_paths
    ):
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release projection paths do not match the activated reviewed closure.",
        )
    reconstructed_base = dict(descriptor.source_hashes)
    for item in parsed:
        if item.base_sha256 is None:
            reconstructed_base.pop(item.path, None)
        else:
            reconstructed_base[item.path] = item.base_sha256
    if workspace_manifest_id(reconstructed_base) != artifact.base_manifest_id:
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release projection paths do not reconstruct the immutable base.",
        )
    return tuple(sorted(parsed, key=lambda item: item.path))


class WorkspaceReleaseProjectionService:
    """Project one immutable Live release without making projection authoritative."""

    def __init__(
        self,
        db: AsyncSession,
        organization_id: UUID,
        *,
        commit_writer: PlatformCommitWriter | None,
        repo_storage: RepoStorage | None = None,
        release_storage_factory: Callable[[str], WorkspaceReleaseStorage] = (
            WorkspaceReleaseStorage
        ),
        file_storage_factory: Callable[[AsyncSession], ProjectionFileStorage] = (
            FileStorageService
        ),
        coherence_inspector: Callable[
            [dict[str, str]], Awaitable[tuple[str, list[Any]]]
        ] = inspect_module_coherence,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.commit_writer = commit_writer
        self.repo_storage = repo_storage or RepoStorage()
        self.release_storage_factory = release_storage_factory
        self.file_storage_factory = file_storage_factory
        self.coherence_inspector = coherence_inspector

    async def lock_release(
        self,
        release_row_id: UUID,
        expected_release_id: str,
        *,
        operator: str,
        report: ProgressReporter | None = None,
    ) -> dict[str, Any]:
        await acquire_workspace_release_lock(self.db, self.organization_id)
        release, artifact = await self._load_release(release_row_id)
        if release.activation_state != "live":
            return await self._mark_superseded(
                release, expected_release_id, release.activation_state
            )
        release.lock_state = "in_progress"
        release.error_code = None
        release.error_message = None
        await self.db.flush()
        try:
            descriptor = self._descriptor(release, artifact, expected_release_id)
            evidence = await self._project(
                release, artifact, descriptor, operator, report=report
            )
        except _ReleaseSuperseded as exc:
            return await self._mark_superseded(
                release, expected_release_id, exc.observed_state
            )
        except WorkspaceReleaseProjectionError as exc:
            release.lock_state = "attention_required"
            release.error_code = exc.code
            release.error_message = str(exc)
            release.lock_evidence = {
                "schema_version": WORKSPACE_RELEASE_LOCK_EVIDENCE_SCHEMA,
                "release_row_id": str(release.id),
                "release_id": expected_release_id,
                "state": "attention_required",
                "live_preserved": release.activation_state == "live",
                "error_code": exc.code,
                "error_message": str(exc),
                **exc.evidence,
            }
            release.lock_evidence["evidence_id"] = canonical_digest(
                release.lock_evidence
            )
            await self.db.flush()
            await self.db.commit()
            raise
        except Exception as exc:
            wrapped = WorkspaceReleaseProjectionError(
                "workspace_release_projection_failed",
                str(exc),
                evidence={"phase": "projection"},
                retryable=True,
            )
            release.lock_state = "attention_required"
            release.error_code = wrapped.code
            release.error_message = str(wrapped)
            release.lock_evidence = {
                "schema_version": WORKSPACE_RELEASE_LOCK_EVIDENCE_SCHEMA,
                "release_row_id": str(release.id),
                "release_id": expected_release_id,
                "state": "attention_required",
                "live_preserved": release.activation_state == "live",
                "error_code": wrapped.code,
                "error_message": str(wrapped),
                **wrapped.evidence,
            }
            release.lock_evidence["evidence_id"] = canonical_digest(
                release.lock_evidence
            )
            await self.db.flush()
            await self.db.commit()
            raise wrapped from exc
        release.lock_state = "locked"
        release.lock_evidence = evidence
        release.error_code = None
        release.error_message = None
        await self.db.flush()
        await self.db.commit()
        return evidence

    async def _project(
        self,
        release: WorkspacePromotionRelease,
        artifact: WorkspacePromotionArtifact,
        descriptor: WorkspaceReleaseDescriptor,
        operator: str,
        *,
        report: ProgressReporter | None,
    ) -> dict[str, Any]:
        projection_paths = _projection_paths(release, artifact, descriptor)
        paths = tuple(item.path for item in projection_paths)
        await self._ensure_still_live(release.id)
        immutable = await self.release_storage_factory(
            descriptor.runtime_storage_prefix
        ).read_many(list(paths))
        invalid_targets = [
            item.path
            for item in projection_paths
            if item.path not in immutable
            or _sha256(immutable[item.path]) != item.target_sha256
        ]
        if invalid_targets:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_immutable_readback_failed",
                "Immutable release readback failed for: " + ", ".join(invalid_targets),
                evidence={"phase": "immutable_readback"},
            )
        repo_hashes = await self._repo_hashes(paths)
        repo_states = tuple(
            classify_workspace_release_path(item, repo_hashes.get(item.path))
            for item in projection_paths
        )
        writer = self.commit_writer
        if writer is None:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_history_unconfigured",
                "Verified GitHub App history writer is not configured.",
                evidence={
                    "phase": "classification",
                    "repo_paths": [asdict(item) for item in repo_states],
                },
            )
        try:
            history_before = await writer.inspect(paths)
        except PlatformCommitError as exc:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_history_inspection_failed",
                str(exc),
                evidence={
                    "phase": "history_classification",
                    "repo_paths": [asdict(item) for item in repo_states],
                },
                retryable=True,
            ) from exc
        if history_before.signature_state != "VALID":
            raise WorkspaceReleaseProjectionError(
                "workspace_release_history_unsigned",
                "production-live history head is not a verified GitHub-signed commit.",
                evidence={"phase": "history_classification"},
            )
        history_states = tuple(
            classify_workspace_release_path(
                item, history_before.file_sha256.get(item.path)
            )
            for item in projection_paths
        )
        divergent = [
            f"repo:{item.path}" for item in repo_states if item.disposition == "other"
        ] + [
            f"history:{item.path}"
            for item in history_states
            if item.disposition == "other"
        ]
        classification_evidence = {
            "repo_paths": [asdict(item) for item in repo_states],
            "history_before": {
                "commit_sha": history_before.commit_sha,
                "tree_sha": history_before.tree_sha,
                "signature_state": history_before.signature_state,
                "paths": [asdict(item) for item in history_states],
            },
        }
        if divergent:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_projection_diverged",
                "Projection found bytes outside the immutable base/target states for: "
                + ", ".join(divergent),
                evidence={"phase": "classification", **classification_evidence},
            )
        if report:
            await report("Workspace projection classified", 1, 3, 30)

        repo_writes = [item for item in repo_states if item.disposition == "base"]
        await self._ensure_still_live(release.id)
        storage = self.file_storage_factory(self.db)
        async with workspace_source_update(
            reason="workspace_release_projection",
            changed_paths=list(paths),
            broadcast=True,
        ):
            for item in repo_writes:
                await self._ensure_still_live(release.id)
                result = await storage.write_file(
                    item.path,
                    immutable[item.path],
                    updated_by=f"workspace-release:{operator}",
                    skip_dirty_flag=True,
                )
                if getattr(result, "pending_deactivations", None):
                    raise WorkspaceReleaseProjectionError(
                        "workspace_release_projection_metadata_changed",
                        "Compatibility projection changed deactivation intent for "
                        f"{item.path}.",
                        evidence={
                            "phase": "repo_projection",
                            **classification_evidence,
                        },
                    )
            # The context exit rotates the shared generation and repairs cache state.
            # Fence that external mutation just as tightly as each durable write.
            await self._ensure_still_live(release.id)

        repo_after = await self._repo_hashes(paths)
        repo_mismatches = [
            item.path
            for item in projection_paths
            if repo_after.get(item.path) != item.target_sha256
        ]
        generation, cache_rows = await self.coherence_inspector(
            {item.path: item.target_sha256 for item in projection_paths}
        )
        cache_evidence = [
            item.to_dict() if hasattr(item, "to_dict") else asdict(item)
            for item in cache_rows
        ]
        cache_by_path = {str(item.get("path")): item for item in cache_evidence}
        cache_mismatches = []
        for item in projection_paths:
            observed = cache_by_path.get(item.path)
            if (
                observed is None
                or observed.get("coherent") is not True
                or observed.get("indexed") is not True
                or observed.get("durable_sha256") != item.target_sha256
                or observed.get("cache_sha256") != item.target_sha256
                or observed.get("cache_generation") != generation
                or observed.get("workspace_generation") != generation
            ):
                cache_mismatches.append(item.path)
        if repo_mismatches or cache_mismatches:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_repo_readback_failed",
                "Compatibility projection did not converge for: "
                + ", ".join(sorted(set(repo_mismatches + cache_mismatches))),
                evidence={
                    "phase": "repo_readback",
                    **classification_evidence,
                    "workspace_generation": generation,
                    "cache": cache_evidence,
                },
                retryable=True,
            )
        if report:
            await report("Compatibility Workspace projection verified", 2, 3, 65)

        history_writes = [item for item in history_states if item.disposition == "base"]
        if history_writes:
            await self._ensure_still_live(release.id)
            try:
                await writer.write(
                    PlatformCommitRequest(
                        commit_message=(
                            "Lock immutable Workspace release "
                            f"{descriptor.release_id.removeprefix('sha256:')[:12]}"
                        ),
                        operator=operator,
                        changeset_id=release.id,
                        files=tuple(
                            PlatformCommitFile(
                                path=item.path,
                                content_base64=base64.b64encode(
                                    immutable[item.path]
                                ).decode("ascii"),
                                expected_before_sha256=item.base_sha256,
                                expected_sha256=item.target_sha256,
                            )
                            for item in history_writes
                        ),
                        plan_id=artifact.candidate_id,
                        protected_main_source_sha=descriptor.source_commit_sha,
                        expected_head_sha=history_before.commit_sha,
                        workspace_release_id=descriptor.release_id,
                        workspace_release_row_id=release.id,
                    )
                )
            except PlatformCommitError as exc:
                raise WorkspaceReleaseProjectionError(
                    "workspace_release_history_write_failed",
                    str(exc),
                    evidence={
                        "phase": "history_projection",
                        **classification_evidence,
                        "candidate_commit_sha": exc.commit_sha,
                    },
                    retryable=True,
                ) from exc

        try:
            history_after = await writer.inspect(paths)
        except PlatformCommitError as exc:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_history_readback_failed",
                str(exc),
                evidence={"phase": "history_readback", **classification_evidence},
                retryable=True,
            ) from exc
        history_mismatches = [
            item.path
            for item in projection_paths
            if history_after.file_sha256.get(item.path) != item.target_sha256
        ]
        if history_after.signature_state != "VALID" or history_mismatches:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_history_readback_failed",
                "Signed production-live readback differs from immutable Live.",
                evidence={
                    "phase": "history_readback",
                    **classification_evidence,
                    "history_after_commit_sha": history_after.commit_sha,
                    "history_after_signature_state": history_after.signature_state,
                    "history_mismatches": history_mismatches,
                },
                retryable=True,
            )
        if report:
            await report("Signed production-live projection verified", 3, 3, 95)
        await self._ensure_still_live(release.id)
        prepared_evidence = release.prepared_evidence or {}
        activation_evidence = release.activation_evidence or {}
        activated_projection = activation_evidence.get("projection_paths") or {}
        evidence = {
            "schema_version": WORKSPACE_RELEASE_LOCK_EVIDENCE_SCHEMA,
            "release_row_id": str(release.id),
            "artifact_id": str(artifact.id),
            "release_id": descriptor.release_id,
            "effective_manifest_id": descriptor.effective_manifest_id,
            "prepared_evidence_id": prepared_evidence["evidence_id"],
            "activation_evidence_id": activation_evidence["evidence_id"],
            "projection_paths_id": activated_projection["projection_paths_id"],
            "state": "locked",
            "live_preserved": True,
            **classification_evidence,
            "repo_after_sha256": repo_after,
            "workspace_generation": generation,
            "cache": cache_evidence,
            "history_after": {
                "commit_sha": history_after.commit_sha,
                "tree_sha": history_after.tree_sha,
                "signature_state": history_after.signature_state,
                "file_sha256": history_after.file_sha256,
            },
            "locked_at": datetime.now(timezone.utc).isoformat(),
        }
        evidence["evidence_id"] = canonical_digest(evidence)
        return evidence

    async def _repo_hashes(self, paths: tuple[str, ...]) -> dict[str, str | None]:
        existing = set(await self.repo_storage.list())
        selected = [path for path in paths if path in existing]
        read = (
            await self.repo_storage.read_many(selected, concurrency=16)
            if selected
            else {}
        )
        if set(read) != set(selected):
            raise WorkspaceReleaseProjectionError(
                "workspace_release_repo_inspection_failed",
                "Durable Workspace projection readback was incomplete.",
                retryable=True,
            )
        return {path: _sha256(read[path]) if path in read else None for path in paths}

    async def _load_release(
        self, release_row_id: UUID
    ) -> tuple[WorkspacePromotionRelease, WorkspacePromotionArtifact]:
        row = (
            await self.db.execute(
                select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
                .join(
                    WorkspacePromotionArtifact,
                    WorkspacePromotionArtifact.id
                    == WorkspacePromotionRelease.artifact_id,
                )
                .where(
                    WorkspacePromotionRelease.id == release_row_id,
                    WorkspacePromotionRelease.organization_id == self.organization_id,
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_missing",
                "Workspace release does not exist in this organization.",
            )
        return row[0], row[1]

    @staticmethod
    def _descriptor(
        release: WorkspacePromotionRelease,
        artifact: WorkspacePromotionArtifact,
        expected_release_id: str,
    ) -> WorkspaceReleaseDescriptor:
        try:
            descriptor = WorkspaceReleaseDescriptor.from_rows(release, artifact)
        except WorkspaceReleaseRuntimeError as exc:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_projection_invalid", str(exc)
            ) from exc
        if descriptor.release_id != expected_release_id:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_identity_mismatch",
                "Workspace release digest differs from the queued lock job.",
            )
        return descriptor

    async def _ensure_still_live(self, release_row_id: UUID) -> None:
        live_ids = (
            (
                await self.db.execute(
                    select(WorkspacePromotionRelease.id)
                    .where(
                        WorkspacePromotionRelease.activation_state == "live",
                    )
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        if release_row_id not in live_ids:
            raise _ReleaseSuperseded("superseded")
        if live_ids != [release_row_id]:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_multiple_live",
                "More than one immutable Workspace release is marked Live.",
                evidence={
                    "phase": "live_fence",
                    "observed_live_release_ids": [str(value) for value in live_ids],
                },
            )

    async def _mark_superseded(
        self,
        release: WorkspacePromotionRelease,
        expected_release_id: str,
        observed_state: str,
    ) -> dict[str, Any]:
        evidence = {
            "schema_version": WORKSPACE_RELEASE_LOCK_EVIDENCE_SCHEMA,
            "release_row_id": str(release.id),
            "release_id": expected_release_id,
            "state": "superseded",
            "live_preserved": False,
            "observed_activation_state": observed_state,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        evidence["evidence_id"] = canonical_digest(evidence)
        release.lock_state = "superseded"
        release.lock_evidence = evidence
        release.error_code = None
        release.error_message = None
        await self.db.flush()
        await self.db.commit()
        return evidence


__all__ = [
    "WORKSPACE_RELEASE_LOCK_EVIDENCE_SCHEMA",
    "WorkspaceReleaseProjectionError",
    "WorkspaceReleaseProjectionPath",
    "WorkspaceReleaseProjectionService",
    "WorkspaceReleasePathState",
    "acquire_workspace_release_lock",
    "classify_workspace_release_path",
]
