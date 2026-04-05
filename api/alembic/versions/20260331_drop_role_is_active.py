"""Drop is_active column from roles

Revision ID: 20260331_drop_role_is_active
Revises: 20260331_user_fk_cascades
Create Date: 2026-03-31

Roles no longer support soft-delete. Inactive roles are deleted outright
(all FKs to roles.id already have ON DELETE CASCADE).
"""
from alembic import op

revision = "20260331_drop_role_is_active"
down_revision = "20260331_user_fk_cascades"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Hard-delete any currently inactive roles (CASCADE cleans up join tables)
    op.execute("DELETE FROM roles WHERE is_active = false")
    op.drop_column("roles", "is_active")


def downgrade() -> None:
    op.add_column(
        "roles",
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )


# Needed only in downgrade
import sqlalchemy as sa  # noqa: E402
