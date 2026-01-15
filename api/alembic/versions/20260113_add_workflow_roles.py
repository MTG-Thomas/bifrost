"""Add workflow_roles junction table and access_level column to workflows.

Revision ID: 20260113_add_workflow_roles
Revises: 20260113_drop_role_org_id
Create Date: 2026-01-13

This migration:
1. Creates workflow_roles junction table for role-based access control
2. Adds access_level column to workflows table (default: role_based)

Follows the same pattern as form_roles, app_roles, agent_roles.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260113_add_workflow_roles"
down_revision = "20260113_drop_role_org_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add access_level column to workflows table
    op.add_column(
        "workflows",
        sa.Column(
            "access_level",
            sa.String(20),
            nullable=False,
            server_default="role_based",
        ),
    )

    # 2. Create workflow_roles junction table
    op.create_table(
        "workflow_roles",
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("assigned_by", sa.String(255), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["workflows.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workflow_id", "role_id"),
    )

    # 3. Create index for role lookups
    op.create_index("ix_workflow_roles_role_id", "workflow_roles", ["role_id"])


def downgrade() -> None:
    # 1. Drop workflow_roles table
    op.drop_index("ix_workflow_roles_role_id", table_name="workflow_roles")
    op.drop_table("workflow_roles")

    # 2. Drop access_level column from workflows
    op.drop_column("workflows", "access_level")
