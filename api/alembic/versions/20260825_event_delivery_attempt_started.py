"""Track the active attempt age for retried event deliveries.

Revision ID: 20260825_delivery_attempt
Revises: 20260823_job_memory_profiles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260825_delivery_attempt"
down_revision: str | Sequence[str] = "20260823_job_memory_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "event_deliveries",
        sa.Column("attempt_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("event_deliveries", "attempt_started_at")
