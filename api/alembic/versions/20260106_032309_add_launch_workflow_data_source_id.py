"""add_launch_workflow_data_source_id

Revision ID: 49ae9dd4e390
Revises: 20260107_000000
Create Date: 2026-01-06 03:23:09.055266+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '49ae9dd4e390'
down_revision: Union[str, None] = 'aa1d28178057'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'app_pages',
        sa.Column('launch_workflow_data_source_id', sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('app_pages', 'launch_workflow_data_source_id')
