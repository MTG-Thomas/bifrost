"""Pin the platform-global singleton contract for Workspace Live."""

from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from src.models.orm.workspace_promotions import (
    WorkspacePromotionArtifact,
    WorkspacePromotionRelease,
)


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260819_workspace_release_active_pointer.py"
)
INITIAL_MIGRATION_PATH = MIGRATION_PATH.with_name(
    "20260813_workspace_promotion_artifacts.py"
)
ARTIFACT_MIGRATION_PATH = MIGRATION_PATH.with_name(
    "20260819_workspace_release_artifact_v1.py"
)
PREPARE_MIGRATION_PATH = MIGRATION_PATH.with_name(
    "20260819_workspace_release_prepare.py"
)
INDEX_NAME = "uq_workspace_promotion_release_live"


def test_runtime_model_live_uniqueness_is_platform_global() -> None:
    index = next(
        item
        for item in WorkspacePromotionRelease.__table__.indexes
        if item.name == INDEX_NAME
    )

    assert [column.name for column in index.columns] == ["activation_state"]
    assert str(index.dialect_options["postgresql"]["where"]) == (
        "activation_state = 'live'"
    )


def test_active_pointer_migration_does_not_scope_live_uniqueness_by_org() -> None:
    source = MIGRATION_PATH.read_text()
    live_index = source[source.index(f'"{INDEX_NAME}"') :]
    live_index = live_index[: live_index.index("\n    )") + 6]

    assert '["activation_state"]' in live_index
    assert "organization_id" not in live_index
    assert "activation_state = 'live'" in live_index


def _foreign_key(table, name: str) -> ForeignKeyConstraint:
    return next(
        constraint
        for constraint in table.foreign_key_constraints
        if constraint.name == name
    )


def test_release_artifact_and_lineage_foreign_keys_are_organization_bound() -> None:
    artifact_fk = _foreign_key(
        WorkspacePromotionRelease.__table__,
        "fk_workspace_promotion_release_artifact_org",
    )
    previous_fk = _foreign_key(
        WorkspacePromotionRelease.__table__,
        "fk_workspace_promotion_release_previous_org",
    )
    supersedes_fk = _foreign_key(
        WorkspacePromotionArtifact.__table__,
        "fk_workspace_promotion_artifact_supersedes",
    )

    assert artifact_fk.column_keys == ["organization_id", "artifact_id"]
    assert previous_fk.column_keys == ["organization_id", "previous_release_id"]
    assert supersedes_fk.column_keys == [
        "organization_id",
        "supersedes_artifact_id",
    ]
    for constraint in (artifact_fk, previous_fk, supersedes_fk):
        assert constraint.ondelete == "CASCADE"
        assert constraint.deferrable is True
        assert constraint.initially == "DEFERRED"


def test_one_release_owns_each_artifact_globally() -> None:
    index = next(
        item
        for item in WorkspacePromotionRelease.__table__.indexes
        if item.name == "uq_workspace_promotion_release_artifact"
    )

    assert index.unique is True
    assert [column.name for column in index.columns] == ["artifact_id"]


def test_live_release_requires_a_durable_projection_job() -> None:
    constraint = next(
        item
        for item in WorkspacePromotionRelease.__table__.constraints
        if isinstance(item, CheckConstraint)
        and item.name == "ck_workspace_promotion_release_live_has_lock_job"
    )
    sql = str(constraint.sqltext)

    assert "activation_state <> 'live'" in sql
    assert "lock_in_job_id IS NOT NULL" in sql
    for state in ("queued", "in_progress", "locked", "attention_required"):
        assert f"'{state}'" in sql


def test_migrations_match_the_organization_ownership_contract() -> None:
    initial = INITIAL_MIGRATION_PATH.read_text()
    artifact = ARTIFACT_MIGRATION_PATH.read_text()
    prepare = PREPARE_MIGRATION_PATH.read_text()
    active = MIGRATION_PATH.read_text()

    assert 'name="fk_workspace_promotion_release_artifact_org"' in initial
    assert '["organization_id", "artifact_id"]' in initial
    assert 'name="fk_workspace_promotion_release_previous_org"' in initial
    assert '["organization_id", "previous_release_id"]' in initial
    assert '["organization_id", "supersedes_artifact_id"]' in artifact
    assert 'ondelete="CASCADE"' in artifact
    release_index = prepare[prepare.index('"uq_workspace_promotion_release_artifact"') :]
    release_index = release_index[: release_index.index("\n    )")]
    assert '["artifact_id"]' in release_index
    assert "organization_id" not in release_index
    assert "ck_workspace_promotion_release_live_has_lock_job" in active
    assert "lock_in_job_id IS NOT NULL" in active
