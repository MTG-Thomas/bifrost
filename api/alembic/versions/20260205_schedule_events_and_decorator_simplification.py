"""schedule_events_and_decorator_simplification

Revision ID: 84b7f1403ead
Revises: cc62b0c9f2ad
Create Date: 2026-02-05 12:00:00.000000+00:00

This migration supports the workflow decorator simplification and schedule events features:

1. Add display_name column to workflows table
   - User-editable display name separate from code-defined name
   - NULL means use the `name` column

2. Create schedule_sources table
   - Stores cron expression and timezone for scheduled event sources
   - Links to event_sources via event_source_id
   - Replaces the workflows.schedule column (not removed yet for backwards compat)

3. Add input_mapping column to event_subscriptions table
   - Allows mapping event payload fields to workflow input parameters
   - Supports static values and template expressions like {{ scheduled_time }}
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '84b7f1403ead'
down_revision: Union[str, None] = 'cc62b0c9f2ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add display_name to workflows
    op.add_column(
        'workflows',
        sa.Column('display_name', sa.String(255), nullable=True)
    )

    # 2. Create schedule_sources table
    op.create_table(
        'schedule_sources',
        sa.Column('id', sa.dialects.postgresql.UUID(), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('event_source_id', sa.dialects.postgresql.UUID(), sa.ForeignKey('event_sources.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('cron_expression', sa.String(100), nullable=False),
        sa.Column('timezone', sa.String(50), nullable=False, server_default='UTC'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
    )
    op.create_index('ix_schedule_sources_event_source_id', 'schedule_sources', ['event_source_id'])
    op.create_index('ix_schedule_sources_enabled', 'schedule_sources', ['enabled'])

    # 3. Add input_mapping to event_subscriptions
    op.add_column(
        'event_subscriptions',
        sa.Column('input_mapping', sa.dialects.postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    # Remove input_mapping from event_subscriptions
    op.drop_column('event_subscriptions', 'input_mapping')

    # Drop schedule_sources table
    op.drop_index('ix_schedule_sources_enabled', 'schedule_sources')
    op.drop_index('ix_schedule_sources_event_source_id', 'schedule_sources')
    op.drop_table('schedule_sources')

    # Remove display_name from workflows
    op.drop_column('workflows', 'display_name')
