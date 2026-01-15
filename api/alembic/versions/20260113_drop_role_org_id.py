"""Drop organization_id from roles table

Revision ID: 20260113_drop_role_org_id
Revises: 20260112_drop_user_type
Create Date: 2026-01-13

This migration removes the organization_id column from the roles table.
Roles should be globally defined - org scoping happens at the entity level
(forms, apps, agents, workflows), not on roles themselves.

Changes:
1. Drop the ix_roles_organization_id index
2. Drop the organization_id column and its foreign key
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260113_drop_role_org_id"
down_revision: Union[str, None] = "20260112_drop_user_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Drop the index on organization_id
    op.drop_index("ix_roles_organization_id", table_name="roles")

    # Step 2: Drop the foreign key constraint first, then the column
    # The foreign key constraint is auto-named by SQLAlchemy
    op.drop_constraint("roles_organization_id_fkey", "roles", type_="foreignkey")
    op.drop_column("roles", "organization_id")


def downgrade() -> None:
    # Step 1: Re-add the organization_id column
    op.add_column(
        "roles",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # Step 2: Re-add the foreign key constraint
    op.create_foreign_key(
        "roles_organization_id_fkey",
        "roles",
        "organizations",
        ["organization_id"],
        ["id"],
    )

    # Step 3: Re-create the index
    op.create_index("ix_roles_organization_id", "roles", ["organization_id"])
