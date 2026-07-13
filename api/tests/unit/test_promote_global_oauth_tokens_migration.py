"""Regression tests for the global-OAuth-token migration safety boundary.

The migration must not infer that an org-level token is mis-stamped solely
because its organization is marked ``is_provider`` and its provider row is global.
Provider organizations are real tenants, so those rows can be legitimate private
org-scoped credentials. Promoting them to ``organization_id = NULL`` would make
them the global fallback for other tenants, and deleting them would be data loss.
"""

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260601_promote_mis_stamped_global_oauth_tokens.py"
)


def _migration_sql() -> list[str]:
    """Capture SQL strings the migration's upgrade() passes to op.execute."""
    spec = importlib.util.spec_from_file_location("_heal_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)

    captured: list[str] = []

    class _FakeOp:
        @staticmethod
        def execute(sql):
            captured.append(str(sql))

    import sys
    import types

    fake_alembic = types.ModuleType("alembic")
    fake_alembic.op = _FakeOp()  # type: ignore[attr-defined]
    original_alembic = sys.modules.get("alembic")
    sys.modules["alembic"] = fake_alembic
    try:
        spec.loader.exec_module(module)
        module.upgrade()
    finally:
        if original_alembic is None:
            sys.modules.pop("alembic", None)
        else:
            sys.modules["alembic"] = original_alembic
    return captured


def test_migration_does_not_emit_automatic_token_repair_sql():
    assert _migration_sql() == []


def test_migration_source_does_not_mutate_oauth_tokens():
    source = MIGRATION_PATH.read_text()

    assert "DELETE FROM oauth_tokens" not in source
    assert "UPDATE oauth_tokens" not in source
    assert "SET organization_id = NULL" not in source


def test_migration_documents_why_provider_org_tokens_are_preserved():
    source = MIGRATION_PATH.read_text()

    assert "provider organizations are also real tenants" in source
    assert "private provider-org credentials" in source
    assert "operator-reviewed SQL" in source
