"""Regression coverage for the forward execution-operations schema repair."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260827_ops_schema_reconcile.py"
)


def _load_migration():
    """Load the migration without importing it as an application module."""
    spec = importlib.util.spec_from_file_location("_ops_schema_reconcile", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_missing_partial_unique_index_is_reconciled_on_existing_table(
    monkeypatch,
) -> None:
    """An existing table must not cause reconciliation to skip its indexes."""
    migration = _load_migration()
    inspector = SimpleNamespace(get_indexes=lambda _table: [])
    create_index = MagicMock()
    monkeypatch.setattr(migration.sa, "inspect", MagicMock(return_value=inspector))
    monkeypatch.setattr(migration.op, "get_bind", MagicMock(return_value=object()))
    monkeypatch.setattr(migration.op, "create_index", create_index)

    where = migration.sa.text(
        "lease_token IS NOT NULL AND completed_at IS NULL"
    )
    migration._ensure_index(
        "execution_attempts",
        "uq_execution_attempt_active_lease",
        ["lease_token"],
        unique=True,
        postgresql_where=where,
    )

    create_index.assert_called_once_with(
        "uq_execution_attempt_active_lease",
        "execution_attempts",
        ["lease_token"],
        unique=True,
        postgresql_where=where,
    )
