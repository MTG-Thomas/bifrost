"""add updated_by to file_index

Revision ID: 20260316_file_index_author
Revises: 20260316_drop_llm_temp
Create Date: 2026-03-16
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "20260316_file_index_author"
down_revision: Union[str, None] = "20260316_drop_llm_temp"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('file_index', sa.Column('updated_by', sa.String(255), nullable=True))

def downgrade() -> None:
    op.drop_column('file_index', 'updated_by')
