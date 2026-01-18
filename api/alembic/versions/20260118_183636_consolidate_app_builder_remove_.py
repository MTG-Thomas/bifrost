"""consolidate_app_builder_remove_components

Drop component engine tables and rename app_code_files to app_files.

This migration:
- Drops app_components table (component engine only)
- Drops app_pages table (component engine only)
- Drops applications.engine column (no longer needed with single engine)
- Renames app_code_files to app_files

Revision ID: 4c7fc18c2b46
Revises: 4b9d382g1173
Create Date: 2026-01-18 18:36:36.894753+00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "4c7fc18c2b46"
down_revision: Union[str, None] = "4b9d382g1173"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # =========================================================================
    # 1. Drop app_components table (depends on app_pages)
    # =========================================================================
    op.drop_index("ix_app_components_page_component_unique", table_name="app_components")
    op.drop_index("ix_app_components_parent_order", table_name="app_components")
    op.drop_index("ix_app_components_page_id", table_name="app_components")
    op.drop_table("app_components")

    # =========================================================================
    # 2. Drop app_pages table
    # =========================================================================
    op.execute("DROP INDEX IF EXISTS ix_app_pages_app_page_version")  # Partial unique index
    op.drop_constraint("fk_app_pages_version_id", "app_pages", type_="foreignkey")
    op.drop_index("ix_app_pages_version_id", table_name="app_pages")
    op.drop_index("ix_app_pages_application_id", table_name="app_pages")
    op.drop_table("app_pages")

    # =========================================================================
    # 3. Drop engine column from applications
    # =========================================================================
    op.drop_column("applications", "engine")

    # =========================================================================
    # 4. Rename app_code_files to app_files
    # =========================================================================
    # Drop old indexes first
    op.drop_index("ix_code_files_path", table_name="app_code_files")
    op.drop_index("ix_code_files_version", table_name="app_code_files")

    # Rename table
    op.rename_table("app_code_files", "app_files")

    # Recreate indexes with new names
    op.create_index("ix_app_files_version", "app_files", ["app_version_id"])
    op.create_index(
        "ix_app_files_path",
        "app_files",
        ["app_version_id", "path"],
        unique=True,
    )


def downgrade() -> None:
    # =========================================================================
    # 1. Rename app_files back to app_code_files
    # =========================================================================
    op.drop_index("ix_app_files_path", table_name="app_files")
    op.drop_index("ix_app_files_version", table_name="app_files")

    op.rename_table("app_files", "app_code_files")

    op.create_index("ix_code_files_version", "app_code_files", ["app_version_id"])
    op.create_index(
        "ix_code_files_path",
        "app_code_files",
        ["app_version_id", "path"],
        unique=True,
    )

    # =========================================================================
    # 2. Restore engine column on applications
    # =========================================================================
    op.add_column(
        "applications",
        sa.Column("engine", sa.String(20), server_default="code", nullable=False),
    )

    # =========================================================================
    # 3. Recreate app_pages table
    # =========================================================================
    op.create_table(
        "app_pages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("page_id", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("path", sa.String(255), nullable=False),
        sa.Column("version_id", sa.UUID(), nullable=False),
        sa.Column("data_sources", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("variables", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("launch_workflow_id", sa.UUID(), nullable=True),
        sa.Column("launch_workflow_params", postgresql.JSONB(), server_default="{}", nullable=True),
        sa.Column("launch_workflow_data_source_id", sa.String(255), nullable=True),
        sa.Column("permission", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("page_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fill_height", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["launch_workflow_id"], ["workflows.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["app_versions.id"],
            name="fk_app_pages_version_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_pages_application_id", "app_pages", ["application_id"])
    op.create_index("ix_app_pages_version_id", "app_pages", ["version_id"])
    op.execute("""
        CREATE UNIQUE INDEX ix_app_pages_app_page_version
        ON app_pages (application_id, page_id, version_id)
        WHERE version_id IS NOT NULL
    """)

    # =========================================================================
    # 4. Recreate app_components table
    # =========================================================================
    op.create_table(
        "app_components",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("page_id", sa.UUID(), nullable=False),
        sa.Column("component_id", sa.String(255), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("props", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("component_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visible", sa.Text(), nullable=True),
        sa.Column("width", sa.String(20), nullable=True),
        sa.Column("loading_workflows", postgresql.JSONB(), server_default="[]", nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["app_pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["app_components.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_components_page_id", "app_components", ["page_id"])
    op.create_index("ix_app_components_parent_order", "app_components", ["parent_id", "component_order"])
    op.create_index(
        "ix_app_components_page_component_unique",
        "app_components",
        ["page_id", "component_id"],
        unique=True,
    )
