"""Add AI usage tracking tables

Revision ID: add_ai_usage_tracking
Revises: add_knowledge_store
Create Date: 2025-12-26

This migration creates:
- ai_model_pricing table for storing per-model token pricing
- ai_usage table for tracking AI usage per execution/conversation
- Seed data for common model pricing
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = 'add_ai_usage_tracking'
down_revision: Union[str, None] = 'add_knowledge_store'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create ai_model_pricing table
    op.create_table(
        'ai_model_pricing',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('input_price_per_million', sa.Numeric(10, 4), nullable=False),
        sa.Column('output_price_per_million', sa.Numeric(10, 4), nullable=False),
        sa.Column('effective_date', sa.Date(), nullable=False, server_default=sa.text('CURRENT_DATE')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('provider', 'model', name='uq_ai_model_pricing_provider_model'),
    )

    # Create ai_usage table
    op.create_table(
        'ai_usage',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('execution_id', UUID(as_uuid=True), nullable=True),
        sa.Column('conversation_id', UUID(as_uuid=True), nullable=True),
        sa.Column('message_id', UUID(as_uuid=True), nullable=True),
        sa.Column('provider', sa.String(50), nullable=False),
        sa.Column('model', sa.String(100), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cost', sa.Numeric(12, 8), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('sequence', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('organization_id', UUID(as_uuid=True), nullable=True),
        sa.Column('user_id', UUID(as_uuid=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['execution_id'], ['executions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.CheckConstraint(
            'execution_id IS NOT NULL OR conversation_id IS NOT NULL',
            name='ai_usage_context_check'
        ),
    )

    # Create indexes for ai_usage
    op.create_index(
        'ix_ai_usage_execution',
        'ai_usage',
        ['execution_id'],
        postgresql_where=sa.text('execution_id IS NOT NULL')
    )
    op.create_index(
        'ix_ai_usage_conversation',
        'ai_usage',
        ['conversation_id'],
        postgresql_where=sa.text('conversation_id IS NOT NULL')
    )
    op.create_index('ix_ai_usage_org', 'ai_usage', ['organization_id'])
    op.create_index('ix_ai_usage_timestamp', 'ai_usage', ['timestamp'])

    # NOTE: No seed data - pricing is configured dynamically by admins when models are first used.
    # When a new model is used, the missing-price notification system alerts admins to configure pricing.
    # Display names are fetched from provider APIs via the model_registry service.


def downgrade() -> None:
    # Drop indexes
    op.drop_index('ix_ai_usage_timestamp', table_name='ai_usage')
    op.drop_index('ix_ai_usage_org', table_name='ai_usage')
    op.drop_index('ix_ai_usage_conversation', table_name='ai_usage')
    op.drop_index('ix_ai_usage_execution', table_name='ai_usage')

    # Drop tables
    op.drop_table('ai_usage')
    op.drop_table('ai_model_pricing')
