"""autonomous_agents

Add AgentRun and AgentRunStep tables, budget columns on agents,
agent_id on event_subscriptions, make workflow_id nullable.

Revision ID: 20260305_autonomous_agents
Revises: 20260302_api_key_ondelete
Create Date: 2026-03-05
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "20260305_autonomous_agents"
down_revision: Union[str, None] = "20260302_api_key_ondelete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Agent budget columns
    op.add_column("agents", sa.Column("max_iterations", sa.Integer(), nullable=True, server_default="50"))
    op.add_column("agents", sa.Column("max_token_budget", sa.Integer(), nullable=True, server_default="100000"))

    # AgentRun table
    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("trigger_type", sa.String(50), nullable=False),
        sa.Column("trigger_source", sa.String(500), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), sa.ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_delivery_id", UUID(as_uuid=True), sa.ForeignKey("event_deliveries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("input", JSONB, nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("output_schema", JSONB, nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("caller_user_id", sa.String(255), nullable=True),
        sa.Column("caller_email", sa.String(255), nullable=True),
        sa.Column("caller_name", sa.String(255), nullable=True),
        sa.Column("iterations_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_max_iterations", sa.Integer(), nullable=True),
        sa.Column("budget_max_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("llm_model", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_trigger_type", "agent_runs", ["trigger_type"])
    op.create_index("ix_agent_runs_created_at", "agent_runs", ["created_at"])

    # AgentRunStep table
    op.create_table(
        "agent_run_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("content", JSONB, nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # EventSubscription: add agent_id + target_type, make workflow_id nullable
    # Drop unique constraint first (workflow_id will be nullable)
    op.drop_index("ix_event_subscriptions_unique_source_workflow", table_name="event_subscriptions")
    op.add_column("event_subscriptions", sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=True))
    op.add_column("event_subscriptions", sa.Column("target_type", sa.String(50), nullable=True, server_default="workflow"))
    op.alter_column("event_subscriptions", "workflow_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.create_index("ix_event_subscriptions_agent_id", "event_subscriptions", ["agent_id"])

    # Backfill target_type for existing rows
    op.execute("UPDATE event_subscriptions SET target_type = 'workflow' WHERE target_type IS NULL")
    op.alter_column("event_subscriptions", "target_type", nullable=False)

    # EventDelivery: make workflow_id nullable (agent deliveries have no workflow)
    op.alter_column("event_deliveries", "workflow_id", existing_type=UUID(as_uuid=True), nullable=True)
    op.add_column("event_deliveries", sa.Column("agent_run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True))


def downgrade() -> None:
    op.drop_column("event_deliveries", "agent_run_id")
    op.alter_column("event_deliveries", "workflow_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.drop_index("ix_event_subscriptions_agent_id", table_name="event_subscriptions")
    op.drop_column("event_subscriptions", "target_type")
    op.drop_column("event_subscriptions", "agent_id")
    op.alter_column("event_subscriptions", "workflow_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.create_index("ix_event_subscriptions_unique_source_workflow", "event_subscriptions", ["event_source_id", "workflow_id"], unique=True)
    op.drop_table("agent_run_steps")
    op.drop_table("agent_runs")
    op.drop_column("agents", "max_token_budget")
    op.drop_column("agents", "max_iterations")
