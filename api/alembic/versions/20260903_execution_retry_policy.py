"""Add workflow execution retry policy and immutable execution snapshot.

Revision ID: 20260903_execution_retry_policy
Revises: 20260831_execution_attempts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260903_execution_retry_policy"
down_revision: str | Sequence[str] = "20260831_execution_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DISABLED_POLICY = (
    "jsonb_build_object('version', 'execution-retry/v1', 'enabled', false, "
    "'max_attempts', 2, 'retry_on', jsonb_build_array())"
)


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "retry_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text(_DISABLED_POLICY),
            nullable=False,
        ),
    )
    op.add_column(
        "executions",
        sa.Column(
            "retry_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text(_DISABLED_POLICY),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("executions", "retry_policy")
    op.drop_column("workflows", "retry_policy")
