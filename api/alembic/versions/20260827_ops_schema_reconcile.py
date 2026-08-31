"""Reconcile execution-operations tables after the deployed-head ancestry incident.

Revision ID: 20260827_ops_reconcile
Revises: 20260827_event_criteria

Production had already stamped ``20260827_event_criteria`` before three new
revisions were inserted behind it. Alembic correctly considered that database
current and therefore never ran the inserted revisions. This forward-only
repair creates the missing objects without restamping or downgrading, while
remaining a no-op for databases that traversed the amended graph from scratch.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260827_ops_reconcile"
down_revision: str | Sequence[str] = "20260827_event_criteria"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    """Return whether the current schema contains a table."""
    return sa.inspect(op.get_bind()).has_table(name)


def _ensure_index(
    table_name: str,
    index_name: str,
    columns: list[str],
    *,
    unique: bool = False,
    postgresql_where: sa.ColumnElement[bool] | None = None,
) -> None:
    """Create one missing index even when its table already existed."""
    existing = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }
    if index_name not in existing:
        op.create_index(
            index_name,
            table_name,
            columns,
            unique=unique,
            postgresql_where=postgresql_where,
        )


def upgrade() -> None:
    """Create every missing table and index required by execution operations."""
    if not _has_table("execution_attempts"):
        op.create_table(
            "execution_attempts",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("logical_job_type", sa.String(length=32), nullable=False),
            sa.Column("logical_job_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("attempt_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=32), server_default="claimed", nullable=False),
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
                "logical_job_type", "logical_job_id", "attempt_number",
                name="uq_execution_attempt_logical_number",
            ),
        )
    _ensure_index(
        "execution_attempts",
        "ix_execution_attempt_status_started",
        ["status", "started_at"],
    )
    _ensure_index(
        "execution_attempts",
        "ix_execution_attempt_organization",
        ["organization_id"],
    )
    _ensure_index(
        "execution_attempts",
        "uq_execution_attempt_active_lease",
        ["lease_token"],
        unique=True,
        postgresql_where=sa.text(
            "lease_token IS NOT NULL AND completed_at IS NULL"
        ),
    )

    if not _has_table("execution_lifecycle_events"):
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
            sa.ForeignKeyConstraint(["attempt_id"], ["execution_attempts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "attempt_id",
                "sequence",
                name="uq_execution_lifecycle_attempt_sequence",
            ),
        )
    _ensure_index(
        "execution_lifecycle_events",
        "ix_execution_lifecycle_logical_occurred",
        ["logical_job_type", "logical_job_id", "occurred_at"],
    )
    _ensure_index(
        "execution_lifecycle_events",
        "ix_execution_lifecycle_org_occurred",
        ["organization_id", "occurred_at"],
    )
    _ensure_index(
        "execution_lifecycle_events",
        "ix_execution_lifecycle_event_occurred",
        ["event_type", "occurred_at"],
    )

    if not _has_table("worker_control_commands"):
        op.create_table(
            "worker_control_commands",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("worker_id", sa.String(length=255), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("process_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
            sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("failure_message", sa.Text(), nullable=True),
            sa.Column(
                "requested_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
    _ensure_index(
        "worker_control_commands",
        "ix_worker_control_worker_requested",
        ["worker_id", "requested_at"],
    )
    _ensure_index(
        "worker_control_commands",
        "ix_worker_control_status_requested",
        ["status", "requested_at"],
    )
    _ensure_index(
        "worker_control_commands",
        "ix_worker_control_requester",
        ["requested_by_user_id"],
    )

    if not _has_table("poison_message_dispositions"):
        op.create_table(
            "poison_message_dispositions",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("queue_name", sa.String(length=255), nullable=False),
            sa.Column("message_id", sa.String(length=255), nullable=True),
            sa.Column("idempotency_key", sa.String(length=255), nullable=True),
            sa.Column("action", sa.String(length=32), nullable=False),
            sa.Column("actor", sa.String(length=255), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("retry_count", sa.Integer(), nullable=False),
            sa.Column("replay_count", sa.Integer(), nullable=False),
            sa.Column("body_sha256", sa.String(length=64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("NOW()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
    _ensure_index(
        "poison_message_dispositions",
        "ix_poison_disposition_queue_created",
        ["queue_name", "created_at"],
    )
    _ensure_index(
        "poison_message_dispositions",
        "ix_poison_disposition_message",
        ["message_id"],
    )


def downgrade() -> None:
    """Leave objects owned by historical revisions intact."""
    # These objects belong to the three historical revisions. Dropping them
    # here would corrupt databases that traversed the amended graph normally.
    pass
