"""Add durable audited worker control commands.

Revision ID: 20260826_worker_controls
Revises: 20260826_execution_attempts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_worker_controls"
down_revision: str | Sequence[str] = "20260826_execution_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_control_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=True),
        sa.Column(
            "status", sa.String(length=32), server_default="pending", nullable=False
        ),
        sa.Column(
            "requested_by_user_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
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
    op.create_index(
        "ix_worker_control_worker_requested",
        "worker_control_commands",
        ["worker_id", "requested_at"],
    )
    op.create_index(
        "ix_worker_control_status_requested",
        "worker_control_commands",
        ["status", "requested_at"],
    )
    op.create_index(
        "ix_worker_control_requester",
        "worker_control_commands",
        ["requested_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_worker_control_requester", table_name="worker_control_commands")
    op.drop_index(
        "ix_worker_control_status_requested", table_name="worker_control_commands"
    )
    op.drop_index(
        "ix_worker_control_worker_requested", table_name="worker_control_commands"
    )
    op.drop_table("worker_control_commands")
