"""Drop navigation and permissions columns from applications.

These were vestigial from the old drag-and-drop app builder.
Code-based apps define their own navigation in TSX layout files.

Revision ID: 20260211_drop_nav
Revises: 20260210_drop_workspace
Create Date: 2026-02-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260211_drop_nav"
down_revision = "20260210_drop_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("applications", "navigation")
    op.drop_column("applications", "permissions")


def downgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("permissions", JSONB, server_default="{}", nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("navigation", JSONB, server_default="{}", nullable=True),
    )
