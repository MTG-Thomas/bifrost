"""add_system_tools_to_agents

Revision ID: add_system_tools_to_agents
Revises: 0fc71a6c9de9
Create Date: 2025-12-27 18:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY


# revision identifiers, used by Alembic.
revision: str = 'add_system_tools_to_agents'
down_revision: Union[str, None] = '0fc71a6c9de9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add system_tools column to store enabled system tool names
    # e.g., ["execute_workflow", "search_knowledge", "list_integrations"]
    op.add_column(
        'agents',
        sa.Column(
            'system_tools',
            ARRAY(sa.String()),
            nullable=False,
            server_default='{}'
        )
    )


def downgrade() -> None:
    op.drop_column('agents', 'system_tools')
