"""drop_developer_api_keys_table

Revision ID: 84a1938c3c52
Revises: rename_economics_to_roi
Create Date: 2025-12-25 02:26:37.977741+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '84a1938c3c52'
down_revision: Union[str, None] = 'rename_economics_to_roi'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table('developer_api_keys')


def downgrade() -> None:
    op.create_table(
        'developer_api_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('key_prefix', sa.String(length=12), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_developer_api_keys_key_prefix', 'developer_api_keys', ['key_prefix'], unique=False)
    op.create_index('ix_developer_api_keys_user_id', 'developer_api_keys', ['user_id'], unique=False)
