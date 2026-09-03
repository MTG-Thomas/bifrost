"""Guard the PostgreSQL Alembic version column contract."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_all_alembic_revision_ids_fit_version_column() -> None:
    api_root = Path(__file__).resolve().parents[2]
    config = Config(api_root / "alembic.ini")
    config.set_main_option("script_location", str(api_root / "alembic"))
    script = ScriptDirectory.from_config(config)
    oversized = sorted(
        revision.revision
        for revision in script.walk_revisions()
        if len(revision.revision) > 32
    )

    assert oversized == []


def test_alembic_migrations_have_one_head() -> None:
    """Every deploy must have one unambiguous upgrade target."""
    api_root = Path(__file__).resolve().parents[2]
    config = Config(api_root / "alembic.ini")
    config.set_main_option("script_location", str(api_root / "alembic"))
    script = ScriptDirectory.from_config(config)

    assert len(script.get_heads()) == 1


def test_deployed_event_criteria_parent_is_immutable() -> None:
    """Never insert migrations behind the revision already stamped in production."""
    api_root = Path(__file__).resolve().parents[2]
    config = Config(api_root / "alembic.ini")
    config.set_main_option("script_location", str(api_root / "alembic"))
    script = ScriptDirectory.from_config(config)

    deployed = script.get_revision("20260827_event_criteria")
    assert deployed is not None
    assert deployed.down_revision == "20260826_poison_dispositions"
    assert script.get_revision("20260827_ops_reconcile").down_revision == deployed.revision
