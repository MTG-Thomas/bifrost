"""event_delivery_agent_support

Make event_deliveries.workflow_id nullable for agent targets,
add agent_run_id column.

Revision ID: 20260305_event_delivery_agents
Revises: 20260305_autonomous_agents
Create Date: 2026-03-05
"""
from typing import Sequence, Union
from alembic import op

revision: str = "20260305_event_delivery_agents"
down_revision: Union[str, None] = "20260305_autonomous_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # workflow_id nullable and agent_run_id column already added in 20260305_autonomous_agents
    # Just add the index that was missing from that migration
    op.create_index("ix_event_deliveries_agent_run_id", "event_deliveries", ["agent_run_id"])


def downgrade() -> None:
    op.drop_index("ix_event_deliveries_agent_run_id", table_name="event_deliveries")
