"""Test sync preview endpoint enriches conflicts with metadata."""
from src.services.github_sync_entity_metadata import extract_entity_metadata


def test_extract_entity_metadata_for_app_file():
    """App files should have parent_slug and relative path as display_name."""
    metadata = extract_entity_metadata(
        "apps/dashboard/src/index.tsx", None,
        app_prefixes={"apps/dashboard"},
    )

    assert metadata.entity_type == "app_file"
    assert metadata.display_name == "src/index.tsx"
    assert metadata.parent_slug == "dashboard"
