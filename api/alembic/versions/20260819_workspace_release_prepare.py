"""Record immutable Workspace release preparation evidence.

Revision ID: 20260819_workspace_release_prepare
Revises: 20260819_workspace_release_active
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_workspace_release_prepare"
down_revision: str | None = "20260819_workspace_release_active"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_promotion_releases",
        sa.Column(
            "prepared_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.create_index(
        "uq_workspace_promotion_release_artifact",
        "workspace_promotion_releases",
        ["artifact_id"],
        unique=True,
    )
    op.add_column(
        "workspace_promotion_releases",
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_workspace_promotion_release_artifact",
        table_name="workspace_promotion_releases",
    )
    op.drop_column("workspace_promotion_releases", "prepared_at")
    op.drop_column("workspace_promotion_releases", "prepared_evidence")
