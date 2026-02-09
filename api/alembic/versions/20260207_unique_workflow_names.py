"""unique_workflow_names

Revision ID: d4f6a8b05e23
Revises: c3e5f7a94d12
Create Date: 2026-02-07 10:00:00.000000+00:00

Add partial unique indexes on workflow names to ensure uniqueness
within each scope (per-org and global). Uses partial indexes because
organization_id is nullable and NULL != NULL in standard UNIQUE constraints.
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'd4f6a8b05e23'
down_revision: Union[str, None] = 'c3e5f7a94d12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Auto-resolve duplicate org-scoped names: keep the most recently
    # updated workflow and deactivate the rest.
    deactivated_org = conn.execute(text("""
        UPDATE workflows w
        SET is_active = false, updated_at = NOW()
        FROM (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY organization_id, name
                ORDER BY updated_at DESC NULLS LAST
            ) as rn
            FROM workflows
            WHERE organization_id IS NOT NULL AND is_active = true
        ) ranked
        WHERE w.id = ranked.id AND ranked.rn > 1
        RETURNING w.id, w.organization_id, w.name
    """)).fetchall()

    for wf_id, org_id, name in deactivated_org:
        print(f"AUTO-RESOLVED: Deactivated duplicate org-scoped workflow "
              f"id={wf_id} org={org_id} name='{name}'")

    if deactivated_org:
        print(f"Deactivated {len(deactivated_org)} duplicate org-scoped workflow(s)")

    # Auto-resolve duplicate global names: same approach.
    deactivated_global = conn.execute(text("""
        UPDATE workflows w
        SET is_active = false, updated_at = NOW()
        FROM (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY name
                ORDER BY updated_at DESC NULLS LAST
            ) as rn
            FROM workflows
            WHERE organization_id IS NULL AND is_active = true
        ) ranked
        WHERE w.id = ranked.id AND ranked.rn > 1
        RETURNING w.id, w.name
    """)).fetchall()

    for wf_id, name in deactivated_global:
        print(f"AUTO-RESOLVED: Deactivated duplicate global workflow "
              f"id={wf_id} name='{name}'")

    if deactivated_global:
        print(f"Deactivated {len(deactivated_global)} duplicate global workflow(s)")

    # Org-scoped: one workflow per name per org (only active workflows)
    op.execute(text("""
        CREATE UNIQUE INDEX uq_workflows_org_name
        ON workflows (organization_id, name)
        WHERE organization_id IS NOT NULL AND is_active = true
    """))

    # Global: one workflow per name globally (only active workflows)
    op.execute(text("""
        CREATE UNIQUE INDEX uq_workflows_global_name
        ON workflows (name)
        WHERE organization_id IS NULL AND is_active = true
    """))


def downgrade() -> None:
    op.execute(text("DROP INDEX IF EXISTS uq_workflows_global_name"))
    op.execute(text("DROP INDEX IF EXISTS uq_workflows_org_name"))
