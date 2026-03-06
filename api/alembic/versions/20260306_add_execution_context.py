"""add execution_context column to executions

Revision ID: 20260306_add_exec_context
Revises: 20260305_agent_run_ai_usage
Create Date: 2026-03-06
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260306_add_exec_context"
down_revision: Union[str, None] = "20260305_agent_run_ai_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('executions', sa.Column('execution_context', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('executions', 'execution_context')
