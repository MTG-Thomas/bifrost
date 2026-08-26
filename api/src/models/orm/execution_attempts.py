"""Durable infrastructure attempts for logical executions and platform jobs."""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.orm.base import Base


class ExecutionAttempt(Base):
    """One runner claim for a logical workflow, agent run, or PlatformJob."""

    __tablename__ = "execution_attempts"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    logical_job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    logical_job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    organization_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="claimed", server_default="claimed"
    )

    policy_identifier: Mapped[str] = mapped_column(String(100), nullable=False)
    workload_class: Mapped[str] = mapped_column(String(64), nullable=False)
    admission_policy: Mapped[str] = mapped_column(String(64), nullable=False)
    mechanism: Mapped[str] = mapped_column(String(32), nullable=False)
    queue_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    replay_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    process_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_token: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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
            "logical_job_type",
            "logical_job_id",
            "attempt_number",
            name="uq_execution_attempt_logical_number",
        ),
        Index(
            "ix_execution_attempt_logical_job",
            "logical_job_type",
            "logical_job_id",
            "attempt_number",
        ),
        Index("ix_execution_attempt_status_started", "status", "started_at"),
        Index("ix_execution_attempt_organization", "organization_id"),
        Index(
            "uq_execution_attempt_active_lease",
            "lease_token",
            unique=True,
            postgresql_where=text("lease_token IS NOT NULL AND completed_at IS NULL"),
        ),
    )
