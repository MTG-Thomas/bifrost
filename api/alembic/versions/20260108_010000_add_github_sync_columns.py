"""Add GitHub sync columns

Revision ID: 20260108_010000
Revises: 20260108_000000
Create Date: 2026-01-08

Adds columns to support the API-based GitHub sync feature:
- workspace_files.github_sha: Tracks the SHA of the blob at last successful sync
  - NULL means the file has never been synced to GitHub
- workflows.is_orphaned: Marks workflows whose backing file no longer exists
  - Orphaned workflows continue to function but cannot be edited via files
  - Users can replace, recreate, or deactivate orphaned workflows
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260108_010000"
down_revision: Union[str, None] = "20260108_000000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add github_sha to workspace_files
    # VARCHAR(40) matches the length of a git SHA-1 hash
    # NULL means the file has never been synced to GitHub
    op.add_column(
        "workspace_files",
        sa.Column("github_sha", sa.String(40), nullable=True),
    )

    # Add is_orphaned to workflows
    # Default FALSE - workflows start linked to their files
    op.add_column(
        "workflows",
        sa.Column(
            "is_orphaned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Create index for efficient orphaned workflow queries
    # Partial index only includes orphaned workflows
    op.create_index(
        "ix_workflows_is_orphaned",
        "workflows",
        ["is_orphaned"],
        postgresql_where=sa.text("is_orphaned = true"),
    )


def downgrade() -> None:
    # Drop index first
    op.drop_index("ix_workflows_is_orphaned", table_name="workflows")

    # Drop columns
    op.drop_column("workflows", "is_orphaned")
    op.drop_column("workspace_files", "github_sha")
