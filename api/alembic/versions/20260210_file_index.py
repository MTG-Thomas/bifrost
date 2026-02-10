"""Create file_index table.

Revision ID: 20260210_file_index
Revises: f7a3dd4b6046
Create Date: 2026-02-10
"""

from alembic import op
import sqlalchemy as sa

revision = "20260210_file_index"
down_revision = "f7a3dd4b6046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_index",
        sa.Column("path", sa.String(1000), primary_key=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()")),
    )


def downgrade() -> None:
    op.drop_table("file_index")
