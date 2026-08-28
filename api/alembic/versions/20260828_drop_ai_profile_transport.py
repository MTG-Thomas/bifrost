"""Drop the superseded AI profile transport selection.

Revision ID: 20260828_drop_ai_transport
Revises: 20260828_ai_transport
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_drop_ai_transport"
down_revision: str | None = "20260828_ai_transport"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_ai_model_profiles_api_transport", "ai_model_profiles", type_="check"
    )
    op.drop_column("ai_model_profiles", "api_transport")


def downgrade() -> None:
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
