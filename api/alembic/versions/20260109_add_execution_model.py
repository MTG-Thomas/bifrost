"""Add execution_model field to executions table

Revision ID: 20260109_add_execution_model
Revises: 20260109_drop_commit_hash
Create Date: 2026-01-09

Adds execution_model column to track which execution system ran a workflow:
- 'process': Process pool model (process_pool.py + simple_worker.py)

This field is useful for:
- Debugging and performance analysis
- Future extensibility if execution models change
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260109_add_execution_model"
down_revision: Union[str, None] = "20260109_drop_commit_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add execution_model column - nullable since existing records won't have it
    op.add_column(
        "executions",
        sa.Column("execution_model", sa.String(20), nullable=True),
    )

    # Optional: Index for filtering by execution model
    op.create_index(
        "ix_executions_execution_model",
        "executions",
        ["execution_model"],
    )


def downgrade() -> None:
    op.drop_index("ix_executions_execution_model", table_name="executions")
    op.drop_column("executions", "execution_model")
