"""Consolidate data_providers into workflows table.

Revision ID: 20260103_000000
Revises: 20260102_010000
Create Date: 2026-01-03

This migration consolidates the data_providers table into the workflows table
using a type discriminator field. This simplifies the codebase by having a
single table for all executable user code (workflows, tools, data providers).

Changes:
- Add 'type' column to workflows (values: 'workflow', 'tool', 'data_provider')
- Add 'cache_ttl_seconds' column to workflows (for data providers)
- Migrate is_tool=true to type='tool'
- Copy data_providers into workflows with type='data_provider'
- Update form_fields FK from data_providers to workflows
- Drop is_tool column and its index
- Keep data_providers table (will be dropped in separate migration)
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260103_000000"
down_revision = "20260102_010000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add new columns to workflows
    op.add_column(
        "workflows",
        sa.Column("type", sa.String(20), nullable=False, server_default="workflow"),
    )
    op.add_column(
        "workflows",
        sa.Column("cache_ttl_seconds", sa.Integer(), nullable=False, server_default="300"),
    )

    # 2. Migrate is_tool to type
    op.execute("UPDATE workflows SET type = 'tool' WHERE is_tool = true")
    op.execute("UPDATE workflows SET type = 'workflow' WHERE is_tool = false OR is_tool IS NULL")

    # 3. Copy data_providers into workflows with type='data_provider'
    # Note: We copy all relevant columns that exist in both tables
    op.execute("""
        INSERT INTO workflows (
            id, name, function_name, description, category, file_path, module_path,
            is_active, last_seen_at, type, cache_ttl_seconds, parameters_schema,
            tags, created_at, updated_at
        )
        SELECT
            id, name, function_name, description, 'General', file_path, module_path,
            is_active, last_seen_at, 'data_provider', 300, '[]'::jsonb,
            '[]'::jsonb, created_at, updated_at
        FROM data_providers
        ON CONFLICT (file_path, function_name) DO UPDATE SET
            type = 'data_provider',
            cache_ttl_seconds = 300,
            is_active = EXCLUDED.is_active,
            last_seen_at = EXCLUDED.last_seen_at,
            updated_at = EXCLUDED.updated_at
    """)

    # 4. Update FormField FK to point to workflows
    # Note: FK constraint may or may not exist depending on db state
    # Use raw SQL to drop if exists
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'form_fields_data_provider_id_fkey'
                AND table_name = 'form_fields'
            ) THEN
                ALTER TABLE form_fields DROP CONSTRAINT form_fields_data_provider_id_fkey;
            END IF;
        END
        $$;
    """)
    op.create_foreign_key(
        "form_fields_data_provider_id_fkey",
        "form_fields",
        "workflows",
        ["data_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 5. Create index on type for performance
    op.create_index("ix_workflows_type", "workflows", ["type"])

    # 6. Drop is_tool column and its index
    op.drop_index("ix_workflows_is_tool", table_name="workflows")
    op.drop_column("workflows", "is_tool")


def downgrade() -> None:
    # 1. Re-add is_tool column
    op.add_column(
        "workflows",
        sa.Column("is_tool", sa.Boolean(), nullable=False, server_default="false"),
    )

    # 2. Migrate type back to is_tool
    op.execute("UPDATE workflows SET is_tool = true WHERE type = 'tool'")
    op.execute("UPDATE workflows SET is_tool = false WHERE type != 'tool'")

    # 3. Re-create is_tool index
    op.create_index(
        "ix_workflows_is_tool",
        "workflows",
        ["is_tool"],
        postgresql_where=sa.text("is_tool = true"),
    )

    # 4. Restore FormField FK to data_providers
    op.drop_constraint(
        "form_fields_data_provider_id_fkey", "form_fields", type_="foreignkey"
    )
    op.create_foreign_key(
        "form_fields_data_provider_id_fkey",
        "form_fields",
        "data_providers",
        ["data_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # 5. Drop type index and columns
    op.drop_index("ix_workflows_type", table_name="workflows")
    op.drop_column("workflows", "cache_ttl_seconds")
    op.drop_column("workflows", "type")

    # Note: We do NOT delete the data_provider rows from workflows
    # because they may have been created manually. The separate
    # drop_data_providers migration handles cleanup.
