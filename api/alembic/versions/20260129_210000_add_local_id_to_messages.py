"""add_local_id_to_messages

Revision ID: 8a2f3c4d5e6f
Revises: 23757e5d7dde
Create Date: 2026-01-29 21:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a2f3c4d5e6f'
down_revision: Union[str, None] = '23757e5d7dde'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('local_id', sa.String(36), nullable=True))
    op.create_index('ix_messages_local_id', 'messages', ['local_id'])


def downgrade() -> None:
    op.drop_index('ix_messages_local_id', table_name='messages')
    op.drop_column('messages', 'local_id')
