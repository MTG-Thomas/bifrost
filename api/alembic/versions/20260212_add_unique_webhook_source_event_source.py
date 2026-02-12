"""Add unique constraint on webhook_sources.event_source_id.

This is a 1:1 relationship (one webhook source per event source).
The unique constraint was missing, causing ON CONFLICT upserts in
git sync to fail with 'no unique or exclusion constraint matching
the ON CONFLICT specification'.

Revision ID: 20260212_uq_webhook_es
Revises: 20260211_drop_nav
Create Date: 2026-02-12
"""

from alembic import op

revision = "20260212_uq_webhook_es"
down_revision = "20260211_drop_nav"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the non-unique index first, then create a unique constraint
    op.drop_index("ix_webhook_sources_event_source_id", table_name="webhook_sources")
    op.create_unique_constraint(
        "uq_webhook_sources_event_source_id",
        "webhook_sources",
        ["event_source_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_webhook_sources_event_source_id",
        "webhook_sources",
        type_="unique",
    )
    op.create_index(
        "ix_webhook_sources_event_source_id",
        "webhook_sources",
        ["event_source_id"],
    )
