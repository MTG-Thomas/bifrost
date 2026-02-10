"""Drop workflows.code and workflows.code_hash columns.

Code now lives in file_index table and _repo/ S3 prefix.

Revision ID: 20260210_drop_code
Revises: 20260210_file_index
Create Date: 2026-02-10
"""

from alembic import op
import sqlalchemy as sa

revision = "20260210_drop_code"
down_revision = "20260210_file_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("workflows", "code")
    op.drop_column("workflows", "code_hash")


def downgrade() -> None:
    op.add_column("workflows", sa.Column("code_hash", sa.String(64), nullable=True))
    op.add_column("workflows", sa.Column("code", sa.Text(), nullable=True))
