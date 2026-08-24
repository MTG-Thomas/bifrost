"""Persist trusted Workspace source-release producer provenance.

Revision ID: 20260824_ws_source_provenance
Revises: 20260824_ws_source_releases
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_ws_source_provenance"
down_revision: str | None = "20260824_ws_source_releases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_source_releases",
        sa.Column("declaration_actor", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "workspace_source_releases",
        sa.Column("producer_oidc_commit_sha", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "workspace_source_releases",
        sa.Column("producer_event_name", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "workspace_source_releases",
        sa.Column("producer_run_id", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "workspace_source_releases",
        sa.Column(
            "producer_triggering_workflow_run_id",
            sa.String(length=30),
            nullable=True,
        ),
    )
    op.execute(
        "UPDATE workspace_source_releases "
        "SET declaration_actor = CASE "
        "WHEN created_by = '00000000-0000-0000-0000-000000000001'::uuid "
        "THEN 'legacy_unattributed' ELSE 'platform_admin' END"
    )
    op.alter_column(
        "workspace_source_releases",
        "declaration_actor",
        existing_type=sa.String(length=30),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_workspace_source_release_declaration_actor",
        "workspace_source_releases",
        "declaration_actor IN "
        "('github_actions_oidc', 'platform_admin', 'legacy_unattributed')",
    )
    op.create_check_constraint(
        "ck_workspace_source_release_producer_provenance",
        "workspace_source_releases",
        "(declaration_actor = 'github_actions_oidc' AND "
        "producer_oidc_commit_sha IS NOT NULL AND "
        "producer_event_name IN ('push', 'workflow_run') AND "
        "producer_run_id IS NOT NULL) OR "
        "(declaration_actor <> 'github_actions_oidc' AND "
        "producer_oidc_commit_sha IS NULL AND producer_event_name IS NULL AND "
        "producer_run_id IS NULL AND "
        "producer_triggering_workflow_run_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_workspace_source_release_triggering_run",
        "workspace_source_releases",
        "producer_event_name IS NULL OR "
        "(producer_event_name = 'push' AND "
        "producer_triggering_workflow_run_id IS NULL) OR "
        "(producer_event_name = 'workflow_run' AND "
        "producer_triggering_workflow_run_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workspace_source_release_triggering_run",
        "workspace_source_releases",
        type_="check",
    )
    op.drop_constraint(
        "ck_workspace_source_release_producer_provenance",
        "workspace_source_releases",
        type_="check",
    )
    op.drop_constraint(
        "ck_workspace_source_release_declaration_actor",
        "workspace_source_releases",
        type_="check",
    )
    op.drop_column(
        "workspace_source_releases", "producer_triggering_workflow_run_id"
    )
    op.drop_column("workspace_source_releases", "producer_run_id")
    op.drop_column("workspace_source_releases", "producer_event_name")
    op.drop_column("workspace_source_releases", "producer_oidc_commit_sha")
    op.drop_column("workspace_source_releases", "declaration_actor")
