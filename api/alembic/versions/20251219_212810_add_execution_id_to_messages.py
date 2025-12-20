"""add_execution_id_to_messages

Revision ID: 60f0741b55d9
Revises: 8f3a2b1c4d5e
Create Date: 2025-12-19 21:28:10.747650+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '60f0741b55d9'
down_revision: Union[str, None] = '8f3a2b1c4d5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add execution_id column to messages table for fetching logs from tool executions
    op.add_column(
        'messages',
        sa.Column('execution_id', sa.String(36), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('messages', 'execution_id')
