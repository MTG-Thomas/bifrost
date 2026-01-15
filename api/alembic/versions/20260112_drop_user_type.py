"""Drop user_type column and make organization_id nullable

Revision ID: 20260112_drop_user_type
Revises: 20260110_drop_workflow_blacklist
Create Date: 2026-01-12

This migration simplifies the auth model by:
1. Making organization_id nullable (NULL = system/global account)
2. Adding a check constraint: org_id can only be NULL if is_superuser=true
3. Dropping the redundant user_type column

The new model uses is_superuser + nullable organization_id as the source of truth:
- is_superuser=true, org_id=UUID: Platform admin in an org
- is_superuser=false, org_id=UUID: Regular org user
- is_superuser=true, org_id=NULL: System account (global scope)
- is_superuser=false, org_id=NULL: INVALID (blocked by constraint)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260112_drop_user_type"
down_revision: Union[str, None] = "20260110_drop_workflow_blacklist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Step 1: Make organization_id nullable
    op.alter_column(
        "users",
        "organization_id",
        existing_type=sa.UUID(),
        nullable=True,
    )

    # Step 2: Add check constraint - org_id can only be NULL if is_superuser=true
    op.execute("""
        ALTER TABLE users ADD CONSTRAINT ck_users_org_requires_superuser
        CHECK (
            organization_id IS NOT NULL
            OR is_superuser = true
        )
    """)

    # Step 3: Drop the user_type column
    # The enum type will remain orphaned but that's harmless
    op.drop_column("users", "user_type")


def downgrade() -> None:
    # Step 1: Re-add user_type column with default
    # Note: The user_type enum should still exist in the database
    op.add_column(
        "users",
        sa.Column(
            "user_type",
            sa.Enum("PLATFORM", "ORG", "SYSTEM", name="user_type", create_type=False),
            nullable=False,
            server_default="ORG",
        ),
    )

    # Step 2: Populate user_type based on is_superuser
    op.execute("""
        UPDATE users
        SET user_type = CASE
            WHEN is_superuser = true THEN 'PLATFORM'
            ELSE 'ORG'
        END
    """)

    # Step 3: Drop the check constraint
    op.execute("ALTER TABLE users DROP CONSTRAINT ck_users_org_requires_superuser")

    # Step 4: Make organization_id NOT NULL again
    # First, ensure no NULL values exist (set them to a default org if needed)
    # This is a destructive downgrade - may need manual intervention
    op.alter_column(
        "users",
        "organization_id",
        existing_type=sa.UUID(),
        nullable=False,
    )
