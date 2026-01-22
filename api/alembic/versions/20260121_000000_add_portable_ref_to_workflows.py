"""Add portable_ref generated column to workflows.

The portable_ref column provides a stable reference format for workflows
that works across environments during GitHub sync. Format: workflow::path::function_name

This is a Postgres generated column - automatically computed from path and
function_name, no application code needed to maintain it.

Revision ID: 8d1he52g6f80
Revises: 7c0ge41f5e79
Create Date: 2026-01-21
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = "8d1he52g6f80"
down_revision = "7c0ge41f5e79"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add portable_ref as a generated column with unique index."""
    # Add the generated column using raw SQL (Alembic doesn't support GENERATED ALWAYS AS directly)
    op.execute("""
        ALTER TABLE workflows
        ADD COLUMN portable_ref VARCHAR(512)
        GENERATED ALWAYS AS ('workflow::' || path || '::' || function_name) STORED
    """)

    # Create unique index for fast lookups
    op.create_index(
        "ix_workflows_portable_ref",
        "workflows",
        ["portable_ref"],
        unique=True,
    )


def downgrade() -> None:
    """Remove portable_ref column and its index."""
    op.drop_index("ix_workflows_portable_ref", table_name="workflows")
    op.drop_column("workflows", "portable_ref")
