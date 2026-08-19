"""Bind immutable Workspace release artifact identity and provenance.

Revision ID: 20260819_workspace_release_artifact_v1
Revises: 20260812_private_memory
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_workspace_release_artifact_v1"
down_revision: str = "20260812_private_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column("content_id", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column("closure_id", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column("release_id", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column("base_release_id", sa.String(length=72), nullable=True),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column("base_manifest_id", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column("effective_manifest_id", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column(
            "effective_registration_manifest_id",
            sa.String(length=71),
            nullable=True,
        ),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column(
            "registration_intent_fingerprint", sa.String(length=71), nullable=True
        ),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column(
            "registration_state_fingerprint", sa.String(length=71), nullable=True
        ),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column("source_tree_sha", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workspace_promotion_artifacts",
        sa.Column(
            "supersedes_artifact_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "fk_workspace_promotion_artifact_supersedes",
        "workspace_promotion_artifacts",
        "workspace_promotion_artifacts",
        ["supersedes_artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_workspace_promotion_artifact_content",
        "workspace_promotion_artifacts",
        ["organization_id", "content_id"],
    )
    op.create_index(
        "ix_workspace_promotion_artifact_release",
        "workspace_promotion_artifacts",
        ["organization_id", "release_id"],
    )
    op.create_index(
        "uq_workspace_promotion_artifact_supersedes",
        "workspace_promotion_artifacts",
        ["supersedes_artifact_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_artifact_id IS NOT NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION reject_workspace_promotion_artifact_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'workspace promotion artifacts are append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER workspace_promotion_artifacts_append_only
        BEFORE UPDATE OR DELETE ON workspace_promotion_artifacts
        FOR EACH ROW EXECUTE FUNCTION reject_workspace_promotion_artifact_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER workspace_promotion_artifacts_append_only "
        "ON workspace_promotion_artifacts"
    )
    op.execute("DROP FUNCTION reject_workspace_promotion_artifact_mutation()")
    op.drop_index(
        "uq_workspace_promotion_artifact_supersedes",
        table_name="workspace_promotion_artifacts",
    )
    op.drop_index(
        "ix_workspace_promotion_artifact_release",
        table_name="workspace_promotion_artifacts",
    )
    op.drop_index(
        "ix_workspace_promotion_artifact_content",
        table_name="workspace_promotion_artifacts",
    )
    op.drop_constraint(
        "fk_workspace_promotion_artifact_supersedes",
        "workspace_promotion_artifacts",
        type_="foreignkey",
    )
    for column in (
        "supersedes_artifact_id",
        "source_tree_sha",
        "registration_state_fingerprint",
        "registration_intent_fingerprint",
        "effective_registration_manifest_id",
        "effective_manifest_id",
        "base_manifest_id",
        "base_release_id",
        "release_id",
        "closure_id",
        "content_id",
    ):
        op.drop_column("workspace_promotion_artifacts", column)
