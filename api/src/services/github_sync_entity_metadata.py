"""
Entity metadata extraction for GitHub sync UI.

Extracts display names and entity types from file paths and content
to provide human-readable labels in the sync preview UI.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class EntityMetadata:
    """Metadata extracted from a sync file for UI display."""
    entity_type: str | None
    display_name: str
    parent_slug: str | None = None


def extract_entity_metadata(
    path: str,
    content: bytes | None = None,
    app_prefixes: set[str] | None = None,
) -> EntityMetadata:
    """
    Extract entity metadata from a file path and optional content.

    Uses extension-based detection (not directory-based):
    - App file: path starts with a known app repo_path prefix
    - Workflow: *.py (display-only metadata)

    Form and agent content lives inline in ``.bifrost/forms.yaml`` /
    ``.bifrost/agents.yaml`` and is not surfaced as a per-file entity.

    Args:
        path: File path relative to workspace root
        content: Optional file content (kept for signature compatibility)
        app_prefixes: Optional set of known app repo_path prefixes from DB

    Returns:
        EntityMetadata with type, display name, and parent slug
    """
    filename = Path(path).name

    # App file: path starts with a known app repo_path prefix
    if app_prefixes:
        for prefix in app_prefixes:
            normalized = prefix.rstrip("/") + "/"
            if path.startswith(normalized):
                slug = Path(prefix).name
                relative_path = path[len(normalized):]
                return EntityMetadata(
                    entity_type="app_file",
                    display_name=relative_path,
                    parent_slug=slug,
                )

    # Workflow: *.py (display-only metadata)
    if path.endswith(".py"):
        return EntityMetadata(entity_type="workflow", display_name=filename)

    # Unknown file type
    return EntityMetadata(entity_type=None, display_name=filename)
