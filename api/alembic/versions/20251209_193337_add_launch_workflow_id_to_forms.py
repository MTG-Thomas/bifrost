"""add_launch_workflow_id_to_forms

Revision ID: 99760511c232
Revises: drop_is_platform
Create Date: 2025-12-09 19:33:37.329150+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '99760511c232'
down_revision: Union[str, None] = 'drop_is_platform'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'forms',
        sa.Column('launch_workflow_id', sa.String(255), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('forms', 'launch_workflow_id')
