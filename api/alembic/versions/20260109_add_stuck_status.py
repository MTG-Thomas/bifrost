"""Add STUCK status to execution_status enum

Revision ID: 20260109_add_stuck_status
Revises: 20260109_add_execution_model
Create Date: 2026-01-09

Adds 'Stuck' value to the execution_status PostgreSQL enum.

The STUCK status is used when:
- An execution does not respond to cancellation within the grace period
- The worker marks it as stuck and drains to recover
- The circuit breaker may blacklist workflows that cause repeated stuck executions
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "20260109_add_stuck_status"
down_revision: Union[str, None] = "20260109_add_execution_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add 'Stuck' value to the execution_status enum
    # Using raw SQL since Alembic doesn't have a helper for ALTER TYPE
    op.execute("ALTER TYPE execution_status ADD VALUE IF NOT EXISTS 'Stuck'")


def downgrade() -> None:
    # PostgreSQL doesn't support removing values from enums easily
    # This is a one-way migration - to downgrade you'd need to:
    # 1. Create a new enum without 'Stuck'
    # 2. Update all rows that have 'Stuck' to a different status (e.g., 'Failed')
    # 3. Alter the column to use the new enum
    # 4. Drop the old enum
    #
    # For safety, we just leave it as-is and document the limitation
    pass
