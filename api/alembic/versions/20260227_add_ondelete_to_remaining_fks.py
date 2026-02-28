"""add ondelete to remaining FKs that block manifest deletion

Revision ID: 20260227_ondelete_fks
Revises: 20260217_auto_fill
Create Date: 2026-02-27

Several FKs lack ondelete behaviour, causing RESTRICT violations when
git-sync deletes agents, forms, or roles via raw SQL DELETE.

conversations.agent_id  -> SET NULL  (preserve history, unlink agent)
executions.form_id      -> SET NULL  (preserve history, unlink form)
form_roles.form_id      -> CASCADE   (junction table, remove assignments)
form_roles.role_id      -> CASCADE   (junction table, remove assignments)
user_roles.role_id      -> CASCADE   (junction table, remove assignments)
"""

from alembic import op
import sqlalchemy as sa

revision = "20260227_ondelete_fks"
down_revision = "20260217_auto_fill"
branch_labels = None
depends_on = None

# (table, column, ref_table, ref_column, new_ondelete, existing_onupdate)
_FK_DEFS = [
    ("conversations", "agent_id", "agents", "id", "SET NULL", "CASCADE"),
    ("executions", "form_id", "forms", "id", "SET NULL", "CASCADE"),
    ("form_roles", "form_id", "forms", "id", "CASCADE", "CASCADE"),
    ("form_roles", "role_id", "roles", "id", "CASCADE", None),
    ("user_roles", "role_id", "roles", "id", "CASCADE", None),
]

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


def _get_fk_name(conn, table, column, ref_table, ref_column):
    row = conn.execute(
        _FIND_FK_SQL,
        {"table": table, "column": column, "ref_table": ref_table, "ref_column": ref_column},
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"FK not found: {table}.{column} -> {ref_table}.{ref_column}"
        )
    return row[0]


def upgrade() -> None:
    conn = op.get_bind()
    for table, column, ref_table, ref_column, ondelete, onupdate in _FK_DEFS:
        fk_name = _get_fk_name(conn, table, column, ref_table, ref_column)
        op.drop_constraint(fk_name, table, type_="foreignkey")
        kwargs = {"ondelete": ondelete}
        if onupdate:
            kwargs["onupdate"] = onupdate
        op.create_foreign_key(
            fk_name, table, ref_table,
            [column], [ref_column],
            **kwargs,
        )


def downgrade() -> None:
    conn = op.get_bind()
    for table, column, ref_table, ref_column, _ondelete, onupdate in _FK_DEFS:
        fk_name = _get_fk_name(conn, table, column, ref_table, ref_column)
        op.drop_constraint(fk_name, table, type_="foreignkey")
        kwargs = {}
        if onupdate:
            kwargs["onupdate"] = onupdate
        op.create_foreign_key(
            fk_name, table, ref_table,
            [column], [ref_column],
            **kwargs,
        )
