"""Make conversation agent_id nullable for agentless chat

Revision ID: 8f3a2b1c4d5e
Revises: c0a523bf530f
Create Date: 2025-12-18

Allows conversations to exist without an agent, enabling
"agentless" chat where users chat with the default LLM
without selecting a specific agent.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "8f3a2b1c4d5e"
down_revision = "c0a523bf530f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Make agent_id nullable on conversations table
    op.alter_column(
        "conversations",
        "agent_id",
        existing_type=sa.UUID(),
        nullable=True,
    )


def downgrade() -> None:
    # Make agent_id required again
    # Note: This will fail if there are any rows with NULL agent_id
    op.alter_column(
        "conversations",
        "agent_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
