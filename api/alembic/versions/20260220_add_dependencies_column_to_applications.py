"""add dependencies column to applications

Revision ID: 20260220_add_deps_col
Revises: 20260219_backfill_config_types
Create Date: 2026-02-20

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260220_add_deps_col"
down_revision = "20260219_backfill_config_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("dependencies", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("applications", "dependencies")
