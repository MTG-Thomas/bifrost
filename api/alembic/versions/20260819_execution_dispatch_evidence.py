"""Persist exact retry-safe workflow dispatch evidence.

Revision ID: 20260819_exec_dispatch
Revises: 20260819_ws_release_prepare
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_exec_dispatch"
down_revision: str | None = "20260819_ws_release_prepare"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column(
            "dispatch_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "executions",
        sa.Column("dispatch_evidence_hash", sa.String(length=71), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executions", "dispatch_evidence_hash")
    op.drop_column("executions", "dispatch_evidence")
