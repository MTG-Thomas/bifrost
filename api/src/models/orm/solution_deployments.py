"""Immutable Solution deployment revisions and exact dependency edges."""

from __future__ import annotations

from datetime import datetime, timezone
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base

_DEPLOYMENT_ID_COLUMN = "solution_deployments.id"
_DEPLOYMENT_SOLUTION_COLUMN = "solution_deployments.solution_id"


class SolutionDeployment(Base):
    """An immutable candidate or activated runtime closure for a Solution."""

    __tablename__ = "solution_deployments"
    __table_args__ = (
        Index("ix_solution_deployments_solution_created", "solution_id", "created_at"),
        Index("ix_solution_deployments_org_state", "organization_id", "state"),
        UniqueConstraint("id", "solution_id", name="uq_solution_deployment_id_solution"),
        ForeignKeyConstraint(
            ["parent_deployment_id", "solution_id"],
            [_DEPLOYMENT_ID_COLUMN, _DEPLOYMENT_SOLUTION_COLUMN],
            name="fk_solution_deployment_parent_same_solution",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["base_deployment_id", "solution_id"],
            [_DEPLOYMENT_ID_COLUMN, _DEPLOYMENT_SOLUTION_COLUMN],
            name="fk_solution_deployment_base_same_solution",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "state IN ('draft','building','validated','ready','activating','active',"
            "'superseded','conflicted','failed','recovery_required','aborted',"
            "'committed_unpushed')",
            name="ck_solution_deployments_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=True
    )
    solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="RESTRICT"), nullable=False
    )
    parent_deployment_id: Mapped[UUID | None] = mapped_column(nullable=True)
    base_deployment_id: Mapped[UUID | None] = mapped_column(nullable=True)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    declared_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bundle_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    compiled_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    compiled_manifest_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    resolution_map: Mapped[dict] = mapped_column(JSONB, nullable=False)
    resolution_map_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    source_artifact_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    runtime_storage_prefix: Mapped[str] = mapped_column(String(2048), nullable=False)
    git_repository: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    git_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    git_commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    git_push_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    codex_worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    validation_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    failure_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    dependencies: Mapped[list["SolutionDeploymentDependency"]] = relationship(
        back_populates="deployment",
        lazy="selectin",
        foreign_keys="SolutionDeploymentDependency.deployment_id",
    )


class SolutionDeploymentDependency(Base):
    """Exact immutable dependency deployment selected during compilation."""

    __tablename__ = "solution_deployment_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["dependency_deployment_id", "dependency_solution_id"],
            [_DEPLOYMENT_ID_COLUMN, _DEPLOYMENT_SOLUTION_COLUMN],
            name="fk_solution_dependency_exact_deployment",
            ondelete="RESTRICT",
        ),
    )
    deployment_id: Mapped[UUID] = mapped_column(
        ForeignKey(_DEPLOYMENT_ID_COLUMN, ondelete="RESTRICT"), primary_key=True
    )
    dependency_solution_id: Mapped[UUID] = mapped_column(
        ForeignKey("solutions.id", ondelete="RESTRICT"), primary_key=True
    )
    dependency_deployment_id: Mapped[UUID] = mapped_column(nullable=False)
    declared_constraint: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_bundle_hash: Mapped[str] = mapped_column(String(71), nullable=False)

    deployment: Mapped[SolutionDeployment] = relationship(
        back_populates="dependencies", foreign_keys=[deployment_id]
    )
