"""pin workflow executions to immutable Solution deployments

Revision ID: 20260716_exec_deploy_pin
Revises: 20260716_solution_deployments
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260716_exec_deploy_pin"
down_revision: str = "20260716_solution_deployments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column(
            "solution_deployment_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column(
        "executions",
        sa.Column("runtime_mode", sa.String(length=32), nullable=False, server_default="legacy"),
    )
    op.add_column("executions", sa.Column("runtime_evidence", postgresql.JSONB(), nullable=True))
    op.add_column("executions", sa.Column("runtime_evidence_hash", sa.String(length=71), nullable=True))
    op.create_foreign_key(
        "fk_executions_solution_deployment",
        "executions",
        "solution_deployments",
        ["solution_deployment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_executions_solution_deployment_id",
        "executions",
        ["solution_deployment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_executions_solution_deployment_id", table_name="executions")
    op.drop_constraint(
        "fk_executions_solution_deployment", "executions", type_="foreignkey"
    )
    op.drop_column("executions", "solution_deployment_id")
    op.drop_column("executions", "runtime_evidence_hash")
    op.drop_column("executions", "runtime_evidence")
    op.drop_column("executions", "runtime_mode")
