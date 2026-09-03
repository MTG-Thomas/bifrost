"""
Execution and ExecutionLog ORM models.

Represents workflow executions and their logs.
"""
# ruff: noqa: F821
# pyright: reportUndefinedVariable=false

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SQLAlchemyEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.enums import ExecutionStatus
from src.models.orm.base import Base



# Identity entity — execution telemetry, not resolved by name with cascade.
# See api/src/repositories/README.md.
class Execution(Base):
    """Execution database table."""

    __tablename__ = "executions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    workflow_name: Mapped[str] = mapped_column(String(255))
    workflow_version: Mapped[str | None] = mapped_column(String(50), default=None)
    status: Mapped[ExecutionStatus] = mapped_column(
        SQLAlchemyEnum(
            ExecutionStatus,
            name="execution_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=ExecutionStatus.PENDING,
    )
    parameters: Mapped[dict] = mapped_column(JSONB, default={})
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_type: Mapped[str | None] = mapped_column(String(50), default=None)
    variables: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    execution_context: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    # Resource metrics (captured from worker process)
    peak_memory_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    process_rss_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    cpu_user_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    cpu_system_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    cpu_total_seconds: Mapped[float | None] = mapped_column(Float, default=None)

    # Economics - final values for this execution
    time_saved: Mapped[int] = mapped_column(Integer, default=0)  # Minutes saved
    value: Mapped[float] = mapped_column(Numeric(10, 2), default=0)  # Value generated

    executed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    executed_by_name: Mapped[str] = mapped_column(String(255))
    organization_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organizations.id"), default=None
    )
    form_id: Mapped[UUID | None] = mapped_column(ForeignKey("forms.id", ondelete="SET NULL", onupdate="CASCADE"), default=None)
    workflow_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL", onupdate="CASCADE"), default=None
    )  # FK to the workflow that was executed (null for inline scripts/legacy)
    solution_deployment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("solution_deployments.id", ondelete="RESTRICT"), default=None
    )
    runtime_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="legacy", server_default="legacy"
    )
    runtime_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    runtime_evidence_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    dispatch_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    dispatch_evidence_hash: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    # Null identifies rows created before attempt tracking was deployed. New
    # ORM-created executions opt in even if they fail before queue claim and
    # therefore legitimately have an empty attempt list.
    attempt_tracking_version: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )
    api_key_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL", onupdate="CASCADE"), default=None
    )  # Workflow whose API key triggered this execution (null for user-triggered)
    is_local_execution: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    execution_model: Mapped[str | None] = mapped_column(
        String(20), default=None
    )  # 'process' or 'thread' - tracks which execution model ran the job
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("cli_sessions.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, nullable=True
    )

    # Relationships
    executed_by_user: Mapped["User"] = relationship(back_populates="executions")
    cli_session: Mapped["CLISession | None"] = relationship(back_populates="executions")
    workflow: Mapped["Workflow | None"] = relationship(
        foreign_keys=[workflow_id]
    )  # The workflow that was executed
    api_key_workflow: Mapped["Workflow | None"] = relationship(
        foreign_keys=[api_key_id]
    )  # The workflow whose API key triggered this execution
    organization: Mapped["Organization | None"] = relationship(
        back_populates="executions"
    )
    form: Mapped["Form | None"] = relationship(back_populates="executions")
    logs: Mapped[list["ExecutionLog"]] = relationship(back_populates="execution")
    ai_usages: Mapped[list["AIUsage"]] = relationship(back_populates="execution")
    attempts: Mapped[list["WorkflowExecutionAttempt"]] = relationship(
        back_populates="execution",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="WorkflowExecutionAttempt.attempt_number",
    )

    __table_args__ = (
        Index("ix_executions_org_status", "organization_id", "status"),
        Index("ix_executions_created", "created_at"),
        Index("ix_executions_started_at", "started_at"),
        Index("ix_executions_user", "executed_by"),
        Index("ix_executions_workflow", "workflow_name"),
        Index("ix_executions_is_local_execution", "is_local_execution"),
        Index("ix_executions_session_id", "session_id"),
        Index("ix_executions_workflow_id", "workflow_id"),
        Index("ix_executions_solution_deployment_id", "solution_deployment_id"),
    )


class WorkflowExecutionAttempt(Base):
    """One durable claim/run attempt for a logical workflow execution.

    Attempt rows contain bounded lifecycle evidence only. Inputs, results,
    variables, credentials, and logs remain on their existing authorized data
    surfaces.
    """

    __tablename__ = "workflow_execution_attempts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    claim_token: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_phase: Mapped[str | None] = mapped_column(String(32), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    worker_incarnation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    process_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    runtime_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    runtime_evidence_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    dispatch_evidence_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    policy_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)
    policy_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="workflow-attempt/v1"
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    peak_memory_bytes: Mapped[int | None] = mapped_column(BigInteger)
    cpu_total_seconds: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default=text("NOW()"),
    )

    execution: Mapped["Execution"] = relationship(back_populates="attempts")

    __table_args__ = (
        UniqueConstraint(
            "execution_id", "attempt_number", name="uq_workflow_execution_attempt_number"
        ),
        UniqueConstraint("claim_token", name="uq_workflow_execution_attempt_claim_token"),
        CheckConstraint(
            "status IN ('dispatching', 'published', 'claimed', 'running', "
            "'succeeded', 'failed', 'timed_out', "
            "'cancelled', 'admission_rejected', 'worker_lost')",
            name="ck_workflow_execution_attempt_status",
        ),
        CheckConstraint(
            "phase IN ('dispatch', 'queue', 'claim', 'admission', 'execution', "
            "'result', 'terminal')",
            name="ck_workflow_execution_attempt_phase",
        ),
        CheckConstraint(
            "failure_phase IS NULL OR failure_phase IN ('dispatch', 'queue', "
            "'claim', 'admission', 'execution', 'result', 'worker', "
            "'cancellation')",
            name="ck_workflow_execution_attempt_failure_phase",
        ),
        CheckConstraint(
            "((status IN ('dispatching', 'published', 'claimed', 'running') "
            "AND completed_at IS NULL) OR (status IN ('succeeded', 'failed', "
            "'timed_out', 'cancelled', 'admission_rejected', 'worker_lost') "
            "AND completed_at IS NOT NULL))",
            name="ck_workflow_execution_attempt_terminal_time",
        ),
        CheckConstraint(
            "((status = 'dispatching' AND claim_token IS NULL AND "
            "published_at IS NULL AND claimed_at IS NULL AND started_at IS NULL) "
            "OR (status = 'published' AND claim_token IS NULL AND "
            "published_at IS NOT NULL AND claimed_at IS NULL AND started_at IS NULL) "
            "OR (status = 'claimed' AND claim_token IS NOT NULL AND "
            "published_at IS NOT NULL AND claimed_at IS NOT NULL) "
            "OR (status = 'running' AND claim_token IS NOT NULL AND "
            "published_at IS NOT NULL AND claimed_at IS NOT NULL AND "
            "started_at IS NOT NULL) OR completed_at IS NOT NULL)",
            name="ck_workflow_execution_attempt_state_shape",
        ),
        Index(
            "uq_workflow_execution_attempt_active",
            "execution_id",
            unique=True,
            postgresql_where=text("completed_at IS NULL"),
        ),
        Index(
            "ix_workflow_execution_attempts_active_heartbeat",
            "status",
            "heartbeat_at",
            postgresql_where=text(
                "completed_at IS NULL AND status IN ('claimed', 'running')"
            ),
        ),
    )


class ExecutionLog(Base):
    """Execution log entries."""

    __tablename__ = "execution_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[UUID] = mapped_column(ForeignKey("executions.id"))
    level: Mapped[str] = mapped_column(String(20))
    message: Mapped[str] = mapped_column(Text)
    log_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=text("NOW()")
    )
    sequence: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    execution: Mapped["Execution"] = relationship(back_populates="logs")

    __table_args__ = (Index("ix_execution_logs_exec_seq", "execution_id", "sequence"),)
