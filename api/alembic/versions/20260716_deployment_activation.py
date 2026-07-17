"""allow immutable deployment rollback activation

Revision ID: 20260716_deploy_activation
Revises: 20260716_exec_deploy_pin
Create Date: 2026-07-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260716_deploy_activation"
down_revision: str = "20260716_exec_deploy_pin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _install_integrity_function(*, allow_rollback: bool) -> None:
    rollback_transition = (
        "OR (OLD.state = 'superseded' AND NEW.state IN ('superseded','activating'))"
        if allow_rollback
        else "OR (OLD.state = 'superseded' AND NEW.state = OLD.state)"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION enforce_solution_deployment_integrity()
        RETURNS trigger AS $$
        DECLARE solution_org uuid;
        BEGIN
          SELECT organization_id INTO solution_org FROM solutions WHERE id = NEW.solution_id;
          IF NOT FOUND OR solution_org IS DISTINCT FROM NEW.organization_id THEN
            RAISE EXCEPTION 'deployment organization scope must match its Solution install';
          END IF;
          IF TG_OP = 'INSERT' AND NEW.state <> 'draft' THEN
            RAISE EXCEPTION 'new SolutionDeployment must start in draft';
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
            (OLD.state IN ('conflicted','failed','recovery_required','aborted')
              AND NEW.state = OLD.state)
            {rollback_transition}
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
        """
    )


def upgrade() -> None:
    _install_integrity_function(allow_rollback=True)


def downgrade() -> None:
    _install_integrity_function(allow_rollback=False)
