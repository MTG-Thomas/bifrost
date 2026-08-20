"""Execution pins for immutable, organization-scoped Workspace releases."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bifrost.workspace_release import (
    canonical_digest,
    workspace_manifest_id,
    workspace_registration_manifest_id,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.models.orm.workflows import Workflow
from src.services.workspace_release_storage import normalize_workspace_release_prefix

WORKSPACE_RELEASE_RUNTIME_SCHEMA = "bifrost.workspace-release-runtime/v1"
WORKSPACE_RELEASE_ARTIFACT_SCHEMA = "bifrost.workspace-release-artifact/v1"
REQUIRED_RUNTIME_BOUNDS = {
    "max_duration_seconds",
    "max_external_calls",
    "max_records_read",
    "max_output_bytes",
}


class WorkspaceReleaseRuntimeError(RuntimeError):
    """An execution could not prove one immutable Workspace release."""


def _canonical_hash(value: object) -> str:
    return canonical_digest(value)


def _required_text(manifest: dict[str, Any], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        raise WorkspaceReleaseRuntimeError(f"Workspace release is missing {key}")
    return value


def _source_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    value = manifest.get("effective_files")
    if not isinstance(value, dict) or not value:
        raise WorkspaceReleaseRuntimeError(
            "Workspace release is missing its effective file manifest"
        )
    result: dict[str, str] = {}
    for path, digest in value.items():
        if (
            not isinstance(path, str)
            or not path
            or ".." in path.replace("\\", "/").split("/")
            or not isinstance(digest, str)
            or len(digest.removeprefix("sha256:")) != 64
        ):
            raise WorkspaceReleaseRuntimeError(
                "Workspace release effective file manifest is invalid"
            )
        normalized_digest = digest.removeprefix("sha256:")
        if any(char not in "0123456789abcdef" for char in normalized_digest):
            raise WorkspaceReleaseRuntimeError(
                "Workspace release effective file manifest is invalid"
            )
        result[path.replace("\\", "/").lstrip("/")] = normalized_digest
    expected_manifest_id = _required_text(manifest, "effective_manifest_id")
    if workspace_manifest_id(result) != expected_manifest_id:
        raise WorkspaceReleaseRuntimeError(
            "Workspace release effective manifest digest does not match"
        )
    return dict(sorted(result.items()))


def _governed_paths(
    manifest: dict[str, Any], source_hashes: dict[str, str]
) -> tuple[str, ...]:
    value = manifest.get("governed_paths")
    if (
        not isinstance(value, list)
        or not value
        or value != sorted(value)
        or len(value) != len(set(value))
        or any(not isinstance(path, str) or path not in source_hashes for path in value)
    ):
        raise WorkspaceReleaseRuntimeError(
            "Workspace release governed path manifest is invalid"
        )
    return tuple(value)


def _effective_registrations(
    manifest: dict[str, Any],
    source_hashes: dict[str, str],
    governed_paths: tuple[str, ...],
    *,
    allow_legacy_exposure: bool,
) -> dict[str, dict[str, Any]]:
    value = manifest.get("effective_registrations")
    if not isinstance(value, dict):
        raise WorkspaceReleaseRuntimeError(
            "Workspace release is missing its effective registration manifest"
        )
    result: dict[str, dict[str, Any]] = {}
    legacy_required = {
        "path",
        "function",
        "workflow_id",
        "type",
        "name",
        "organization_id",
        "is_active",
        "source_sha256",
        "runtime_bounds",
    }
    exposure_fields = {
        "access_level",
        "role_ids",
        "endpoint_enabled",
        "public_endpoint",
        "api_key_enabled",
    }
    for key, raw in value.items():
        legacy = (
            frozenset(raw) == frozenset(legacy_required)
            if isinstance(raw, dict)
            else False
        )
        if (
            not isinstance(key, str)
            or not isinstance(raw, dict)
            or frozenset(raw)
            not in {
                frozenset(legacy_required),
                frozenset(legacy_required | exposure_fields),
            }
            or (legacy and not allow_legacy_exposure)
        ):
            raise WorkspaceReleaseRuntimeError(
                "Workspace release effective registration manifest is invalid"
            )
        path = str(raw.get("path") or "").replace("\\", "/").lstrip("/")
        function = raw.get("function")
        source_hash = raw.get("source_sha256")
        try:
            UUID(str(raw.get("workflow_id")))
            if raw.get("organization_id") is not None:
                UUID(str(raw["organization_id"]))
        except ValueError as exc:
            raise WorkspaceReleaseRuntimeError(
                "Workspace release effective registration identity is invalid"
            ) from exc
        if (
            key != f"{path}::{function}"
            or not isinstance(function, str)
            or not function
            or raw.get("is_active") is not True
            or path not in governed_paths
            or source_hash != source_hashes.get(path)
        ):
            raise WorkspaceReleaseRuntimeError(
                "Workspace release effective registration binding is invalid"
            )
        _runtime_bounds({"bounds": raw.get("runtime_bounds")})
        if set(raw) == legacy_required | exposure_fields:
            role_ids = raw.get("role_ids")
            if (
                raw.get("access_level")
                not in {"role_based", "authenticated", "everyone"}
                or not isinstance(role_ids, list)
                or any(not isinstance(value, str) for value in role_ids)
                or role_ids != sorted(set(role_ids))
                or any(
                    not isinstance(raw.get(field), bool)
                    for field in (
                        "endpoint_enabled",
                        "public_endpoint",
                        "api_key_enabled",
                    )
                )
            ):
                raise WorkspaceReleaseRuntimeError(
                    "Workspace release registration exposure is invalid"
                )
        result[key] = dict(raw)
    expected_id = _required_text(manifest, "effective_registration_manifest_id")
    if workspace_registration_manifest_id(result) != expected_id:
        raise WorkspaceReleaseRuntimeError(
            "Workspace release effective registration manifest digest does not match"
        )
    if allow_legacy_exposure:
        for registration in result.values():
            if set(registration) == legacy_required:
                registration.update(
                    {
                        "access_level": "role_based",
                        "role_ids": [],
                        "endpoint_enabled": False,
                        "public_endpoint": False,
                        "api_key_enabled": False,
                    }
                )
    return dict(sorted(result.items()))


def _runtime_bounds(manifest: dict[str, Any]) -> dict[str, int]:
    value = manifest.get("bounds")
    if not isinstance(value, dict) or not REQUIRED_RUNTIME_BOUNDS.issubset(value):
        raise WorkspaceReleaseRuntimeError(
            "Workspace release is missing its enforced runtime bounds"
        )
    result: dict[str, int] = {}
    for key, raw in value.items():
        if (
            not isinstance(key, str)
            or not isinstance(raw, int)
            or isinstance(raw, bool)
            or raw <= 0
        ):
            raise WorkspaceReleaseRuntimeError(
                "Workspace release runtime bounds are invalid"
            )
        result[key] = raw
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class WorkspaceReleaseDescriptor:
    release_row_id: UUID
    artifact_id: UUID
    organization_id: UUID
    release_id: str
    effective_manifest_id: str
    runtime_storage_prefix: str
    source_hashes: dict[str, str]
    governed_paths: tuple[str, ...]
    governed_manifest_id: str
    effective_registrations: dict[str, dict[str, Any]]
    effective_registration_manifest_id: str
    source_commit_sha: str
    source_tree_sha: str
    registration_state_fingerprint: str

    @classmethod
    def from_rows(
        cls,
        release: WorkspacePromotionRelease,
        artifact: WorkspacePromotionArtifact,
    ) -> "WorkspaceReleaseDescriptor":
        manifest = dict(artifact.manifest or {})
        if manifest.get("schema_version") != WORKSPACE_RELEASE_ARTIFACT_SCHEMA:
            raise WorkspaceReleaseRuntimeError(
                "Workspace release artifact schema is not executable"
            )
        release_id = _required_text(manifest, "release_id")
        if not release_id.startswith("sha256:") or len(release_id) != 71:
            raise WorkspaceReleaseRuntimeError("Workspace release id is invalid")
        source_hashes = _source_hashes(manifest)
        governed_paths = _governed_paths(manifest, source_hashes)
        governed_manifest_id = _required_text(manifest, "governed_manifest_id")
        if (
            workspace_manifest_id(
                {path: source_hashes[path] for path in governed_paths}
            )
            != governed_manifest_id
        ):
            raise WorkspaceReleaseRuntimeError(
                "Workspace release governed manifest digest does not match"
            )
        effective_registrations = _effective_registrations(
            manifest,
            source_hashes,
            governed_paths,
            allow_legacy_exposure=(
                artifact.policy_version == "workspace-release-artifact/2026-08-19"
            ),
        )
        release_digest = release_id.removeprefix("sha256:")
        runtime_prefix = normalize_workspace_release_prefix(
            f"_workspace_releases/{release.organization_id}/{release_digest}/files/"
        )
        protected_source = manifest.get("protected_source")
        registration = manifest.get("registration")
        if not isinstance(protected_source, dict) or not isinstance(registration, dict):
            raise WorkspaceReleaseRuntimeError(
                "Workspace release provenance or registration evidence is missing"
            )
        return cls(
            release_row_id=release.id,
            artifact_id=artifact.id,
            organization_id=release.organization_id,
            release_id=release_id,
            effective_manifest_id=_required_text(manifest, "effective_manifest_id"),
            runtime_storage_prefix=runtime_prefix,
            source_hashes=source_hashes,
            governed_paths=governed_paths,
            governed_manifest_id=governed_manifest_id,
            effective_registrations=effective_registrations,
            effective_registration_manifest_id=_required_text(
                manifest, "effective_registration_manifest_id"
            ),
            source_commit_sha=_required_text(protected_source, "commit_sha"),
            source_tree_sha=_required_text(protected_source, "tree_sha"),
            registration_state_fingerprint=_required_text(
                registration, "state_fingerprint"
            ),
        )

    @property
    def governed_source_hashes(self) -> dict[str, str]:
        """Hashes available to Live reads and immutable execution imports."""
        return {path: self.source_hashes[path] for path in self.governed_paths}


@dataclass(frozen=True)
class PinnedWorkspaceRuntime:
    workflow_id: UUID
    release: WorkspaceReleaseDescriptor
    name: str
    function_name: str
    path: str
    source_hash: str
    timeout_seconds: int
    time_saved: int
    value: float
    execution_mode: str
    workflow_type: str
    cache_ttl_seconds: int
    organization_id: str | None
    runtime_bounds: dict[str, int]

    @property
    def runtime_mode(self) -> str:
        return "workspace-release-v1"

    @property
    def deployment_id(self) -> None:
        return None

    def queue_evidence(self) -> dict[str, Any]:
        return {
            "schema_version": WORKSPACE_RELEASE_RUNTIME_SCHEMA,
            "workspace_release_row_id": str(self.release.release_row_id),
            "workspace_release_artifact_id": str(self.release.artifact_id),
            "workspace_release_id": self.release.release_id,
            "workspace_release_effective_manifest_id": (
                self.release.effective_manifest_id
            ),
            "workspace_release_governed_manifest_id": (
                self.release.governed_manifest_id
            ),
            "workspace_release_runtime_storage_prefix": (
                self.release.runtime_storage_prefix
            ),
            "workspace_release_source_hashes": self.release.governed_source_hashes,
            "workspace_release_registration_manifest_id": (
                self.release.effective_registration_manifest_id
            ),
            "workspace_release_source_commit_sha": self.release.source_commit_sha,
            "workspace_release_source_tree_sha": self.release.source_tree_sha,
            "workspace_release_registration_state_fingerprint": (
                self.release.registration_state_fingerprint
            ),
            "workflow_runtime_bounds": self.runtime_bounds,
            "workflow_id": str(self.workflow_id),
            "workflow_name": self.name,
            "workflow_function_name": self.function_name,
            "workflow_path": self.path,
            "workflow_source_hash": self.source_hash,
            "workflow_timeout_seconds": self.timeout_seconds,
            "workflow_time_saved": self.time_saved,
            "workflow_value": self.value,
            "workflow_execution_mode": self.execution_mode,
            "workflow_type": self.workflow_type,
            "workflow_cache_ttl_seconds": self.cache_ttl_seconds,
            "workflow_organization_id": self.organization_id,
        }


@dataclass(frozen=True)
class WorkspaceReleaseFileCoherence:
    path: str
    expected_sha256: str
    immutable_sha256: str | None
    cache_sha256: str | None
    projected_repo_sha256: str | None
    history_sha256: str | None
    immutable_coherent: bool
    cache_coherent: bool
    projected_repo_coherent: bool
    history_coherent: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "expected_sha256": self.expected_sha256,
            "immutable_sha256": self.immutable_sha256,
            "cache_sha256": self.cache_sha256,
            "projected_repo_sha256": self.projected_repo_sha256,
            "history_sha256": self.history_sha256,
            "immutable_coherent": self.immutable_coherent,
            "cache_coherent": self.cache_coherent,
            "projected_repo_coherent": self.projected_repo_coherent,
            "history_coherent": self.history_coherent,
        }


async def active_workspace_release(
    session: AsyncSession, organization_id: UUID
) -> WorkspaceReleaseDescriptor | None:
    """Resolve the single platform-global Live Workspace release.

    The organization argument remains for compatibility with the original
    caller contract. Shared Workspace source, ``_repo``, and production-live
    history are platform-global, so organization scoping must never select a
    different runtime authority.
    """
    del organization_id
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
    release, artifact = rows[0]
    return WorkspaceReleaseDescriptor.from_rows(release, artifact)


async def pin_workspace_runtime(
    session: AsyncSession, workflow_id: UUID
) -> PinnedWorkspaceRuntime | None:
    """Pin governed Workspace source or leave an ungoverned path on repo-v1."""
    workflow = await session.get(
        Workflow,
        workflow_id,
        options=(selectinload(Workflow.roles),),
    )
    if workflow is None or not workflow.is_active:
        raise WorkspaceReleaseRuntimeError(f"workflow {workflow_id} is not executable")
    if workflow.solution_id is not None:
        return None
    release = await active_workspace_release(
        session,
        workflow.organization_id or UUID(int=0),
    )
    if release is None:
        return None
    workflow_path = workflow.path.replace("\\", "/").lstrip("/")
    if workflow_path not in release.governed_paths:
        return None
    registration_key = f"{workflow_path}::{workflow.function_name}"
    if registration_key not in release.effective_registrations:
        raise WorkspaceReleaseRuntimeError(
            "governed Workspace workflow is not bound to the Live release"
        )
    return _pin_workflow_to_release(workflow, release)


def _pin_workflow_to_release(
    workflow: Workflow, release: WorkspaceReleaseDescriptor
) -> PinnedWorkspaceRuntime:
    path = workflow.path.replace("\\", "/").lstrip("/")
    source_hash = release.source_hashes.get(path)
    registration = release.effective_registrations.get(
        f"{path}::{workflow.function_name}"
    )
    if registration is None:
        raise WorkspaceReleaseRuntimeError("workflow is not bound to release-v1")
    if (
        source_hash is None
        or registration.get("workflow_id") != str(workflow.id)
        or registration.get("path") != path
        or registration.get("function") != workflow.function_name
        or registration.get("name") != workflow.name
        or registration.get("type") != workflow.type
        or registration.get("source_sha256") != source_hash
        or registration.get("organization_id")
        != (str(workflow.organization_id) if workflow.organization_id else None)
        or registration.get("is_active") is not True
        or registration["endpoint_enabled"] != bool(workflow.endpoint_enabled)
        or registration["public_endpoint"] != bool(workflow.public_endpoint)
        or registration["api_key_enabled"] != bool(workflow.api_key_enabled)
        or registration["access_level"] != workflow.access_level
        or registration["role_ids"] != sorted(str(role.id) for role in workflow.roles)
    ):
        raise WorkspaceReleaseRuntimeError(
            "workflow registration does not match the live Workspace release"
        )
    configured_timeout = (
        workflow.timeout_seconds if workflow.timeout_seconds is not None else 1800
    )
    return PinnedWorkspaceRuntime(
        workflow_id=workflow.id,
        release=release,
        name=workflow.name,
        function_name=workflow.function_name,
        path=path,
        source_hash=source_hash,
        timeout_seconds=min(
            configured_timeout,
            int(registration["runtime_bounds"]["max_duration_seconds"]),
        ),
        time_saved=workflow.time_saved or 0,
        value=float(workflow.value or 0),
        execution_mode=workflow.execution_mode or "async",
        workflow_type=workflow.type or "workflow",
        cache_ttl_seconds=workflow.cache_ttl_seconds or 0,
        organization_id=(
            str(workflow.organization_id) if workflow.organization_id else None
        ),
        runtime_bounds=dict(registration["runtime_bounds"]),
    )


async def resolve_pinned_workspace_runtime(
    session: AsyncSession,
    evidence: dict[str, Any],
    workflow_id: UUID,
) -> PinnedWorkspaceRuntime:
    """Resolve one durable pin; later Live advancement does not invalidate it."""
    try:
        release_row_id = UUID(str(evidence["workspace_release_row_id"]))
        artifact_id = UUID(str(evidence["workspace_release_artifact_id"]))
        release_id = str(evidence["workspace_release_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceReleaseRuntimeError(
            "queued Workspace release identity is invalid"
        ) from exc
    row = (
        await session.execute(
            select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
            .join(
                WorkspacePromotionArtifact,
                WorkspacePromotionArtifact.id == WorkspacePromotionRelease.artifact_id,
            )
            .where(
                WorkspacePromotionRelease.id == release_row_id,
                WorkspacePromotionRelease.artifact_id == artifact_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise WorkspaceReleaseRuntimeError("queued Workspace release is missing")
    release_row, artifact = row
    if release_row.activation_state not in {"live", "superseded"}:
        raise WorkspaceReleaseRuntimeError(
            "queued Workspace release is no longer executable"
        )
    descriptor = WorkspaceReleaseDescriptor.from_rows(release_row, artifact)
    if descriptor.release_id != release_id:
        raise WorkspaceReleaseRuntimeError("queued Workspace release id is invalid")
    path = str(evidence.get("workflow_path") or "").replace("\\", "/").lstrip("/")
    function_name = str(evidence.get("workflow_function_name") or "")
    source_hash = descriptor.source_hashes.get(path)
    registration = descriptor.effective_registrations.get(f"{path}::{function_name}")
    if (
        evidence.get("workflow_id") != str(workflow_id)
        or source_hash is None
        or evidence.get("workflow_source_hash") != source_hash
        or registration is None
        or registration.get("workflow_id") != str(workflow_id)
        or registration.get("source_sha256") != source_hash
        or evidence.get("workflow_organization_id")
        != registration.get("organization_id")
        or registration.get("is_active") is not True
        or evidence.get("workflow_name") != registration.get("name")
        or evidence.get("workflow_type") != registration.get("type")
    ):
        raise WorkspaceReleaseRuntimeError(
            "queued Workspace release workflow source is invalid"
        )
    try:
        registration_bounds = _runtime_bounds(
            {"bounds": registration["runtime_bounds"]}
        )
        queued_bounds = _runtime_bounds({"bounds": evidence["workflow_runtime_bounds"]})
        timeout_seconds = int(evidence["workflow_timeout_seconds"])
        if (
            queued_bounds != registration_bounds
            or isinstance(evidence["workflow_timeout_seconds"], bool)
            or timeout_seconds <= 0
            or timeout_seconds > registration_bounds["max_duration_seconds"]
        ):
            raise WorkspaceReleaseRuntimeError(
                "queued Workspace release runtime bounds are invalid"
            )
        return PinnedWorkspaceRuntime(
            workflow_id=workflow_id,
            release=descriptor,
            name=str(evidence["workflow_name"]),
            function_name=str(evidence["workflow_function_name"]),
            path=path,
            source_hash=source_hash,
            timeout_seconds=timeout_seconds,
            time_saved=int(evidence["workflow_time_saved"]),
            value=float(evidence["workflow_value"]),
            execution_mode=str(evidence["workflow_execution_mode"]),
            workflow_type=str(evidence["workflow_type"]),
            cache_ttl_seconds=int(evidence["workflow_cache_ttl_seconds"]),
            organization_id=registration.get("organization_id"),
            runtime_bounds=registration_bounds,
        )
    except WorkspaceReleaseRuntimeError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceReleaseRuntimeError(
            "queued Workspace release workflow metadata is invalid"
        ) from exc


def verify_workspace_runtime_evidence(
    queued: dict[str, Any] | None,
    durable: dict[str, Any] | None,
    durable_hash: str | None,
    authoritative: dict[str, Any],
) -> None:
    """Require queue, execution row, and current immutable manifest to agree."""
    if not isinstance(queued, dict) or queued != durable:
        raise WorkspaceReleaseRuntimeError(
            "Workspace release queue evidence differs from durable execution pin"
        )
    if queued.get("schema_version") != WORKSPACE_RELEASE_RUNTIME_SCHEMA:
        raise WorkspaceReleaseRuntimeError("Workspace release runtime schema mismatch")
    if _canonical_hash(queued) != durable_hash:
        raise WorkspaceReleaseRuntimeError(
            "Workspace release durable evidence hash mismatch"
        )
    if queued != authoritative:
        raise WorkspaceReleaseRuntimeError(
            "Workspace release runtime evidence differs from its immutable artifact"
        )


def workflow_data_from_workspace_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Expose the worker's existing metadata shape after evidence verification."""
    required = (
        "workspace_release_id",
        "workspace_release_runtime_storage_prefix",
        "workspace_release_source_hashes",
        "workflow_name",
        "workflow_function_name",
        "workflow_path",
        "workflow_source_hash",
        "workflow_runtime_bounds",
    )
    if any(not evidence.get(key) for key in required):
        raise WorkspaceReleaseRuntimeError(
            "Workspace release runtime evidence is incomplete"
        )
    source_hashes = evidence["workspace_release_source_hashes"]
    path = str(evidence["workflow_path"])
    if not isinstance(source_hashes, dict) or source_hashes.get(path) != evidence.get(
        "workflow_source_hash"
    ):
        raise WorkspaceReleaseRuntimeError(
            "Workspace release workflow hash is outside its effective manifest"
        )
    bounds = evidence["workflow_runtime_bounds"]
    if (
        not isinstance(bounds, dict)
        or not REQUIRED_RUNTIME_BOUNDS.issubset(bounds)
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value <= 0
            for value in bounds.values()
        )
    ):
        raise WorkspaceReleaseRuntimeError(
            "Workspace release runtime bounds are invalid"
        )
    return {
        "name": evidence["workflow_name"],
        "function_name": evidence["workflow_function_name"],
        "path": path,
        "content_hash": evidence["workflow_source_hash"],
        "type": evidence.get("workflow_type", "workflow"),
        "cache_ttl_seconds": evidence.get("workflow_cache_ttl_seconds", 0),
        "timeout_seconds": evidence.get("workflow_timeout_seconds", 1800),
        "time_saved": evidence.get("workflow_time_saved", 0),
        "value": evidence.get("workflow_value", 0),
        "organization_id": evidence.get("workflow_organization_id"),
        "workspace_release_id": evidence["workspace_release_id"],
        "workspace_release_runtime_storage_prefix": evidence[
            "workspace_release_runtime_storage_prefix"
        ],
        "workspace_release_source_hashes": source_hashes,
        "workflow_runtime_bounds": bounds,
        "workspace_release_max_output_bytes": bounds["max_output_bytes"],
    }


async def inspect_workspace_release_coherence(
    release: WorkspaceReleaseDescriptor,
    *,
    history_hashes: dict[str, str] | None = None,
) -> tuple[bool, list[WorkspaceReleaseFileCoherence]]:
    """Compare immutable runtime, cache, `_repo` projection, and Git evidence."""
    from src.core.module_cache import MODULE_KEY_PREFIX, _decode_cached_module
    from src.core.redis_client import get_redis_client
    from src.services.repo_storage import RepoStorage
    from src.services.workspace_release_storage import WorkspaceReleaseStorage

    paths = list(release.governed_paths)
    storage = WorkspaceReleaseStorage(release.runtime_storage_prefix)
    try:
        immutable = await storage.read_many(paths)
    except Exception as exc:
        raise WorkspaceReleaseRuntimeError(
            "immutable Workspace release tree is unreadable"
        ) from exc
    repo = RepoStorage()
    projected: dict[str, bytes | None] = {}
    for path in paths:
        try:
            projected[path] = await repo.read(path)
        except Exception:
            projected[path] = None
    python_paths = [path for path in paths if path.endswith(".py")]
    redis_conn = await get_redis_client()._get_redis()
    cache_values = await redis_conn.mget(
        [
            f"{MODULE_KEY_PREFIX}{release.runtime_storage_prefix}{path}"
            for path in python_paths
        ]
    )
    cache_by_path = dict(zip(python_paths, cache_values, strict=True))
    evidence: list[WorkspaceReleaseFileCoherence] = []
    for path in paths:
        expected = release.governed_source_hashes[path]
        immutable_raw = immutable.get(path)
        immutable_hash = (
            hashlib.sha256(immutable_raw).hexdigest()
            if immutable_raw is not None
            else None
        )
        projected_raw = projected[path]
        projected_hash = (
            hashlib.sha256(projected_raw).hexdigest()
            if projected_raw is not None
            else None
        )
        cached = _decode_cached_module(cache_by_path.get(path)) or {}
        cached_content = cached.get("content")
        cache_hash = (
            hashlib.sha256(cached_content.encode("utf-8")).hexdigest()
            if isinstance(cached_content, str)
            else None
        )
        cache_recorded_hash = cached.get("hash")
        history_hash = (history_hashes or {}).get(path)
        evidence.append(
            WorkspaceReleaseFileCoherence(
                path=path,
                expected_sha256=expected,
                immutable_sha256=immutable_hash,
                cache_sha256=cache_hash,
                projected_repo_sha256=projected_hash,
                history_sha256=history_hash,
                immutable_coherent=immutable_hash == expected,
                cache_coherent=(
                    cache_hash == expected and cache_recorded_hash == expected
                    if path.endswith(".py")
                    else True
                ),
                projected_repo_coherent=projected_hash == expected,
                history_coherent=(
                    history_hash == expected if history_hashes is not None else None
                ),
            )
        )
    runtime_coherent = all(
        item.immutable_coherent and item.cache_coherent for item in evidence
    )
    return runtime_coherent, evidence
