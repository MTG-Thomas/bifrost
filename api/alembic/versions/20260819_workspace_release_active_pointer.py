"""Enforce one idempotent Workspace release and one platform-global Live pointer.

Revision ID: 20260819_workspace_release_active
Revises: 20260819_workspace_release_artifact_v1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260819_workspace_release_active"
down_revision: str | None = "20260819_workspace_release_artifact_v1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_promotion_releases",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "uq_workspace_promotion_release_idempotency",
        "workspace_promotion_releases",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "uq_workspace_promotion_release_live",
        "workspace_promotion_releases",
        ["activation_state"],
        unique=True,
        postgresql_where=sa.text("activation_state = 'live'"),
    )
    op.create_check_constraint(
        "ck_workspace_promotion_release_live_has_lock_job",
        "workspace_promotion_releases",
        "activation_state <> 'live' OR ("
        "lock_state IN ('queued', 'in_progress', 'locked', 'attention_required') "
        "AND lock_in_job_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workspace_promotion_release_live_has_lock_job",
        "workspace_promotion_releases",
        type_="check",
    )
    op.drop_index(
        "uq_workspace_promotion_release_live",
        table_name="workspace_promotion_releases",
    )
    op.drop_index(
        "uq_workspace_promotion_release_idempotency",
        table_name="workspace_promotion_releases",
    )
    op.drop_column("workspace_promotion_releases", "idempotency_key")
