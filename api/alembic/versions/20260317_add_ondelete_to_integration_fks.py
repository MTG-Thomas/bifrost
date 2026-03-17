"""add ondelete to integration FK constraints

Revision ID: 20260317_integration_fk_ondelete
Revises: 20260316_file_index_author
Create Date: 2026-03-17

configs and integration_mappings get CASCADE (manifest-managed),
oauth_providers gets SET NULL (user-managed, preserve token link).
"""
from alembic import op
import sqlalchemy as sa

revision = "20260317_integration_fk_ondelete"
down_revision = "20260316_file_index_author"
branch_labels = None
depends_on = None

_FIND_FK_SQL = sa.text("""
    SELECT tc.constraint_name
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
        ON tc.constraint_name = kcu.constraint_name
        AND tc.table_schema = kcu.table_schema
    JOIN information_schema.constraint_column_usage ccu
        ON tc.constraint_name = ccu.constraint_name
        AND tc.table_schema = ccu.table_schema
    WHERE tc.constraint_type = 'FOREIGN KEY'
        AND tc.table_name = :table
        AND kcu.column_name = :column
        AND ccu.table_name = :ref_table
        AND ccu.column_name = :ref_column
    LIMIT 1
""")

# (table, column, ref_table, ref_column, ondelete)
_FKS = [
    ("configs", "integration_id", "integrations", "id", "CASCADE"),
    ("integration_mappings", "integration_id", "integrations", "id", "CASCADE"),
    ("oauth_providers", "integration_id", "integrations", "id", "SET NULL"),
]


def _find_fk(conn: sa.engine.Connection, table: str, column: str, ref_table: str, ref_column: str) -> str:
    row = conn.execute(
        _FIND_FK_SQL,
        {"table": table, "column": column, "ref_table": ref_table, "ref_column": ref_column},
    ).fetchone()
    if row is None:
        raise RuntimeError(f"FK not found: {table}.{column} -> {ref_table}.{ref_column}")
    return row[0]


def upgrade() -> None:
    conn = op.get_bind()
    for table, column, ref_table, ref_column, ondelete in _FKS:
        fk_name = _find_fk(conn, table, column, ref_table, ref_column)
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.create_foreign_key(
            fk_name, table, ref_table,
            [column], [ref_column],
            ondelete=ondelete, onupdate="CASCADE",
        )


def downgrade() -> None:
    conn = op.get_bind()
    for table, column, ref_table, ref_column, _ondelete in reversed(_FKS):
        fk_name = _find_fk(conn, table, column, ref_table, ref_column)
        op.drop_constraint(fk_name, table, type_="foreignkey")
        op.create_foreign_key(
            fk_name, table, ref_table,
            [column], [ref_column],
            onupdate="CASCADE",
        )
