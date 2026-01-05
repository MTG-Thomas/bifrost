"""Workflow identity redesign Phase 1 schema changes.

Revision ID: 20260104_000000
Revises: 20260103_000001
Create Date: 2026-01-04

This migration adds schema support for the workflow identity redesign:

1. Workflows table:
   - Add 'code' column for storing full Python source
   - Add 'code_hash' column for SHA256 hash of code
   - Rename 'file_path' to 'path' (update unique constraint)

2. workspace_files table:
   - Add 'entity_type' for polymorphic entity reference
   - Add 'entity_id' for entity UUID reference

3. forms table:
   - Add 'workflow_path' for redundant workflow reference
   - Add 'workflow_function_name' for redundant workflow reference

Data backfill is handled in a separate migration.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260104_000000"
down_revision = "20260103_000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # 1. Workflows table changes
    # =========================================================================

    # Add code column for storing full Python source
    op.add_column(
        "workflows",
        sa.Column("code", sa.Text(), nullable=True),
    )

    # Add code_hash column for SHA256 hash of code
    op.add_column(
        "workflows",
        sa.Column("code_hash", sa.String(64), nullable=True),
    )

    # Rename file_path to path
    op.alter_column("workflows", "file_path", new_column_name="path")

    # Drop old unique constraint and create new one with renamed column
    op.drop_constraint("workflows_file_function_key", "workflows", type_="unique")
    op.create_unique_constraint(
        "workflows_path_function_key",
        "workflows",
        ["path", "function_name"],
    )

    # =========================================================================
    # 2. workspace_files table changes
    # =========================================================================

    # Add entity_type for polymorphic reference
    # Values: 'workflow', 'form', 'app', 'agent', or NULL
    op.add_column(
        "workspace_files",
        sa.Column("entity_type", sa.String(20), nullable=True),
    )

    # Add entity_id for entity UUID reference
    # No FK constraint since it's polymorphic (points to different tables)
    op.add_column(
        "workspace_files",
        sa.Column("entity_id", sa.UUID(), nullable=True),
    )

    # =========================================================================
    # 3. Forms table changes
    # =========================================================================

    # Add workflow_path for redundant workflow reference (portability)
    op.add_column(
        "forms",
        sa.Column("workflow_path", sa.String(1000), nullable=True),
    )

    # Add workflow_function_name for redundant workflow reference (portability)
    op.add_column(
        "forms",
        sa.Column("workflow_function_name", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    # =========================================================================
    # 3. Forms table - remove columns
    # =========================================================================
    op.drop_column("forms", "workflow_function_name")
    op.drop_column("forms", "workflow_path")

    # =========================================================================
    # 2. workspace_files table - remove columns
    # =========================================================================
    op.drop_column("workspace_files", "entity_id")
    op.drop_column("workspace_files", "entity_type")

    # =========================================================================
    # 1. Workflows table - revert changes
    # =========================================================================

    # Rename path back to file_path first
    op.alter_column("workflows", "path", new_column_name="file_path")

    # Restore original unique constraint with file_path column name
    op.drop_constraint("workflows_path_function_key", "workflows", type_="unique")
    op.create_unique_constraint(
        "workflows_file_function_key",
        "workflows",
        ["file_path", "function_name"],
    )

    # Drop code columns
    op.drop_column("workflows", "code_hash")
    op.drop_column("workflows", "code")
