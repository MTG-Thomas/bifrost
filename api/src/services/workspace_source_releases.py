"""Durable accountability for reviewed Workspace source commits."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bifrost.promotion import normalize_workspace_path
from bifrost.workspace_release import canonical_digest
from src.models.contracts.workspace_promotions import (
    WorkspaceSourceReleaseDeclareRequest,
    WorkspaceSourceReleaseListResponse,
    WorkspaceSourceReleaseResponse,
)
from src.models.orm.workspace_promotions import (
    WorkspacePromotionRelease,
    WorkspaceSourceRelease,
)
from src.services.github_actions_oidc import WorkspaceSourceReleaseProducer

DEFAULT_RELEASE_DUE_AFTER = timedelta(minutes=30)
COMPLETION_EVIDENCE_SCHEMA = "bifrost.workspace-source-release-completion/v1"


class WorkspaceSourceReleaseConflict(ValueError):
    """A source commit was redeclared with different immutable evidence."""


def _assert_compatible_replay(
    record: WorkspaceSourceRelease,
    request: WorkspaceSourceReleaseDeclareRequest,
    paths: dict[str, str | None],
) -> None:
    if (
        record.source_tree_sha != request.source_tree_sha
        or dict(record.paths or {}) != paths
        or record.declared_disposition != request.disposition
    ):
        raise WorkspaceSourceReleaseConflict(
            "source commit already has different release accountability evidence"
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_paths(paths: dict[str, str | None]) -> dict[str, str | None]:
    normalized: dict[str, str | None] = {}
    for raw_path, digest in paths.items():
        path = normalize_workspace_path(raw_path)
        if path != raw_path:
            raise ValueError(f"Workspace source path is not normalized: {raw_path!r}")
        normalized[path] = digest
    return dict(sorted(normalized.items()))


def source_release_declaration_digest(
    request: WorkspaceSourceReleaseDeclareRequest,
) -> str:
    payload = request.model_dump(mode="json", exclude_none=True)
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def source_release_response(
    record: WorkspaceSourceRelease,
    *,
    now: datetime | None = None,
) -> WorkspaceSourceReleaseResponse:
    now = now or _utc_now()
    overdue = bool(
        record.disposition in {"pending", "attention_required"}
        and record.due_at is not None
        and record.due_at <= now
    )
    return WorkspaceSourceReleaseResponse(
        id=record.id,
        organization_id=record.organization_id,
        source_commit_sha=record.source_commit_sha,
        source_tree_sha=record.source_tree_sha,
        paths=dict(record.paths or {}),
        declaration_actor=record.declaration_actor,
        producer_oidc_commit_sha=record.producer_oidc_commit_sha,
        producer_event_name=record.producer_event_name,
        producer_run_id=record.producer_run_id,
        producer_triggering_workflow_run_id=(
            record.producer_triggering_workflow_run_id
        ),
        producer_triggering_workflow_run_attempt=(
            record.producer_triggering_workflow_run_attempt
        ),
        producer_declaration_digest=record.producer_declaration_digest,
        producer_actor=record.producer_actor,
        producer_actor_id=record.producer_actor_id,
        disposition=record.disposition,
        reason=record.reason,
        release_row_id=record.release_row_id,
        completion_evidence=record.completion_evidence,
        due_at=record.due_at,
        overdue=overdue,
        requires_attention=record.disposition == "attention_required" or overdue,
        resolved_at=record.resolved_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


class WorkspaceSourceReleaseService:
    def __init__(self, db: AsyncSession, organization_id: UUID):
        self.db = db
        self.organization_id = organization_id

    async def declare(
        self,
        request: WorkspaceSourceReleaseDeclareRequest,
        *,
        created_by: UUID,
        producer: WorkspaceSourceReleaseProducer | None = None,
    ) -> WorkspaceSourceReleaseResponse:
        if producer is not None:
            if producer.organization_id != self.organization_id:
                raise ValueError(
                    "authenticated producer organization does not match the service"
                )
            if producer.source_commit_sha != request.source_commit_sha:
                raise ValueError(
                    "authenticated producer source commit does not match the declaration"
                )
            if (
                producer.declaration_digest is not None
                and producer.declaration_digest
                != source_release_declaration_digest(request)
            ):
                raise ValueError(
                    "authenticated producer declaration digest does not match the request"
                )
        paths = _normalize_paths(request.paths)
        existing = await self.db.scalar(
            select(WorkspaceSourceRelease).where(
                WorkspaceSourceRelease.organization_id == self.organization_id,
                WorkspaceSourceRelease.source_commit_sha == request.source_commit_sha,
            )
        )
        if existing is not None:
            _assert_compatible_replay(existing, request, paths)
            return source_release_response(existing)

        now = _utc_now()
        disposition = request.disposition
        record = WorkspaceSourceRelease(
            organization_id=self.organization_id,
            source_commit_sha=request.source_commit_sha,
            source_tree_sha=request.source_tree_sha,
            paths=paths,
            declaration_actor=(
                "github_actions_oidc" if producer is not None else "platform_admin"
            ),
            producer_oidc_commit_sha=(
                producer.oidc_commit_sha if producer is not None else None
            ),
            producer_event_name=(producer.event_name if producer is not None else None),
            producer_run_id=(producer.run_id if producer is not None else None),
            producer_triggering_workflow_run_id=(
                producer.triggering_workflow_run_id if producer is not None else None
            ),
            producer_triggering_workflow_run_attempt=(
                producer.triggering_workflow_run_attempt
                if producer is not None
                else None
            ),
            producer_declaration_digest=(
                producer.declaration_digest if producer is not None else None
            ),
            producer_actor=(producer.actor if producer is not None else None),
            producer_actor_id=(producer.actor_id if producer is not None else None),
            disposition=disposition,
            declared_disposition=disposition,
            reason=request.reason,
            due_at=(now + DEFAULT_RELEASE_DUE_AFTER)
            if disposition in {"pending", "attention_required"}
            else None,
            resolved_at=now if disposition == "non_production" else None,
            created_by=created_by,
        )
        self.db.add(record)
        try:
            await self.db.commit()
        except IntegrityError:
            # Two delivery attempts for one push can race. The unique key is the
            # serialization point; after rollback, accept only exact evidence.
            await self.db.rollback()
            existing = await self.db.scalar(
                select(WorkspaceSourceRelease).where(
                    WorkspaceSourceRelease.organization_id == self.organization_id,
                    WorkspaceSourceRelease.source_commit_sha
                    == request.source_commit_sha,
                )
            )
            if existing is None:
                raise
            _assert_compatible_replay(existing, request, paths)
            return source_release_response(existing)
        await self.db.refresh(record)
        return source_release_response(record, now=now)

    async def set_manual_disposition(
        self,
        record_id: UUID,
        *,
        disposition: str,
        reason: str,
    ) -> WorkspaceSourceReleaseResponse:
        record = await self._get(record_id, for_update=True)
        if record is None:
            raise KeyError(record_id)
        if record.disposition == "released":
            raise WorkspaceSourceReleaseConflict(
                "released source accountability evidence is immutable"
            )
        if record.disposition == disposition and record.reason == reason:
            return source_release_response(record)
        record.disposition = disposition
        record.reason = reason
        record.resolved_at = _utc_now()
        await self.db.commit()
        await self.db.refresh(record)
        return source_release_response(record)

    async def list(
        self,
        *,
        limit: int = 100,
        tracking_expected: bool = False,
    ) -> WorkspaceSourceReleaseListResponse:
        records = list(
            (
                await self.db.scalars(
                    select(WorkspaceSourceRelease)
                    .where(
                        WorkspaceSourceRelease.organization_id == self.organization_id
                    )
                    .order_by(WorkspaceSourceRelease.created_at.desc())
                    .limit(limit)
                )
            ).all()
        )
        now = _utc_now()
        responses = [source_release_response(record, now=now) for record in records]
        counts = {
            disposition: int(count)
            for disposition, count in (
                await self.db.execute(
                    select(
                        WorkspaceSourceRelease.disposition,
                        func.count(WorkspaceSourceRelease.id),
                    )
                    .where(
                        WorkspaceSourceRelease.organization_id == self.organization_id
                    )
                    .group_by(WorkspaceSourceRelease.disposition)
                )
            ).all()
        }
        overdue_pending = int(
            await self.db.scalar(
                select(func.count(WorkspaceSourceRelease.id)).where(
                    WorkspaceSourceRelease.organization_id == self.organization_id,
                    WorkspaceSourceRelease.disposition == "pending",
                    WorkspaceSourceRelease.due_at.is_not(None),
                    WorkspaceSourceRelease.due_at <= now,
                )
            )
            or 0
        )
        overdue = int(
            await self.db.scalar(
                select(func.count(WorkspaceSourceRelease.id)).where(
                    WorkspaceSourceRelease.organization_id == self.organization_id,
                    WorkspaceSourceRelease.disposition.in_(
                        ("pending", "attention_required")
                    ),
                    WorkspaceSourceRelease.due_at.is_not(None),
                    WorkspaceSourceRelease.due_at <= now,
                )
            )
            or 0
        )
        total = sum(counts.values())
        return WorkspaceSourceReleaseListResponse(
            records=responses,
            total=total,
            pending=counts.get("pending", 0),
            attention_required=(counts.get("attention_required", 0) + overdue_pending),
            overdue=overdue,
            tracking_state=(
                "active" if total or tracking_expected else "not_configured"
            ),
            last_observed_source_commit_sha=(
                responses[0].source_commit_sha if responses else None
            ),
            last_observed_at=responses[0].created_at if responses else None,
            producer_contract=(
                "The trusted protected-main merge producer must declare every "
                "new source commit, including non-production commits."
            ),
        )

    async def get(self, record_id: UUID) -> WorkspaceSourceReleaseResponse:
        record = await self._get(record_id)
        if record is None:
            raise KeyError(record_id)
        return source_release_response(record)

    async def _get(
        self, record_id: UUID, *, for_update: bool = False
    ) -> WorkspaceSourceRelease | None:
        statement = select(WorkspaceSourceRelease).where(
            WorkspaceSourceRelease.id == record_id,
            WorkspaceSourceRelease.organization_id == self.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.db.scalar(statement)


async def reconcile_source_releases_after_lock(
    db: AsyncSession,
    *,
    organization_id: UUID,
    release_row_id: UUID,
    release_id: str,
    runtime_hashes: dict[str, str],
    history_commit_sha: str,
    history_hashes: dict[str, str | None],
    verified_at: datetime | None = None,
) -> list[UUID]:
    """Close records only when runtime and signed history prove exact bytes."""
    verified_at = verified_at or _utc_now()
    records = list(
        (
            await db.scalars(
                select(WorkspaceSourceRelease)
                .where(
                    WorkspaceSourceRelease.organization_id == organization_id,
                    WorkspaceSourceRelease.disposition.in_(
                        ("pending", "attention_required", "deferred")
                    ),
                )
                .with_for_update()
            )
        ).all()
    )
    completed: list[UUID] = []
    for record in records:
        paths = dict(record.paths or {})
        if any(digest is None for digest in paths.values()):
            continue
        if not paths or any(
            runtime_hashes.get(path) != digest for path, digest in paths.items()
        ):
            continue
        if any(history_hashes.get(path) != digest for path, digest in paths.items()):
            continue
        evidence: dict[str, Any] = {
            "schema_version": COMPLETION_EVIDENCE_SCHEMA,
            "source_commit_sha": record.source_commit_sha,
            "source_tree_sha": record.source_tree_sha,
            "workspace_release_row_id": str(release_row_id),
            "workspace_release_id": release_id,
            "runtime_sha256": paths,
            "history": {
                "commit_sha": history_commit_sha,
                "file_sha256": paths,
            },
            "verified_at": verified_at.isoformat(),
        }
        evidence["evidence_id"] = canonical_digest(evidence)
        record.disposition = "released"
        record.reason = None
        record.release_row_id = release_row_id
        record.completion_evidence = evidence
        record.resolved_at = verified_at
        completed.append(record.id)
    await db.flush()
    return completed


async def mark_source_release_attention(
    db: AsyncSession,
    *,
    organization_id: UUID,
    source_commit_sha: str,
    code: str,
    message: str,
) -> UUID | None:
    record = await db.scalar(
        select(WorkspaceSourceRelease)
        .where(
            WorkspaceSourceRelease.organization_id == organization_id,
            WorkspaceSourceRelease.source_commit_sha == source_commit_sha,
            WorkspaceSourceRelease.disposition == "pending",
        )
        .with_for_update()
    )
    if record is None:
        return None
    record.disposition = "attention_required"
    record.reason = f"{code}: {message}"[:2000]
    await db.flush()
    return record.id


async def sweep_overdue_workspace_releases(
    db: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, list[str]]:
    """Turn missed source and history deadlines into durable attention state."""
    now = now or _utc_now()
    # Projection takes the Live release row before source-accountability rows.
    # Keep the scheduler in the same order so the two transactions cannot
    # deadlock while a history lock completes at the attention deadline.
    live_releases = list(
        (
            await db.scalars(
                select(WorkspacePromotionRelease)
                .where(
                    WorkspacePromotionRelease.activation_state == "live",
                    WorkspacePromotionRelease.lock_state.in_(("queued", "in_progress")),
                    WorkspacePromotionRelease.attention_deadline.is_not(None),
                    WorkspacePromotionRelease.attention_deadline <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for release in live_releases:
        release.lock_state = "attention_required"
        release.error_code = "workspace_release_history_overdue"
        release.error_message = (
            "Live runtime has not reached verified production-live history "
            "before its accountability deadline"
        )

    source_records = list(
        (
            await db.scalars(
                select(WorkspaceSourceRelease)
                .where(
                    WorkspaceSourceRelease.disposition == "pending",
                    WorkspaceSourceRelease.due_at.is_not(None),
                    WorkspaceSourceRelease.due_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for record in source_records:
        record.disposition = "attention_required"
        record.reason = "reviewed Workspace source has not reached verified production"
    await db.flush()
    return {
        "source_release_ids": [str(record.id) for record in source_records],
        "workspace_release_ids": [str(release.id) for release in live_releases],
    }


__all__ = [
    "WorkspaceSourceReleaseConflict",
    "WorkspaceSourceReleaseService",
    "mark_source_release_attention",
    "reconcile_source_releases_after_lock",
    "source_release_declaration_digest",
    "source_release_response",
    "sweep_overdue_workspace_releases",
]
