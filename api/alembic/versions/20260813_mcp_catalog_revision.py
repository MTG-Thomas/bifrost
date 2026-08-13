"""Add the durable MCP workflow-catalog revision.

Revision ID: 20260813_mcp_catalog_rev
Revises: 20260812_mcp_oauth_binding
"""

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_mcp_catalog_rev"
down_revision: str = "20260812_mcp_oauth_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    table = op.create_table(
        "mcp_catalog_revisions",
        sa.Column("catalog", sa.String(length=64), nullable=False),
        sa.Column(
            "revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.PrimaryKeyConstraint("catalog"),
    )
    op.bulk_insert(table, [{"catalog": "workflow_tools", "revision": 0}])


def downgrade() -> None:
    op.drop_table("mcp_catalog_revisions")
