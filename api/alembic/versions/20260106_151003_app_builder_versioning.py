"""Add App Builder versioning with app_versions table.

Revision ID: aa1d28178057
Revises: 20260107_000000
Create Date: 2026-01-06 15:10:03.105892+00:00

Replaces is_draft boolean on app_pages with a proper versioning system:
- app_versions table: version snapshots linked to applications
- applications: active_version_id (live) and draft_version_id (current draft)
- app_pages: version_id linking to app_versions

This enables:
- Version history tracking
- Rollback to any previous version
- Cleaner publish/draft semantics
"""

from uuid import uuid4

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "aa1d28178057"
down_revision = "20260107_000000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # 1. Create app_versions table
    # =========================================================================
    op.create_table(
        "app_versions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("application_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_app_versions_application_id", "app_versions", ["application_id"])

    # =========================================================================
    # 2. Add version pointers to applications
    # =========================================================================
    op.add_column(
        "applications",
        sa.Column("active_version_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "applications",
        sa.Column("draft_version_id", sa.UUID(), nullable=True),
    )

    # Add foreign key constraints (use_alter for circular reference)
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

    # =========================================================================
    # 3. Add version_id to app_pages
    # =========================================================================
    op.add_column(
        "app_pages",
        sa.Column("version_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_app_pages_version_id",
        "app_pages",
        "app_versions",
        ["version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_app_pages_version_id", "app_pages", ["version_id"])

    # Create unique constraint for new versioning (nullable version_id is OK initially)
    # This is a partial index that only applies when version_id is not null
    op.execute("""
        CREATE UNIQUE INDEX ix_app_pages_app_page_version
        ON app_pages (application_id, page_id, version_id)
        WHERE version_id IS NOT NULL
    """)

    # =========================================================================
    # 4. Migrate existing data
    # =========================================================================
    # For each application:
    # - Create a version for is_draft=true pages, set as draft_version_id
    # - Create a version for is_draft=false pages, set as active_version_id
    # - Update pages to point to their respective versions

    conn = op.get_bind()

    # Get all applications
    apps = conn.execute(sa.text("SELECT id FROM applications")).fetchall()

    for (app_id,) in apps:
        # Check for draft pages
        draft_pages = conn.execute(
            sa.text("""
                SELECT id FROM app_pages
                WHERE application_id = :app_id AND is_draft = true
            """),
            {"app_id": app_id},
        ).fetchall()

        if draft_pages:
            # Create draft version
            draft_version_id = str(uuid4())
            conn.execute(
                sa.text("""
                    INSERT INTO app_versions (id, application_id, created_at)
                    VALUES (:id, :app_id, NOW())
                """),
                {"id": draft_version_id, "app_id": app_id},
            )

            # Update draft pages to point to this version
            conn.execute(
                sa.text("""
                    UPDATE app_pages
                    SET version_id = :version_id
                    WHERE application_id = :app_id AND is_draft = true
                """),
                {"version_id": draft_version_id, "app_id": app_id},
            )

            # Set as draft_version_id on application
            conn.execute(
                sa.text("""
                    UPDATE applications
                    SET draft_version_id = :version_id
                    WHERE id = :app_id
                """),
                {"version_id": draft_version_id, "app_id": app_id},
            )

        # Check for live pages
        live_pages = conn.execute(
            sa.text("""
                SELECT id FROM app_pages
                WHERE application_id = :app_id AND is_draft = false
            """),
            {"app_id": app_id},
        ).fetchall()

        if live_pages:
            # Create active version
            active_version_id = str(uuid4())
            conn.execute(
                sa.text("""
                    INSERT INTO app_versions (id, application_id, created_at)
                    VALUES (:id, :app_id, NOW())
                """),
                {"id": active_version_id, "app_id": app_id},
            )

            # Update live pages to point to this version
            conn.execute(
                sa.text("""
                    UPDATE app_pages
                    SET version_id = :version_id
                    WHERE application_id = :app_id AND is_draft = false
                """),
                {"version_id": active_version_id, "app_id": app_id},
            )

            # Set as active_version_id on application
            conn.execute(
                sa.text("""
                    UPDATE applications
                    SET active_version_id = :version_id
                    WHERE id = :app_id
                """),
                {"version_id": active_version_id, "app_id": app_id},
            )


def downgrade() -> None:
    # Drop the new unique constraint
    op.execute("DROP INDEX IF EXISTS ix_app_pages_app_page_version")

    # Drop version_id from app_pages
    op.drop_constraint("fk_app_pages_version_id", "app_pages", type_="foreignkey")
    op.drop_index("ix_app_pages_version_id", table_name="app_pages")
    op.drop_column("app_pages", "version_id")

    # Drop version pointers from applications
    op.drop_constraint("fk_applications_draft_version_id", "applications", type_="foreignkey")
    op.drop_constraint("fk_applications_active_version_id", "applications", type_="foreignkey")
    op.drop_column("applications", "draft_version_id")
    op.drop_column("applications", "active_version_id")

    # Drop app_versions table
    op.drop_index("ix_app_versions_application_id", table_name="app_versions")
    op.drop_table("app_versions")
