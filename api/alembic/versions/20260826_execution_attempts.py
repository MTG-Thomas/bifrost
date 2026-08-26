"""Add durable infrastructure attempt history.

Revision ID: 20260826_execution_attempts
Revises: 20260825_delivery_attempt
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_execution_attempts"
down_revision: str | Sequence[str] = "20260825_delivery_attempt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("logical_job_type", sa.String(length=32), nullable=False),
        sa.Column("logical_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="claimed",
            nullable=False,
        ),
        sa.Column("policy_identifier", sa.String(length=100), nullable=False),
        sa.Column("workload_class", sa.String(length=64), nullable=False),
        sa.Column("admission_policy", sa.String(length=64), nullable=False),
        sa.Column("mechanism", sa.String(length=32), nullable=False),
        sa.Column("queue_name", sa.String(length=255), nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("replay_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("process_id", sa.Integer(), nullable=True),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "logical_job_type",
            "logical_job_id",
            "attempt_number",
            name="uq_execution_attempt_logical_number",
        ),
    )
    op.create_index(
        "ix_execution_attempt_logical_job",
        "execution_attempts",
        ["logical_job_type", "logical_job_id", "attempt_number"],
    )
    op.create_index(
        "ix_execution_attempt_status_started",
        "execution_attempts",
        ["status", "started_at"],
    )
    op.create_index(
        "ix_execution_attempt_organization",
        "execution_attempts",
        ["organization_id"],
    )
    op.create_index(
        "uq_execution_attempt_active_lease",
        "execution_attempts",
        ["lease_token"],
        unique=True,
        postgresql_where=sa.text("lease_token IS NOT NULL AND completed_at IS NULL"),
    )
    op.create_table(
        "execution_lifecycle_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("logical_job_type", sa.String(length=32), nullable=False),
        sa.Column("logical_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("policy_identifier", sa.String(length=100), nullable=False),
        sa.Column("workload_class", sa.String(length=64), nullable=False),
        sa.Column("admission_policy", sa.String(length=64), nullable=False),
        sa.Column("mechanism", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=True),
        sa.Column("reason_code", sa.String(length=100), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["attempt_id"], ["execution_attempts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attempt_id",
            "sequence",
            name="uq_execution_lifecycle_attempt_sequence",
        ),
    )
    op.create_index(
        "ix_execution_lifecycle_logical_occurred",
        "execution_lifecycle_events",
        ["logical_job_type", "logical_job_id", "occurred_at"],
    )
    op.create_index(
        "ix_execution_lifecycle_org_occurred",
        "execution_lifecycle_events",
        ["organization_id", "occurred_at"],
    )
    op.create_index(
        "ix_execution_lifecycle_event_occurred",
        "execution_lifecycle_events",
        ["event_type", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_execution_lifecycle_event_occurred",
        table_name="execution_lifecycle_events",
    )
    op.drop_index(
        "ix_execution_lifecycle_org_occurred",
        table_name="execution_lifecycle_events",
    )
    op.drop_index(
        "ix_execution_lifecycle_logical_occurred",
        table_name="execution_lifecycle_events",
    )
    op.drop_table("execution_lifecycle_events")
    op.drop_index("uq_execution_attempt_active_lease", table_name="execution_attempts")
    op.drop_index("ix_execution_attempt_organization", table_name="execution_attempts")
    op.drop_index("ix_execution_attempt_status_started", table_name="execution_attempts")
    op.drop_index("ix_execution_attempt_logical_job", table_name="execution_attempts")
    op.drop_table("execution_attempts")
