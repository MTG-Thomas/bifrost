"""add_fill_height_to_app_pages

Revision ID: 2c9b170e7951
Revises: 9e270d1bba50
Create Date: 2026-01-17 22:24:39.609649+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c9b170e7951'
down_revision: Union[str, None] = '9e270d1bba50'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_pages",
        sa.Column("fill_height", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("app_pages", "fill_height")
