"""audit log overhaul

Revision ID: 20260406_audit_overhaul
Revises: 20260406_mmax_nullable
Create Date: 2026-04-06

Audit log overhaul:
- Drop unused system_logs table (was a stub, never populated).
- Add outcome and source columns to audit_logs.
- Add composite index on (action, created_at) for filter performance.

audit_logs semantics (post-migration):
- user_id: the acting user (nullable for system-initiated events)
- organization_id: the acting user's org (not the target entity's org)
- action: dotted event name like "auth.login.success", "user.create"
- resource_type/resource_id: the target entity
- outcome: "success" or "failure"
- source: "http" (default), "sso_sync", "scheduler", "cli", etc.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260406_audit_overhaul"
down_revision = "20260406_mmax_nullable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop unused system_logs table and its indexes
    op.drop_index("ix_system_logs_category_timestamp", table_name="system_logs", if_exists=True)
    op.drop_index("ix_system_logs_executed_by", table_name="system_logs", if_exists=True)
    op.drop_index("ix_system_logs_timestamp", table_name="system_logs", if_exists=True)
    op.drop_index("ix_system_logs_level", table_name="system_logs", if_exists=True)
    op.drop_index("ix_system_logs_category", table_name="system_logs", if_exists=True)
    op.drop_table("system_logs", if_exists=True)

    # Extend audit_logs
    op.add_column(
        "audit_logs",
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="success"),
    )
    op.add_column(
        "audit_logs",
        sa.Column("source", sa.String(length=32), nullable=False, server_default="http"),
    )
    op.create_index(
        "ix_audit_logs_action_created",
        "audit_logs",
        ["action", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_action_created", table_name="audit_logs")
    op.drop_column("audit_logs", "source")
    op.drop_column("audit_logs", "outcome")

    # Recreate system_logs table (structure matches original migration)
    op.create_table(
        "system_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("executed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("executed_by_name", sa.String(length=255), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("ix_system_logs_category", "system_logs", ["category"])
    op.create_index("ix_system_logs_level", "system_logs", ["level"])
    op.create_index("ix_system_logs_timestamp", "system_logs", ["timestamp"])
    op.create_index("ix_system_logs_executed_by", "system_logs", ["executed_by"])
    op.create_index("ix_system_logs_category_timestamp", "system_logs", ["category", "timestamp"])
