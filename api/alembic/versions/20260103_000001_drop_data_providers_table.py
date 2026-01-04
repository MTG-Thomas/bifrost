"""Drop data_providers table after consolidation.

Revision ID: 20260103_000001
Revises: 20260103_000000
Create Date: 2026-01-03

This migration drops the data_providers table after all data has been
consolidated into the workflows table. Run this only after verifying
the consolidation migration was successful.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "20260103_000001"
down_revision = "20260103_000000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop any remaining FK constraints pointing to data_providers
    # The form_fields FK was updated in the previous migration
    # But there might be other constraints we need to handle
    op.execute("""
        DO $$
        DECLARE
            r RECORD;
        BEGIN
            FOR r IN (
                SELECT conname, conrelid::regclass::text as table_name
                FROM pg_constraint
                WHERE confrelid = 'data_providers'::regclass
            ) LOOP
                EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',
                               r.table_name, r.conname);
            END LOOP;
        END
        $$;
    """)

    # Drop the data_providers table
    op.drop_table("data_providers")


def downgrade() -> None:
    # Re-create data_providers table
    op.create_table(
        "data_providers",
        sa.Column("id", UUID(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("function_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("file_path", sa.String(1000), nullable=False),
        sa.Column("module_path", sa.String(500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_path", "function_name", name="data_providers_file_function_key"
        ),
    )
    op.create_index("ix_data_providers_name", "data_providers", ["name"])
    op.create_index("ix_data_providers_is_active", "data_providers", ["is_active"])

    # Copy data providers back from workflows table
    op.execute("""
        INSERT INTO data_providers (
            id, name, function_name, description, file_path, module_path,
            is_active, last_seen_at, created_at, updated_at
        )
        SELECT
            id, name, function_name, description, file_path, module_path,
            is_active, last_seen_at, created_at, updated_at
        FROM workflows
        WHERE type = 'data_provider'
    """)
