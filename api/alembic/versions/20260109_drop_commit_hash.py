"""Drop unused last_git_commit_hash column from workspace_files

Revision ID: 20260109_drop_commit_hash
Revises: 20260108_010000
Create Date: 2026-01-09

The last_git_commit_hash column was originally intended to track the git commit
that last modified a file, but this approach has been superseded by the new
API-based GitHub sync which uses github_sha to track blob SHAs directly.

The github_sha column provides more accurate tracking by storing the actual
blob SHA from GitHub, which allows for precise content comparison during sync.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260109_drop_commit_hash"
down_revision: Union[str, None] = "20260108_010000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the unused last_git_commit_hash column
    # This column has been superseded by github_sha for tracking sync state
    op.drop_column("workspace_files", "last_git_commit_hash")


def downgrade() -> None:
    # Re-add the column if rolling back
    # VARCHAR(40) matches the length of a git SHA-1 hash
    op.add_column(
        "workspace_files",
        sa.Column("last_git_commit_hash", sa.String(40), nullable=True),
    )
