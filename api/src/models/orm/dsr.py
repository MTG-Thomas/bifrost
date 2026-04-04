"""Desired State Resource ORM models."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import ENUM as PgEnum, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.orm.base import Base


class DesiredStateResource(Base):
    """Declarative resource tracked by engine-backed desired state."""

    __tablename__ = "desired_state_resources"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(255), nullable=False)
    engine: Mapped[str] = mapped_column(
        PgEnum("tofu", "terraform", "python", name="dsr_engine", create_type=False),
        nullable=False,
        default="tofu",
        server_default="tofu",
    )
    spec: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        PgEnum("pending", "planned", "applied", "failed", name="dsr_resource_status", create_type=False),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        default=None,
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    plans: Mapped[list["DesiredStatePlan"]] = relationship(back_populates="resource", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_dsr_organization_id", "organization_id"),
        Index("ix_dsr_status", "status"),
        Index("ix_dsr_engine", "engine"),
    )


class DesiredStatePlan(Base):
    """Immutable plan artifact for a specific resource version."""

    __tablename__ = "desired_state_plans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    resource_id: Mapped[UUID] = mapped_column(
        ForeignKey("desired_state_resources.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    engine: Mapped[str] = mapped_column(
        PgEnum("tofu", "terraform", "python", name="dsr_engine", create_type=False),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        PgEnum("pending", "approved", "applied", "failed", name="dsr_plan_status", create_type=False),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    plan_path: Mapped[str] = mapped_column(Text, nullable=False)
    plan_json_path: Mapped[str] = mapped_column(Text, nullable=False)
    plan_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    summary_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    risk_level: Mapped[str] = mapped_column(
        PgEnum("low", "medium", "high", name="dsr_risk_level", create_type=False),
        nullable=False,
    )
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    approved_by: Mapped[str | None] = mapped_column(String(255), default=None)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )

    resource: Mapped[DesiredStateResource] = relationship(back_populates="plans")
    runs: Mapped[list["DesiredStateRun"]] = relationship(back_populates="plan", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_dsr_plan_resource_id", "resource_id"),
        Index("ix_dsr_plan_status", "status"),
        Index("ix_dsr_plan_requires_approval", "requires_approval"),
    )


class DesiredStateRun(Base):
    """Apply execution record for a plan."""

    __tablename__ = "desired_state_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("desired_state_plans.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        PgEnum("pending", "running", "completed", "failed", name="dsr_run_status", create_type=False),
        nullable=False,
        default="pending",
        server_default="pending",
    )
    logs_path: Mapped[str] = mapped_column(Text, nullable=False)
    outputs_path: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    plan: Mapped[DesiredStatePlan] = relationship(back_populates="runs")

    __table_args__ = (
        Index("ix_dsr_run_plan_id", "plan_id"),
        Index("ix_dsr_run_status", "status"),
    )
