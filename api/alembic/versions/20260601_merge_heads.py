"""Merge summary idempotency and developer context heads.

Revision ID: 20260601_merge_heads
Revises: 20260501a_bf_idempotency, 20260526_drop_developer_contexts
Create Date: 2026-06-01
"""

from collections.abc import Sequence


revision: str = "20260601_merge_heads"
down_revision: tuple[str, str] = (
    "20260501a_bf_idempotency",
    "20260526_drop_developer_contexts",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No schema changes; this revision only merges two Alembic heads.
    pass


def downgrade() -> None:
    # No schema changes to reverse for this merge-only revision.
    pass
