"""Add AI metrics columns to daily metrics and snapshot tables

Revision ID: add_ai_metrics_columns
Revises: add_ai_usage_tracking
Create Date: 2025-12-26

This migration adds AI usage tracking columns to:
- execution_metrics_daily: total_ai_input_tokens, total_ai_output_tokens, total_ai_cost, total_ai_calls
- platform_metrics_snapshot: ai_cost_24h, ai_calls_24h
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_ai_metrics_columns'
down_revision: Union[str, None] = 'add_ai_usage_tracking'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add AI metrics columns to execution_metrics_daily
    op.add_column(
        'execution_metrics_daily',
        sa.Column('total_ai_input_tokens', sa.BigInteger(), nullable=True)
    )
    op.add_column(
        'execution_metrics_daily',
        sa.Column('total_ai_output_tokens', sa.BigInteger(), nullable=True)
    )
    op.add_column(
        'execution_metrics_daily',
        sa.Column('total_ai_cost', sa.Numeric(12, 4), nullable=True)
    )
    op.add_column(
        'execution_metrics_daily',
        sa.Column('total_ai_calls', sa.Integer(), nullable=True)
    )

    # Add AI metrics columns to platform_metrics_snapshot
    op.add_column(
        'platform_metrics_snapshot',
        sa.Column('ai_cost_24h', sa.Numeric(12, 4), nullable=True)
    )
    op.add_column(
        'platform_metrics_snapshot',
        sa.Column('ai_calls_24h', sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    # Remove columns from platform_metrics_snapshot
    op.drop_column('platform_metrics_snapshot', 'ai_calls_24h')
    op.drop_column('platform_metrics_snapshot', 'ai_cost_24h')

    # Remove columns from execution_metrics_daily
    op.drop_column('execution_metrics_daily', 'total_ai_calls')
    op.drop_column('execution_metrics_daily', 'total_ai_cost')
    op.drop_column('execution_metrics_daily', 'total_ai_output_tokens')
    op.drop_column('execution_metrics_daily', 'total_ai_input_tokens')
