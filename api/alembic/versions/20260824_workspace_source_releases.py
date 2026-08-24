"""Track reviewed Workspace source release dispositions.

Revision ID: 20260824_ws_source_releases
Revises: 20260819_exec_dispatch
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260824_ws_source_releases"
down_revision: str | None = "20260819_exec_dispatch"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_source_releases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_commit_sha", sa.String(length=40), nullable=False),
        sa.Column("source_tree_sha", sa.String(length=40), nullable=False),
        sa.Column("paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("disposition", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("release_row_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "completion_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "disposition IN ('pending', 'attention_required', 'released', "
            "'deferred', 'non_production')",
            name="ck_workspace_source_release_disposition",
        ),
        sa.CheckConstraint(
            "disposition <> 'released' OR "
            "(release_row_id IS NOT NULL AND completion_evidence IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="ck_workspace_source_release_released_evidence",
        ),
        sa.CheckConstraint(
            "disposition <> 'attention_required' OR reason IS NOT NULL",
            name="ck_workspace_source_release_attention_reason",
        ),
        sa.CheckConstraint(
            "disposition NOT IN ('deferred', 'non_production') OR "
            "(reason IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_workspace_source_release_manual_reason",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["release_row_id"],
            ["workspace_promotion_releases.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_commit_sha",
            name="uq_workspace_source_release_commit",
        ),
    )
    op.create_index(
        "ix_workspace_source_releases_organization_id",
        "workspace_source_releases",
        ["organization_id"],
    )
    op.create_index(
        "ix_workspace_source_release_attention",
        "workspace_source_releases",
        ["disposition", "due_at"],
        postgresql_where=sa.text("disposition IN ('pending', 'attention_required')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_source_release_attention",
        table_name="workspace_source_releases",
    )
    op.drop_index(
        "ix_workspace_source_releases_organization_id",
        table_name="workspace_source_releases",
    )
    op.drop_table("workspace_source_releases")
