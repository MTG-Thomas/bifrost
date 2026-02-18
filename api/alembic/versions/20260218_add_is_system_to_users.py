"""add is_system to users

Revision ID: 20260218_is_system_users
Revises: 20260217_form_embed_secrets
Create Date: 2026-02-18
"""

from alembic import op
import sqlalchemy as sa

revision = "20260218_is_system_users"
down_revision = "20260217_form_embed_secrets"
branch_labels = None
depends_on = None

# System user UUID from constants
SYSTEM_USER_UUID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Mark the system user
    op.execute(
        f"UPDATE users SET is_system = true WHERE id = '{SYSTEM_USER_UUID}'"
    )


def downgrade() -> None:
    op.drop_column("users", "is_system")
