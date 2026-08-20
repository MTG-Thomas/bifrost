"""Bind immutable Workspace release artifact identity and provenance.

Revision ID: 20260819_ws_release_artifact
Revises: 20260812_private_memory
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260819_ws_release_artifact"
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
    # The promotion tables were deployed before organization-bound ownership
    # was introduced.  Transform that already-applied schema here rather than
    # changing the historical table-creation migration: production must gain
    # the referenced unique keys before any composite foreign key is added.
    op.create_unique_constraint(
        "uq_workspace_promotion_artifact_org_id",
        "workspace_promotion_artifacts",
        ["organization_id", "id"],
    )
    op.create_unique_constraint(
        "uq_workspace_promotion_release_org_id",
        "workspace_promotion_releases",
        ["organization_id", "id"],
    )
    op.drop_constraint(
        "workspace_promotion_releases_artifact_id_fkey",
        "workspace_promotion_releases",
        type_="foreignkey",
    )
    op.drop_constraint(
        "workspace_promotion_releases_previous_release_id_fkey",
        "workspace_promotion_releases",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_workspace_promotion_release_artifact_org",
        "workspace_promotion_releases",
        "workspace_promotion_artifacts",
        ["organization_id", "artifact_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_workspace_promotion_release_previous_org",
        "workspace_promotion_releases",
        "workspace_promotion_releases",
        ["organization_id", "previous_release_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_foreign_key(
        "fk_workspace_promotion_artifact_supersedes",
        "workspace_promotion_artifacts",
        "workspace_promotion_artifacts",
        ["organization_id", "supersedes_artifact_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
        deferrable=True,
        initially="DEFERRED",
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
        "ix_workspace_promotion_artifact_draft_expiry",
        "workspace_promotion_artifacts",
        ["expires_at"],
        postgresql_where=sa.text("target_kind = 'draft'"),
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
            IF TG_OP = 'DELETE' THEN
                -- Local uploads are deliberately ephemeral and never carry
                -- release authority. Reviewed artifacts remain immutable.
                IF OLD.target_kind = 'draft' AND OLD.expires_at <= NOW() THEN
                    RETURN OLD;
                END IF;
                -- An organizations.id ON DELETE CASCADE reaches this row from
                -- PostgreSQL's parent FK trigger while the parent is still
                -- visible to the statement. Its nested trigger depth is the
                -- reliable distinction from a direct artifact DELETE.
                IF pg_trigger_depth() > 1 THEN
                    RETURN OLD;
                END IF;
            END IF;
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
        "ix_workspace_promotion_artifact_draft_expiry",
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
    op.drop_constraint(
        "fk_workspace_promotion_release_previous_org",
        "workspace_promotion_releases",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_workspace_promotion_release_artifact_org",
        "workspace_promotion_releases",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "workspace_promotion_releases_artifact_id_fkey",
        "workspace_promotion_releases",
        "workspace_promotion_artifacts",
        ["artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "workspace_promotion_releases_previous_release_id_fkey",
        "workspace_promotion_releases",
        "workspace_promotion_releases",
        ["previous_release_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "uq_workspace_promotion_release_org_id",
        "workspace_promotion_releases",
        type_="unique",
    )
    op.drop_constraint(
        "uq_workspace_promotion_artifact_org_id",
        "workspace_promotion_artifacts",
        type_="unique",
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
