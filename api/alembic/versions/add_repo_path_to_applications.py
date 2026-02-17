"""Add repo_path column to applications table.

Revision ID: 20260216_app_repo_path
Revises: 20260212_drop_old_app_tables
Create Date: 2026-02-16

Canonical app source root (e.g. "apps/tickbox-grc").
Backfilled from slug for existing rows.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260216_app_repo_path"
down_revision = "20260212_drop_old_app_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("applications", sa.Column("repo_path", sa.String(500), nullable=True))
    # Backfill existing rows: repo_path = 'apps/' || slug
    op.execute("UPDATE applications SET repo_path = 'apps/' || slug WHERE repo_path IS NULL")


def downgrade() -> None:
    op.drop_column("applications", "repo_path")
