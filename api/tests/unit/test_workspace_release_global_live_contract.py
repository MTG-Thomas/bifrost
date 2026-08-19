"""Pin the platform-global singleton contract for Workspace Live."""

from pathlib import Path

from src.models.orm.workspace_promotions import WorkspacePromotionRelease


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "20260819_workspace_release_active_pointer.py"
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
