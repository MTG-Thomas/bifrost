"""add persistent workspace _repo changesets

Revision ID: 20260716_ws_repo_changesets
Revises: 20260705_merge_deploy_export
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_ws_repo_changesets"
down_revision: str = "20260705_merge_deploy_export"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_repo_changesets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scope", sa.String(1000), nullable=False),
        sa.Column("base_revision", sa.String(64), nullable=False),
        sa.Column("base_files", postgresql.JSONB(), nullable=False),
        sa.Column("mutations", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("worker_id", sa.String(255), nullable=True),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("validation", postgresql.JSONB(), nullable=True),
        sa.Column("activated_revision", sa.String(64), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("failure_detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_repo_changesets_org_scope_status",
        "workspace_repo_changesets",
        ["organization_id", "scope", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_repo_changesets_org_scope_status", table_name="workspace_repo_changesets")
    op.drop_table("workspace_repo_changesets")
