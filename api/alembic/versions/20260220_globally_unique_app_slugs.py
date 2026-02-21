"""make app slugs globally unique

Revision ID: 20260220_global_app_slugs
Revises: 20260220_add_deps_col
Create Date: 2026-02-20

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260220_global_app_slugs"
down_revision = "20260220_add_deps_col"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Pre-check: ensure no duplicate slugs exist across orgs
    conn = op.get_bind()
    dupes = conn.execute(
        sa.text(
            "SELECT slug, COUNT(*) as cnt FROM applications "
            "GROUP BY slug HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if dupes:
        dupe_list = ", ".join(f"'{row[0]}' ({row[1]}x)" for row in dupes)
        raise RuntimeError(
            f"Cannot make slugs globally unique: duplicate slugs found: {dupe_list}. "
            "Resolve duplicates manually before running this migration."
        )

    # Drop partial unique indexes
    op.drop_index("ix_applications_org_slug_unique", table_name="applications")
    op.drop_index("ix_applications_global_slug_unique", table_name="applications")

    # Create global unique index on slug (no WHERE clause)
    op.create_index(
        "ix_applications_slug_unique",
        "applications",
        ["slug"],
        unique=True,
    )


def downgrade() -> None:
    # Drop global unique index
    op.drop_index("ix_applications_slug_unique", table_name="applications")

    # Restore partial unique indexes
    op.create_index(
        "ix_applications_org_slug_unique",
        "applications",
        ["organization_id", "slug"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_index(
        "ix_applications_global_slug_unique",
        "applications",
        ["slug"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )
