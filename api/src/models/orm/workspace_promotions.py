"""Immutable Workspace promotion artifacts and their mutable release state."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class WorkspacePromotionArtifact(Base):
    """One content-addressed preview; reviewed rows are permanently immutable."""

    __tablename__ = "workspace_promotion_artifacts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[str] = mapped_column(String(71), nullable=False)
    content_id: Mapped[str | None] = mapped_column(String(71), nullable=True)
    closure_id: Mapped[str | None] = mapped_column(String(71), nullable=True)
    release_id: Mapped[str | None] = mapped_column(String(71), nullable=True)
    base_release_id: Mapped[str | None] = mapped_column(String(72), nullable=True)
    base_manifest_id: Mapped[str | None] = mapped_column(String(71), nullable=True)
    effective_manifest_id: Mapped[str | None] = mapped_column(String(71), nullable=True)
    effective_registration_manifest_id: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    registration_intent_fingerprint: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    registration_state_fingerprint: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    target_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="workspace"
    )
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="workflow"
    )
    entry_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    entry_function: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(71), nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_tree_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_artifact_key: Mapped[str] = mapped_column(String(1500), nullable=False)
    manifest_key: Mapped[str] = mapped_column(String(1500), nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_class: Mapped[str] = mapped_column(String(10), nullable=False)
    disposition: Mapped[str] = mapped_column(String(30), nullable=False)
    artifact_state: Mapped[str] = mapped_column(String(30), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    supersedes_artifact_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_workspace_promotion_artifact_org_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "supersedes_artifact_id"],
            [
                "workspace_promotion_artifacts.organization_id",
                "workspace_promotion_artifacts.id",
            ],
            name="fk_workspace_promotion_artifact_supersedes",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_workspace_promotion_artifact_candidate",
            "organization_id",
            "candidate_id",
            unique=True,
        ),
        Index(
            "ix_workspace_promotion_artifact_content",
            "organization_id",
            "content_id",
        ),
        Index(
            "ix_workspace_promotion_artifact_release",
            "organization_id",
            "release_id",
        ),
        Index(
            "ix_workspace_promotion_artifact_draft_expiry",
            "expires_at",
            postgresql_where=text("target_kind = 'draft'"),
        ),
        Index(
            "uq_workspace_promotion_artifact_supersedes",
            "supersedes_artifact_id",
            unique=True,
            postgresql_where=text("supersedes_artifact_id IS NOT NULL"),
        ),
        CheckConstraint(
            "artifact_state IN ('previewed', 'eligible', 'review_required', 'invalid')",
            name="ck_workspace_promotion_artifact_state",
        ),
        CheckConstraint(
            "risk_class IN ('R0', 'R1', 'R2')",
            name="ck_workspace_promotion_artifact_risk",
        ),
    )


class WorkspacePromotionRelease(Base):
    """Mutable activation and Git lock-in state for one immutable artifact."""

    __tablename__ = "workspace_promotion_releases"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    artifact_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    previous_release_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    activation_state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="prepared"
    )
    lock_state: Mapped[str] = mapped_column(
        String(30), nullable=False, default="not_queued"
    )
    workspace_changeset_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace_repo_changesets.id", ondelete="SET NULL"),
        nullable=True,
    )
    lock_in_job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("platform_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    activation_evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    prepared_evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    prepared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lock_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attention_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "id",
            name="uq_workspace_promotion_release_org_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "artifact_id"],
            [
                "workspace_promotion_artifacts.organization_id",
                "workspace_promotion_artifacts.id",
            ],
            name="fk_workspace_promotion_release_artifact_org",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["organization_id", "previous_release_id"],
            [
                "workspace_promotion_releases.organization_id",
                "workspace_promotion_releases.id",
            ],
            name="fk_workspace_promotion_release_previous_org",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_workspace_promotion_release_idempotency",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_workspace_promotion_release_artifact",
            "artifact_id",
            unique=True,
        ),
        Index(
            "uq_workspace_promotion_release_live",
            "activation_state",
            unique=True,
            postgresql_where=text("activation_state = 'live'"),
        ),
        CheckConstraint(
            "activation_state IN ('prepared', 'activating', 'live', "
            "'activation_failed', 'recovery_required', 'rolled_back', 'superseded')",
            name="ck_workspace_promotion_release_activation_state",
        ),
        CheckConstraint(
            "lock_state IN ('not_queued', 'queued', 'in_progress', 'locked', "
            "'attention_required', 'superseded')",
            name="ck_workspace_promotion_release_lock_state",
        ),
        CheckConstraint(
            "activation_state <> 'live' OR ("
            "lock_state IN ('queued', 'in_progress', 'locked', 'attention_required') "
            "AND lock_in_job_id IS NOT NULL)",
            name="ck_workspace_promotion_release_live_has_lock_job",
        ),
    )


class WorkspaceSourceRelease(Base):
    """Durable disposition for one reviewed Workspace source commit."""

    __tablename__ = "workspace_source_releases"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    source_tree_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    paths: Mapped[dict[str, str | None]] = mapped_column(JSONB, nullable=False)
    disposition: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending"
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    release_row_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workspace_promotion_releases.id", ondelete="SET NULL"),
        nullable=True,
    )
    completion_evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "source_commit_sha",
            name="uq_workspace_source_release_commit",
        ),
        Index(
            "ix_workspace_source_release_attention",
            "disposition",
            "due_at",
            postgresql_where=text("disposition IN ('pending', 'attention_required')"),
        ),
        CheckConstraint(
            "disposition IN ('pending', 'attention_required', 'released', "
            "'deferred', 'non_production')",
            name="ck_workspace_source_release_disposition",
        ),
        CheckConstraint(
            "disposition <> 'released' OR "
            "(release_row_id IS NOT NULL AND completion_evidence IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="ck_workspace_source_release_released_evidence",
        ),
        CheckConstraint(
            "disposition <> 'attention_required' OR reason IS NOT NULL",
            name="ck_workspace_source_release_attention_reason",
        ),
        CheckConstraint(
            "disposition NOT IN ('deferred', 'non_production') OR "
            "(reason IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_workspace_source_release_manual_reason",
        ),
    )
