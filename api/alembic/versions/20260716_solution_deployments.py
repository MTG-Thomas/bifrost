"""add immutable Solution deployment runtime closures

Revision ID: 20260716_solution_deployments
Revises: 20260705_merge_deploy_export
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_solution_deployments"
down_revision: str = "20260705_merge_deploy_export"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "solution_deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("solution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("declared_version", sa.String(length=64), nullable=True),
        sa.Column("bundle_hash", sa.String(length=71), nullable=False),
        sa.Column("compiled_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("compiled_manifest_hash", sa.String(length=71), nullable=False),
        sa.Column("resolution_map", postgresql.JSONB(), nullable=False),
        sa.Column("source_artifact_key", sa.String(length=2048), nullable=False),
        sa.Column("runtime_storage_prefix", sa.String(length=2048), nullable=False),
        sa.Column("git_repository", sa.String(length=2048), nullable=True),
        sa.Column("git_ref", sa.String(length=255), nullable=True),
        sa.Column("git_commit_sha", sa.String(length=64), nullable=True),
        sa.Column("git_push_state", sa.String(length=32), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("codex_worker_id", sa.String(length=255), nullable=True),
        sa.Column("validation_result", postgresql.JSONB(), nullable=True),
        sa.Column("failure_detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_deployment_id"], ["solution_deployments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["base_deployment_id"], ["solution_deployments.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "state IN ('draft','building','validated','ready','activating','active',"
            "'superseded','conflicted','failed','recovery_required','aborted',"
            "'committed_unpushed')",
            name="ck_solution_deployments_state",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_solution_deployments_solution_created",
        "solution_deployments",
        ["solution_id", "created_at"],
    )
    op.create_index(
        "ix_solution_deployments_org_state",
        "solution_deployments",
        ["organization_id", "state"],
    )
    op.create_table(
        "solution_deployment_dependencies",
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "dependency_solution_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "dependency_deployment_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("declared_constraint", sa.Text(), nullable=True),
        sa.Column("resolved_bundle_hash", sa.String(length=71), nullable=False),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["solution_deployments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["dependency_solution_id"], ["solutions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["dependency_deployment_id"],
            ["solution_deployments.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("deployment_id", "dependency_solution_id"),
    )
    op.add_column(
        "solutions",
        sa.Column("active_deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_solutions_active_deployment",
        "solutions",
        "solution_deployments",
        ["active_deployment_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_solutions_active_deployment", "solutions", type_="foreignkey"
    )
    op.drop_column("solutions", "active_deployment_id")
    op.drop_table("solution_deployment_dependencies")
    op.drop_index(
        "ix_solution_deployments_org_state", table_name="solution_deployments"
    )
    op.drop_index(
        "ix_solution_deployments_solution_created", table_name="solution_deployments"
    )
    op.drop_table("solution_deployments")
