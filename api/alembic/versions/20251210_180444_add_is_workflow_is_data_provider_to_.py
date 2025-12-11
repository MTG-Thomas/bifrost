"""add_is_workflow_is_data_provider_to_workspace_files

Revision ID: 68515920579a
Revises: 311d0802e654
Create Date: 2025-12-10 18:04:44.035171+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '68515920579a'
down_revision: Union[str, None] = '311d0802e654'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('workspace_files', sa.Column('is_workflow', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('workspace_files', sa.Column('is_data_provider', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    op.drop_column('workspace_files', 'is_data_provider')
    op.drop_column('workspace_files', 'is_workflow')
