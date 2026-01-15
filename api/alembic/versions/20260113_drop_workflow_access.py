"""Drop workflow_access table - replaced by workflow_roles.

Revision ID: 20260113_drop_workflow_access
Revises: 20260113_add_workflow_roles
Create Date: 2026-01-13

The workflow_access table was a precomputed authorization table that stored
workflow references from forms/apps. This has been replaced by the
workflow_roles table which directly assigns roles to workflows.

The new authorization model:
- Workflows have access_level (authenticated or role_based)
- For role_based, check workflow_roles junction table
- Roles are synced from forms/apps when they're created/updated

This is a cleanup migration for Phase 6 of the workflow-role-access plan.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260113_drop_workflow_access"
down_revision = "20260113_add_workflow_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop indexes first
    op.drop_index("ix_workflow_access_entity", table_name="workflow_access")
    op.drop_index("ix_workflow_access_lookup", table_name="workflow_access")

    # Drop the table
    op.drop_table("workflow_access")


def downgrade() -> None:
    # Recreate workflow_access table
    op.create_table(
        "workflow_access",
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("entity_type", sa.String(20), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("access_level", sa.String(20), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("workflow_id", "entity_type", "entity_id"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
    )

    # Recreate indexes
    op.create_index(
        "ix_workflow_access_lookup",
        "workflow_access",
        ["workflow_id", "organization_id"],
    )
    op.create_index(
        "ix_workflow_access_entity",
        "workflow_access",
        ["entity_type", "entity_id"],
    )

    # Note: Data would need to be re-populated via application code
    # Run backfill scripts after downgrade if needed
