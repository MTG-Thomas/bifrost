"""Remove legacy versioning fields from App Builder.

Revision ID: bb2e39289168
Revises: aa1d28178057
Create Date: 2026-01-06 20:00:00.000000+00:00

Removes legacy versioning fields now that all code uses version_id:
- applications: live_version, draft_version (kept published_at for history)
- app_pages: is_draft, version + related indexes
- app_components: is_draft + related indexes
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "bb2e39289168"
down_revision = "49ae9dd4e390"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # 1. Remove legacy columns from applications
    # =========================================================================
    op.drop_column("applications", "live_version")
    op.drop_column("applications", "draft_version")

    # =========================================================================
    # 2. Remove legacy columns and indexes from app_pages
    # =========================================================================
    # Drop legacy indexes first
    op.drop_index("ix_app_pages_unique", table_name="app_pages")
    op.drop_index("ix_app_pages_application_draft", table_name="app_pages")

    # Drop legacy columns
    op.drop_column("app_pages", "is_draft")
    op.drop_column("app_pages", "version")

    # Make version_id NOT NULL now that migration is complete
    op.alter_column(
        "app_pages",
        "version_id",
        existing_type=sa.UUID(),
        nullable=False,
    )

    # =========================================================================
    # 3. Remove legacy columns and indexes from app_components
    # =========================================================================
    # Drop legacy indexes first
    op.drop_index("ix_app_components_unique", table_name="app_components")
    op.drop_index("ix_app_components_page_draft", table_name="app_components")

    # Drop legacy column
    op.drop_column("app_components", "is_draft")

    # Create new unique index without is_draft
    op.create_index(
        "ix_app_components_page_component_unique",
        "app_components",
        ["page_id", "component_id"],
        unique=True,
    )


def downgrade() -> None:
    # =========================================================================
    # Restore app_components
    # =========================================================================
    op.drop_index("ix_app_components_page_component_unique", table_name="app_components")

    op.add_column(
        "app_components",
        sa.Column("is_draft", sa.Boolean(), server_default="true", nullable=False),
    )
    op.create_index(
        "ix_app_components_page_draft",
        "app_components",
        ["page_id", "is_draft"],
    )
    op.create_index(
        "ix_app_components_unique",
        "app_components",
        ["page_id", "component_id", "is_draft"],
        unique=True,
    )

    # =========================================================================
    # Restore app_pages
    # =========================================================================
    op.alter_column(
        "app_pages",
        "version_id",
        existing_type=sa.UUID(),
        nullable=True,
    )

    op.add_column(
        "app_pages",
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "app_pages",
        sa.Column("is_draft", sa.Boolean(), server_default="true", nullable=False),
    )
    op.create_index(
        "ix_app_pages_application_draft",
        "app_pages",
        ["application_id", "is_draft"],
    )
    op.create_index(
        "ix_app_pages_unique",
        "app_pages",
        ["application_id", "page_id", "is_draft"],
        unique=True,
    )

    # =========================================================================
    # Restore applications
    # =========================================================================
    op.add_column(
        "applications",
        sa.Column("draft_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "applications",
        sa.Column("live_version", sa.Integer(), server_default="0", nullable=False),
    )
