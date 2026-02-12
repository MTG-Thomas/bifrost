"""Drop app_files, app_versions, app_file_dependencies tables.

These tables are replaced by S3 storage via file_index + FileStorageService.
App files now live at _repo/apps/{slug}/ paths.
Published state is tracked via applications.published_snapshot JSONB column.

Revision ID: 20260212_drop_old_app_tables
Revises: 20260212_pub_snap
Create Date: 2026-02-12
"""

import sqlalchemy as sa
from alembic import op

revision = "20260212_drop_old_app_tables"
down_revision = "20260212_pub_snap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop FK constraints from applications pointing to app_versions
    op.drop_constraint("fk_applications_active_version_id", "applications", type_="foreignkey")
    op.drop_constraint("fk_applications_draft_version_id", "applications", type_="foreignkey")

    # 2. Drop version pointer columns from applications
    op.drop_column("applications", "active_version_id")
    op.drop_column("applications", "draft_version_id")

    # 3. Drop tables in dependency order (children first)
    op.drop_table("app_file_dependencies")
    op.drop_table("app_files")
    op.drop_table("app_versions")


def downgrade() -> None:
    # Recreate app_versions table
    op.create_table(
        "app_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_app_versions_application_id", "app_versions", ["application_id"])

    # Recreate app_files table
    op.create_table(
        "app_files",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("app_version_id", sa.UUID(), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("compiled", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["app_version_id"],
            ["app_versions.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_app_files_version", "app_files", ["app_version_id"])
    op.create_index(
        "ix_app_files_path", "app_files", ["app_version_id", "path"], unique=True
    )

    # Recreate app_file_dependencies table
    op.create_table(
        "app_file_dependencies",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("app_file_id", sa.UUID(), nullable=False),
        sa.Column("dependency_type", sa.String(50), nullable=False),
        sa.Column("dependency_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["app_file_id"],
            ["app_files.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_app_file_dep_file_id", "app_file_dependencies", ["app_file_id"])
    op.create_index(
        "ix_app_file_dep_target",
        "app_file_dependencies",
        ["dependency_type", "dependency_id"],
    )
    op.create_index(
        "ix_app_file_dep_unique",
        "app_file_dependencies",
        ["app_file_id", "dependency_type", "dependency_id"],
        unique=True,
    )

    # Re-add version pointer columns and FKs to applications
    op.add_column(
        "applications",
        sa.Column("active_version_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("draft_version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_applications_active_version_id",
        "applications",
        "app_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_applications_draft_version_id",
        "applications",
        "app_versions",
        ["draft_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
