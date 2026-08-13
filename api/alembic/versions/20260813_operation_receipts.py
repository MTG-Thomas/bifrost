"""add durable operation receipts

Revision ID: 20260813_operation_receipts
Revises: 20260813_mcp_catalog_rev
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_operation_receipts"
down_revision: str = "20260813_mcp_catalog_rev"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operation_receipts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("namespace", sa.String(length=100), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="started",
            nullable=False,
        ),
        sa.Column("owner_token", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "response", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "durable_handle",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'failed')",
            name="ck_operation_receipts_status",
        ),
        sa.CheckConstraint(
            "(status = 'started' AND response IS NULL AND error IS NULL "
            "AND completed_at IS NULL AND payload_cleared_at IS NULL) OR "
            "(status = 'succeeded' AND error IS NULL AND completed_at IS NOT NULL "
            "AND ((response IS NOT NULL AND payload_cleared_at IS NULL) OR "
            "(response IS NULL AND payload_cleared_at IS NOT NULL))) OR "
            "(status = 'failed' AND response IS NULL AND completed_at IS NOT NULL "
            "AND ((error IS NOT NULL AND payload_cleared_at IS NULL) OR "
            "(error IS NULL AND payload_cleared_at IS NOT NULL)))",
            name="ck_operation_receipts_terminal_payload",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_operation_receipts_namespace_scope",
        "operation_receipts",
        ["namespace", "scope_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_operation_receipts_namespace_scope",
        table_name="operation_receipts",
    )
    op.drop_table("operation_receipts")
