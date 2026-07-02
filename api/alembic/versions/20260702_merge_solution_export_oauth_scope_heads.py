"""merge solution export and OAuth scope metadata heads

Merging current main into the upstream-sync branch brought in
``20260702_oauth_scope_metadata`` while the upstream solution-file chain still
ended at ``20260625_solution_export_jobs``. Both branches descend from
``20260617_solution_deploy_jobs`` and are independent schema/data changes, so
this no-op revision restores a single Alembic head.

Revision ID: 20260702_merge_sol_oauth_heads
Revises: 20260625_solution_export_jobs, 20260702_oauth_scope_metadata
Create Date: 2026-07-02 00:00:00.000000
"""

revision = "20260702_merge_sol_oauth_heads"
down_revision = ("20260625_solution_export_jobs", "20260702_oauth_scope_metadata")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
