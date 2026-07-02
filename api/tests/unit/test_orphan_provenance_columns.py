"""Legacy orphan provenance columns stay retired after solution status migration."""

from src.models.orm.config import Config
from src.models.orm.tables import Table


def test_table_has_no_orphan_provenance_columns() -> None:
    cols = set(Table.__table__.columns.keys())

    assert "origin_solution_slug" not in cols
    assert "origin_solution_id" not in cols
    assert "orphaned_at" not in cols


def test_config_has_no_orphan_provenance_columns() -> None:
    cols = set(Config.__table__.columns.keys())

    assert "origin_solution_slug" not in cols
    assert "origin_solution_id" not in cols
    assert "orphaned_at" not in cols
