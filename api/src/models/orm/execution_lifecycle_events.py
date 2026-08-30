"""Append-only normalized lifecycle events for infrastructure attempts."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class ExecutionLifecycleEvent(Base):
    """One durable, payload-free transition in an execution attempt."""

    __tablename__ = "execution_lifecycle_events"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    attempt_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("execution_attempts.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    logical_job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    logical_job_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    workload_class: Mapped[str] = mapped_column(String(64), nullable=False)
    admission_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    mechanism: Mapped[str] = mapped_column(String(32), nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "attempt_id", "sequence", name="uq_execution_lifecycle_attempt_sequence"
        ),
        Index(
            "ix_execution_lifecycle_logical_occurred",
            "logical_job_type",
            "logical_job_id",
            "occurred_at",
        ),
        Index(
            "ix_execution_lifecycle_org_occurred",
            "organization_id",
            "occurred_at",
        ),
        Index("ix_execution_lifecycle_event_occurred", "event_type", "occurred_at"),
    )
