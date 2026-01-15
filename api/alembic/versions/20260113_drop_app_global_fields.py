"""Drop global_data_sources and global_variables from applications.

Revision ID: 20260113_drop_app_global_fields
Revises: 20260113_drop_workflow_access
Create Date: 2026-01-13

The global_data_sources and global_variables fields were unused application-level
fields. Variables come from workflow execution results, not pre-defined globals.

This migration removes these unused columns from the applications table.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = "20260113_drop_app_global_fields"
down_revision = "20260113_drop_workflow_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop global_data_sources and global_variables columns
    op.drop_column("applications", "global_data_sources")
    op.drop_column("applications", "global_variables")


def downgrade() -> None:
    # Add back global_data_sources and global_variables columns
    op.add_column(
        "applications",
        sa.Column(
            "global_data_sources",
            JSONB,
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "applications",
        sa.Column(
            "global_variables",
            JSONB,
            nullable=False,
            server_default="{}",
        ),
    )
