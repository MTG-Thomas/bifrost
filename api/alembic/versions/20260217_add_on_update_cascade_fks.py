"""add ON UPDATE CASCADE to workflow, application, and integration FKs

Revision ID: 20260217_update_cascade
Revises: 20260216_embed_secrets
Create Date: 2026-02-17

When git pull changes a workflow or application UUID primary key to match the
manifest, PostgreSQL must propagate the change to all child rows.  Adding
ON UPDATE CASCADE lets this happen automatically.
"""

from alembic import op
import sqlalchemy as sa

revision = "20260217_update_cascade"
down_revision = "20260216_embed_secrets"
branch_labels = None
depends_on = None

# (table, column, ref_table, ref_column, ondelete, onupdate)
_FK_DEFS = [
    # ── workflows.id ──
    ("workflow_roi_daily", "workflow_id", "workflows", "id", "CASCADE", "CASCADE"),
    ("executions", "workflow_id", "workflows", "id", "SET NULL", "CASCADE"),
    ("executions", "api_key_id", "workflows", "id", None, "CASCADE"),
    ("event_subscriptions", "workflow_id", "workflows", "id", "CASCADE", "CASCADE"),
    ("event_deliveries", "workflow_id", "workflows", "id", "CASCADE", "CASCADE"),
    ("form_fields", "data_provider_id", "workflows", "id", "SET NULL", "CASCADE"),
    ("agent_tools", "workflow_id", "workflows", "id", "CASCADE", "CASCADE"),
    ("workflow_roles", "workflow_id", "workflows", "id", "CASCADE", "CASCADE"),
    # ── applications.id ──
    ("app_roles", "app_id", "applications", "id", "CASCADE", "CASCADE"),
    ("tables", "application_id", "applications", "id", "SET NULL", "CASCADE"),
    ("app_embed_secrets", "application_id", "applications", "id", "CASCADE", "CASCADE"),
    # ── integrations.id ──
    ("integration_config_schema", "integration_id", "integrations", "id", "CASCADE", "CASCADE"),
    ("integration_mappings", "integration_id", "integrations", "id", None, "CASCADE"),
    ("oauth_providers", "integration_id", "integrations", "id", None, "CASCADE"),
    ("configs", "integration_id", "integrations", "id", None, "CASCADE"),
    ("webhook_sources", "integration_id", "integrations", "id", "SET NULL", "CASCADE"),
    # ── agents.id ──
    ("agent_tools", "agent_id", "agents", "id", "CASCADE", "CASCADE"),
    ("agent_delegations", "parent_agent_id", "agents", "id", "CASCADE", "CASCADE"),
    ("agent_delegations", "child_agent_id", "agents", "id", "CASCADE", "CASCADE"),
    ("agent_roles", "agent_id", "agents", "id", "CASCADE", "CASCADE"),
    ("conversations", "agent_id", "agents", "id", None, "CASCADE"),
    # ── event_sources.id ──
    ("schedule_sources", "event_source_id", "event_sources", "id", "CASCADE", "CASCADE"),
    ("webhook_sources", "event_source_id", "event_sources", "id", "CASCADE", "CASCADE"),
    ("event_subscriptions", "event_source_id", "event_sources", "id", "CASCADE", "CASCADE"),
    ("events", "event_source_id", "event_sources", "id", "CASCADE", "CASCADE"),
    # ── forms.id ──
    ("form_fields", "form_id", "forms", "id", "CASCADE", "CASCADE"),
    ("form_roles", "form_id", "forms", "id", None, "CASCADE"),
    ("executions", "form_id", "forms", "id", None, "CASCADE"),
    # ── tables.id ──
    ("documents", "table_id", "tables", "id", "CASCADE", "CASCADE"),
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


def _recreate_fks(conn, with_onupdate):
    for table, column, ref_table, ref_column, ondelete, onupdate in _FK_DEFS:
        fk_name = _get_fk_name(conn, table, column, ref_table, ref_column)
        op.drop_constraint(fk_name, table, type_="foreignkey")
        kwargs = {}
        if ondelete:
            kwargs["ondelete"] = ondelete
        if with_onupdate and onupdate:
            kwargs["onupdate"] = onupdate
        op.create_foreign_key(
            fk_name, table, ref_table,
            [column], [ref_column],
            **kwargs,
        )


def upgrade() -> None:
    conn = op.get_bind()
    _recreate_fks(conn, with_onupdate=True)


def downgrade() -> None:
    conn = op.get_bind()
    _recreate_fks(conn, with_onupdate=False)
