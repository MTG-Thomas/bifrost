"""add durable workspace closure fencing and audit fields

Revision ID: 20260811_workspace_closure
Revises: 20260807_withdraw_builder
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260811_workspace_closure"
down_revision: str = "20260807_withdraw_builder"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("writer_job_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("dirty_generation", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("authoritative_revision", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("authoritative_files", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("authoritative_base_files", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("activation_backup", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("remote_sha", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("commit_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column(
            "push_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("closure_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "workspace_repo_changesets",
        sa.Column("closure_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_workspace_repo_changesets_writer_job_id",
        "workspace_repo_changesets",
        ["writer_job_id"],
    )
    op.create_foreign_key(
        "fk_workspace_repo_changesets_writer_job_id",
        "workspace_repo_changesets",
        "platform_jobs",
        ["writer_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_workspace_repo_changesets_writer_job_id",
        "workspace_repo_changesets",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_workspace_repo_changesets_writer_job_id",
        table_name="workspace_repo_changesets",
    )
    for column in (
        "closure_completed_at",
        "closure_started_at",
        "push_requested",
        "commit_message",
        "remote_sha",
        "authoritative_base_files",
        "authoritative_files",
        "activation_backup",
        "authoritative_revision",
        "dirty_generation",
        "writer_job_id",
    ):
        op.drop_column("workspace_repo_changesets", column)
