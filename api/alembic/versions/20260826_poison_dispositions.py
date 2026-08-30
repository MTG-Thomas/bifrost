"""Add poison-message disposition audit receipts.

Revision ID: 20260826_poison_dispositions
Revises: 20260826_worker_controls
"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_poison_dispositions"
down_revision: str | Sequence[str] = "20260826_worker_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_poison_disposition_queue_created", "poison_message_dispositions", ["queue_name", "created_at"])
    op.create_index("ix_poison_disposition_message", "poison_message_dispositions", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_poison_disposition_message", table_name="poison_message_dispositions")
    op.drop_index("ix_poison_disposition_queue_created", table_name="poison_message_dispositions")
    op.drop_table("poison_message_dispositions")
