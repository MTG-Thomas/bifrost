"""Allow exact-bound workflow-dispatch source-release provenance.

Revision ID: 20260824_ws_dispatch_oidc
Revises: 20260824_ws_source_provenance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_ws_dispatch_oidc"
down_revision: str | None = "20260824_ws_source_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_source_releases",
        sa.Column(
            "producer_triggering_workflow_run_attempt",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "workspace_source_releases",
        sa.Column("producer_declaration_digest", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workspace_source_releases",
        sa.Column("producer_actor", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "workspace_source_releases",
        sa.Column("producer_actor_id", sa.String(length=30), nullable=True),
    )
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
    op.create_check_constraint(
        "ck_workspace_source_release_producer_provenance",
        "workspace_source_releases",
        "(declaration_actor = 'github_actions_oidc' AND "
        "producer_oidc_commit_sha IS NOT NULL AND "
        "producer_event_name IN ('push', 'workflow_run', 'workflow_dispatch') AND "
        "producer_run_id IS NOT NULL) OR "
        "(declaration_actor <> 'github_actions_oidc' AND "
        "producer_oidc_commit_sha IS NULL AND producer_event_name IS NULL AND "
        "producer_run_id IS NULL AND "
        "producer_triggering_workflow_run_id IS NULL AND "
        "producer_triggering_workflow_run_attempt IS NULL AND "
        "producer_declaration_digest IS NULL AND producer_actor IS NULL AND "
        "producer_actor_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_workspace_source_release_triggering_run",
        "workspace_source_releases",
        "producer_event_name IS NULL OR "
        "(producer_event_name = 'push' AND "
        "producer_triggering_workflow_run_id IS NULL AND "
        "producer_triggering_workflow_run_attempt IS NULL AND "
        "producer_declaration_digest IS NULL) OR "
        "(producer_event_name = 'workflow_run' AND "
        "producer_triggering_workflow_run_id IS NOT NULL AND "
        "producer_triggering_workflow_run_attempt IS NULL AND "
        "producer_declaration_digest IS NULL) OR "
        "(producer_event_name = 'workflow_dispatch' AND "
        "producer_triggering_workflow_run_id IS NOT NULL AND "
        "producer_triggering_workflow_run_attempt > 0 AND "
        "producer_declaration_digest ~ '^[0-9a-f]{64}$' AND "
        "producer_actor = 'github-actions[bot]' AND "
        "producer_actor_id = '41898282')",
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
    op.drop_column(
        "workspace_source_releases",
        "producer_triggering_workflow_run_attempt",
    )
    op.drop_column("workspace_source_releases", "producer_declaration_digest")
    op.drop_column("workspace_source_releases", "producer_actor_id")
    op.drop_column("workspace_source_releases", "producer_actor")
