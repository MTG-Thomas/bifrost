"""Add OpenAI transport selection to AI model profiles.

Revision ID: 20260828_ai_transport
Revises: 20260826_solution_obligations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_ai_transport"
down_revision: str | None = "20260826_solution_obligations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ai_model_profiles",
        sa.Column(
            "api_transport", sa.String(length=30), server_default="auto", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_ai_model_profiles_api_transport",
        "ai_model_profiles",
        "api_transport IN ('auto', 'chat_completions', 'responses')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_ai_model_profiles_api_transport", "ai_model_profiles", type_="check"
    )
    op.drop_column("ai_model_profiles", "api_transport")
