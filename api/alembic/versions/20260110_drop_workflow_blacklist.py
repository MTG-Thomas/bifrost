"""Drop workflow_blacklist table

Revision ID: 20260110_drop_workflow_blacklist
Revises: 20260109_add_stuck_status
Create Date: 2026-01-10

The blacklist feature was never completed and has been rolled back.
This migration drops the workflow_blacklist table if it exists.
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260110_drop_workflow_blacklist"
down_revision: Union[str, None] = "20260109_add_stuck_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop indexes first (if they exist) - separate statements
    op.execute("DROP INDEX IF EXISTS ix_workflow_blacklist_active")
    op.execute("DROP INDEX IF EXISTS ix_workflow_blacklist_workflow_id")

    # Drop table if it exists
    op.execute("DROP TABLE IF EXISTS workflow_blacklist CASCADE")


def downgrade() -> None:
    # No-op: we don't want to recreate this table
    pass
