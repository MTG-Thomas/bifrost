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
