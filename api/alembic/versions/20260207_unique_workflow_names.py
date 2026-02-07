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

    # Check for duplicate org-scoped names before creating index
    dupes = conn.execute(text("""
        SELECT organization_id, name, COUNT(*) as cnt
        FROM workflows
        WHERE organization_id IS NOT NULL AND is_active = true
        GROUP BY organization_id, name
        HAVING COUNT(*) > 1
    """)).fetchall()

    if dupes:
        for org_id, name, cnt in dupes:
            print(f"WARNING: Duplicate org-scoped workflow name: org={org_id} name='{name}' count={cnt}")
        raise RuntimeError(
            f"Cannot create unique index: {len(dupes)} duplicate org-scoped workflow name(s) found. "
            "Resolve duplicates before retrying."
        )

    # Check for duplicate global names
    global_dupes = conn.execute(text("""
        SELECT name, COUNT(*) as cnt
        FROM workflows
        WHERE organization_id IS NULL AND is_active = true
        GROUP BY name
        HAVING COUNT(*) > 1
    """)).fetchall()

    if global_dupes:
        for name, cnt in global_dupes:
            print(f"WARNING: Duplicate global workflow name: name='{name}' count={cnt}")
        raise RuntimeError(
            f"Cannot create unique index: {len(global_dupes)} duplicate global workflow name(s) found. "
            "Resolve duplicates before retrying."
        )

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
