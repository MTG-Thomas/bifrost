"""Idempotent compatibility and signed-history projection for immutable Live."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
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
from src.services.workspace_source_releases import (
    mark_source_release_attention,
    reconcile_source_releases_after_lock,
)

WORKSPACE_RELEASE_LOCK_EVIDENCE_SCHEMA = "bifrost.workspace-release-lock/v1"
PROJECTION_PATHS_SCHEMA = "bifrost.workspace-release-projection-paths/v1"
WORKSPACE_RELEASE_LEDGER_SCHEMA = "bifrost.workspace-release-ledger/v1"
WORKSPACE_RELEASE_LEDGER_ROOT = ".bifrost/workspace-releases/ledger"
ProgressReporter = Callable[[str, int, int | None, float | None], Awaitable[None]]
logger = logging.getLogger(__name__)


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
    db: AsyncSession, organization_id: UUID | None
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


def _release_ledger(
    release: WorkspacePromotionRelease,
    artifact: WorkspacePromotionArtifact,
    descriptor: WorkspaceReleaseDescriptor,
) -> tuple[str, bytes, str]:
    activation = release.activation_evidence
    if not isinstance(activation, dict) or not isinstance(
        activation.get("registration_actions"), list
    ):
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release activation is missing its registration outcome.",
        )
    ledger = {
        "schema_version": WORKSPACE_RELEASE_LEDGER_SCHEMA,
        "release_row_id": str(release.id),
        "artifact_row_id": str(artifact.id),
        "artifact_candidate_id": artifact.candidate_id,
        "artifact_content_id": artifact.content_id,
        "artifact_closure_id": artifact.closure_id,
        "release_id": descriptor.release_id,
        "base_release_id": artifact.base_release_id,
        "effective_source_manifest_id": descriptor.effective_manifest_id,
        "governed_manifest_id": descriptor.governed_manifest_id,
        "governed_paths": list(descriptor.governed_paths),
        "effective_registration_manifest_id": (
            descriptor.effective_registration_manifest_id
        ),
        "registration_outcome": activation["registration_actions"],
        "prepared_evidence_id": activation.get("prepared_evidence_id"),
        "activation_evidence_id": activation.get("evidence_id"),
        "protected_source": {
            "commit_sha": descriptor.source_commit_sha,
            "tree_sha": descriptor.source_tree_sha,
        },
    }
    content = json.dumps(
        ledger,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    digest = _sha256(content)
    return f"{WORKSPACE_RELEASE_LEDGER_ROOT}/{digest}.json", content, digest


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
    governed_targets = descriptor.governed_source_hashes
    if any(
        path not in governed_targets or governed_targets[path] != target
        for path, target in closure_targets.items()
    ):
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release closure is outside its governed paths.",
        )
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
            or path not in governed_targets
            or not isinstance(target_sha256, str)
            or target_sha256 != governed_targets.get(path)
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
        len(parsed) != len(governed_targets)
        or [item.path for item in parsed] != sorted(governed_targets)
        or activation_projection.get("projection_paths_id")
        != canonical_digest({"schema": PROJECTION_PATHS_SCHEMA, "paths": raw_paths})
        or prepared.get("projection_paths") != raw_paths
    ):
        raise WorkspaceReleaseProjectionError(
            "workspace_release_projection_invalid",
            "Workspace release projection paths do not match cumulative governed paths.",
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
            await self._persist_projection_attention(
                release.id,
                expected_release_id,
                str(artifact.source_revision),
                exc,
            )
            raise
        except Exception as exc:
            wrapped = WorkspaceReleaseProjectionError(
                "workspace_release_projection_failed",
                str(exc),
                evidence={"phase": "projection"},
                retryable=True,
            )
            await self._persist_projection_attention(
                release.id,
                expected_release_id,
                str(artifact.source_revision),
                wrapped,
            )
            raise wrapped from exc
        release.lock_state = "locked"
        release.lock_evidence = evidence
        release.error_code = None
        release.error_message = None
        release.attention_deadline = None
        await reconcile_source_releases_after_lock(
            self.db,
            organization_id=self.organization_id,
            release_row_id=release.id,
            release_id=descriptor.release_id,
            runtime_hashes=descriptor.governed_source_hashes,
            history_commit_sha=str(evidence["history_after"]["commit_sha"]),
            history_hashes=evidence["history_after"]["file_sha256"],
        )
        await self.db.flush()
        await self.db.commit()
        return evidence

    async def _persist_projection_attention(
        self,
        release_row_id: UUID,
        expected_release_id: str,
        source_commit_sha: str,
        error: WorkspaceReleaseProjectionError,
    ) -> None:
        """Persist failure evidence in a fresh transaction without masking it."""
        try:
            await self.db.rollback()
            await acquire_workspace_release_lock(self.db, self.organization_id)
            release, _artifact = await self._load_release(release_row_id)
            if release.activation_state != "live":
                await self.db.rollback()
                return
            release.lock_state = "attention_required"
            release.error_code = error.code
            release.error_message = str(error)
            release.lock_evidence = {
                "schema_version": WORKSPACE_RELEASE_LOCK_EVIDENCE_SCHEMA,
                "release_row_id": str(release.id),
                "release_id": expected_release_id,
                "state": "attention_required",
                "live_preserved": True,
                "error_code": error.code,
                "error_message": str(error),
                **error.evidence,
            }
            release.lock_evidence["evidence_id"] = canonical_digest(
                release.lock_evidence
            )
            await mark_source_release_attention(
                self.db,
                organization_id=self.organization_id,
                source_commit_sha=source_commit_sha,
                code=error.code,
                message=str(error),
            )
            await self.db.flush()
            await self.db.commit()
        except Exception:
            logger.exception(
                "Workspace release projection attention could not be persisted",
                extra={
                    "workspace_release_row_id": str(release_row_id),
                    "error_code": error.code,
                },
            )
            try:
                await self.db.rollback()
            except Exception:
                logger.exception(
                    "Workspace release projection attention rollback failed",
                    extra={"workspace_release_row_id": str(release_row_id)},
                )

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
        projection_by_path = {item.path: item for item in projection_paths}
        governed_hashes = descriptor.governed_source_hashes
        paths = descriptor.governed_paths
        previous_governed_paths = await self._previous_governed_paths(release)
        newly_governed_paths = (
            frozenset(paths) - previous_governed_paths
            if release.previous_release_id is not None
            else frozenset()
        )
        ledger_path, ledger_content, ledger_sha256 = _release_ledger(
            release, artifact, descriptor
        )
        history_paths = (*paths, ledger_path)
        await self._ensure_still_live(release.id)
        immutable_full = await self.release_storage_factory(
            descriptor.runtime_storage_prefix
        ).read_many(sorted(descriptor.source_hashes))
        invalid_targets = [
            path
            for path, target_sha256 in governed_hashes.items()
            if path not in immutable_full
            or _sha256(immutable_full[path]) != target_sha256
        ]
        if invalid_targets:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_immutable_readback_failed",
                "Immutable release readback failed for: " + ", ".join(invalid_targets),
                evidence={"phase": "immutable_readback"},
            )
        immutable = {path: immutable_full[path] for path in paths}
        repo_hashes = await self._repo_hashes(paths)
        repo_states = tuple(
            classify_workspace_release_path(
                projection_by_path.get(path)
                or WorkspaceReleaseProjectionPath(
                    path=path,
                    base_sha256=target_sha256,
                    target_sha256=target_sha256,
                ),
                repo_hashes.get(path),
            )
            for path, target_sha256 in governed_hashes.items()
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
            history_before = await writer.inspect(history_paths)
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
                projection_by_path.get(path)
                or WorkspaceReleaseProjectionPath(
                    path=path,
                    base_sha256=target_sha256,
                    target_sha256=target_sha256,
                ),
                history_before.file_sha256.get(path),
            )
            for path, target_sha256 in governed_hashes.items()
        )
        ledger_state = classify_workspace_release_path(
            WorkspaceReleaseProjectionPath(
                path=ledger_path,
                base_sha256=None,
                target_sha256=ledger_sha256,
            ),
            history_before.file_sha256.get(ledger_path),
        )
        divergent = [
            f"repo:{item.path}" for item in repo_states if item.disposition == "other"
        ] + [
            f"history:{item.path}"
            for item in history_states
            if item.disposition == "other" and item.path not in newly_governed_paths
        ]
        history_adoptions = [
            asdict(item)
            for item in history_states
            if item.disposition == "other" and item.path in newly_governed_paths
        ]
        if ledger_state.disposition == "other":
            divergent.append(f"history:{ledger_path}")
        classification_evidence = {
            "previous_governed_paths": sorted(previous_governed_paths),
            "newly_governed_paths": sorted(newly_governed_paths),
            "repo_paths": [asdict(item) for item in repo_states],
            "history_before": {
                "commit_sha": history_before.commit_sha,
                "tree_sha": history_before.tree_sha,
                "signature_state": history_before.signature_state,
                "paths": [asdict(item) for item in history_states],
                "adoptions": history_adoptions,
                "ledger": asdict(ledger_state),
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

        repo_writes = [
            item
            for item in repo_states
            if item.path in projection_by_path and item.disposition == "base"
        ]
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
            path
            for path, target_sha256 in governed_hashes.items()
            if repo_after.get(path) != target_sha256
        ]
        generation, cache_rows = await self.coherence_inspector(governed_hashes)
        cache_evidence = [
            item.to_dict() if hasattr(item, "to_dict") else asdict(item)
            for item in cache_rows
        ]
        cache_by_path = {str(item.get("path")): item for item in cache_evidence}
        cache_mismatches = []
        for path, target_sha256 in governed_hashes.items():
            observed = cache_by_path.get(path)
            if (
                observed is None
                or observed.get("coherent") is not True
                or observed.get("indexed") is not True
                or observed.get("durable_sha256") != target_sha256
                or observed.get("cache_sha256") != target_sha256
                or observed.get("cache_generation") != generation
                or observed.get("workspace_generation") != generation
            ):
                cache_mismatches.append(path)
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

        history_writes = [
            item
            for item in history_states
            if item.path in projection_by_path
            and (
                item.disposition == "base"
                or (item.disposition == "other" and item.path in newly_governed_paths)
            )
        ]
        commit_files = [
            PlatformCommitFile(
                path=item.path,
                content_base64=base64.b64encode(immutable[item.path]).decode("ascii"),
                expected_before_sha256=item.observed_sha256,
                expected_sha256=item.target_sha256,
            )
            for item in history_writes
        ]
        if ledger_state.disposition == "base":
            commit_files.append(
                PlatformCommitFile(
                    path=ledger_path,
                    content_base64=base64.b64encode(ledger_content).decode("ascii"),
                    expected_before_sha256=None,
                    expected_sha256=ledger_sha256,
                )
            )
        if commit_files:
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
                        files=tuple(commit_files),
                        plan_id=artifact.candidate_id,
                        protected_main_source_sha=descriptor.source_commit_sha,
                        expected_head_sha=history_before.commit_sha,
                        expected_head_tree_sha=history_before.tree_sha,
                        workspace_release_id=descriptor.release_id,
                        workspace_release_row_id=release.id,
                        workspace_release_ledger_sha256=ledger_sha256,
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
            history_after = await writer.inspect(history_paths)
        except PlatformCommitError as exc:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_history_readback_failed",
                str(exc),
                evidence={"phase": "history_readback", **classification_evidence},
                retryable=True,
            ) from exc
        history_mismatches = [
            path
            for path, target_sha256 in governed_hashes.items()
            if history_after.file_sha256.get(path) != target_sha256
        ]
        if history_after.file_sha256.get(ledger_path) != ledger_sha256:
            history_mismatches.append(ledger_path)
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
            "governed_manifest_id": descriptor.governed_manifest_id,
            "governed_paths": list(descriptor.governed_paths),
            "effective_registration_manifest_id": (
                descriptor.effective_registration_manifest_id
            ),
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
            "release_ledger": {
                "path": ledger_path,
                "sha256": ledger_sha256,
                "schema_version": WORKSPACE_RELEASE_LEDGER_SCHEMA,
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

    async def _previous_governed_paths(
        self, release: WorkspacePromotionRelease
    ) -> frozenset[str]:
        """Return the immutable predecessor boundary for guarded history adoption."""
        if release.previous_release_id is None:
            return frozenset()
        row = (
            await self.db.execute(
                select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
                .join(
                    WorkspacePromotionArtifact,
                    WorkspacePromotionArtifact.id
                    == WorkspacePromotionRelease.artifact_id,
                )
                .where(
                    WorkspacePromotionRelease.id == release.previous_release_id,
                    WorkspacePromotionRelease.organization_id == self.organization_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_projection_invalid",
                "Workspace release predecessor evidence is missing.",
            )
        try:
            descriptor = WorkspaceReleaseDescriptor.from_rows(row[0], row[1])
        except WorkspaceReleaseRuntimeError as exc:
            raise WorkspaceReleaseProjectionError(
                "workspace_release_projection_invalid",
                "Workspace release predecessor evidence is invalid.",
            ) from exc
        return frozenset(descriptor.governed_paths)

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
        release.attention_deadline = None
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
