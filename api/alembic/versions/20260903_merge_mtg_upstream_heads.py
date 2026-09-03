"""Merge Midtown and upstream migration heads.

Revision ID: 20260903_merge_mtg_upstream
Revises: 20260903_execution_retry_policy, 20260902_chat_run_agentless
Create Date: 2026-09-03

The Midtown operational-reconciliation chain and upstream's independent-app,
OpenAI transport, and durable-chat chain both descend from
``20260823_job_memory_profiles``. Midtown's retry-policy revision continues its
branch after execution attempts. This no-op revision restores the single head
required by ``alembic upgrade head`` while preserving both histories.
"""

from collections.abc import Sequence

revision: str = "20260903_merge_mtg_upstream"
down_revision: tuple[str, str] = (
    "20260903_execution_retry_policy",
    "20260902_chat_run_agentless",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Merge migration heads only; schema changes live in the parents."""
    pass


def downgrade() -> None:
    """Merge migration heads only; downgrade behavior lives in the parents."""
    pass
