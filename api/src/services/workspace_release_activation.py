"""Atomic Live pointer activation for prepared immutable Workspace releases."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.promotion import sha256_bytes
from bifrost.workspace_release import (
    canonical_digest,
    repo_v1_release_id,
    workspace_manifest_id,
)
from bifrost.workspace_release_authorization import (
    AUTHORIZATION_EVIDENCE_SCHEMA,
    validate_risk_acknowledgement,
)
from src.models.contracts.workspace_promotions import (
    WorkspaceLiveStatusResponse,
    WorkspaceReleaseActivateRequest,
    WorkspaceReleaseHistoryStatus,
    WorkspaceReleaseRuntimeStatus,
    WorkspaceReleaseStatusResponse,
)
from src.models.orm.executions import Execution
from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)
from src.models.orm.workflows import Workflow
from src.services.audit import emit_audit
from src.services.repo_storage import RepoStorage
from src.services.workspace_draft_canary import (
    WorkspaceDraftCanaryError,
    draft_canary_attestation,
)
from src.services.workspace_promotions import (
    WorkspacePromotionInvalid,
    overlay_governed_base,
    read_generation_stable_executable_snapshot,
)
from src.services.workspace_release_materialization import (
    PREPARED_EVIDENCE_SCHEMA,
    prepared_activation_challenge,
)
from src.services.workspace_release_runtime import (
    WorkspaceReleaseDescriptor,
    WorkspaceReleaseRuntimeError,
)
from src.services.workspace_release_projection import acquire_workspace_release_lock
from src.services.workspace_release_storage import WorkspaceReleaseStorage
from src.services.workflow_registration import (
    WorkflowRegistrationConflict,
    apply_workspace_registration_plan,
    find_workspace_workflow,
)
from src.services.workspace_release_registration_authority import (
    WorkspaceRegistrationMutationAuthority,
)

ACTIVATION_EVIDENCE_SCHEMA = "bifrost.workspace-release-activation/v2"
REGISTRATION_STATE_SCHEMA = "bifrost.workspace-registration-state/v1"
REGISTRATION_INTENT_SCHEMA = "bifrost.workspace-registration-intent/v1"
PROJECTION_PATHS_SCHEMA = "bifrost.workspace-release-projection-paths/v1"


class WorkspaceReleaseActivationError(ValueError):
    """A prepared immutable release failed an activation invariant."""


def validate_prepared_release_evidence(
    release: WorkspacePromotionRelease,
    artifact: WorkspacePromotionArtifact,
    descriptor: WorkspaceReleaseDescriptor,
) -> dict[str, Any]:
    evidence = release.prepared_evidence
    if not isinstance(evidence, dict):
        raise WorkspaceReleaseActivationError("release has no prepared evidence")
    required = {
        "schema_version",
        "artifact_id",
        "candidate_id",
        "content_id",
        "release_id",
        "base_release_id",
        "base_manifest_id",
        "effective_manifest_id",
        "effective_files",
        "governed_paths",
        "governed_manifest_id",
        "effective_registration_manifest_id",
        "risk_class",
        "policy_version",
        "computed_effects",
        "computed_effects_id",
        "protected_source",
        "runtime_storage_prefix",
        "file_count",
        "total_bytes",
        "compile",
        "import_validation",
        "effect_execution",
        "prepared_at",
        "projection_paths",
        "evidence_id",
    }
    if set(evidence) != required:
        raise WorkspaceReleaseActivationError("prepared evidence shape is invalid")
    evidence_without_id = dict(evidence)
    evidence_id = evidence_without_id.pop("evidence_id")
    if (
        evidence.get("schema_version") != PREPARED_EVIDENCE_SCHEMA
        or not isinstance(evidence_id, str)
        or canonical_digest(evidence_without_id) != evidence_id
    ):
        raise WorkspaceReleaseActivationError("prepared evidence digest is invalid")
    manifest = dict(artifact.manifest or {})
    expected = {
        "artifact_id": str(artifact.id),
        "candidate_id": artifact.candidate_id,
        "content_id": artifact.content_id,
        "release_id": descriptor.release_id,
        "base_release_id": artifact.base_release_id,
        "base_manifest_id": artifact.base_manifest_id,
        "effective_manifest_id": descriptor.effective_manifest_id,
        "effective_files": descriptor.source_hashes,
        "governed_paths": list(descriptor.governed_paths),
        "governed_manifest_id": descriptor.governed_manifest_id,
        "effective_registration_manifest_id": (
            descriptor.effective_registration_manifest_id
        ),
        "risk_class": artifact.risk_class,
        "policy_version": artifact.policy_version,
        "computed_effects": manifest.get("computed_effects"),
        "protected_source": manifest.get("protected_source"),
        "runtime_storage_prefix": descriptor.runtime_storage_prefix,
        "file_count": len(descriptor.source_hashes),
    }
    if any(evidence.get(key) != value for key, value in expected.items()):
        raise WorkspaceReleaseActivationError(
            "prepared evidence does not match the immutable release"
        )
    if (
        workspace_manifest_id(evidence["effective_files"])
        != descriptor.effective_manifest_id
    ):
        raise WorkspaceReleaseActivationError("prepared manifest digest is invalid")
    if (
        workspace_manifest_id(
            {path: descriptor.source_hashes[path] for path in descriptor.governed_paths}
        )
        != evidence["governed_manifest_id"]
    ):
        raise WorkspaceReleaseActivationError(
            "prepared governed manifest digest is invalid"
        )
    if not isinstance(evidence.get("total_bytes"), int) or evidence["total_bytes"] <= 0:
        raise WorkspaceReleaseActivationError("prepared byte count is invalid")
    compile_evidence = evidence.get("compile")
    if compile_evidence != {
        "succeeded": True,
        "file_count": len(descriptor.source_hashes),
    }:
        raise WorkspaceReleaseActivationError("prepared compile evidence is invalid")
    try:
        challenge = prepared_activation_challenge(evidence)
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceReleaseActivationError(
            "prepared activation challenge is invalid"
        ) from exc
    if challenge["computed_effects_id"] != evidence.get("computed_effects_id"):
        raise WorkspaceReleaseActivationError(
            "prepared computed effects digest is invalid"
        )
    import_validation = evidence.get("import_validation")
    if artifact.risk_class == "R0":
        selected = (
            import_validation.get("selected")
            if isinstance(import_validation, dict)
            else None
        )
        entry = manifest.get("entry") or {}
        if (
            not isinstance(selected, dict)
            or import_validation.get("state") != "succeeded"
            or selected.get("imported") is not True
            or selected.get("function_callable") is not True
            or selected.get("source") != "immutable_candidate_tree"
            or selected.get("entry_path") != entry.get("path")
            or selected.get("entry_function") != entry.get("function")
            or evidence.get("effect_execution") != "reviewed_canary_required"
        ):
            raise WorkspaceReleaseActivationError(
                "prepared R0 import validation is invalid"
            )
    elif (
        import_validation
        != {
            "state": "not_performed",
            "reason": "non_r0_source_is_not_executed_during_prepare",
        }
        or evidence.get("effect_execution") != "not_performed"
    ):
        raise WorkspaceReleaseActivationError(
            "prepared R1/R2 non-execution evidence is invalid"
        )
    try:
        prepared_at = datetime.fromisoformat(str(evidence["prepared_at"]))
    except ValueError as exc:
        raise WorkspaceReleaseActivationError(
            "prepared evidence timestamp is invalid"
        ) from exc
    if release.prepared_at is None or prepared_at != release.prepared_at:
        raise WorkspaceReleaseActivationError(
            "prepared evidence timestamp differs from the release row"
        )
    projection_paths = evidence.get("projection_paths")
    if not isinstance(projection_paths, list):
        raise WorkspaceReleaseActivationError("prepared projection paths are missing")
    expected_targets = {
        path: descriptor.source_hashes[path] for path in descriptor.governed_paths
    }
    normalized_projection: list[dict[str, str | None]] = []
    for item in projection_paths:
        if not isinstance(item, dict) or set(item) != {
            "path",
            "base_sha256",
            "target_sha256",
        }:
            raise WorkspaceReleaseActivationError(
                "prepared projection path shape is invalid"
            )
        path = item.get("path")
        before = item.get("base_sha256")
        target = item.get("target_sha256")
        if (
            not isinstance(path, str)
            or target != expected_targets.get(path)
            or not _optional_sha256(before)
            or not _sha256(target)
        ):
            raise WorkspaceReleaseActivationError(
                "prepared projection path hash is invalid"
            )
        normalized_projection.append(
            {"path": path, "base_sha256": before, "target_sha256": target}
        )
    if len(normalized_projection) != len(expected_targets) or [
        item["path"] for item in normalized_projection
    ] != sorted(expected_targets):
        raise WorkspaceReleaseActivationError(
            "prepared projection paths do not exactly match the governed manifest"
        )
    reconstructed_base = dict(descriptor.source_hashes)
    for item in normalized_projection:
        if item["base_sha256"] is None:
            reconstructed_base.pop(str(item["path"]), None)
        else:
            reconstructed_base[str(item["path"])] = str(item["base_sha256"])
    if workspace_manifest_id(reconstructed_base) != artifact.base_manifest_id:
        raise WorkspaceReleaseActivationError(
            "prepared projection paths do not reconstruct the immutable base manifest"
        )
    return evidence


def _sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _optional_sha256(value: Any) -> bool:
    return value is None or _sha256(value)


def projection_paths_id(paths: list[dict[str, Any]]) -> str:
    return canonical_digest({"schema": PROJECTION_PATHS_SCHEMA, "paths": paths})


def _activation_state(existing: Workflow | None) -> dict[str, Any] | None:
    if existing is None:
        return None
    return {
        "workflow_id": str(existing.id),
        "organization_id": (
            str(existing.organization_id) if existing.organization_id else None
        ),
        "type": existing.type,
        "is_active": existing.is_active,
        "endpoint_enabled": existing.endpoint_enabled,
        "public_endpoint": existing.public_endpoint,
        "api_key_enabled": existing.api_key_enabled,
        "access_level": existing.access_level,
        "role_ids": sorted(str(role.id) for role in existing.roles),
    }


def registration_state_fingerprint(existing: Workflow | None) -> str:
    return canonical_digest(
        {"schema": REGISTRATION_STATE_SCHEMA, "state": _activation_state(existing)}
    )


def _history_status(
    release: WorkspacePromotionRelease,
) -> WorkspaceReleaseHistoryStatus:
    if release.activation_state == "superseded":
        state = "superseded"
    elif release.lock_state == "locked":
        state = "locked"
    elif release.lock_state == "attention_required":
        state = "attention_required"
    elif release.activation_state == "live":
        state = "pending"
    else:
        state = "not_queued"
    return WorkspaceReleaseHistoryStatus(
        state=state,
        lock_state=release.lock_state,
        job_id=release.lock_in_job_id,
    )


def release_status(
    release: WorkspacePromotionRelease,
    artifact: WorkspacePromotionArtifact,
) -> WorkspaceReleaseStatusResponse:
    manifest = dict(artifact.manifest or {})
    activation = release.activation_evidence or {}
    prepared = release.prepared_evidence or {}
    authorization = (
        activation.get("authorization") if isinstance(activation, dict) else None
    )
    authorization_kind = None
    authorization_id = None
    risk_acknowledgement_id = None
    canary_execution_id = None
    if isinstance(authorization, dict):
        authorization_kind = authorization.get("kind")
        authorization_id = authorization.get("authorization_id")
        canary = authorization.get("canary_attestation")
        acknowledgement = authorization.get("acknowledgement")
        if isinstance(canary, dict) and canary.get("execution_id"):
            canary_execution_id = UUID(str(canary["execution_id"]))
        if isinstance(acknowledgement, dict):
            risk_acknowledgement_id = acknowledgement.get("acknowledgement_id")
    activation_authorization = None
    if isinstance(prepared, dict) and prepared.get("evidence_id"):
        activation_authorization = prepared_activation_challenge(prepared)
    if release.activation_state == "live":
        runtime_state = "coherent"
    elif release.prepared_evidence is not None:
        runtime_state = "prepared"
    else:
        runtime_state = "not_prepared"
    activated_at = None
    if isinstance(activation, dict) and activation.get("activated_at"):
        activated_at = datetime.fromisoformat(str(activation["activated_at"]))
    return WorkspaceReleaseStatusResponse(
        release_row_id=release.id,
        artifact_id=artifact.id,
        organization_id=release.organization_id,
        candidate_id=artifact.candidate_id,
        release_id=str(artifact.release_id or manifest.get("release_id")),
        base_release_id=str(
            artifact.base_release_id or manifest.get("base_release_id")
        ),
        activation_state=release.activation_state,
        is_live=release.activation_state == "live",
        previous_release_row_id=release.previous_release_id,
        runtime=WorkspaceReleaseRuntimeStatus(
            state=runtime_state,
            immutable_release_id=str(artifact.release_id or manifest.get("release_id")),
            prepared_evidence_id=(
                str(prepared["evidence_id"])
                if isinstance(prepared, dict) and prepared.get("evidence_id")
                else None
            ),
            activation_authorization=activation_authorization,
            authorization_kind=authorization_kind,
            authorization_id=authorization_id,
            canary_execution_id=canary_execution_id,
            risk_acknowledgement_id=risk_acknowledgement_id,
        ),
        history=_history_status(release),
        activated_at=activated_at,
    )


class WorkspaceReleaseActivationService:
    def __init__(self, db: AsyncSession, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    async def activate(
        self,
        release_row_id: UUID,
        request: WorkspaceReleaseActivateRequest,
        *,
        authorized_by_user_id: UUID,
        authorized_by_email: str,
        authorized_by_name: str,
    ) -> WorkspaceReleaseStatusResponse:
        try:
            return await self._activate_locked(
                release_row_id,
                request,
                authorized_by_user_id=authorized_by_user_id,
                authorized_by_email=authorized_by_email,
                authorized_by_name=authorized_by_name,
            )
        except Exception:
            # The route translates safety/CAS failures into HTTP responses. Roll back
            # here so that translation cannot accidentally commit partial registry or
            # pointer mutations in the request dependency's finalizer.
            await self.db.rollback()
            raise

    async def _activate_locked(
        self,
        release_row_id: UUID,
        request: WorkspaceReleaseActivateRequest,
        *,
        authorized_by_user_id: UUID,
        authorized_by_email: str,
        authorized_by_name: str,
    ) -> WorkspaceReleaseStatusResponse:
        await acquire_workspace_release_lock(self.db, self.organization_id)
        target = await self._release_rows(release_row_id, for_update=True)
        if target is None:
            raise KeyError(release_row_id)
        release, artifact = target
        self._validate_request(request, release, artifact)
        current = await self._current_live_any_organization(for_update=True)
        if current is not None and current[0].organization_id != self.organization_id:
            raise WorkspaceReleaseActivationError(
                "a different organization already owns the global Live Workspace release"
            )
        if release.activation_state == "live":
            if current is None or current[0].id != release.id:
                raise WorkspaceReleaseActivationError(
                    "Live Workspace pointer does not match the requested release"
                )
            self._validate_idempotent_activation(request, release)
            return release_status(release, artifact)
        if release.activation_state != "prepared":
            raise WorkspaceReleaseActivationError(
                "release is not in the prepared activation state"
            )
        await self._validate_artifact_lifecycle(artifact)
        try:
            descriptor = WorkspaceReleaseDescriptor.from_rows(release, artifact)
        except WorkspaceReleaseRuntimeError as exc:
            raise WorkspaceReleaseActivationError(str(exc)) from exc
        prepared = validate_prepared_release_evidence(release, artifact, descriptor)
        if prepared["evidence_id"] != request.prepared_evidence_id:
            raise WorkspaceReleaseActivationError("prepared evidence CAS mismatch")

        await self._validate_base_cas(request, artifact, current)
        now = datetime.now(timezone.utc)
        authorization = await self._authorization_evidence(
            request,
            artifact,
            prepared,
            authorized_by_user_id=authorized_by_user_id,
            authorized_at=now,
        )
        applied = await self._apply_registration(artifact)

        previous_release_id = current[0].id if current else None
        if current is not None:
            current[0].activation_state = "superseded"
            await self.db.flush()
        release.previous_release_id = previous_release_id
        release.activation_state = "live"
        from src.jobs.platform.workspace_release_lock import (
            enqueue_workspace_release_lock,
        )

        projection_job, _reused = await enqueue_workspace_release_lock(
            self.db,
            release=release,
            artifact=artifact,
            requested_by_user_id=authorized_by_user_id,
            requested_by_email=authorized_by_email,
            requested_by_name=authorized_by_name,
        )
        activation_evidence = {
            "schema_version": ACTIVATION_EVIDENCE_SCHEMA,
            "artifact_id": str(artifact.id),
            "candidate_id": artifact.candidate_id,
            "release_id": descriptor.release_id,
            "base_release_id": artifact.base_release_id,
            "previous_release_row_id": (
                str(previous_release_id) if previous_release_id else None
            ),
            "prepared_evidence_id": prepared["evidence_id"],
            "projection_paths": {
                "projection_paths_id": projection_paths_id(
                    prepared["projection_paths"]
                ),
                "paths": prepared["projection_paths"],
            },
            "authorization": authorization,
            "registration_actions": applied,
            "runtime": {
                "state": "coherent",
                "runtime_storage_prefix": descriptor.runtime_storage_prefix,
                "effective_manifest_id": descriptor.effective_manifest_id,
                "governed_paths": list(descriptor.governed_paths),
                "governed_manifest_id": descriptor.governed_manifest_id,
            },
            "history": {
                "state": "pending",
                "lock_state": release.lock_state,
                "job_id": str(projection_job.id),
            },
            "activated_at": now.isoformat(),
        }
        activation_evidence["evidence_id"] = canonical_digest(activation_evidence)
        release.activation_evidence = activation_evidence
        release.error_code = None
        release.error_message = None
        await self.db.flush()
        await emit_audit(
            self.db,
            "workspace_promotion.release_activated",
            resource_type="workspace_promotion_release",
            resource_id=release.id,
            details={
                "artifact_id": str(artifact.id),
                "candidate_id": artifact.candidate_id,
                "release_id": descriptor.release_id,
                "previous_release_row_id": (
                    str(previous_release_id) if previous_release_id else None
                ),
                "authorization_kind": authorization["kind"],
                "authorization_id": authorization["authorization_id"],
                "challenge_id": authorization["challenge_id"],
                "risk_class": artifact.risk_class,
                "computed_effects_id": prepared["computed_effects_id"],
                "protected_source_commit": prepared["protected_source"]["commit_sha"],
                "projection_job_id": str(projection_job.id),
                "runtime_state": "coherent",
                "history_state": "pending",
            },
            strict=True,
        )
        await self.db.commit()
        await self.db.refresh(release)
        return release_status(release, artifact)

    async def get_release(self, release_row_id: UUID) -> WorkspaceReleaseStatusResponse:
        rows = await self._release_rows(release_row_id)
        if rows is None:
            raise KeyError(release_row_id)
        return release_status(*rows)

    async def enqueue_projection(
        self,
        release_row_id: UUID,
        *,
        requested_by_user_id: UUID,
        requested_by_email: str,
        requested_by_name: str,
    ) -> tuple[WorkspaceReleaseStatusResponse, Any, bool]:
        """Queue compatibility/history projection after Live is durable."""
        from src.jobs.platform.workspace_release_lock import (
            enqueue_workspace_release_lock,
        )

        rows = await self._release_rows(release_row_id, for_update=True)
        if rows is None:
            raise KeyError(release_row_id)
        release, artifact = rows
        job, reused = await enqueue_workspace_release_lock(
            self.db,
            release=release,
            artifact=artifact,
            requested_by_user_id=requested_by_user_id,
            requested_by_email=requested_by_email,
            requested_by_name=requested_by_name,
        )
        await self.db.commit()
        await self.db.refresh(release)
        return release_status(release, artifact), job, reused

    async def mark_projection_queue_failed(
        self, release_row_id: UUID, message: str
    ) -> WorkspaceReleaseStatusResponse:
        """Preserve Live while exposing a projection queue failure."""
        await acquire_workspace_release_lock(self.db, self.organization_id)
        rows = await self._release_rows(release_row_id, for_update=True)
        if rows is None:
            raise KeyError(release_row_id)
        release, artifact = rows
        current = await self._current_live_any_organization(for_update=True)
        if (
            release.activation_state != "live"
            or current is None
            or current[0].id != release.id
        ):
            raise WorkspaceReleaseActivationError(
                "projection queue failure cannot be attached to a non-Live release"
            )
        release.lock_state = "attention_required"
        release.error_code = "workspace_release_lock_queue_failed"
        release.error_message = message[:2000]
        await self.db.commit()
        await self.db.refresh(release)
        return release_status(release, artifact)

    async def get_live(self) -> WorkspaceLiveStatusResponse:
        rows = await self._current_live()
        return WorkspaceLiveStatusResponse(
            organization_id=self.organization_id,
            active_release=release_status(*rows) if rows else None,
        )

    async def _release_rows(
        self, release_row_id: UUID, *, for_update: bool = False
    ) -> tuple[WorkspacePromotionRelease, WorkspacePromotionArtifact] | None:
        statement = (
            select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
            .join(
                WorkspacePromotionArtifact,
                WorkspacePromotionArtifact.id == WorkspacePromotionRelease.artifact_id,
            )
            .where(
                WorkspacePromotionRelease.id == release_row_id,
                WorkspacePromotionRelease.organization_id == self.organization_id,
            )
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self.db.execute(statement)).one_or_none()
        if row is None:
            return None
        release, artifact = row
        return release, artifact

    async def _current_live(
        self, *, for_update: bool = False
    ) -> tuple[WorkspacePromotionRelease, WorkspacePromotionArtifact] | None:
        statement = (
            select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
            .join(
                WorkspacePromotionArtifact,
                WorkspacePromotionArtifact.id == WorkspacePromotionRelease.artifact_id,
            )
            .where(
                WorkspacePromotionRelease.organization_id == self.organization_id,
                WorkspacePromotionRelease.activation_state == "live",
            )
            .limit(2)
        )
        if for_update:
            statement = statement.with_for_update()
        rows = (await self.db.execute(statement)).all()
        if len(rows) > 1:
            raise WorkspaceReleaseActivationError(
                "organization has more than one Live Workspace release"
            )
        if not rows:
            return None
        release, artifact = rows[0]
        return release, artifact

    async def _current_live_any_organization(
        self, *, for_update: bool = False
    ) -> tuple[WorkspacePromotionRelease, WorkspacePromotionArtifact] | None:
        statement = (
            select(WorkspacePromotionRelease, WorkspacePromotionArtifact)
            .join(
                WorkspacePromotionArtifact,
                WorkspacePromotionArtifact.id == WorkspacePromotionRelease.artifact_id,
            )
            .where(WorkspacePromotionRelease.activation_state == "live")
            .limit(2)
        )
        if for_update:
            statement = statement.with_for_update()
        rows = (await self.db.execute(statement)).all()
        if len(rows) > 1:
            raise WorkspaceReleaseActivationError(
                "platform has more than one global Live Workspace release"
            )
        if not rows:
            return None
        release, artifact = rows[0]
        return release, artifact

    @staticmethod
    def _validate_request(
        request: WorkspaceReleaseActivateRequest,
        release: WorkspacePromotionRelease,
        artifact: WorkspacePromotionArtifact,
    ) -> None:
        if (
            request.artifact_id != artifact.id
            or request.candidate_id != artifact.candidate_id
            or request.workspace_release_id != artifact.release_id
            or request.expected_base_release_id != artifact.base_release_id
            or release.artifact_id != artifact.id
        ):
            raise WorkspaceReleaseActivationError("activation identity CAS mismatch")

    @staticmethod
    def _validate_idempotent_activation(
        request: WorkspaceReleaseActivateRequest,
        release: WorkspacePromotionRelease,
    ) -> None:
        evidence = release.activation_evidence or {}
        authorization = (
            evidence.get("authorization") if isinstance(evidence, dict) else None
        )
        request_authorization = request.authorization
        matches = False
        if isinstance(authorization, dict):
            if request_authorization.kind == "reviewed_canary":
                canary = authorization.get("canary_attestation")
                matches = (
                    authorization.get("kind") == "reviewed_canary"
                    and authorization.get("challenge_id")
                    == request_authorization.challenge_id
                    and isinstance(canary, dict)
                    and canary.get("execution_id")
                    == str(request_authorization.canary_execution_id)
                )
            else:
                acknowledgement = authorization.get("acknowledgement")
                matches = (
                    authorization.get("kind") == "risk_acknowledgement"
                    and isinstance(acknowledgement, dict)
                    and acknowledgement.get("acknowledgement_id")
                    == request_authorization.acknowledgement.acknowledgement_id
                    and authorization.get("challenge_id")
                    == request_authorization.acknowledgement.challenge_id
                )
        if (
            evidence.get("prepared_evidence_id") != request.prepared_evidence_id
            or not matches
        ):
            raise WorkspaceReleaseActivationError(
                "Live release activation evidence differs from this request"
            )

    async def _validate_artifact_lifecycle(
        self, artifact: WorkspacePromotionArtifact
    ) -> None:
        now = datetime.now(timezone.utc)
        manifest = artifact.manifest or {}
        protected = manifest.get("protected_source") or {}
        effects = manifest.get("computed_effects")
        if (
            artifact.target_kind != "workspace"
            or artifact.schema_version != "bifrost.workspace-promotion-bundle/v2"
            or artifact.artifact_state == "invalid"
            or artifact.expires_at <= now
            or protected.get("commit_sha") != artifact.source_revision
            or protected.get("tree_sha") != artifact.source_tree_sha
            or artifact.risk_class not in {"R0", "R1", "R2"}
            or manifest.get("risk_class") != artifact.risk_class
            or not isinstance(effects, list)
            or not effects
            or any(not isinstance(effect, str) or not effect for effect in effects)
            or effects != sorted(set(effects))
        ):
            raise WorkspaceReleaseActivationError(
                "artifact is not an unexpired canonical protected-source release"
            )
        child = await self.db.scalar(
            select(WorkspacePromotionArtifact.id).where(
                WorkspacePromotionArtifact.organization_id == self.organization_id,
                WorkspacePromotionArtifact.supersedes_artifact_id == artifact.id,
            )
        )
        if child is not None:
            raise WorkspaceReleaseActivationError("release artifact is superseded")

    async def _validate_base_cas(
        self,
        request: WorkspaceReleaseActivateRequest,
        artifact: WorkspacePromotionArtifact,
        current: tuple[WorkspacePromotionRelease, WorkspacePromotionArtifact] | None,
    ) -> None:
        from src.core.module_cache import get_workspace_generation

        if current is not None and current[0].lock_state != "locked":
            raise WorkspaceReleaseActivationError(
                "current Live Workspace release is not history-locked"
            )
        try:
            repo_files = await read_generation_stable_executable_snapshot(
                RepoStorage(), get_workspace_generation
            )
        except (WorkspacePromotionInvalid, ValueError) as exc:
            raise WorkspaceReleaseActivationError(str(exc)) from exc
        if current is None:
            if request.expected_active_release_id is not None or not str(
                artifact.base_release_id
            ).startswith("repo-v1:"):
                raise WorkspaceReleaseActivationError(
                    "Live Workspace base changed after preview"
                )
            repo_hashes = {
                path: sha256_bytes(raw) for path, raw in sorted(repo_files.items())
            }
            if (
                repo_v1_release_id(repo_hashes) != artifact.base_release_id
                or workspace_manifest_id(repo_hashes) != artifact.base_manifest_id
            ):
                raise WorkspaceReleaseActivationError(
                    "durable Workspace base changed after preparation"
                )
            return
        try:
            current_descriptor = WorkspaceReleaseDescriptor.from_rows(*current)
        except WorkspaceReleaseRuntimeError as exc:
            raise WorkspaceReleaseActivationError(str(exc)) from exc
        try:
            immutable = await WorkspaceReleaseStorage(
                current_descriptor.runtime_storage_prefix
            ).read_many(list(current_descriptor.governed_paths))
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceReleaseActivationError(
                "current Live governed bytes could not be read"
            ) from exc
        immutable_hashes = {
            path: sha256_bytes(raw) for path, raw in sorted(immutable.items())
        }
        if immutable_hashes != current_descriptor.governed_source_hashes:
            raise WorkspaceReleaseActivationError(
                "current Live governed bytes failed immutable readback"
            )
        try:
            hybrid = overlay_governed_base(
                repo_files,
                immutable,
                current_descriptor.governed_paths,
            )
        except WorkspacePromotionInvalid as exc:
            raise WorkspaceReleaseActivationError(str(exc)) from exc
        hybrid_hashes = {
            path: sha256_bytes(raw) for path, raw in sorted(hybrid.items())
        }
        if (
            request.expected_active_release_id != current_descriptor.release_id
            or artifact.base_release_id != current_descriptor.release_id
            or artifact.base_manifest_id != workspace_manifest_id(hybrid_hashes)
        ):
            raise WorkspaceReleaseActivationError(
                "Live Workspace base changed after preview"
            )

    async def _canary_attestation(
        self, execution_id: UUID, artifact: WorkspacePromotionArtifact
    ) -> dict[str, Any]:
        execution = await self.db.get(Execution, execution_id)
        if execution is None:
            raise WorkspaceReleaseActivationError("canary execution does not exist")
        try:
            attestation = draft_canary_attestation(execution, artifact)
        except WorkspaceDraftCanaryError as exc:
            raise WorkspaceReleaseActivationError(str(exc)) from exc
        if (
            attestation.get("candidate_id") != artifact.candidate_id
            or attestation.get("content_id") != artifact.content_id
            or attestation.get("closure_id") != artifact.closure_id
        ):
            raise WorkspaceReleaseActivationError(
                "canary execution is for different immutable content"
            )
        return attestation

    async def _authorization_evidence(
        self,
        request: WorkspaceReleaseActivateRequest,
        artifact: WorkspacePromotionArtifact,
        prepared: dict[str, Any],
        *,
        authorized_by_user_id: UUID,
        authorized_at: datetime,
    ) -> dict[str, Any]:
        try:
            challenge = prepared_activation_challenge(prepared)
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkspaceReleaseActivationError(
                "prepared activation challenge is invalid"
            ) from exc
        supplied = request.authorization
        if supplied.kind == "reviewed_canary":
            if challenge["required_authorization"] != "reviewed_canary":
                raise WorkspaceReleaseActivationError(
                    "R1/R2 releases require an explicit risk acknowledgement"
                )
            if supplied.challenge_id != challenge["challenge_id"]:
                raise WorkspaceReleaseActivationError(
                    "reviewed canary authorization challenge CAS mismatch"
                )
            attestation = await self._canary_attestation(
                supplied.canary_execution_id, artifact
            )
            attestation_id = canonical_digest(attestation)
            payload = {
                "schema_version": AUTHORIZATION_EVIDENCE_SCHEMA,
                "kind": "reviewed_canary",
                "challenge_id": challenge["challenge_id"],
                "canary_attestation": attestation,
                "canary_attestation_id": attestation_id,
                "authorized_by_user_id": str(authorized_by_user_id),
                "authorized_at": authorized_at.isoformat(),
            }
        else:
            if challenge["required_authorization"] != "risk_acknowledgement":
                raise WorkspaceReleaseActivationError(
                    "R0 releases require a successful exact reviewed canary"
                )
            try:
                acknowledgement = validate_risk_acknowledgement(
                    supplied.acknowledgement.model_dump(mode="json"), challenge
                )
            except ValueError as exc:
                raise WorkspaceReleaseActivationError(str(exc)) from exc
            payload = {
                "schema_version": AUTHORIZATION_EVIDENCE_SCHEMA,
                "kind": "risk_acknowledgement",
                "challenge_id": challenge["challenge_id"],
                "acknowledgement": acknowledgement,
                "authorized_by_user_id": str(authorized_by_user_id),
                "authorized_at": authorized_at.isoformat(),
            }
        return {**payload, "authorization_id": canonical_digest(payload)}

    async def _apply_registration(
        self, artifact: WorkspacePromotionArtifact
    ) -> list[dict[str, Any]]:
        manifest = artifact.manifest or {}
        registration = manifest.get("registration")
        entry = manifest.get("entry") or {}
        if not isinstance(registration, dict):
            raise WorkspaceReleaseActivationError(
                "artifact registration evidence is missing"
            )
        intent = registration.get("intent")
        if not isinstance(intent, list) or len(intent) != 1:
            raise WorkspaceReleaseActivationError(
                "Live v1 requires one exact registration intent"
            )
        intent_fingerprint = canonical_digest(
            {"schema": REGISTRATION_INTENT_SCHEMA, "actions": intent}
        )
        if (
            intent_fingerprint != registration.get("intent_fingerprint")
            or intent_fingerprint != artifact.registration_intent_fingerprint
        ):
            raise WorkspaceReleaseActivationError(
                "registration intent fingerprint is invalid"
            )
        existing = await find_workspace_workflow(
            self.db,
            self.organization_id,
            str(entry.get("path") or ""),
            str(entry.get("function") or ""),
            for_update=True,
        )
        fingerprint = registration_state_fingerprint(existing)
        if (
            fingerprint != registration.get("state_fingerprint")
            or fingerprint != artifact.registration_state_fingerprint
            or _activation_state(existing) != registration.get("state")
        ):
            raise WorkspaceReleaseActivationError(
                "registration state changed after preview"
            )
        try:
            applied = await apply_workspace_registration_plan(
                self.db,
                self.organization_id,
                intent,
                authority=WorkspaceRegistrationMutationAuthority.RELEASE_ACTIVATION,
            )
        except WorkflowRegistrationConflict as exc:
            raise WorkspaceReleaseActivationError(str(exc)) from exc
        effective = manifest.get("effective_registrations") or {}
        key = f"{entry.get('path')}::{entry.get('function')}"
        expected = effective.get(key)
        if (
            not isinstance(expected, dict)
            or len(applied) != 1
            or applied[0].get("workflow_id") != expected.get("workflow_id")
        ):
            raise WorkspaceReleaseActivationError(
                "applied registration differs from the effective manifest"
            )
        workflow = await self.db.get(Workflow, UUID(str(expected["workflow_id"])))
        if workflow is not None:
            workflow.name = str(expected["name"])
            workflow.type = str(expected["type"])
            await self.db.flush()
        if (
            workflow is None
            or workflow.path != expected.get("path")
            or workflow.function_name != expected.get("function")
            or workflow.name != expected.get("name")
            or workflow.type != expected.get("type")
            or (str(workflow.organization_id) if workflow.organization_id else None)
            != expected.get("organization_id")
            or workflow.is_active is not True
            or workflow.access_level != expected.get("access_level")
            or sorted(str(role.id) for role in workflow.roles)
            != expected.get("role_ids")
            or bool(workflow.endpoint_enabled) != expected.get("endpoint_enabled")
            or bool(workflow.public_endpoint) != expected.get("public_endpoint")
            or bool(workflow.api_key_enabled) != expected.get("api_key_enabled")
        ):
            raise WorkspaceReleaseActivationError(
                "registered workflow does not match the effective manifest"
            )
        return applied
