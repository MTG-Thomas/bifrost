"""Add immutable Workspace promotion artifacts and release state.

Revision ID: 20260813_workspace_promotions
Revises: 20260813_operation_receipts
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260813_workspace_promotions"
down_revision: str = "20260813_operation_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workspace_promotion_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_id", sa.String(length=71), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("target_kind", sa.String(length=20), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("entry_path", sa.String(length=1000), nullable=False),
        sa.Column("entry_function", sa.String(length=255), nullable=False),
        sa.Column("snapshot_id", sa.String(length=71), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=True),
        sa.Column("source_artifact_key", sa.String(length=1500), nullable=False),
        sa.Column("manifest_key", sa.String(length=1500), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_class", sa.String(length=10), nullable=False),
        sa.Column("disposition", sa.String(length=30), nullable=False),
        sa.Column("artifact_state", sa.String(length=30), nullable=False),
        sa.Column("policy_version", sa.String(length=100), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "artifact_state IN ('previewed', 'eligible', 'review_required', 'invalid')",
            name="ck_workspace_promotion_artifact_state",
        ),
        sa.CheckConstraint(
            "risk_class IN ('R0', 'R1', 'R2')",
            name="ck_workspace_promotion_artifact_risk",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_promotion_artifacts_organization_id",
        "workspace_promotion_artifacts",
        ["organization_id"],
    )
    op.create_index(
        "uq_workspace_promotion_artifact_candidate",
        "workspace_promotion_artifacts",
        ["organization_id", "candidate_id"],
        unique=True,
    )
    op.create_table(
        "workspace_promotion_releases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_release_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("activation_state", sa.String(length=30), nullable=False),
        sa.Column("lock_state", sa.String(length=30), nullable=False),
        sa.Column(
            "workspace_changeset_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("lock_in_job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "activation_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "lock_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attention_deadline", sa.DateTime(timezone=True), nullable=True),
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
            "activation_state IN ('prepared', 'activating', 'live', 'activation_failed', "
            "'recovery_required', 'rolled_back', 'superseded')",
            name="ck_workspace_promotion_release_activation_state",
        ),
        sa.CheckConstraint(
            "lock_state IN ('not_queued', 'queued', 'in_progress', 'locked', "
            "'attention_required', 'superseded')",
            name="ck_workspace_promotion_release_lock_state",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["workspace_promotion_artifacts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["lock_in_job_id"], ["platform_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["previous_release_id"],
            ["workspace_promotion_releases.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_changeset_id"],
            ["workspace_repo_changesets.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_workspace_promotion_releases_organization_id",
        "workspace_promotion_releases",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_promotion_releases_organization_id",
        table_name="workspace_promotion_releases",
    )
    op.drop_table("workspace_promotion_releases")
    op.drop_index(
        "uq_workspace_promotion_artifact_candidate",
        table_name="workspace_promotion_artifacts",
    )
    op.drop_index(
        "ix_workspace_promotion_artifacts_organization_id",
        table_name="workspace_promotion_artifacts",
    )
    op.drop_table("workspace_promotion_artifacts")
