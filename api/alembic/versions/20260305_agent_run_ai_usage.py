"""agent_run_ai_usage

Add agent_run_id FK to ai_usage table for tracking AI usage per agent run.

Revision ID: 20260305_agent_run_ai_usage
Revises: 20260305_event_delivery_agents
Create Date: 2026-03-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "20260305_agent_run_ai_usage"
down_revision: Union[str, None] = "20260305_event_delivery_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add agent_run_id column
    op.add_column(
        "ai_usage",
        sa.Column(
            "agent_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    # Drop existing CHECK constraint
    op.drop_constraint("ai_usage_context_check", "ai_usage", type_="check")

    # Create new CHECK constraint including agent_run_id
    op.create_check_constraint(
        "ai_usage_context_check",
        "ai_usage",
        "execution_id IS NOT NULL OR conversation_id IS NOT NULL OR agent_run_id IS NOT NULL",
    )

    # Add partial index for agent_run_id
    op.create_index(
        "ix_ai_usage_agent_run",
        "ai_usage",
        ["agent_run_id"],
        postgresql_where=sa.text("agent_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_ai_usage_agent_run", table_name="ai_usage")

    op.drop_constraint("ai_usage_context_check", "ai_usage", type_="check")
    op.create_check_constraint(
        "ai_usage_context_check",
        "ai_usage",
        "execution_id IS NOT NULL OR conversation_id IS NOT NULL",
    )

    op.drop_column("ai_usage", "agent_run_id")
