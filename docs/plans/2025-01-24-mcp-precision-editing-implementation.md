# MCP Precision Editing Tools - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement 7 precision editing tools for database-stored code (app_files, workflows, modules) that mirror Claude Code's grep → read range → surgical edit workflow.

**Architecture:** New tools in `api/src/services/mcp_server/tools/code_editor.py` using the `@system_tool` decorator pattern. Tools query content from three tables: `app_files` (source column), `workflows` (code column), `workspace_files` (content column for modules). Write operations for workflows/modules route through `FileStorageService` for validation.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, PostgreSQL, pytest

---

## Task 1: Create Code Editor Tool File with Helper Functions

**Files:**
- Create: `api/src/services/mcp_server/tools/code_editor.py`
- Modify: `api/src/services/mcp_server/tools/__init__.py`

**Step 1: Create the code_editor.py file with imports and helpers**

```python
"""
Code Editor MCP Tools - Precision Editing

Tools for searching, reading, and editing code stored in the database:
- app_files (TSX/TypeScript for App Builder)
- workflows (Python workflow code)
- workspace_files/modules (Python helper modules)

These tools mirror Claude Code's precision editing workflow:
1. list_content - List files by entity type
2. search_content - Find code with regex
3. read_content_lines - Read specific line ranges
4. get_content - Full content read (fallback)
5. patch_content - Surgical old→new replacement
6. replace_content - Full content write (fallback)
7. delete_content - Delete a file
"""

import json
import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select

from src.core.database import get_db_context
from src.models.orm.applications import AppFile, Application
from src.models.orm.workflows import Workflow
from src.models.orm.workspace import WorkspaceFile
from src.services.mcp_server.tool_decorator import system_tool
from src.services.mcp_server.tool_registry import ToolCategory

logger = logging.getLogger(__name__)


# =============================================================================
# Helper Functions
# =============================================================================


def _normalize_line_endings(content: str) -> str:
    """Normalize line endings to \n for consistent matching."""
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _get_lines_with_context(
    content: str, line_number: int, context_lines: int = 3
) -> tuple[list[str], list[str]]:
    """Get context lines before and after a given line number (1-indexed)."""
    lines = content.split("\n")
    idx = line_number - 1  # Convert to 0-indexed

    start_before = max(0, idx - context_lines)
    end_after = min(len(lines), idx + context_lines + 1)

    before = [f"{i + 1}: {lines[i]}" for i in range(start_before, idx)]
    after = [f"{i + 1}: {lines[i]}" for i in range(idx + 1, end_after)]

    return before, after


def _find_match_locations(content: str, search_string: str) -> list[dict[str, Any]]:
    """Find all locations where search_string appears in content."""
    locations = []
    lines = content.split("\n")

    for i, line in enumerate(lines):
        if search_string in line:
            # Get a preview (truncated if too long)
            preview = line.strip()
            if len(preview) > 60:
                preview = preview[:57] + "..."
            locations.append({"line": i + 1, "preview": preview})

    return locations


async def _get_content_by_entity(
    entity_type: str,
    path: str,
    app_id: str | None = None,
    organization_id: str | None = None,
    context: Any = None,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """
    Get content for an entity by type and path.

    Returns:
        Tuple of (content, metadata_dict, error_message)
        - If successful: (content_str, {"path": ..., "entity_id": ...}, None)
        - If error: (None, None, "error message")
    """
    async with get_db_context() as db:
        if entity_type == "app_file":
            if not app_id:
                return None, None, "app_id is required for app_file entity type"

            try:
                app_uuid = UUID(app_id)
            except ValueError:
                return None, None, f"Invalid app_id format: {app_id}"

            # Get app and verify access
            app = await db.get(Application, app_uuid)
            if not app:
                return None, None, f"Application not found: {app_id}"

            if not context.is_platform_admin and context.org_id:
                if app.organization_id and app.organization_id != context.org_id:
                    return None, None, "Access denied"

            if not app.draft_version_id:
                return None, None, "No draft version found"

            # Get file
            query = select(AppFile).where(
                AppFile.app_version_id == app.draft_version_id,
                AppFile.path == path.strip("/"),
            )
            result = await db.execute(query)
            file = result.scalar_one_or_none()

            if not file:
                return None, None, f"File not found: {path}"

            return file.source, {
                "path": file.path,
                "entity_id": str(file.id),
                "app_id": app_id,
            }, None

        elif entity_type == "workflow":
            # Query workflows table
            query = select(Workflow).where(
                Workflow.path == path,
                Workflow.is_active == True,  # noqa: E712
            )

            # Filter by organization if provided
            if organization_id:
                try:
                    org_uuid = UUID(organization_id)
                    query = query.where(Workflow.organization_id == org_uuid)
                except ValueError:
                    return None, None, f"Invalid organization_id format: {organization_id}"
            elif not context.is_platform_admin:
                # Non-admins can see their org's workflows + global
                if context.org_id:
                    query = query.where(
                        (Workflow.organization_id == context.org_id)
                        | (Workflow.organization_id.is_(None))
                    )

            result = await db.execute(query)
            workflow = result.scalar_one_or_none()

            if not workflow:
                return None, None, f"Workflow not found: {path}"

            if not workflow.code:
                return None, None, f"Workflow has no code: {path}"

            return workflow.code, {
                "path": workflow.path,
                "entity_id": str(workflow.id),
                "organization_id": str(workflow.organization_id) if workflow.organization_id else None,
            }, None

        elif entity_type == "module":
            # Query workspace_files for modules
            query = select(WorkspaceFile).where(
                WorkspaceFile.path == path,
                WorkspaceFile.entity_type == "module",
                WorkspaceFile.is_deleted == False,  # noqa: E712
            )

            result = await db.execute(query)
            module = result.scalar_one_or_none()

            if not module:
                return None, None, f"Module not found: {path}"

            if not module.content:
                return None, None, f"Module has no content: {path}"

            return module.content, {
                "path": module.path,
                "entity_id": str(module.id),
            }, None

        else:
            return None, None, f"Invalid entity_type: {entity_type}"
```

**Step 2: Add import to tools/__init__.py**

In `api/src/services/mcp_server/tools/__init__.py`, add:

```python
from src.services.mcp_server.tools import code_editor  # noqa: F401
```

**Step 3: Run linting to verify**

Run: `cd api && ruff check src/services/mcp_server/tools/code_editor.py`
Expected: No errors (or only fixable ones)

**Step 4: Commit**

```bash
git add api/src/services/mcp_server/tools/code_editor.py api/src/services/mcp_server/tools/__init__.py
git commit -m "feat(mcp): add code_editor.py with helper functions for precision editing"
```

---

## Task 2: Implement list_content Tool

**Files:**
- Modify: `api/src/services/mcp_server/tools/code_editor.py`
- Create: `api/tests/unit/services/test_code_editor_tools.py`

**Step 1: Write the failing test**

Create `api/tests/unit/services/test_code_editor_tools.py`:

```python
"""
Unit tests for Code Editor MCP Tools.

Tests the precision editing tools:
- list_content: List files by entity type
- search_content: Regex search with context
- read_content_lines: Line range reading
- get_content: Full content read
- patch_content: Surgical edits
- replace_content: Full content write
- delete_content: Delete files
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.server import MCPContext


@pytest.fixture
def platform_admin_context() -> MCPContext:
    """Create an MCPContext for a platform admin user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=None,
        is_platform_admin=True,
        user_email="admin@platform.local",
        user_name="Platform Admin",
    )


@pytest.fixture
def org_user_context() -> MCPContext:
    """Create an MCPContext for a regular org user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_admin=False,
        user_email="user@org.local",
        user_name="Org User",
    )


class TestListContent:
    """Tests for the list_content MCP tool."""

    @pytest.mark.asyncio
    async def test_list_workflows(self, platform_admin_context):
        """Should list workflow paths."""
        from src.services.mcp_server.tools.code_editor import list_content

        mock_wf1 = MagicMock()
        mock_wf1.path = "workflows/sync_tickets.py"
        mock_wf1.organization_id = None

        mock_wf2 = MagicMock()
        mock_wf2.path = "workflows/sync_users.py"
        mock_wf2.organization_id = None

        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_wf1, mock_wf2]
            mock_session.execute.return_value = mock_result

            result = await list_content(
                context=platform_admin_context,
                entity_type="workflow",
            )

            data = json.loads(result)
            assert "files" in data
            assert len(data["files"]) == 2
            assert data["files"][0]["path"] == "workflows/sync_tickets.py"

    @pytest.mark.asyncio
    async def test_list_requires_app_id_for_app_files(self, platform_admin_context):
        """Should return error if app_id not provided for app_file."""
        from src.services.mcp_server.tools.code_editor import list_content

        result = await list_content(
            context=platform_admin_context,
            entity_type="app_file",
        )

        data = json.loads(result)
        assert "error" in data
        assert "app_id" in data["error"]
```

**Step 2: Run test to verify it fails**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestListContent -v`
Expected: FAIL (ImportError - list_content not defined)

**Step 3: Implement list_content**

Add to `api/src/services/mcp_server/tools/code_editor.py`:

```python
@system_tool(
    id="list_content",
    name="List Content",
    description="List files by entity type. Returns paths without content. Use to discover what files exist before searching or reading.",
    category=ToolCategory.CODE_EDITOR,
    default_enabled_for_coding_agent=True,
    input_schema={
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "enum": ["app_file", "workflow", "module"],
                "description": "Type of entity to list",
            },
            "app_id": {
                "type": "string",
                "description": "For app_file: the app UUID (required)",
            },
            "organization_id": {
                "type": "string",
                "description": "For workflow: limit to this organization (optional). Not applicable to modules.",
            },
            "path_prefix": {
                "type": "string",
                "description": "Filter to paths starting with this prefix (optional)",
            },
        },
        "required": ["entity_type"],
    },
)
async def list_content(
    context: Any,
    entity_type: str,
    app_id: str | None = None,
    organization_id: str | None = None,
    path_prefix: str | None = None,
) -> str:
    """List files by entity type."""
    logger.info(f"MCP list_content: entity_type={entity_type}")

    if entity_type not in ("app_file", "workflow", "module"):
        return json.dumps({"error": f"Invalid entity_type: {entity_type}. Must be one of: app_file, workflow, module"})

    if entity_type == "app_file" and not app_id:
        return json.dumps({"error": "app_id is required for app_file entity type"})

    try:
        async with get_db_context() as db:
            if entity_type == "app_file":
                files = await _list_app_files(db, context, app_id, path_prefix)
            elif entity_type == "workflow":
                files = await _list_workflows(db, context, organization_id, path_prefix)
            elif entity_type == "module":
                files = await _list_modules(db, context, path_prefix)

        return json.dumps({
            "entity_type": entity_type,
            "files": files,
            "count": len(files),
        })

    except Exception as e:
        logger.exception(f"Error in list_content: {e}")
        return json.dumps({"error": f"List failed: {str(e)}"})


async def _list_app_files(
    db, context: Any, app_id: str, path_prefix: str | None
) -> list[dict[str, Any]]:
    """List app files for an application."""
    app_uuid = UUID(app_id)
    app = await db.get(Application, app_uuid)
    if not app:
        return []

    if not context.is_platform_admin and context.org_id:
        if app.organization_id and app.organization_id != context.org_id:
            return []

    if not app.draft_version_id:
        return []

    query = select(AppFile).where(AppFile.app_version_id == app.draft_version_id)
    if path_prefix:
        query = query.where(AppFile.path.startswith(path_prefix.strip("/")))

    result = await db.execute(query)
    files = result.scalars().all()

    return [
        {"path": f.path, "app_id": app_id}
        for f in files
    ]


async def _list_workflows(
    db, context: Any, organization_id: str | None, path_prefix: str | None
) -> list[dict[str, Any]]:
    """List workflows."""
    query = select(Workflow).where(Workflow.is_active == True)  # noqa: E712

    if path_prefix:
        query = query.where(Workflow.path.startswith(path_prefix))

    if organization_id:
        org_uuid = UUID(organization_id)
        query = query.where(Workflow.organization_id == org_uuid)
    elif not context.is_platform_admin and context.org_id:
        query = query.where(
            (Workflow.organization_id == context.org_id)
            | (Workflow.organization_id.is_(None))
        )

    result = await db.execute(query)
    workflows = result.scalars().all()

    return [
        {
            "path": wf.path,
            "organization_id": str(wf.organization_id) if wf.organization_id else None,
        }
        for wf in workflows
    ]


async def _list_modules(
    db, context: Any, path_prefix: str | None
) -> list[dict[str, Any]]:
    """List modules."""
    query = select(WorkspaceFile).where(
        WorkspaceFile.entity_type == "module",
        WorkspaceFile.is_deleted == False,  # noqa: E712
    )

    if path_prefix:
        query = query.where(WorkspaceFile.path.startswith(path_prefix))

    result = await db.execute(query)
    modules = result.scalars().all()

    return [{"path": m.path} for m in modules]
```

**Step 4: Run test to verify it passes**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestListContent -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/mcp_server/tools/code_editor.py api/tests/unit/services/test_code_editor_tools.py
git commit -m "feat(mcp): implement list_content tool for file discovery"
```

---

## Task 3: Implement search_content Tool

**Files:**
- Modify: `api/src/services/mcp_server/tools/code_editor.py`
- Modify: `api/tests/unit/services/test_code_editor_tools.py`

**Step 1: Write the failing test**

Add to `api/tests/unit/services/test_code_editor_tools.py`:

```python
class TestSearchContent:
    """Tests for the search_content MCP tool."""

    @pytest.mark.asyncio
    async def test_search_workflow_content(self, platform_admin_context):
        """Should find matches in workflow code with context."""
        from src.services.mcp_server.tools.code_editor import search_content

        mock_workflow = MagicMock()
        mock_workflow.id = uuid4()
        mock_workflow.path = "workflows/sync_tickets.py"
        mock_workflow.organization_id = None
        mock_workflow.code = '''from bifrost import workflow

@workflow(name="Sync Tickets")
async def sync_tickets(client_id: str) -> dict:
    """Sync tickets from HaloPSA."""
    return {"synced": True}
'''

        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_workflow]
            mock_session.execute.return_value = mock_result

            result = await search_content(
                context=platform_admin_context,
                pattern="async def",
                entity_type="workflow",
            )

            data = json.loads(result)
            assert "matches" in data
            assert len(data["matches"]) == 1
            assert data["matches"][0]["line_number"] == 4
            assert "sync_tickets" in data["matches"][0]["match"]

    @pytest.mark.asyncio
    async def test_search_requires_entity_type(self, platform_admin_context):
        """Should return error if entity_type not provided."""
        from src.services.mcp_server.tools.code_editor import search_content

        result = await search_content(
            context=platform_admin_context,
            pattern="test",
            entity_type="",
        )

        data = json.loads(result)
        assert "error" in data
```

**Step 2: Run test to verify it fails**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestSearchContent::test_search_workflow_content -v`
Expected: FAIL (ImportError - search_content not defined)

**Step 3: Implement search_content**

Add to `api/src/services/mcp_server/tools/code_editor.py`:

```python
@system_tool(
    id="search_content",
    name="Search Content",
    description="Search for patterns in code files. Returns matching lines with context. Use to find functions, imports, or usages before making edits.",
    category=ToolCategory.FILE,
    default_enabled_for_coding_agent=True,
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for (e.g., 'def get_.*agent', 'useWorkflow')",
            },
            "entity_type": {
                "type": "string",
                "enum": ["app_file", "workflow", "module"],
                "description": "Type of entity to search",
            },
            "app_id": {
                "type": "string",
                "description": "For app_file: the app UUID (required). For workflow/module: optional filter.",
            },
            "path": {
                "type": "string",
                "description": "Filter to a specific file path (optional - searches all if omitted)",
            },
            "organization_id": {
                "type": "string",
                "description": "For workflow: limit to this organization (optional). Not applicable to modules.",
            },
            "context_lines": {
                "type": "integer",
                "description": "Number of lines to show before and after each match (default: 3)",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of matches to return (default: 20)",
            },
        },
        "required": ["pattern", "entity_type"],
    },
)
async def search_content(
    context: Any,
    pattern: str,
    entity_type: str,
    app_id: str | None = None,
    path: str | None = None,
    organization_id: str | None = None,
    context_lines: int = 3,
    max_results: int = 20,
) -> str:
    """Search for regex patterns in code content."""
    logger.info(f"MCP search_content: pattern={pattern}, entity_type={entity_type}")

    if not pattern:
        return json.dumps({"error": "pattern is required"})

    if entity_type not in ("app_file", "workflow", "module"):
        return json.dumps({"error": f"Invalid entity_type: {entity_type}. Must be one of: app_file, workflow, module"})

    if entity_type == "app_file" and not app_id:
        return json.dumps({"error": "app_id is required for app_file entity type"})

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return json.dumps({"error": f"Invalid regex pattern: {e}"})

    matches = []
    truncated = False

    try:
        async with get_db_context() as db:
            if entity_type == "app_file":
                matches = await _search_app_files(
                    db, context, app_id, path, regex, context_lines, max_results
                )
            elif entity_type == "workflow":
                matches = await _search_workflows(
                    db, context, path, organization_id, regex, context_lines, max_results
                )
            elif entity_type == "module":
                matches = await _search_modules(
                    db, context, path, regex, context_lines, max_results
                )

            if len(matches) > max_results:
                matches = matches[:max_results]
                truncated = True

        return json.dumps({
            "matches": matches,
            "total_matches": len(matches),
            "truncated": truncated,
        })

    except Exception as e:
        logger.exception(f"Error in search_content: {e}")
        return json.dumps({"error": f"Search failed: {str(e)}"})


async def _search_app_files(
    db, context: Any, app_id: str, path: str | None, regex: re.Pattern, context_lines: int, max_results: int
) -> list[dict[str, Any]]:
    """Search app files for regex matches."""
    app_uuid = UUID(app_id)
    app = await db.get(Application, app_uuid)
    if not app:
        return []

    if not context.is_platform_admin and context.org_id:
        if app.organization_id and app.organization_id != context.org_id:
            return []

    if not app.draft_version_id:
        return []

    query = select(AppFile).where(AppFile.app_version_id == app.draft_version_id)
    if path:
        query = query.where(AppFile.path == path.strip("/"))

    result = await db.execute(query)
    files = result.scalars().all()

    matches = []
    for file in files:
        content = _normalize_line_endings(file.source or "")
        lines = content.split("\n")

        for i, line in enumerate(lines):
            if regex.search(line):
                before, after = _get_lines_with_context(content, i + 1, context_lines)
                matches.append({
                    "path": file.path,
                    "app_id": app_id,
                    "line_number": i + 1,
                    "match": line,
                    "context_before": before,
                    "context_after": after,
                })
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    return matches


async def _search_workflows(
    db, context: Any, path: str | None, organization_id: str | None, regex: re.Pattern, context_lines: int, max_results: int
) -> list[dict[str, Any]]:
    """Search workflows for regex matches."""
    query = select(Workflow).where(Workflow.is_active == True)  # noqa: E712

    if path:
        query = query.where(Workflow.path == path)

    if organization_id:
        org_uuid = UUID(organization_id)
        query = query.where(Workflow.organization_id == org_uuid)
    elif not context.is_platform_admin and context.org_id:
        query = query.where(
            (Workflow.organization_id == context.org_id)
            | (Workflow.organization_id.is_(None))
        )

    result = await db.execute(query)
    workflows = result.scalars().all()

    matches = []
    for wf in workflows:
        if not wf.code:
            continue

        content = _normalize_line_endings(wf.code)
        lines = content.split("\n")

        for i, line in enumerate(lines):
            if regex.search(line):
                before, after = _get_lines_with_context(content, i + 1, context_lines)
                matches.append({
                    "path": wf.path,
                    "organization_id": str(wf.organization_id) if wf.organization_id else None,
                    "line_number": i + 1,
                    "match": line,
                    "context_before": before,
                    "context_after": after,
                })
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    return matches


async def _search_modules(
    db, context: Any, path: str | None, regex: re.Pattern, context_lines: int, max_results: int
) -> list[dict[str, Any]]:
    """Search modules for regex matches."""
    query = select(WorkspaceFile).where(
        WorkspaceFile.entity_type == "module",
        WorkspaceFile.is_deleted == False,  # noqa: E712
    )

    if path:
        query = query.where(WorkspaceFile.path == path)

    result = await db.execute(query)
    modules = result.scalars().all()

    matches = []
    for mod in modules:
        if not mod.content:
            continue

        content = _normalize_line_endings(mod.content)
        lines = content.split("\n")

        for i, line in enumerate(lines):
            if regex.search(line):
                before, after = _get_lines_with_context(content, i + 1, context_lines)
                matches.append({
                    "path": mod.path,
                    "line_number": i + 1,
                    "match": line,
                    "context_before": before,
                    "context_after": after,
                })
                if len(matches) >= max_results:
                    break
        if len(matches) >= max_results:
            break

    return matches
```

**Step 4: Run test to verify it passes**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestSearchContent -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/mcp_server/tools/code_editor.py api/tests/unit/services/test_code_editor_tools.py
git commit -m "feat(mcp): implement search_content tool with regex search across entities"
```

---

## Task 4: Implement read_content_lines Tool

**Files:**
- Modify: `api/src/services/mcp_server/tools/code_editor.py`
- Modify: `api/tests/unit/services/test_code_editor_tools.py`

**Step 1: Write the failing test**

Add to `api/tests/unit/services/test_code_editor_tools.py`:

```python
class TestReadContentLines:
    """Tests for the read_content_lines MCP tool."""

    @pytest.mark.asyncio
    async def test_read_line_range(self, platform_admin_context):
        """Should read specific line range from workflow."""
        from src.services.mcp_server.tools.code_editor import read_content_lines

        mock_workflow = MagicMock()
        mock_workflow.id = uuid4()
        mock_workflow.path = "workflows/sync.py"
        mock_workflow.organization_id = None
        mock_workflow.code = """line 1
line 2
line 3
line 4
line 5
line 6
line 7
line 8
line 9
line 10"""

        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_workflow
            mock_session.execute.return_value = mock_result

            result = await read_content_lines(
                context=platform_admin_context,
                entity_type="workflow",
                path="workflows/sync.py",
                start_line=3,
                end_line=6,
            )

            data = json.loads(result)
            assert data["start_line"] == 3
            assert data["end_line"] == 6
            assert data["total_lines"] == 10
            assert "3: line 3" in data["content"]
            assert "6: line 6" in data["content"]
            assert "line 2" not in data["content"]
```

**Step 2: Run test to verify it fails**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestReadContentLines -v`
Expected: FAIL (ImportError)

**Step 3: Implement read_content_lines**

Add to `api/src/services/mcp_server/tools/code_editor.py`:

```python
@system_tool(
    id="read_content_lines",
    name="Read Content Lines",
    description="Read specific line range from a file. Use to get context around a search match without loading entire file.",
    category=ToolCategory.FILE,
    default_enabled_for_coding_agent=True,
    input_schema={
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "enum": ["app_file", "workflow", "module"],
                "description": "Type of entity",
            },
            "app_id": {
                "type": "string",
                "description": "For app_file: the app UUID (required)",
            },
            "path": {
                "type": "string",
                "description": "File path (required)",
            },
            "organization_id": {
                "type": "string",
                "description": "For workflow: the organization UUID (optional for global). Not applicable to modules.",
            },
            "start_line": {
                "type": "integer",
                "description": "First line to read (1-indexed, default: 1)",
            },
            "end_line": {
                "type": "integer",
                "description": "Last line to read (defaults to start_line + 100)",
            },
        },
        "required": ["entity_type", "path"],
    },
)
async def read_content_lines(
    context: Any,
    entity_type: str,
    path: str,
    app_id: str | None = None,
    organization_id: str | None = None,
    start_line: int = 1,
    end_line: int | None = None,
) -> str:
    """Read a specific range of lines from a file."""
    logger.info(f"MCP read_content_lines: entity_type={entity_type}, path={path}, lines={start_line}-{end_line}")

    if not path:
        return json.dumps({"error": "path is required"})

    if entity_type not in ("app_file", "workflow", "module"):
        return json.dumps({"error": f"Invalid entity_type: {entity_type}"})

    if entity_type == "app_file" and not app_id:
        return json.dumps({"error": "app_id is required for app_file entity type"})

    content, metadata, error = await _get_content_by_entity(
        entity_type, path, app_id, organization_id, context
    )

    if error:
        return json.dumps({"error": error})

    content = _normalize_line_endings(content)
    lines = content.split("\n")
    total_lines = len(lines)

    # Apply defaults
    if start_line < 1:
        start_line = 1
    if end_line is None:
        end_line = min(start_line + 100, total_lines)
    if end_line > total_lines:
        end_line = total_lines

    # Extract requested lines (1-indexed)
    selected_lines = []
    for i in range(start_line - 1, end_line):
        if i < len(lines):
            selected_lines.append(f"{i + 1}: {lines[i]}")

    return json.dumps({
        "path": metadata["path"],
        "organization_id": metadata.get("organization_id"),
        "app_id": metadata.get("app_id"),
        "start_line": start_line,
        "end_line": end_line,
        "total_lines": total_lines,
        "content": "\n".join(selected_lines),
    })
```

**Step 4: Run test to verify it passes**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestReadContentLines -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/mcp_server/tools/code_editor.py api/tests/unit/services/test_code_editor_tools.py
git commit -m "feat(mcp): implement read_content_lines tool for line range reading"
```

---

## Task 5: Implement get_content Tool

**Files:**
- Modify: `api/src/services/mcp_server/tools/code_editor.py`
- Modify: `api/tests/unit/services/test_code_editor_tools.py`

**Step 1: Write the failing test**

Add to `api/tests/unit/services/test_code_editor_tools.py`:

```python
class TestGetContent:
    """Tests for the get_content MCP tool."""

    @pytest.mark.asyncio
    async def test_get_full_content(self, platform_admin_context):
        """Should return full file content with metadata."""
        from src.services.mcp_server.tools.code_editor import get_content

        mock_workflow = MagicMock()
        mock_workflow.id = uuid4()
        mock_workflow.path = "workflows/sync.py"
        mock_workflow.organization_id = None
        mock_workflow.code = "line 1\nline 2\nline 3"

        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_workflow
            mock_session.execute.return_value = mock_result

            result = await get_content(
                context=platform_admin_context,
                entity_type="workflow",
                path="workflows/sync.py",
            )

            data = json.loads(result)
            assert data["path"] == "workflows/sync.py"
            assert data["total_lines"] == 3
            assert "line 1" in data["content"]
            assert "line 3" in data["content"]
```

**Step 2: Run test to verify it fails**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestGetContent -v`
Expected: FAIL

**Step 3: Implement get_content**

Add to `api/src/services/mcp_server/tools/code_editor.py`:

```python
@system_tool(
    id="get_content",
    name="Get Content",
    description="Get entire file content. Prefer search_content + read_content_lines for large files. Use this for small files or when you need the complete picture.",
    category=ToolCategory.FILE,
    default_enabled_for_coding_agent=True,
    input_schema={
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "enum": ["app_file", "workflow", "module"],
                "description": "Type of entity",
            },
            "app_id": {
                "type": "string",
                "description": "For app_file: the app UUID (required)",
            },
            "path": {
                "type": "string",
                "description": "File path (required)",
            },
            "organization_id": {
                "type": "string",
                "description": "For workflow: the organization UUID (optional for global). Not applicable to modules.",
            },
        },
        "required": ["entity_type", "path"],
    },
)
async def get_content(
    context: Any,
    entity_type: str,
    path: str,
    app_id: str | None = None,
    organization_id: str | None = None,
) -> str:
    """Get the entire content of a file."""
    logger.info(f"MCP get_content: entity_type={entity_type}, path={path}")

    if not path:
        return json.dumps({"error": "path is required"})

    if entity_type not in ("app_file", "workflow", "module"):
        return json.dumps({"error": f"Invalid entity_type: {entity_type}"})

    if entity_type == "app_file" and not app_id:
        return json.dumps({"error": "app_id is required for app_file entity type"})

    content, metadata, error = await _get_content_by_entity(
        entity_type, path, app_id, organization_id, context
    )

    if error:
        return json.dumps({"error": error})

    content = _normalize_line_endings(content)
    lines = content.split("\n")

    return json.dumps({
        "path": metadata["path"],
        "organization_id": metadata.get("organization_id"),
        "app_id": metadata.get("app_id"),
        "entity_id": metadata.get("entity_id"),
        "total_lines": len(lines),
        "content": content,
    })
```

**Step 4: Run test to verify it passes**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestGetContent -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/mcp_server/tools/code_editor.py api/tests/unit/services/test_code_editor_tools.py
git commit -m "feat(mcp): implement get_content tool for full file retrieval"
```

---

## Task 6: Implement patch_content Tool

**Files:**
- Modify: `api/src/services/mcp_server/tools/code_editor.py`
- Modify: `api/tests/unit/services/test_code_editor_tools.py`

**Step 1: Write the failing test**

Add to `api/tests/unit/services/test_code_editor_tools.py`:

```python
class TestPatchContent:
    """Tests for the patch_content MCP tool."""

    @pytest.mark.asyncio
    async def test_patch_unique_string(self, platform_admin_context):
        """Should replace unique string successfully."""
        from src.services.mcp_server.tools.code_editor import patch_content

        mock_workflow = MagicMock()
        mock_workflow.id = uuid4()
        mock_workflow.path = "workflows/sync.py"
        mock_workflow.organization_id = None
        mock_workflow.code = '''async def sync_tickets():
    return {"status": "old"}
'''

        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_workflow
            mock_session.execute.return_value = mock_result

            # Mock FileStorageService for validation
            with patch("src.services.mcp_server.tools.code_editor.FileStorageService") as mock_fs:
                mock_fs_instance = MagicMock()
                mock_fs_instance.write_file = AsyncMock()
                mock_fs.return_value = mock_fs_instance

                result = await patch_content(
                    context=platform_admin_context,
                    entity_type="workflow",
                    path="workflows/sync.py",
                    old_string='return {"status": "old"}',
                    new_string='return {"status": "new"}',
                )

                data = json.loads(result)
                assert data["success"] is True

    @pytest.mark.asyncio
    async def test_patch_non_unique_string_fails(self, platform_admin_context):
        """Should fail when old_string matches multiple locations."""
        from src.services.mcp_server.tools.code_editor import patch_content

        mock_workflow = MagicMock()
        mock_workflow.id = uuid4()
        mock_workflow.path = "workflows/sync.py"
        mock_workflow.organization_id = None
        mock_workflow.code = '''def func1():
    return "duplicate"

def func2():
    return "duplicate"
'''

        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_workflow
            mock_session.execute.return_value = mock_result

            result = await patch_content(
                context=platform_admin_context,
                entity_type="workflow",
                path="workflows/sync.py",
                old_string='return "duplicate"',
                new_string='return "new_value"',
            )

            data = json.loads(result)
            assert data["success"] is False
            assert "matches" in data["error"].lower() or "match_locations" in data
```

**Step 2: Run test to verify it fails**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestPatchContent -v`
Expected: FAIL

**Step 3: Implement patch_content**

Add to `api/src/services/mcp_server/tools/code_editor.py` (also add import at top):

```python
# Add to imports at top of file:
from src.services.file_storage import FileStorageService


@system_tool(
    id="patch_content",
    name="Patch Content",
    description="Surgical edit: replace old_string with new_string. The old_string must be unique in the file. Include enough context to ensure uniqueness. Use replace_content if patch fails due to syntax issues.",
    category=ToolCategory.FILE,
    default_enabled_for_coding_agent=True,
    is_restricted=True,
    input_schema={
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "enum": ["app_file", "workflow", "module"],
                "description": "Type of entity",
            },
            "app_id": {
                "type": "string",
                "description": "For app_file: the app UUID (required)",
            },
            "path": {
                "type": "string",
                "description": "File path (required)",
            },
            "organization_id": {
                "type": "string",
                "description": "For workflow: the organization UUID (optional for global). Not applicable to modules.",
            },
            "old_string": {
                "type": "string",
                "description": "Exact string to find and replace (must be unique in file)",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement string",
            },
        },
        "required": ["entity_type", "path", "old_string", "new_string"],
    },
)
async def patch_content(
    context: Any,
    entity_type: str,
    path: str,
    old_string: str,
    new_string: str,
    app_id: str | None = None,
    organization_id: str | None = None,
) -> str:
    """Make a surgical edit by replacing a unique string."""
    logger.info(f"MCP patch_content: entity_type={entity_type}, path={path}")

    if not path:
        return json.dumps({"error": "path is required"})
    if not old_string:
        return json.dumps({"error": "old_string is required"})
    if entity_type not in ("app_file", "workflow", "module"):
        return json.dumps({"error": f"Invalid entity_type: {entity_type}"})
    if entity_type == "app_file" and not app_id:
        return json.dumps({"error": "app_id is required for app_file entity type"})

    content, metadata, error = await _get_content_by_entity(
        entity_type, path, app_id, organization_id, context
    )

    if error:
        return json.dumps({"error": error})

    content = _normalize_line_endings(content)
    old_string = _normalize_line_endings(old_string)
    new_string = _normalize_line_endings(new_string)

    # Check uniqueness
    match_count = content.count(old_string)

    if match_count == 0:
        return json.dumps({
            "success": False,
            "error": "old_string not found in file",
        })

    if match_count > 1:
        locations = _find_match_locations(content, old_string)
        return json.dumps({
            "success": False,
            "error": f"old_string matches {match_count} locations. Include more context to make it unique.",
            "match_locations": locations,
        })

    # Perform replacement
    new_content = content.replace(old_string, new_string, 1)

    # Count lines changed
    old_lines = old_string.count("\n") + 1
    new_lines = new_string.count("\n") + 1
    lines_changed = max(old_lines, new_lines)

    # Persist the change
    try:
        await _persist_content(
            entity_type, path, new_content, app_id, organization_id, context
        )

        return json.dumps({
            "success": True,
            "path": metadata["path"],
            "lines_changed": lines_changed,
        })

    except Exception as e:
        logger.exception(f"Error persisting patch: {e}")
        return json.dumps({
            "success": False,
            "error": f"Failed to save changes: {str(e)}",
        })


async def _persist_content(
    entity_type: str,
    path: str,
    content: str,
    app_id: str | None,
    organization_id: str | None,
    context: Any,
) -> None:
    """Persist content changes to the database."""
    async with get_db_context() as db:
        if entity_type == "app_file":
            from src.core.pubsub import publish_app_code_file_update

            app_uuid = UUID(app_id)
            app = await db.get(Application, app_uuid)

            query = select(AppFile).where(
                AppFile.app_version_id == app.draft_version_id,
                AppFile.path == path.strip("/"),
            )
            result = await db.execute(query)
            file = result.scalar_one_or_none()

            file.source = content
            await db.flush()

            # Publish update for real-time preview
            await publish_app_code_file_update(
                app_id=app_id,
                user_id=str(context.user_id) if context.user_id else "mcp",
                user_name=context.user_name or "MCP Tool",
                path=path,
                source=content,
                compiled=file.compiled,
                action="update",
            )

            await db.commit()

        elif entity_type in ("workflow", "module"):
            # Route through FileStorageService for validation
            service = FileStorageService(db)
            await service.write_file(
                path=path,
                content=content.encode("utf-8"),
                updated_by=context.user_email or "mcp",
                force_deactivation=True,  # Allow changes
            )
```

**Step 4: Run test to verify it passes**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestPatchContent -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/mcp_server/tools/code_editor.py api/tests/unit/services/test_code_editor_tools.py
git commit -m "feat(mcp): implement patch_content tool for surgical edits"
```

---

## Task 7: Implement replace_content Tool

**Files:**
- Modify: `api/src/services/mcp_server/tools/code_editor.py`
- Modify: `api/tests/unit/services/test_code_editor_tools.py`

**Step 1: Write the failing test**

Add to `api/tests/unit/services/test_code_editor_tools.py`:

```python
class TestReplaceContent:
    """Tests for the replace_content MCP tool."""

    @pytest.mark.asyncio
    async def test_replace_existing_file(self, platform_admin_context):
        """Should replace entire file content."""
        from src.services.mcp_server.tools.code_editor import replace_content

        mock_workflow = MagicMock()
        mock_workflow.id = uuid4()
        mock_workflow.path = "workflows/sync.py"
        mock_workflow.organization_id = None
        mock_workflow.code = "old content"

        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_workflow
            mock_session.execute.return_value = mock_result

            with patch("src.services.mcp_server.tools.code_editor.FileStorageService") as mock_fs:
                mock_fs_instance = MagicMock()
                mock_write_result = MagicMock()
                mock_write_result.file_record = MagicMock()
                mock_fs_instance.write_file = AsyncMock(return_value=mock_write_result)
                mock_fs.return_value = mock_fs_instance

                result = await replace_content(
                    context=platform_admin_context,
                    entity_type="workflow",
                    path="workflows/sync.py",
                    content='''from bifrost import workflow

@workflow(name="Sync")
async def sync():
    return {"done": True}
''',
                )

                data = json.loads(result)
                assert data["success"] is True
                assert data["entity_type"] == "workflow"

    @pytest.mark.asyncio
    async def test_replace_validates_entity_type_match(self, platform_admin_context):
        """Should error if declared entity_type doesn't match content."""
        from src.services.mcp_server.tools.code_editor import replace_content

        # Trying to create a "module" with @workflow decorator should fail
        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            # No existing file
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute.return_value = mock_result

            with patch("src.services.mcp_server.tools.code_editor.FileStorageService") as mock_fs:
                mock_fs_instance = MagicMock()
                # Simulate validation failure
                mock_fs_instance.write_file = AsyncMock(
                    side_effect=ValueError("entity_type mismatch: declared 'module' but content contains @workflow")
                )
                mock_fs.return_value = mock_fs_instance

                result = await replace_content(
                    context=platform_admin_context,
                    entity_type="module",
                    path="modules/helpers.py",
                    content='''from bifrost import workflow

@workflow(name="Should Be Module")
async def oops():
    return {}
''',
                )

                data = json.loads(result)
                assert data["success"] is False
                assert "mismatch" in data["error"].lower() or "error" in data
```

**Step 2: Run test to verify it fails**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestReplaceContent -v`
Expected: FAIL

**Step 3: Implement replace_content**

Add to `api/src/services/mcp_server/tools/code_editor.py`:

```python
@system_tool(
    id="replace_content",
    name="Replace Content",
    description="Replace entire file content or create new file. For workflows/modules: validates syntax and confirms entity_type matches content (e.g., workflow must have @workflow decorator). Use when: (1) creating new files, (2) patch_content fails due to syntax issues, (3) file is small and full replacement is simpler. Prefer patch_content for targeted edits.",
    category=ToolCategory.FILE,
    default_enabled_for_coding_agent=True,
    is_restricted=True,
    input_schema={
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "enum": ["app_file", "workflow", "module"],
                "description": "Type of entity. Must match content (e.g., workflow code must have @workflow decorator)",
            },
            "app_id": {
                "type": "string",
                "description": "For app_file: the app UUID (required)",
            },
            "path": {
                "type": "string",
                "description": "File path (required)",
            },
            "organization_id": {
                "type": "string",
                "description": "For workflow: the organization UUID. Omit for global scope. Not applicable to modules.",
            },
            "content": {
                "type": "string",
                "description": "New file content",
            },
        },
        "required": ["entity_type", "path", "content"],
    },
)
async def replace_content(
    context: Any,
    entity_type: str,
    path: str,
    content: str,
    app_id: str | None = None,
    organization_id: str | None = None,
) -> str:
    """Replace entire file content or create a new file."""
    logger.info(f"MCP replace_content: entity_type={entity_type}, path={path}")

    if not path:
        return json.dumps({"error": "path is required"})
    if not content:
        return json.dumps({"error": "content is required"})
    if entity_type not in ("app_file", "workflow", "module"):
        return json.dumps({"error": f"Invalid entity_type: {entity_type}"})
    if entity_type == "app_file" and not app_id:
        return json.dumps({"error": "app_id is required for app_file entity type"})

    content = _normalize_line_endings(content)

    try:
        if entity_type == "app_file":
            created = await _replace_app_file(context, app_id, path, content)
        else:
            # Validate entity_type matches content before writing
            validation_error = _validate_entity_type_match(entity_type, content)
            if validation_error:
                return json.dumps({
                    "success": False,
                    "error": validation_error,
                })

            created = await _replace_workspace_file(context, entity_type, path, content, organization_id)

        return json.dumps({
            "success": True,
            "path": path,
            "entity_type": entity_type,
            "organization_id": organization_id,
            "app_id": app_id,
            "created": created,
        })

    except Exception as e:
        logger.exception(f"Error in replace_content: {e}")
        return json.dumps({
            "success": False,
            "error": str(e),
        })


def _validate_entity_type_match(entity_type: str, content: str) -> str | None:
    """
    Validate that declared entity_type matches the content.

    Returns error message if mismatch, None if valid.
    """
    has_workflow_decorator = "@workflow" in content or "@tool" in content or "@data_provider" in content

    if entity_type == "workflow" and not has_workflow_decorator:
        return "entity_type mismatch: declared 'workflow' but no @workflow, @tool, or @data_provider decorator found"

    if entity_type == "module" and has_workflow_decorator:
        return "entity_type mismatch: declared 'module' but content contains @workflow/@tool/@data_provider decorator. Use entity_type='workflow' instead."

    return None


async def _replace_app_file(
    context: Any, app_id: str, path: str, content: str
) -> bool:
    """Replace or create an app file. Returns True if created, False if updated."""
    from src.core.pubsub import publish_app_code_file_update

    async with get_db_context() as db:
        app_uuid = UUID(app_id)
        app = await db.get(Application, app_uuid)

        if not app:
            raise ValueError(f"Application not found: {app_id}")

        if not context.is_platform_admin and context.org_id:
            if app.organization_id and app.organization_id != context.org_id:
                raise PermissionError("Access denied")

        if not app.draft_version_id:
            raise ValueError("No draft version found")

        # Check if file exists
        query = select(AppFile).where(
            AppFile.app_version_id == app.draft_version_id,
            AppFile.path == path.strip("/"),
        )
        result = await db.execute(query)
        file = result.scalar_one_or_none()

        created = False
        if file:
            # Update existing
            file.source = content
            action = "update"
        else:
            # Create new
            file = AppFile(
                app_version_id=app.draft_version_id,
                path=path.strip("/"),
                source=content,
            )
            db.add(file)
            created = True
            action = "create"

        await db.flush()

        # Publish update for real-time preview
        await publish_app_code_file_update(
            app_id=app_id,
            user_id=str(context.user_id) if context.user_id else "mcp",
            user_name=context.user_name or "MCP Tool",
            path=path,
            source=content,
            compiled=file.compiled if hasattr(file, 'compiled') else None,
            action=action,
        )

        await db.commit()
        return created


async def _replace_workspace_file(
    context: Any, entity_type: str, path: str, content: str, organization_id: str | None
) -> bool:
    """Replace or create a workflow/module file. Returns True if created, False if updated."""
    async with get_db_context() as db:
        service = FileStorageService(db)

        # Check if file exists to determine created status
        try:
            existing_content, _ = await service.read_file(path)
            created = False
        except FileNotFoundError:
            created = True

        # Write through FileStorageService for validation
        # This will detect decorators and route to appropriate table
        await service.write_file(
            path=path,
            content=content.encode("utf-8"),
            updated_by=context.user_email or "mcp",
            force_deactivation=True,
        )

        return created
```

**Step 4: Run test to verify it passes**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestReplaceContent -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/mcp_server/tools/code_editor.py api/tests/unit/services/test_code_editor_tools.py
git commit -m "feat(mcp): implement replace_content tool for full file replacement"
```

---

## Task 8: Implement delete_content Tool

**Files:**
- Modify: `api/src/services/mcp_server/tools/code_editor.py`
- Modify: `api/tests/unit/services/test_code_editor_tools.py`

**Step 1: Write the failing test**

Add to `api/tests/unit/services/test_code_editor_tools.py`:

```python
class TestDeleteContent:
    """Tests for the delete_content MCP tool."""

    @pytest.mark.asyncio
    async def test_delete_workflow(self, platform_admin_context):
        """Should delete a workflow."""
        from src.services.mcp_server.tools.code_editor import delete_content

        mock_workflow = MagicMock()
        mock_workflow.id = uuid4()
        mock_workflow.path = "workflows/old_sync.py"
        mock_workflow.organization_id = None

        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_workflow
            mock_session.execute.return_value = mock_result

            result = await delete_content(
                context=platform_admin_context,
                entity_type="workflow",
                path="workflows/old_sync.py",
            )

            data = json.loads(result)
            assert data["success"] is True
            assert data["path"] == "workflows/old_sync.py"

    @pytest.mark.asyncio
    async def test_delete_not_found(self, platform_admin_context):
        """Should return error if file not found."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.get_db_context") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            mock_session.execute.return_value = mock_result

            result = await delete_content(
                context=platform_admin_context,
                entity_type="workflow",
                path="workflows/nonexistent.py",
            )

            data = json.loads(result)
            assert data["success"] is False
            assert "not found" in data["error"].lower()
```

**Step 2: Run test to verify it fails**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestDeleteContent -v`
Expected: FAIL (ImportError)

**Step 3: Implement delete_content**

Add to `api/src/services/mcp_server/tools/code_editor.py`:

```python
@system_tool(
    id="delete_content",
    name="Delete Content",
    description="Delete a file. For workflows, this deactivates the workflow. For modules, marks as deleted. For app files, removes from the draft version.",
    category=ToolCategory.CODE_EDITOR,
    default_enabled_for_coding_agent=True,
    is_restricted=True,
    input_schema={
        "type": "object",
        "properties": {
            "entity_type": {
                "type": "string",
                "enum": ["app_file", "workflow", "module"],
                "description": "Type of entity to delete",
            },
            "app_id": {
                "type": "string",
                "description": "For app_file: the app UUID (required)",
            },
            "path": {
                "type": "string",
                "description": "File path to delete (required)",
            },
            "organization_id": {
                "type": "string",
                "description": "For workflow: the organization UUID (optional). Not applicable to modules.",
            },
        },
        "required": ["entity_type", "path"],
    },
)
async def delete_content(
    context: Any,
    entity_type: str,
    path: str,
    app_id: str | None = None,
    organization_id: str | None = None,
) -> str:
    """Delete a file."""
    logger.info(f"MCP delete_content: entity_type={entity_type}, path={path}")

    if not path:
        return json.dumps({"error": "path is required"})
    if entity_type not in ("app_file", "workflow", "module"):
        return json.dumps({"error": f"Invalid entity_type: {entity_type}"})
    if entity_type == "app_file" and not app_id:
        return json.dumps({"error": "app_id is required for app_file entity type"})

    try:
        async with get_db_context() as db:
            if entity_type == "app_file":
                success = await _delete_app_file(db, context, app_id, path)
            elif entity_type == "workflow":
                success = await _delete_workflow(db, context, path, organization_id)
            elif entity_type == "module":
                success = await _delete_module(db, context, path)

            if not success:
                return json.dumps({
                    "success": False,
                    "error": f"File not found: {path}",
                })

            await db.commit()

        return json.dumps({
            "success": True,
            "path": path,
            "entity_type": entity_type,
        })

    except Exception as e:
        logger.exception(f"Error in delete_content: {e}")
        return json.dumps({
            "success": False,
            "error": str(e),
        })


async def _delete_app_file(
    db, context: Any, app_id: str, path: str
) -> bool:
    """Delete an app file. Returns True if deleted."""
    from src.core.pubsub import publish_app_code_file_update

    app_uuid = UUID(app_id)
    app = await db.get(Application, app_uuid)

    if not app or not app.draft_version_id:
        return False

    if not context.is_platform_admin and context.org_id:
        if app.organization_id and app.organization_id != context.org_id:
            return False

    query = select(AppFile).where(
        AppFile.app_version_id == app.draft_version_id,
        AppFile.path == path.strip("/"),
    )
    result = await db.execute(query)
    file = result.scalar_one_or_none()

    if not file:
        return False

    await db.delete(file)

    # Publish delete event
    await publish_app_code_file_update(
        app_id=app_id,
        user_id=str(context.user_id) if context.user_id else "mcp",
        user_name=context.user_name or "MCP Tool",
        path=path,
        source=None,
        compiled=None,
        action="delete",
    )

    return True


async def _delete_workflow(
    db, context: Any, path: str, organization_id: str | None
) -> bool:
    """Deactivate a workflow. Returns True if found and deactivated."""
    query = select(Workflow).where(
        Workflow.path == path,
        Workflow.is_active == True,  # noqa: E712
    )

    if organization_id:
        org_uuid = UUID(organization_id)
        query = query.where(Workflow.organization_id == org_uuid)
    elif not context.is_platform_admin and context.org_id:
        query = query.where(
            (Workflow.organization_id == context.org_id)
            | (Workflow.organization_id.is_(None))
        )

    result = await db.execute(query)
    workflow = result.scalar_one_or_none()

    if not workflow:
        return False

    workflow.is_active = False
    return True


async def _delete_module(
    db, context: Any, path: str
) -> bool:
    """Mark a module as deleted. Returns True if found and marked."""
    query = select(WorkspaceFile).where(
        WorkspaceFile.path == path,
        WorkspaceFile.entity_type == "module",
        WorkspaceFile.is_deleted == False,  # noqa: E712
    )

    result = await db.execute(query)
    module = result.scalar_one_or_none()

    if not module:
        return False

    module.is_deleted = True
    return True
```

**Step 4: Run test to verify it passes**

Run: `cd api && python -m pytest tests/unit/services/test_code_editor_tools.py::TestDeleteContent -v`
Expected: PASS

**Step 5: Commit**

```bash
git add api/src/services/mcp_server/tools/code_editor.py api/tests/unit/services/test_code_editor_tools.py
git commit -m "feat(mcp): implement delete_content tool for file deletion"
```

---

## Task 9: Add Integration Tests

**Files:**
- Create: `api/tests/integration/mcp/test_code_editor_tools.py`

**Step 1: Create integration test file**

```python
"""
Integration tests for Code Editor MCP Tools.

Tests the full stack with actual database operations.
"""

import pytest
from uuid import uuid4

from src.services.mcp_server.server import MCPContext
from src.services.mcp_server.tools.code_editor import (
    search_content,
    read_content_lines,
    get_content,
    patch_content,
    replace_content,
)


@pytest.fixture
def admin_context() -> MCPContext:
    """Platform admin context for tests."""
    return MCPContext(
        user_id=uuid4(),
        org_id=None,
        is_platform_admin=True,
        user_email="admin@test.local",
        user_name="Test Admin",
    )


@pytest.mark.integration
class TestCodeEditorIntegration:
    """Integration tests for code editor tools."""

    @pytest.mark.asyncio
    async def test_search_then_read_workflow(self, admin_context, db_session, sample_workflow):
        """Should search for pattern then read the matching lines."""
        import json

        # First search
        search_result = await search_content(
            context=admin_context,
            pattern="async def",
            entity_type="workflow",
            path=sample_workflow.path,
        )

        data = json.loads(search_result)
        assert len(data["matches"]) > 0

        # Then read the lines around the match
        match = data["matches"][0]
        read_result = await read_content_lines(
            context=admin_context,
            entity_type="workflow",
            path=sample_workflow.path,
            start_line=max(1, match["line_number"] - 5),
            end_line=match["line_number"] + 10,
        )

        read_data = json.loads(read_result)
        assert "async def" in read_data["content"]

    @pytest.mark.asyncio
    async def test_patch_workflow_content(self, admin_context, db_session, sample_workflow):
        """Should patch workflow content and persist changes."""
        import json

        # Get original content
        original = await get_content(
            context=admin_context,
            entity_type="workflow",
            path=sample_workflow.path,
        )
        original_data = json.loads(original)

        # Patch a unique string
        patch_result = await patch_content(
            context=admin_context,
            entity_type="workflow",
            path=sample_workflow.path,
            old_string='return {"status": "ok"}',
            new_string='return {"status": "patched"}',
        )

        patch_data = json.loads(patch_result)
        assert patch_data["success"] is True

        # Verify the change persisted
        updated = await get_content(
            context=admin_context,
            entity_type="workflow",
            path=sample_workflow.path,
        )
        updated_data = json.loads(updated)
        assert '"patched"' in updated_data["content"]
```

**Step 2: Run integration tests**

Run: `./test.sh tests/integration/mcp/test_code_editor_tools.py -v`
Expected: Tests run (may need fixtures adjusted for your test setup)

**Step 3: Commit**

```bash
git add api/tests/integration/mcp/test_code_editor_tools.py
git commit -m "test(mcp): add integration tests for code editor tools"
```

---

## Task 10: Run Full Test Suite and Type Checking

**Step 1: Run all unit tests**

Run: `./test.sh tests/unit/services/test_code_editor_tools.py -v`
Expected: All tests pass

**Step 2: Run type checking**

Run: `cd api && pyright src/services/mcp_server/tools/code_editor.py`
Expected: No errors

**Step 3: Run linting**

Run: `cd api && ruff check src/services/mcp_server/tools/code_editor.py`
Expected: No errors (or auto-fixable only)

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "chore(mcp): fix type and lint issues in code_editor tools"
```

---

## Task 11: Update ToolCategory Enum (if needed)

**Files:**
- Modify: `api/src/services/mcp_server/tool_registry.py`

**Step 1: Check if CODE_EDITOR category exists**

Read the file and check the `ToolCategory` enum. If `CODE_EDITOR` doesn't exist, add it.

**Step 2: Add category if missing**

```python
class ToolCategory(str, Enum):
    """Categories for grouping system tools."""

    WORKFLOW = "workflow"
    FILE = "file"
    FORM = "form"
    AGENT = "agent"
    APP_BUILDER = "app_builder"
    DATA_PROVIDER = "data_provider"
    KNOWLEDGE = "knowledge"
    INTEGRATION = "integration"
    ORGANIZATION = "organization"
    CODE_EDITOR = "code_editor"  # Add this
```

**Step 3: Update tools to use CODE_EDITOR category**

Update the `category=` in all 5 tools in code_editor.py to use `ToolCategory.CODE_EDITOR`

**Step 4: Commit**

```bash
git add api/src/services/mcp_server/tool_registry.py api/src/services/mcp_server/tools/code_editor.py
git commit -m "feat(mcp): add CODE_EDITOR category for precision editing tools"
```

---

## Summary

After completing all tasks, you will have:

1. **7 new MCP tools** in `api/src/services/mcp_server/tools/code_editor.py`:
   - `list_content` - List files by entity type
   - `search_content` - Regex search with context
   - `read_content_lines` - Line range reading
   - `get_content` - Full content retrieval
   - `patch_content` - Surgical string replacement
   - `replace_content` - Full content replacement
   - `delete_content` - Delete a file

2. **Unit tests** in `api/tests/unit/services/test_code_editor_tools.py`

3. **Integration tests** in `api/tests/integration/mcp/test_code_editor_tools.py`

4. **Entity type validation** - Workflows must have `@workflow` decorator, modules must not

5. **FileStorageService integration** - Workflows/modules route through existing validation

---

# Phase 2: Update Coding Agent Configuration

## Task 12: Update Default Coding Agent System Prompt

**Files:**
- Modify: `api/src/core/system_agents.py`

**Step 1: Read the current CODING_AGENT_SYSTEM_PROMPT**

Understand the current prompt structure before modifying.

**Step 2: Update the system prompt**

Replace the file editing guidance to reference the new precision editing workflow:

```python
# In CODING_AGENT_SYSTEM_PROMPT, update the file editing section to:

"""
## Code Editing Workflow

Follow the precision editing workflow for all code changes:

1. **Search first**: Use `search_content` to find relevant code with regex patterns
   - Search for function definitions: `pattern="async def process_"`
   - Search for imports: `pattern="from bifrost import"`
   - Always specify `entity_type`: "app_file", "workflow", or "module"

2. **Read targeted lines**: Use `read_content_lines` to get context around matches
   - Read 10-20 lines around the match location
   - Understand the surrounding code before editing

3. **Make surgical edits**: Use `patch_content` for precise changes
   - The `old_string` must be unique in the file
   - Include enough context to ensure uniqueness
   - If patch fails (non-unique), add more surrounding lines to `old_string`

4. **Full replacement (fallback)**: Use `replace_content` only when:
   - Creating a new file
   - `patch_content` fails due to syntax issues
   - The file is small and full replacement is simpler

### Entity Types

- `app_file`: TSX/TypeScript files in App Builder apps (requires `app_id`)
- `workflow`: Python files with `@workflow`, `@tool`, or `@data_provider` decorators
- `module`: Python helper modules (no decorators)

### Important Notes

- Always specify `entity_type` explicitly to ground your understanding
- For workflows, optionally specify `organization_id` to scope to an org (omit for global)
- Modules are always global (no organization_id)
- Workflows MUST have a decorator; modules MUST NOT
"""
```

**Step 3: Run linting**

Run: `cd api && ruff check src/core/system_agents.py`
Expected: No errors

**Step 4: Commit**

```bash
git add api/src/core/system_agents.py
git commit -m "feat(agents): update coding agent prompt for precision editing workflow"
```

---

## Task 13: Update Default Tools List for Coding Agent

**Files:**
- Modify: `api/src/core/system_agents.py` or wherever `DEFAULT_CODING_TOOLS` is defined

**Step 1: Find where default tools are configured**

Search for where the coding agent's default tool list is defined.

**Step 2: Update the tools list**

Replace old file tools with new precision editing tools:

```python
# Remove from default tools:
# - list_app_files
# - get_app_file
# - create_app_file
# - update_app_file
# - delete_app_file
# - list_files
# - read_file
# - write_file
# - delete_file
# - search_files
# - create_folder
# - list_workflows (content listing - get_workflow stays for metadata)

# Add new precision tools:
# - list_content
# - search_content
# - read_content_lines
# - get_content
# - patch_content
# - replace_content
# - delete_content
```

**Step 3: Verify tools are registered**

Run the API and verify the new tools appear in MCP tool list.

**Step 4: Commit**

```bash
git add api/src/core/system_agents.py
git commit -m "feat(agents): replace old file tools with precision editing tools in default list"
```

---

# Phase 3: Remove Old Tools

## Task 14: Delete Old File Tools

**Files:**
- Delete or gut: `api/src/services/mcp_server/tools/app_files.py`
- Delete or gut: `api/src/services/mcp_server/tools/files.py`
- Modify: `api/src/services/mcp_server/tools/__init__.py`

**Step 1: Remove old tool functions from app_files.py**

Delete these functions entirely:
- `list_app_files`
- `get_app_file`
- `create_app_file`
- `update_app_file`
- `delete_app_file`

**Step 2: Remove old tool functions from files.py**

Delete these functions entirely:
- `list_files`
- `read_file`
- `write_file`
- `delete_file`
- `search_files`
- `create_folder`

**Step 3: Remove list_workflows from workflows.py**

Delete `list_workflows` function (replaced by `list_content` with `entity_type="workflow"`).
Keep `get_workflow` for metadata access.

**Step 4: Update __init__.py**

Remove imports for deleted modules if they're now empty.

**Step 5: Delete or update tests**

Remove tests for deleted functions in:
- `api/tests/unit/services/test_app_files.py` (or similar)
- `api/tests/unit/services/test_files.py` (or similar)

**Step 6: Run full test suite**

Run: `./test.sh -v`
Expected: All tests pass

**Step 7: Run type checking**

Run: `cd api && pyright`
Expected: No errors from missing imports

**Step 8: Commit**

```bash
git add -A
git commit -m "feat(mcp): remove old file tools, replaced by precision editing tools"
```

---

# Phase 4: Remove Claude Agent SDK

## Task 15: Research Claude Agent SDK Usage

**Files to examine:**
- `api/src/services/agent_sdk/` or similar directory
- `api/src/services/sdk_generator.py` or similar
- `docker-compose.*.yml` for SDK container references
- Client UI code for SDK checkbox/toggle

**Step 1: Find all SDK-related code**

Search for:
- "claude_agent" or "claude-agent"
- "sdk_generator" or "SDKGenerator"
- "agent_sdk" or "AgentSDK"
- Container definitions for SDK

**Step 2: Document what needs removal**

Create a list of:
- Files to delete
- Docker services to remove
- UI components to remove
- Database columns/tables (if any)
- API endpoints to remove

**Step 3: Commit documentation**

```bash
git add docs/plans/
git commit -m "docs: document Claude Agent SDK removal scope"
```

---

## Task 16: Remove SDK Generator Service

**Files:**
- Delete: `api/src/services/sdk_generator.py` (or wherever located)
- Modify: Any files that import from sdk_generator

**Step 1: Delete the SDK generator module**

Remove the file entirely.

**Step 2: Remove imports**

Find and remove all imports of sdk_generator functions.

**Step 3: Remove API endpoints**

Delete any endpoints that serve SDK generation (e.g., `/api/generate-sdk`).

**Step 4: Run tests**

Run: `./test.sh -v`
Expected: Tests pass (delete SDK-specific tests)

**Step 5: Commit**

```bash
git add -A
git commit -m "feat: remove Claude Agent SDK generator"
```

---

## Task 17: Remove SDK Container from Docker Compose

**Files:**
- Modify: `docker-compose.yml`
- Modify: `docker-compose.dev.yml`
- Modify: `docker-compose.prod.yml` (if exists)

**Step 1: Remove SDK service definition**

Delete the SDK container service block.

**Step 2: Remove volume mounts**

Remove any volumes specific to SDK.

**Step 3: Remove network references**

Clean up any network aliases for SDK.

**Step 4: Test docker-compose**

Run: `docker-compose config`
Expected: Valid configuration with no SDK references

**Step 5: Commit**

```bash
git add docker-compose*.yml
git commit -m "infra: remove Claude Agent SDK container"
```

---

## Task 18: Remove SDK UI Components

**Files:**
- Client code for SDK checkbox/toggle in agent configuration
- Client code for SDK download/generation buttons

**Step 1: Find SDK UI components**

Search client code for:
- "sdk" checkbox or toggle
- "generate sdk" buttons
- SDK-related configuration forms

**Step 2: Remove UI components**

Delete the components and their imports.

**Step 3: Remove related hooks/stores**

Clean up any React hooks or Zustand store slices for SDK state.

**Step 4: Run frontend checks**

Run: `cd client && npm run tsc && npm run lint`
Expected: No errors

**Step 5: Commit**

```bash
git add client/
git commit -m "feat(ui): remove Claude Agent SDK configuration components"
```

---

# Phase 5: Update External Documentation

## Task 19: Update bifrost-integrations-docs AI Coding Guide

**Files:**
- Modify: `bifrost-integrations-docs/src/content/docs/how-to-guides/local-dev/ai-coding.md`
- Potentially other docs files referencing AI/MCP tools

**Step 1: Read current documentation**

Understand the current structure and content.

**Step 2: Update the MCP tools section**

Replace the old tools documentation with the new precision editing tools:

```markdown
## Available MCP Tools

### Code Editing Tools

These tools follow a precision editing workflow for efficient code changes:

#### search_content
Search for patterns in code files using regex.

**Parameters:**
- `pattern` (required): Regex pattern to search for
- `entity_type` (required): "app_file", "workflow", or "module"
- `app_id`: Required for app_file entity type
- `path`: Filter to specific file path
- `organization_id`: For workflow, limit to organization scope
- `context_lines`: Lines of context around matches (default: 3)
- `max_results`: Maximum matches to return (default: 20)

**Example:**
```json
{
  "pattern": "async def sync_",
  "entity_type": "workflow"
}
```

#### read_content_lines
Read specific line range from a file.

**Parameters:**
- `entity_type` (required): "app_file", "workflow", or "module"
- `path` (required): File path
- `app_id`: Required for app_file
- `start_line`: First line to read (default: 1)
- `end_line`: Last line to read (default: start_line + 100)

#### get_content
Get entire file content. Prefer search_content + read_content_lines for large files.

**Parameters:**
- `entity_type` (required): "app_file", "workflow", or "module"
- `path` (required): File path
- `app_id`: Required for app_file

#### patch_content
Make surgical edits by replacing a unique string.

**Parameters:**
- `entity_type` (required): "app_file", "workflow", or "module"
- `path` (required): File path
- `old_string` (required): Exact string to replace (must be unique)
- `new_string` (required): Replacement string
- `app_id`: Required for app_file

**Important:** The `old_string` must appear exactly once in the file. Include enough surrounding context to ensure uniqueness.

#### replace_content
Replace entire file content or create a new file.

**Parameters:**
- `entity_type` (required): "app_file", "workflow", or "module"
- `path` (required): File path
- `content` (required): New file content
- `app_id`: Required for app_file
- `organization_id`: For workflow, set organization scope

### Recommended Workflow

1. **Search** - Use `search_content` to find the code you need to modify
2. **Read** - Use `read_content_lines` to get context around matches
3. **Edit** - Use `patch_content` for surgical changes
4. **Fallback** - Use `replace_content` only when patch fails or creating new files
```

**Step 3: Remove SDK documentation**

Delete any sections about Claude Agent SDK setup/usage.

**Step 4: Update the example prompts**

Update the example Claude prompts to reference the new tools.

**Step 5: Build docs locally to verify**

Run: `cd bifrost-integrations-docs && npm run build`
Expected: Build succeeds with no errors

**Step 6: Commit**

```bash
cd bifrost-integrations-docs
git add src/content/docs/
git commit -m "docs: update AI coding guide for precision editing tools"
```

---

## Task 20: Update Any Other Documentation References

**Files:**
- Search across bifrost-integrations-docs for references to old tools
- Check README files

**Step 1: Search for old tool references**

```bash
grep -r "get_app_file\|update_app_file\|write_file\|read_file" bifrost-integrations-docs/
```

**Step 2: Update found references**

Replace old tool names with new precision editing tools.

**Step 3: Commit**

```bash
git add -A
git commit -m "docs: update remaining references to new MCP tools"
```

---

# Summary

## Phase 1: Implement New Tools (Tasks 1-11)
- Create `code_editor.py` with 7 precision editing tools
- Add unit and integration tests
- Add CODE_EDITOR category

## Phase 2: Update Coding Agent (Tasks 12-13)
- Update system prompt for precision editing workflow
- Replace default tools list

## Phase 3: Remove Old Tools (Task 14)
- Delete old file tools from `app_files.py` and `files.py`
- Delete `list_workflows` from workflows.py
- Remove related tests
- Clean up imports

## Phase 4: Remove Claude Agent SDK (Tasks 15-18)
- Research usage
- Remove generator service
- Remove Docker container
- Remove UI components

## Phase 5: Update Documentation (Tasks 19-20)
- Update bifrost-integrations-docs AI coding guide
- Update remaining documentation references
