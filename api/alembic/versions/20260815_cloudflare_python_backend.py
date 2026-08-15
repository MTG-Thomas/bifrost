"""Add opt-in workflow execution backend.

Revision ID: 20260815_cloudflare_python
Revises: 20260812_private_memory
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_cloudflare_python"
down_revision: str = "20260812_private_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflows",
        sa.Column(
            "execution_backend",
            sa.String(length=32),
            server_default="process",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_workflows_execution_backend",
        "workflows",
        "execution_backend IN ('process', 'cloudflare-python')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workflows_execution_backend", "workflows", type_="check"
    )
    op.drop_column("workflows", "execution_backend")
