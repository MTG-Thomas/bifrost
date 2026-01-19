"""Add app_file_dependencies table

Revision ID: 5a8fc29d3c57
Revises: 4c7fc18c2b46
Create Date: 2026-01-19

Tracks dependencies from app files to other entities (workflows, forms, data providers).
Dependencies are extracted by parsing source code for patterns like useWorkflow('uuid').

Note: dependency_id is NOT a foreign key - it's an index of what entities are
referenced in code. Referenced entities may or may not exist.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "5a8fc29d3c57"
down_revision = "4c7fc18c2b46"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_file_dependencies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("app_file_id", sa.UUID(), nullable=False),
        sa.Column("dependency_type", sa.String(50), nullable=False),
        sa.Column("dependency_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["app_file_id"],
            ["app_files.id"],
            ondelete="CASCADE",
        ),
    )

    # Fast lookup: "what dependencies does this file have?"
    op.create_index(
        "ix_app_file_dep_file_id",
        "app_file_dependencies",
        ["app_file_id"],
    )

    # Fast lookup: "what files reference this entity?"
    op.create_index(
        "ix_app_file_dep_target",
        "app_file_dependencies",
        ["dependency_type", "dependency_id"],
    )

    # Prevent duplicate entries for same file + type + id
    op.create_index(
        "ix_app_file_dep_unique",
        "app_file_dependencies",
        ["app_file_id", "dependency_type", "dependency_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_app_file_dep_unique", table_name="app_file_dependencies")
    op.drop_index("ix_app_file_dep_target", table_name="app_file_dependencies")
    op.drop_index("ix_app_file_dep_file_id", table_name="app_file_dependencies")
    op.drop_table("app_file_dependencies")
