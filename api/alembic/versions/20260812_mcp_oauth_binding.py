"""Bind stored OAuth tokens to their MCP issuer and resource.

Revision ID: 20260812_mcp_oauth_binding
Revises: 20260807_withdraw_builder
"""

from alembic import op
import sqlalchemy as sa


revision: str = "20260812_mcp_oauth_binding"
down_revision: str = "20260807_withdraw_builder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "oauth_tokens",
        sa.Column("oauth_issuer", sa.String(length=2048), nullable=True),
    )
    op.add_column(
        "oauth_tokens",
        sa.Column("oauth_resource", sa.String(length=2048), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("oauth_tokens", "oauth_resource")
    op.drop_column("oauth_tokens", "oauth_issuer")
