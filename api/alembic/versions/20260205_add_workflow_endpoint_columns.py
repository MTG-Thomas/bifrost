"""add_workflow_endpoint_columns

Revision ID: a1c3f5e72b90
Revises: 84b7f1403ead
Create Date: 2026-02-05 20:00:00.000000+00:00

Add public_endpoint and disable_global_key columns to workflows table.
These were previously hardcoded in the API response but are now stored
as editable fields managed via the Workflow Settings UI.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a1c3f5e72b90'
down_revision: Union[str, None] = '84b7f1403ead'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'workflows',
        sa.Column('public_endpoint', sa.Boolean(), nullable=False, server_default='false')
    )
    op.add_column(
        'workflows',
        sa.Column('disable_global_key', sa.Boolean(), nullable=False, server_default='false')
    )


def downgrade() -> None:
    op.drop_column('workflows', 'disable_global_key')
    op.drop_column('workflows', 'public_endpoint')
