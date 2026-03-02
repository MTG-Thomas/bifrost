"""
Entity metadata extraction for GitHub sync UI.

Extracts display names and entity types from file paths and content
to provide human-readable labels in the sync preview UI.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

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
    - Form: *.form.yaml
    - Agent: *.agent.yaml
    - App file: path starts with a known app repo_path prefix
    - Workflow: *.py (display-only metadata)

    Args:
        path: File path relative to workspace root
        content: Optional file content for YAML/JSON parsing
        app_prefixes: Optional set of known app repo_path prefixes from DB

    Returns:
        EntityMetadata with type, display name, and parent slug
    """
    filename = Path(path).name

    # Form: *.form.yaml (any directory)
    if path.endswith(".form.yaml"):
        display_name = _extract_yaml_name(content, filename)
        return EntityMetadata(entity_type="form", display_name=display_name)

    # Agent: *.agent.yaml (any directory)
    if path.endswith(".agent.yaml"):
        display_name = _extract_yaml_name(content, filename)
        return EntityMetadata(entity_type="agent", display_name=display_name)

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


def _extract_yaml_name(content: bytes | None, fallback: str) -> str:
    """Extract 'name' field from YAML content, with fallback."""
    if content is None:
        return fallback

    try:
        data = yaml.safe_load(content.decode("utf-8"))
        if isinstance(data, dict):
            return data.get("name", fallback)
        return fallback
    except (yaml.YAMLError, UnicodeDecodeError):
        logger.debug(f"Failed to parse YAML for name extraction, using fallback: {fallback}")
        return fallback
