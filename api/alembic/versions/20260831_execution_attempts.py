"""Add durable workflow execution attempt evidence.

Revision ID: 20260831_execution_attempts
Revises: 20260827_event_criteria
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260831_execution_attempts"
down_revision: str | Sequence[str] = "20260827_event_criteria"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("attempt_tracking_version", sa.String(length=16), nullable=True),
    )
    op.create_table(
        "execution_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("failure_phase", sa.String(length=32), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("worker_incarnation_id", sa.Uuid(), nullable=True),
        sa.Column("process_id", sa.String(length=255), nullable=True),
        sa.Column("runtime_mode", sa.String(length=32), nullable=True),
        sa.Column("runtime_evidence_hash", sa.String(length=71), nullable=True),
        sa.Column("dispatch_evidence_hash", sa.String(length=71), nullable=True),
        sa.Column("policy_digest", sa.String(length=71), nullable=True),
        sa.Column(
            "policy_version",
            sa.String(length=32),
            server_default="workflow-attempt/v1",
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("peak_memory_bytes", sa.BigInteger(), nullable=True),
        sa.Column("cpu_total_seconds", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('dispatching', 'published', 'claimed', 'running', "
            "'succeeded', 'failed', 'timed_out', "
            "'cancelled', 'admission_rejected', 'worker_lost')",
            name="ck_execution_attempt_status",
        ),
        sa.CheckConstraint(
            "phase IN ('dispatch', 'queue', 'claim', 'admission', 'execution', "
            "'result', 'terminal')",
            name="ck_execution_attempt_phase",
        ),
        sa.CheckConstraint(
            "failure_phase IS NULL OR failure_phase IN ('dispatch', 'queue', "
            "'claim', 'admission', 'execution', 'result', 'worker', "
            "'cancellation')",
            name="ck_execution_attempt_failure_phase",
        ),
        sa.CheckConstraint(
            "((status IN ('dispatching', 'published', 'claimed', 'running') "
            "AND completed_at IS NULL) OR (status IN ('succeeded', 'failed', "
            "'timed_out', 'cancelled', 'admission_rejected', 'worker_lost') "
            "AND completed_at IS NOT NULL))",
            name="ck_execution_attempt_terminal_time",
        ),
        sa.CheckConstraint(
            "((status = 'dispatching' AND claim_token IS NULL AND "
            "published_at IS NULL AND claimed_at IS NULL AND started_at IS NULL) "
            "OR (status = 'published' AND claim_token IS NULL AND "
            "published_at IS NOT NULL AND claimed_at IS NULL AND started_at IS NULL) "
            "OR (status = 'claimed' AND claim_token IS NOT NULL AND "
            "published_at IS NOT NULL AND claimed_at IS NOT NULL) "
            "OR (status = 'running' AND claim_token IS NOT NULL AND "
            "published_at IS NOT NULL AND claimed_at IS NOT NULL AND "
            "started_at IS NOT NULL) OR completed_at IS NOT NULL)",
            name="ck_execution_attempt_state_shape",
        ),
        sa.ForeignKeyConstraint(["execution_id"], ["executions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_id", "attempt_number", name="uq_execution_attempt_number"
        ),
        sa.UniqueConstraint("claim_token", name="uq_execution_attempt_claim_token"),
    )
    op.create_index(
        "uq_execution_attempt_active",
        "execution_attempts",
        ["execution_id"],
        unique=True,
        postgresql_where=sa.text("completed_at IS NULL"),
    )
    op.create_index(
        "ix_execution_attempts_active_heartbeat",
        "execution_attempts",
        ["status", "heartbeat_at"],
        postgresql_where=sa.text(
            "completed_at IS NULL AND status IN ('claimed', 'running')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_attempts_active_heartbeat",
        table_name="execution_attempts",
    )
    op.drop_index("uq_execution_attempt_active", table_name="execution_attempts")
    op.drop_table("execution_attempts")
    op.drop_column("executions", "attempt_tracking_version")
