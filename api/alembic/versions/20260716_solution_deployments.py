"""add immutable Solution deployment runtime closures

Revision ID: 20260716_solution_deployments
Revises: 20260716_ws_repo_changesets
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_solution_deployments"
down_revision: str = "20260716_ws_repo_changesets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEPLOYMENT_ID_COLUMN = "solution_deployments.id"
_DEPLOYMENT_SOLUTION_COLUMN = "solution_deployments.solution_id"


def upgrade() -> None:
    op.create_table(
        "solution_deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("solution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("base_deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("declared_version", sa.String(length=64), nullable=True),
        sa.Column("bundle_hash", sa.String(length=71), nullable=False),
        sa.Column("compiled_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("compiled_manifest_hash", sa.String(length=71), nullable=False),
        sa.Column("resolution_map", postgresql.JSONB(), nullable=False),
        sa.Column("resolution_map_hash", sa.String(length=71), nullable=False),
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
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["solution_id"], ["solutions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["parent_deployment_id", "solution_id"],
            [_DEPLOYMENT_ID_COLUMN, _DEPLOYMENT_SOLUTION_COLUMN],
            name="fk_solution_deployment_parent_same_solution",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["base_deployment_id", "solution_id"],
            [_DEPLOYMENT_ID_COLUMN, _DEPLOYMENT_SOLUTION_COLUMN],
            name="fk_solution_deployment_base_same_solution",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "state IN ('draft','building','validated','ready','activating','active',"
            "'superseded','conflicted','failed','recovery_required','aborted',"
            "'committed_unpushed')",
            name="ck_solution_deployments_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "solution_id", name="uq_solution_deployment_id_solution"),
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
            ["deployment_id"], [_DEPLOYMENT_ID_COLUMN], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["dependency_solution_id"], ["solutions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["dependency_deployment_id", "dependency_solution_id"],
            [_DEPLOYMENT_ID_COLUMN, _DEPLOYMENT_SOLUTION_COLUMN],
            name="fk_solution_dependency_exact_deployment",
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
        ["active_deployment_id", "id"],
        ["id", "solution_id"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        CREATE FUNCTION enforce_solution_deployment_integrity() RETURNS trigger AS $$
        DECLARE solution_org uuid;
        BEGIN
          SELECT organization_id INTO solution_org FROM solutions WHERE id = NEW.solution_id;
          IF NOT FOUND OR solution_org IS DISTINCT FROM NEW.organization_id THEN
            RAISE EXCEPTION 'deployment organization scope must match its Solution install';
          END IF;
          IF TG_OP = 'UPDATE' AND NOT (
            (OLD.state = 'draft' AND NEW.state IN ('draft','building','aborted')) OR
            (OLD.state = 'building' AND NEW.state IN ('building','validated','failed','aborted')) OR
            (OLD.state = 'validated' AND NEW.state IN ('validated','ready','aborted')) OR
            (OLD.state = 'ready' AND NEW.state IN ('ready','activating','aborted')) OR
            (OLD.state = 'activating' AND NEW.state IN
              ('activating','active','conflicted','failed','recovery_required')) OR
            (OLD.state = 'active' AND NEW.state IN
              ('active','superseded','committed_unpushed','recovery_required')) OR
            (OLD.state = 'committed_unpushed' AND NEW.state IN
              ('committed_unpushed','active','superseded','recovery_required')) OR
            (OLD.state IN ('superseded','conflicted','failed','recovery_required','aborted')
              AND NEW.state = OLD.state)
          ) THEN
            RAISE EXCEPTION 'invalid SolutionDeployment state transition: % -> %',
              OLD.state, NEW.state;
          END IF;
          IF TG_OP = 'UPDATE' AND OLD.state NOT IN ('draft', 'building') AND (
            NEW.id IS DISTINCT FROM OLD.id OR
            NEW.organization_id IS DISTINCT FROM OLD.organization_id OR
            NEW.solution_id IS DISTINCT FROM OLD.solution_id OR
            NEW.parent_deployment_id IS DISTINCT FROM OLD.parent_deployment_id OR
            NEW.base_deployment_id IS DISTINCT FROM OLD.base_deployment_id OR
            NEW.declared_version IS DISTINCT FROM OLD.declared_version OR
            NEW.bundle_hash IS DISTINCT FROM OLD.bundle_hash OR
            NEW.compiled_manifest IS DISTINCT FROM OLD.compiled_manifest OR
            NEW.compiled_manifest_hash IS DISTINCT FROM OLD.compiled_manifest_hash OR
            NEW.resolution_map IS DISTINCT FROM OLD.resolution_map OR
            NEW.resolution_map_hash IS DISTINCT FROM OLD.resolution_map_hash OR
            NEW.source_artifact_key IS DISTINCT FROM OLD.source_artifact_key OR
            NEW.runtime_storage_prefix IS DISTINCT FROM OLD.runtime_storage_prefix OR
            NEW.git_repository IS DISTINCT FROM OLD.git_repository OR
            NEW.git_ref IS DISTINCT FROM OLD.git_ref OR
            NEW.git_commit_sha IS DISTINCT FROM OLD.git_commit_sha OR
            NEW.created_by IS DISTINCT FROM OLD.created_by OR
            NEW.codex_worker_id IS DISTINCT FROM OLD.codex_worker_id OR
            NEW.created_at IS DISTINCT FROM OLD.created_at
          ) THEN
            RAISE EXCEPTION 'SolutionDeployment runtime closure is write-once';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_solution_deployment_integrity
          BEFORE INSERT OR UPDATE ON solution_deployments
          FOR EACH ROW EXECUTE FUNCTION enforce_solution_deployment_integrity();

        CREATE FUNCTION prevent_solution_deployment_delete() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'SolutionDeployment history cannot be deleted';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_solution_deployment_no_delete
          BEFORE DELETE ON solution_deployments
          FOR EACH ROW EXECUTE FUNCTION prevent_solution_deployment_delete();

        CREATE FUNCTION enforce_solution_dependency_integrity() RETURNS trigger AS $$
        DECLARE owner_org uuid; dependency_org uuid;
        BEGIN
          SELECT organization_id INTO owner_org FROM solution_deployments WHERE id = NEW.deployment_id;
          SELECT organization_id INTO dependency_org FROM solution_deployments
            WHERE id = NEW.dependency_deployment_id;
          IF dependency_org IS NOT NULL AND dependency_org IS DISTINCT FROM owner_org THEN
            RAISE EXCEPTION 'dependency deployment must be global or in the owning organization';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_solution_dependency_integrity
          BEFORE INSERT ON solution_deployment_dependencies
          FOR EACH ROW EXECUTE FUNCTION enforce_solution_dependency_integrity();
        CREATE TRIGGER trg_solution_dependency_immutable
          BEFORE UPDATE OR DELETE ON solution_deployment_dependencies
          FOR EACH ROW EXECUTE FUNCTION prevent_solution_deployment_delete();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER trg_solution_dependency_immutable ON solution_deployment_dependencies;
        DROP TRIGGER trg_solution_dependency_integrity ON solution_deployment_dependencies;
        DROP FUNCTION enforce_solution_dependency_integrity();
        DROP TRIGGER trg_solution_deployment_no_delete ON solution_deployments;
        DROP TRIGGER trg_solution_deployment_integrity ON solution_deployments;
        DROP FUNCTION prevent_solution_deployment_delete();
        DROP FUNCTION enforce_solution_deployment_integrity();
        """
    )
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
