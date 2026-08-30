"""Track reviewed Solution deploy obligations.

Revision ID: 20260826_solution_obligations
Revises: 20260825_delivery_attempt
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_solution_obligations"
down_revision: str | None = "20260825_delivery_attempt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_deploy_obligations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_commit_sha", sa.String(length=40), nullable=False),
        sa.Column("source_tree_sha", sa.String(length=40), nullable=False),
        sa.Column("base_commit_sha", sa.String(length=40), nullable=True),
        sa.Column("solution_slug", sa.String(length=255), nullable=False),
        sa.Column("repo_subpath", sa.String(length=1000), nullable=False),
        sa.Column("source_subtree_sha", sa.String(length=40), nullable=True),
        sa.Column("source_content_id", sa.String(length=71), nullable=True),
        sa.Column("base_source_content_id", sa.String(length=71), nullable=True),
        sa.Column("declared_version", sa.String(length=64), nullable=True),
        sa.Column("source_files", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("changed_paths", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=40),
            server_default="solution_deploy_required",
            nullable=False,
        ),
        sa.Column("disposition", sa.String(length=30), nullable=False),
        sa.Column("declared_disposition", sa.String(length=30), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("solution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("deploy_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_id", sa.String(length=71), nullable=True),
        sa.Column("source_artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("completion_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            "kind = 'solution_deploy_required'",
            name="ck_solution_deploy_obligation_kind",
        ),
        sa.CheckConstraint(
            "disposition IN ('pending', 'attention_required', 'released', "
            "'superseded')",
            name="ck_solution_deploy_obligation_disposition",
        ),
        sa.CheckConstraint(
            "declared_disposition IN ('solution_deploy_required', 'attention_required')",
            name="ck_solution_deploy_obligation_declared_disposition",
        ),
        sa.CheckConstraint(
            "disposition <> 'released' OR "
            "(solution_id IS NOT NULL AND deploy_job_id IS NOT NULL AND "
            "candidate_id IS NOT NULL AND source_artifact_sha256 IS NOT NULL AND "
            "completion_evidence IS NOT NULL AND resolved_at IS NOT NULL)",
            name="ck_solution_deploy_obligation_released_evidence",
        ),
        sa.CheckConstraint(
            "disposition <> 'attention_required' OR reason IS NOT NULL",
            name="ck_solution_deploy_obligation_attention_reason",
        ),
        sa.ForeignKeyConstraint(
            ["source_release_id"],
            ["workspace_source_releases.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_commit_sha",
            "solution_slug",
            name="uq_solution_deploy_obligation_commit_slug",
        ),
    )
    op.create_index(
        "ix_solution_deploy_obligations_organization_id",
        "solution_deploy_obligations",
        ["organization_id"],
    )
    op.create_index(
        "ix_solution_deploy_obligation_attention",
        "solution_deploy_obligations",
        ["disposition", "due_at"],
        postgresql_where=sa.text("disposition IN ('pending', 'attention_required')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_solution_deploy_obligation_attention",
        table_name="solution_deploy_obligations",
    )
    op.drop_index(
        "ix_solution_deploy_obligations_organization_id",
        table_name="solution_deploy_obligations",
    )
    op.drop_table("solution_deploy_obligations")
