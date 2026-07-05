"""merge deploy-job nullable and solution export heads

Revision ID: 20260705_merge_deploy_export
Revises: 20260702_merge_sol_oauth_heads, 20260702_deployjob_install_null
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "20260705_merge_deploy_export"
down_revision: tuple[str, str] = (
    "20260702_merge_sol_oauth_heads",
    "20260702_deployjob_install_null",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
