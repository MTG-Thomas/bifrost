"""Regression coverage for the withdrawn unfinished Builder migrations."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_fresh_database_does_not_install_withdrawn_builder_schema(
    db_session: AsyncSession,
) -> None:
    api_root = Path(__file__).resolve().parents[3]
    config = Config(api_root / "alembic.ini")
    config.set_main_option("script_location", str(api_root / "alembic"))
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    revision = (
        await db_session.execute(text("SELECT version_num FROM alembic_version"))
    ).scalar_one()
    assert revision == expected_head

    catalog_revision = (
        await db_session.execute(
            text(
                "SELECT revision FROM mcp_catalog_revisions "
                "WHERE catalog = 'workflow_tools'"
            )
        )
    ).scalar_one()
    assert isinstance(catalog_revision, int)
    assert catalog_revision >= 0

    builder_tables = (
        (
            await db_session.execute(
                text(
                    """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name IN (
                      'solution_build_jobs',
                      'solution_builder_projects',
                      'solution_builder_sessions',
                      'solution_builder_turns',
                      'solution_source_revisions'
                  )
                ORDER BY table_name
                """
                )
            )
        )
        .scalars()
        .all()
    )
    assert builder_tables == []

    builder_roles = (
        (
            await db_session.execute(
                text(
                    """
                SELECT id
                FROM roles
                WHERE id IN (
                    '00000000-0000-0000-0000-000000000003',
                    '00000000-0000-0000-0000-000000000004'
                )
                """
                )
            )
        )
        .scalars()
        .all()
    )
    assert builder_roles == []
