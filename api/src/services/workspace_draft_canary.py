"""Server-issued execution pins for immutable Workspace draft artifacts."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.workspace_release import workspace_closure_id, workspace_content_id
from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution
from src.models.orm.workspace_promotions import WorkspacePromotionArtifact
from src.services.solutions.deployment_manifest import canonical_json, sha256_digest
from src.services.workspace_promotion_storage import (
    WorkspaceDraftRuntimeStorage,
    WorkspacePromotionArtifactStorage,
    workspace_draft_runtime_prefix,
)

DRAFT_RUNTIME_MODE = "workspace-canary-v1"
DRAFT_RUNTIME_SCHEMA = "bifrost.workspace-reviewed-canary-runtime/v1"
DRAFT_CANARY_ATTESTATION_SCHEMA = (
    "bifrost.workspace-reviewed-canary-attestation/v1"
)
ALLOWED_CANARY_EFFECTS = {"bifrost.read"}
MAX_CANARY_DURATION_SECONDS = 60
MAX_CANARY_OUTPUT_BYTES = 1_048_576
PROMOTION_BUNDLE_SCHEMA_V2 = "bifrost.workspace-promotion-bundle/v2"


class WorkspaceDraftCanaryError(ValueError):
    """A draft artifact cannot be safely executed as a server canary."""


def validate_reviewed_canary_artifact(
    artifact: WorkspacePromotionArtifact,
) -> None:
    """Reject local uploads; the credentialed worker is not a draft sandbox."""
    manifest = artifact.manifest
    protected = manifest.get("protected_source") if isinstance(manifest, dict) else None
    if (
        artifact.target_kind != "workspace"
        or artifact.schema_version != PROMOTION_BUNDLE_SCHEMA_V2
        or not isinstance(protected, dict)
        or not artifact.source_revision
        or protected.get("commit_sha") != artifact.source_revision
        or not artifact.source_tree_sha
        or protected.get("tree_sha") != artifact.source_tree_sha
        or not artifact.release_id
        or not artifact.base_release_id
        or not artifact.effective_manifest_id
    ):
        raise WorkspaceDraftCanaryError(
            "server canaries require a reviewed protected-main Workspace artifact; "
            "local-only drafts must run on the operator machine"
        )
    if sha256_digest(canonical_json(manifest)) != artifact.candidate_id:
        raise WorkspaceDraftCanaryError(
            "reviewed canary artifact candidate identity is invalid"
        )


def _artifact_closure(artifact: WorkspacePromotionArtifact) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in artifact.manifest.get("closure", []):
        path = row.get("path")
        digest = row.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or path != path.replace("\\", "/").lstrip("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or path in result
        ):
            raise WorkspaceDraftCanaryError("artifact closure is not canonical")
        result[path] = digest
    if not result:
        raise WorkspaceDraftCanaryError("artifact closure is empty")
    return dict(sorted(result.items()))


def extract_verified_draft_source(
    source_zip: bytes, expected: dict[str, str]
) -> dict[str, bytes]:
    """Extract an exact archive, rejecting aliases, extras, and stale bytes."""
    try:
        with zipfile.ZipFile(io.BytesIO(source_zip)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected):
                raise WorkspaceDraftCanaryError(
                    "draft source archive does not match its immutable closure"
                )
            files = {name: archive.read(name) for name in names}
    except zipfile.BadZipFile as exc:
        raise WorkspaceDraftCanaryError("draft source archive is invalid") from exc
    for path, content in files.items():
        if hashlib.sha256(content).hexdigest() != expected[path]:
            raise WorkspaceDraftCanaryError(
                f"draft source integrity mismatch for {path}"
            )
    return files


def _canary_bounds(artifact: WorkspacePromotionArtifact) -> dict[str, int]:
    bounds = artifact.manifest.get("bounds") or {}
    duration = bounds.get("max_duration_seconds")
    output = bounds.get("max_output_bytes")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
        raise WorkspaceDraftCanaryError("draft canary requires max_duration_seconds")
    if not isinstance(output, int) or isinstance(output, bool) or output <= 0:
        raise WorkspaceDraftCanaryError("draft canary requires max_output_bytes")
    if duration > MAX_CANARY_DURATION_SECONDS:
        raise WorkspaceDraftCanaryError(
            f"draft canary duration exceeds {MAX_CANARY_DURATION_SECONDS} seconds"
        )
    if output > MAX_CANARY_OUTPUT_BYTES:
        raise WorkspaceDraftCanaryError(
            f"draft canary output exceeds {MAX_CANARY_OUTPUT_BYTES} bytes"
        )
    return {
        "max_duration_seconds": duration,
        "max_output_bytes": output,
    }


def build_draft_runtime_evidence(
    artifact: WorkspacePromotionArtifact,
    runtime_prefix: str,
) -> dict[str, Any]:
    validate_reviewed_canary_artifact(artifact)
    manifest = artifact.manifest
    if (
        getattr(artifact, "target_kind", None) != "workspace"
        or getattr(artifact, "schema_version", None)
        != "bifrost.workspace-promotion-bundle/v2"
    ):
        raise WorkspaceDraftCanaryError(
            "only reviewed protected-main artifacts are canary eligible"
        )
    declared = set(manifest.get("declared_effects") or [])
    computed = set(manifest.get("computed_effects") or [])
    if declared != ALLOWED_CANARY_EFFECTS or computed != ALLOWED_CANARY_EFFECTS:
        raise WorkspaceDraftCanaryError(
            "draft canaries currently require declared and computed effects "
            "to be exactly bifrost.read"
        )
    expected_prefix = workspace_draft_runtime_prefix(
        artifact.organization_id, str(artifact.content_id)
    )
    if runtime_prefix != expected_prefix:
        raise WorkspaceDraftCanaryError(
            "draft runtime prefix is not bound to the artifact content"
        )
    closure = _artifact_closure(artifact)
    entry = manifest.get("entry") or {}
    entry_path = entry.get("path")
    entry_function = entry.get("function")
    if (
        entry_path not in closure
        or not isinstance(entry_function, str)
        or not entry_function
    ):
        raise WorkspaceDraftCanaryError("artifact entry is not in its closure")
    canonical_entry = {"path": entry_path, "function": entry_function}
    closure_id = workspace_closure_id(canonical_entry, closure)
    content_id = workspace_content_id(canonical_entry, closure_id)
    if closure_id != artifact.closure_id or content_id != artifact.content_id:
        raise WorkspaceDraftCanaryError(
            "artifact content identity does not match its closure"
        )
    return {
        "schema": DRAFT_RUNTIME_SCHEMA,
        "runtime_mode": DRAFT_RUNTIME_MODE,
        "artifact_id": str(artifact.id),
        "organization_id": str(artifact.organization_id),
        "candidate_id": artifact.candidate_id,
        "content_id": artifact.content_id,
        "closure_id": artifact.closure_id,
        "runtime_storage_prefix": runtime_prefix,
        "entry": {
            "path": entry_path,
            "function": entry_function,
            "name": (manifest.get("effective_registrations") or {}).get(
                f"{entry_path}::{entry_function}", {}
            ).get("name", entry_function),
            "source_sha256": closure[entry_path],
        },
        "source_hashes": closure,
        "effects": ["bifrost.read"],
        "bounds": _canary_bounds(artifact),
    }


def verify_draft_runtime_evidence(
    queued: Any,
    durable: Any,
    durable_hash: str | None,
    artifact: WorkspacePromotionArtifact,
) -> dict[str, Any]:
    validate_reviewed_canary_artifact(artifact)
    if not isinstance(queued, dict) or not isinstance(durable, dict):
        raise WorkspaceDraftCanaryError("draft execution is missing runtime evidence")
    if queued != durable:
        raise WorkspaceDraftCanaryError("queued and durable draft pins differ")
    if sha256_digest(canonical_json(durable)) != durable_hash:
        raise WorkspaceDraftCanaryError("durable draft pin hash is invalid")
    expected = build_draft_runtime_evidence(
        artifact, str(durable.get("runtime_storage_prefix") or "")
    )
    if durable != expected:
        raise WorkspaceDraftCanaryError("draft pin no longer matches its artifact")
    if artifact.artifact_state == "invalid":
        raise WorkspaceDraftCanaryError("draft artifact has been revoked")
    return expected


async def resolve_draft_runtime_evidence(
    db: AsyncSession,
    queued: Any,
    execution: Execution,
) -> dict[str, Any]:
    if not isinstance(queued, dict):
        raise WorkspaceDraftCanaryError("queued draft pin is missing")
    try:
        artifact_id = UUID(str(queued.get("artifact_id")))
        organization_id = UUID(str(queued.get("organization_id")))
    except (TypeError, ValueError) as exc:
        raise WorkspaceDraftCanaryError("queued draft identity is invalid") from exc
    if execution.organization_id != organization_id:
        raise WorkspaceDraftCanaryError("draft organization pin mismatch")
    artifact = await db.scalar(
        select(WorkspacePromotionArtifact).where(
            WorkspacePromotionArtifact.id == artifact_id,
            WorkspacePromotionArtifact.organization_id == organization_id,
        )
    )
    if artifact is None:
        raise WorkspaceDraftCanaryError("draft artifact no longer exists")
    return verify_draft_runtime_evidence(
        queued,
        execution.runtime_evidence,
        execution.runtime_evidence_hash,
        artifact,
    )


def workflow_data_from_draft_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    entry = evidence["entry"]
    bounds = evidence["bounds"]
    return {
        "name": f"Draft canary: {entry['name']}",
        "function_name": entry["function"],
        "path": entry["path"],
        "type": "workflow",
        "cache_ttl_seconds": 0,
        "timeout_seconds": bounds["max_duration_seconds"],
        "time_saved": 0,
        "value": 0.0,
        "content_hash": entry["source_sha256"],
        "runtime_storage_prefix": evidence["runtime_storage_prefix"],
        "source_hashes": evidence["source_hashes"],
        "draft_runtime_id": evidence["content_id"],
        "max_output_bytes": bounds["max_output_bytes"],
    }


def draft_canary_attestation(
    execution: Execution, artifact: WorkspacePromotionArtifact
) -> dict[str, Any]:
    """Derive activation evidence only from a successful, verified Execution."""
    if (
        execution.status != ExecutionStatus.SUCCESS
        or execution.completed_at is None
        or execution.runtime_mode != DRAFT_RUNTIME_MODE
        or execution.workflow_id is not None
        or execution.organization_id != artifact.organization_id
    ):
        raise WorkspaceDraftCanaryError(
            "execution is not a successful canary for this organization"
        )
    evidence = verify_draft_runtime_evidence(
        execution.runtime_evidence,
        execution.runtime_evidence,
        execution.runtime_evidence_hash,
        artifact,
    )
    return {
        "schema": DRAFT_CANARY_ATTESTATION_SCHEMA,
        "execution_id": str(execution.id),
        "artifact_id": str(artifact.id),
        "candidate_id": artifact.candidate_id,
        "content_id": artifact.content_id,
        "closure_id": artifact.closure_id,
        "runtime_evidence_hash": execution.runtime_evidence_hash,
        "completed_at": execution.completed_at.isoformat(),
        "duration_ms": execution.duration_ms,
        "bounds": evidence["bounds"],
    }


async def find_successful_draft_canary_attestation(
    db: AsyncSession, artifact: WorkspacePromotionArtifact
) -> dict[str, Any] | None:
    """Return the newest exact successful canary usable by activation."""
    result = await db.execute(
        select(Execution)
        .where(
            Execution.organization_id == artifact.organization_id,
            Execution.runtime_mode == DRAFT_RUNTIME_MODE,
            Execution.status == ExecutionStatus.SUCCESS,
            Execution.workflow_id.is_(None),
            Execution.runtime_evidence["artifact_id"].as_string()
            == str(artifact.id),
        )
        .order_by(Execution.completed_at.desc())
        .limit(20)
    )
    for execution in result.scalars():
        try:
            return draft_canary_attestation(execution, artifact)
        except WorkspaceDraftCanaryError:
            continue
    return None


class WorkspaceDraftCanaryService:
    def __init__(
        self,
        db: AsyncSession,
        organization_id: UUID,
        *,
        artifact_storage_factory: Callable[..., WorkspacePromotionArtifactStorage]
        = WorkspacePromotionArtifactStorage,
        runtime_storage_factory: Callable[..., WorkspaceDraftRuntimeStorage]
        = WorkspaceDraftRuntimeStorage,
        publisher: Callable[..., Awaitable[bool]] | None = None,
    ):
        self.db = db
        self.organization_id = organization_id
        self.artifact_storage_factory = artifact_storage_factory
        self.runtime_storage_factory = runtime_storage_factory
        self.publisher = publisher

    async def issue(
        self,
        artifact_id: UUID,
        parameters: dict[str, Any],
        *,
        user_id: UUID,
        user_name: str,
        user_email: str,
        is_platform_admin: bool,
        is_provider_org: bool,
        is_external: bool,
    ) -> UUID:
        artifact = await self._artifact(artifact_id)
        validate_reviewed_canary_artifact(artifact)
        if artifact.expires_at <= datetime.now(timezone.utc):
            raise WorkspaceDraftCanaryError("draft artifact has expired")
        if artifact.artifact_state == "invalid":
            raise WorkspaceDraftCanaryError("draft artifact has been revoked")
        superseder = await self.db.scalar(
            select(WorkspacePromotionArtifact.id).where(
                WorkspacePromotionArtifact.organization_id == self.organization_id,
                WorkspacePromotionArtifact.supersedes_artifact_id == artifact.id,
            )
        )
        if superseder is not None:
            raise WorkspaceDraftCanaryError("draft artifact has been superseded")

        runtime_storage = self.runtime_storage_factory(
            self.organization_id, str(artifact.content_id)
        )
        evidence = build_draft_runtime_evidence(
            artifact, runtime_storage.runtime_prefix
        )
        artifact_storage = self.artifact_storage_factory(
            self.organization_id, str(artifact.content_id)
        )
        if artifact.source_artifact_key != artifact_storage.source_artifact_key:
            raise WorkspaceDraftCanaryError(
                "artifact source key is not bound to its content identity"
            )
        files = extract_verified_draft_source(
            await artifact_storage.read_source(), evidence["source_hashes"]
        )
        for path, content in files.items():
            await runtime_storage.write_file(path, content)

        execution_id = uuid4()
        entry = evidence["entry"]
        evidence_hash = sha256_digest(canonical_json(evidence))
        self.db.add(
            Execution(
                id=execution_id,
                workflow_name=f"Draft canary: {entry['name']}",
                workflow_id=None,
                runtime_mode=DRAFT_RUNTIME_MODE,
                runtime_evidence=evidence,
                runtime_evidence_hash=evidence_hash,
                status=ExecutionStatus.SCHEDULED,
                parameters=parameters,
                executed_by=user_id,
                executed_by_name=user_name,
                organization_id=self.organization_id,
            )
        )
        from src.services.audit import emit_audit

        await emit_audit(
            self.db,
            "workspace_promotion.reviewed_canary_issued",
            resource_type="execution",
            resource_id=execution_id,
            details={
                "artifact_id": str(artifact.id),
                "candidate_id": artifact.candidate_id,
                "content_id": artifact.content_id,
                "closure_id": artifact.closure_id,
                "runtime_mode": DRAFT_RUNTIME_MODE,
                "entry_path": entry["path"],
                "entry_function": entry["function"],
                "bounds": evidence["bounds"],
            },
            strict=True,
        )
        await self.db.commit()

        if self.publisher is None:
            from src.services.execution.async_executor import _publish_scheduled_once

            self.publisher = _publish_scheduled_once
        await self.publisher(
            execution_id=str(execution_id),
            publish_kwargs={
                "execution_id": str(execution_id),
                "workflow_id": None,
                "parameters": parameters,
                "org_id": str(self.organization_id),
                "user_id": str(user_id),
                "user_name": user_name,
                "user_email": user_email,
                "form_id": None,
                "startup": None,
                "form_inputs": {},
                "embed": {},
                "api_key_id": None,
                "sync": False,
                "is_platform_admin": is_platform_admin,
                "is_provider_org": is_provider_org,
                "is_external": is_external,
                "file_path": entry["path"],
                "event": None,
                "solution_deployment_id": None,
                "runtime_evidence": evidence,
                "runtime_mode": DRAFT_RUNTIME_MODE,
            },
        )
        return execution_id

    async def _artifact(self, artifact_id: UUID) -> WorkspacePromotionArtifact:
        artifact = await self.db.scalar(
            select(WorkspacePromotionArtifact).where(
                WorkspacePromotionArtifact.id == artifact_id,
                WorkspacePromotionArtifact.organization_id == self.organization_id,
            )
        )
        if artifact is None:
            raise KeyError(artifact_id)
        if artifact.content_id is None or artifact.closure_id is None:
            raise WorkspaceDraftCanaryError("legacy preview is not canary eligible")
        if (
            artifact.target_kind != "workspace"
            or artifact.schema_version != "bifrost.workspace-promotion-bundle/v2"
        ):
            raise WorkspaceDraftCanaryError(
                "only reviewed protected-main artifacts are canary eligible"
            )
        return artifact
