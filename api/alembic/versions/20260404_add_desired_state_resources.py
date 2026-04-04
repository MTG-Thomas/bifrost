"""add desired state resources

Revision ID: 20260404_desired_state
Revises: 20260316_file_index_author
Create Date: 2026-04-04
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260404_desired_state"
down_revision: Union[str, None] = "20260316_file_index_author"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE dsr_engine AS ENUM ('tofu', 'terraform', 'python')")
    op.execute("CREATE TYPE dsr_resource_status AS ENUM ('pending', 'planned', 'applied', 'failed')")
    op.execute("CREATE TYPE dsr_plan_status AS ENUM ('pending', 'approved', 'applied', 'failed')")
    op.execute("CREATE TYPE dsr_risk_level AS ENUM ('low', 'medium', 'high')")
    op.execute("CREATE TYPE dsr_run_status AS ENUM ('pending', 'running', 'completed', 'failed')")

    op.create_table(
        "desired_state_resources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=255), nullable=False),
        sa.Column("engine", postgresql.ENUM(name="dsr_engine", create_type=False), nullable=False, server_default="tofu"),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", postgresql.ENUM(name="dsr_resource_status", create_type=False), nullable=False, server_default="pending"),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dsr_organization_id", "desired_state_resources", ["organization_id"])
    op.create_index("ix_dsr_status", "desired_state_resources", ["status"])
    op.create_index("ix_dsr_engine", "desired_state_resources", ["engine"])

    op.create_table(
        "desired_state_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("engine", postgresql.ENUM(name="dsr_engine", create_type=False), nullable=False),
        sa.Column("status", postgresql.ENUM(name="dsr_plan_status", create_type=False), nullable=False, server_default="pending"),
        sa.Column("plan_path", sa.Text(), nullable=False),
        sa.Column("plan_json_path", sa.Text(), nullable=False),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summary_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("risk_level", postgresql.ENUM(name="dsr_risk_level", create_type=False), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["resource_id"], ["desired_state_resources.id"], ondelete="CASCADE", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dsr_plan_resource_id", "desired_state_plans", ["resource_id"])
    op.create_index("ix_dsr_plan_status", "desired_state_plans", ["status"])
    op.create_index("ix_dsr_plan_requires_approval", "desired_state_plans", ["requires_approval"])

    op.create_table(
        "desired_state_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", postgresql.ENUM(name="dsr_run_status", create_type=False), nullable=False, server_default="pending"),
        sa.Column("logs_path", sa.Text(), nullable=False),
        sa.Column("outputs_path", sa.Text(), nullable=False),
        sa.Column("result_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["desired_state_plans.id"], ondelete="CASCADE", onupdate="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_dsr_run_plan_id", "desired_state_runs", ["plan_id"])
    op.create_index("ix_dsr_run_status", "desired_state_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_dsr_run_status", table_name="desired_state_runs")
    op.drop_index("ix_dsr_run_plan_id", table_name="desired_state_runs")
    op.drop_table("desired_state_runs")

    op.drop_index("ix_dsr_plan_requires_approval", table_name="desired_state_plans")
    op.drop_index("ix_dsr_plan_status", table_name="desired_state_plans")
    op.drop_index("ix_dsr_plan_resource_id", table_name="desired_state_plans")
    op.drop_table("desired_state_plans")

    op.drop_index("ix_dsr_engine", table_name="desired_state_resources")
    op.drop_index("ix_dsr_status", table_name="desired_state_resources")
    op.drop_index("ix_dsr_organization_id", table_name="desired_state_resources")
    op.drop_table("desired_state_resources")

    op.execute("DROP TYPE dsr_run_status")
    op.execute("DROP TYPE dsr_risk_level")
    op.execute("DROP TYPE dsr_plan_status")
    op.execute("DROP TYPE dsr_resource_status")
    op.execute("DROP TYPE dsr_engine")
