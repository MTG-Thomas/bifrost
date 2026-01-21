"""
Workflow reference translation utilities for git sync.

Handles translation between workflow UUIDs and portable path::function_name references.
This enables forms, agents, and apps to be exported/imported across environments.

Export (DB → Git): UUID → "workflows/my_module.py::my_function"
Import (Git → DB): "workflows/my_module.py::my_function" → UUID
"""

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow

logger = logging.getLogger(__name__)


# =============================================================================
# Map Building Functions
# =============================================================================


async def build_workflow_ref_map(db: AsyncSession) -> dict[str, str]:
    """
    Build mapping of workflow UUID -> path::function_name for export.

    Used when serializing entities to transform UUIDs to portable references.

    Args:
        db: Database session

    Returns:
        Dict mapping UUID string -> "path::function_name"
    """
    stmt = select(Workflow).where(Workflow.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    workflows = result.scalars().all()

    workflow_map = {
        str(wf.id): f"{wf.path}::{wf.function_name}"
        for wf in workflows
        if wf.path and wf.function_name
    }

    logger.debug(f"Built workflow ref map with {len(workflow_map)} entries")
    return workflow_map


async def build_ref_to_uuid_map(db: AsyncSession) -> dict[str, str]:
    """
    Build mapping of path::function_name -> UUID for import.

    Used when deserializing entities to resolve references back to UUIDs.

    Args:
        db: Database session

    Returns:
        Dict mapping "path::function_name" -> UUID string
    """
    stmt = select(Workflow).where(Workflow.is_active == True)  # noqa: E712
    result = await db.execute(stmt)
    workflows = result.scalars().all()

    ref_map = {
        f"{wf.path}::{wf.function_name}": str(wf.id)
        for wf in workflows
        if wf.path and wf.function_name
    }

    logger.debug(f"Built ref-to-UUID map with {len(ref_map)} entries")
    return ref_map


# =============================================================================
# Helper Functions
# =============================================================================


def get_nested_value(data: dict, field_path: str) -> str | None:
    """
    Get a value from a nested dict using dot notation with array indices.

    Args:
        data: Dictionary to search
        field_path: Dot-separated path like "form_schema.fields.0.workflow_id"

    Returns:
        The value at the path, or None if not found
    """
    parts = field_path.split(".")
    current: Any = data

    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx] if 0 <= idx < len(current) else None
            except (ValueError, TypeError):
                current = None
        else:
            return None

        if current is None:
            return None

    return current if isinstance(current, str) else None


# =============================================================================
# App Source Transformation Functions
# =============================================================================

# Pattern to match useWorkflow('...') or useWorkflow("...")
# Captures the quote style and the argument
USE_WORKFLOW_PATTERN = re.compile(r"useWorkflow\((['\"])([^'\"]+)\1\)")


def transform_app_source_uuids_to_refs(
    source: str,
    workflow_map: dict[str, str],
) -> tuple[str, list[str]]:
    """
    Transform useWorkflow('{uuid}') to useWorkflow('{ref}') in TSX source.

    Scans source code for useWorkflow() calls and replaces UUIDs with
    portable workflow references (path::function_name format).

    Args:
        source: TSX/TypeScript source code
        workflow_map: Mapping of UUID string -> "path::function_name"

    Returns:
        Tuple of (transformed_source, list_of_transformed_uuids)
    """
    if not source or not workflow_map:
        return source, []

    transformed_uuids: list[str] = []

    def replace_uuid(match: re.Match[str]) -> str:
        quote = match.group(1)
        arg = match.group(2)

        if arg in workflow_map:
            transformed_uuids.append(arg)
            return f"useWorkflow({quote}{workflow_map[arg]}{quote})"
        return match.group(0)

    result = USE_WORKFLOW_PATTERN.sub(replace_uuid, source)
    return result, transformed_uuids


def transform_app_source_refs_to_uuids(
    source: str,
    ref_to_uuid: dict[str, str],
) -> tuple[str, list[str]]:
    """
    Transform useWorkflow('{ref}') to useWorkflow('{uuid}') in TSX source.

    Scans source code for useWorkflow() calls and resolves portable
    workflow references back to UUIDs.

    Args:
        source: TSX/TypeScript source code
        ref_to_uuid: Mapping of "path::function_name" -> UUID string

    Returns:
        Tuple of (transformed_source, list_of_unresolved_refs)
    """
    if not source:
        return source, []

    unresolved_refs: list[str] = []

    def replace_ref(match: re.Match[str]) -> str:
        quote = match.group(1)
        arg = match.group(2)

        # Check if already a UUID (skip transformation)
        if _looks_like_uuid(arg):
            return match.group(0)

        # Check if it's a portable ref we can resolve
        if arg in ref_to_uuid:
            return f"useWorkflow({quote}{ref_to_uuid[arg]}{quote})"

        # Unresolved ref - keep as-is but track it
        if "::" in arg:  # Looks like a portable ref
            unresolved_refs.append(arg)

        return match.group(0)

    result = USE_WORKFLOW_PATTERN.sub(replace_ref, source)
    return result, unresolved_refs


def _looks_like_uuid(value: str) -> bool:
    """
    Check if a string looks like a UUID.

    Simple heuristic: 36 chars with hyphens in the right places.
    """
    if len(value) != 36:
        return False
    if value[8] != "-" or value[13] != "-" or value[18] != "-" or value[23] != "-":
        return False
    return True
