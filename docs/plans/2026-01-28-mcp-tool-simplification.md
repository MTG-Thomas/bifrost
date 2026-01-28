# MCP Tool Simplification Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Simplify MCP tool registration by removing custom decorator/registry layers and using FastMCP directly with a minimal context-injection wrapper.

**Architecture:** Delete `tool_decorator.py` and `tool_registry.py` (~200 lines). Keep a ~30 line `register_tool_with_context()` helper that injects auth context at runtime. FastMCP becomes the source of truth for tool metadata. Access control (agent→role→tool) unchanged - only the metadata source changes.

**Tech Stack:** FastMCP, Python, Pydantic

---

## Task 1: Update tool_result.py to use FastMCP's ToolResult

**Files:**
- Modify: `api/src/services/mcp_server/tool_result.py`
- Test: `api/tests/unit/services/mcp_server/test_tool_result.py`

**Step 1: Update imports and success_result function**

Replace the imports and `success_result` function:

```python
# api/src/services/mcp_server/tool_result.py
"""
MCP Tool Result Helpers

Helpers for building FastMCP ToolResult objects with proper
content (human-readable) and structured_content (machine-parseable).
"""

from typing import Any

from fastmcp.tools.tool import ToolResult


def success_result(display_text: str, data: dict[str, Any] | None = None) -> ToolResult:
    """
    Create a successful tool result with display text and structured data.

    Args:
        display_text: Human-readable text for display in CLI/UI
        data: Optional structured data dict for LLM parsing

    Returns:
        ToolResult with content and structured_content per MCP spec.
    """
    return ToolResult(
        content=display_text,
        structured_content=data,
    )
```

**Step 2: Update error_result function**

```python
def error_result(error_message: str, extra_data: dict[str, Any] | None = None) -> ToolResult:
    """
    Create an error tool result.

    Args:
        error_message: Human-readable error description
        extra_data: Optional additional data to include in structured_content

    Returns:
        ToolResult with error information.
    """
    display_text = f"Error: {error_message}"
    data = {"error": error_message}
    if extra_data:
        data.update(extra_data)

    return ToolResult(
        content=display_text,
        structured_content=data,
    )
```

**Step 3: Keep helper functions unchanged**

The `format_grep_matches`, `format_diff`, and `format_file_content` functions stay the same (they return strings, not ToolResult).

**Step 4: Run tests**

Run: `./test.sh tests/unit/services/mcp_server/test_tool_result.py -v`
Expected: Some failures (tests reference old CallToolResult)

**Step 5: Update test file**

```python
# api/tests/unit/services/mcp_server/test_tool_result.py
"""Tests for MCP tool result helpers."""

import pytest
from fastmcp.tools.tool import ToolResult

from src.services.mcp_server.tool_result import (
    error_result,
    format_diff,
    format_file_content,
    format_grep_matches,
    success_result,
)


class TestSuccessResult:
    def test_creates_tool_result_with_text(self):
        result = success_result("Hello world", None)
        assert isinstance(result, ToolResult)
        assert result.structured_content is None

    def test_creates_tool_result_with_data(self):
        result = success_result("Found items", {"count": 5, "items": ["a", "b"]})
        assert isinstance(result, ToolResult)
        assert result.structured_content == {"count": 5, "items": ["a", "b"]}


class TestErrorResult:
    def test_creates_error_result(self):
        result = error_result("Something went wrong")
        assert isinstance(result, ToolResult)
        assert result.structured_content["error"] == "Something went wrong"

    def test_includes_extra_data(self):
        result = error_result("Failed", {"code": 500})
        assert result.structured_content["error"] == "Failed"
        assert result.structured_content["code"] == 500


class TestFormatGrepMatches:
    def test_formats_matches(self):
        matches = [
            {"path": "file.py", "line_number": 10, "match": "def foo():"},
        ]
        result = format_grep_matches(matches, "def")
        assert "Found 1 match for 'def'" in result
        assert "file.py:10: def foo():" in result

    def test_handles_empty_matches(self):
        result = format_grep_matches([], "pattern")
        assert "No matches found" in result


class TestFormatDiff:
    def test_formats_diff(self):
        result = format_diff("file.py", ["old line"], ["new line"])
        assert "Updated file.py" in result
        assert "-  old line" in result
        assert "+  new line" in result


class TestFormatFileContent:
    def test_formats_with_line_numbers(self):
        result = format_file_content("file.py", "line1\nline2")
        assert "file.py" in result
        assert "1: line1" in result
        assert "2: line2" in result
```

**Step 6: Run tests again**

Run: `./test.sh tests/unit/services/mcp_server/test_tool_result.py -v`
Expected: All pass

**Step 7: Commit**

```bash
git add api/src/services/mcp_server/tool_result.py api/tests/unit/services/mcp_server/test_tool_result.py
git commit -m "refactor: use FastMCP ToolResult in tool_result helpers"
```

---

## Task 2: Simplify fastmcp_generator.py to minimal wrapper

**Files:**
- Modify: `api/src/services/mcp_server/generators/fastmcp_generator.py`

**Step 1: Replace entire file with minimal context wrapper**

```python
# api/src/services/mcp_server/generators/fastmcp_generator.py
"""
FastMCP Tool Registration

Minimal helper for registering tools with context injection.
"""

import inspect
import logging
from typing import TYPE_CHECKING, Any, Callable

from fastmcp.tools.tool import ToolResult

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)


def register_tool_with_context(
    mcp: "FastMCP",
    func: Callable[..., Any],
    name: str,
    description: str,
    get_context_fn: Callable[[], Any],
) -> None:
    """
    Register a tool with automatic context injection.

    The tool function should have `context` as its first parameter.
    This wrapper removes that parameter from the FastMCP-visible signature
    and injects the context at runtime from get_context_fn().

    Args:
        mcp: FastMCP server instance
        func: Tool function with context as first param
        name: Tool name for MCP
        description: Tool description for LLM
        get_context_fn: Function to get context at runtime
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.items())

    # Skip first param (context) for FastMCP's signature
    impl_params = params[1:] if params else []

    async def wrapper(**kwargs: Any) -> ToolResult:
        ctx = get_context_fn()
        return await func(ctx, **kwargs)

    # Set function metadata for FastMCP
    wrapper.__name__ = name
    wrapper.__qualname__ = name
    wrapper.__doc__ = description

    # Build signature without context param
    new_params = [param for _, param in impl_params]
    wrapper.__signature__ = sig.replace(parameters=new_params)  # type: ignore[attr-defined]

    # Copy annotations (except context)
    annotations: dict[str, Any] = {}
    for param_name, param in impl_params:
        if param.annotation != inspect.Parameter.empty:
            annotations[param_name] = param.annotation
    annotations["return"] = ToolResult
    wrapper.__annotations__ = annotations

    # Register with FastMCP
    mcp.tool(name=name, description=description)(wrapper)
    logger.debug(f"Registered tool: {name}")
```

**Step 2: Run type check**

Run: `cd api && pyright src/services/mcp_server/generators/fastmcp_generator.py`
Expected: Pass

**Step 3: Commit**

```bash
git add api/src/services/mcp_server/generators/fastmcp_generator.py
git commit -m "refactor: simplify fastmcp_generator to minimal context wrapper"
```

---

## Task 3: Update one tool file as template (code_editor.py)

**Files:**
- Modify: `api/src/services/mcp_server/tools/code_editor.py`

**Step 1: Update imports**

Remove old imports, add new ones:

```python
# Remove these:
# from mcp.types import CallToolResult, TextContent
# from src.services.mcp_server.tool_decorator import system_tool
# from src.services.mcp_server.tool_registry import ToolCategory

# Add this:
from fastmcp.tools.tool import ToolResult
```

**Step 2: Update return type annotations**

Change all `-> CallToolResult:` to `-> ToolResult:`

**Step 3: Remove @system_tool decorators**

For each tool function, remove the decorator but keep the function. Example for `list_content`:

```python
# Before:
@system_tool(
    id="list_content",
    name="List Content",
    description="List files by entity type...",
    category=ToolCategory.CODE_EDITOR,
    default_enabled_for_coding_agent=True,
    input_schema={...},
)
async def list_content(context: Any, ...) -> CallToolResult:

# After:
async def list_content(context: Any, ...) -> ToolResult:
```

**Step 4: Add registration function at end of file**

```python
# At the end of code_editor.py

# Tool metadata for registration
TOOLS = [
    ("list_content", "List Content", "List files by entity type. Returns paths without content."),
    ("search_content", "Search Content", "Search for patterns in code files. Returns matching lines with context."),
    ("read_content_lines", "Read Content Lines", "Read specific line range from a file."),
    ("get_content", "Get Content", "Get entire file content."),
    ("patch_content", "Patch Content", "Surgical edit: replace old_string with new_string."),
    ("replace_content", "Replace Content", "Replace entire file content or create new file."),
    ("delete_content", "Delete Content", "Delete a file."),
]


def register_tools(mcp: Any, get_context_fn: Any) -> None:
    """Register all code editor tools with FastMCP."""
    from src.services.mcp_server.generators.fastmcp_generator import register_tool_with_context

    tool_funcs = {
        "list_content": list_content,
        "search_content": search_content,
        "read_content_lines": read_content_lines,
        "get_content": get_content,
        "patch_content": patch_content,
        "replace_content": replace_content,
        "delete_content": delete_content,
    }

    for tool_id, name, description in TOOLS:
        register_tool_with_context(mcp, tool_funcs[tool_id], tool_id, description, get_context_fn)
```

**Step 5: Run type check**

Run: `cd api && pyright src/services/mcp_server/tools/code_editor.py`
Expected: Pass (or only unrelated issues)

**Step 6: Commit**

```bash
git add api/src/services/mcp_server/tools/code_editor.py
git commit -m "refactor: update code_editor.py to use direct FastMCP registration"
```

---

## Task 4: Update remaining tool files

**Files:**
- Modify: `api/src/services/mcp_server/tools/workflow.py`
- Modify: `api/src/services/mcp_server/tools/execution.py`
- Modify: `api/src/services/mcp_server/tools/apps.py`
- Modify: `api/src/services/mcp_server/tools/forms.py`
- Modify: `api/src/services/mcp_server/tools/tables.py`
- Modify: `api/src/services/mcp_server/tools/agents.py`
- Modify: `api/src/services/mcp_server/tools/organizations.py`
- Modify: `api/src/services/mcp_server/tools/knowledge.py`
- Modify: `api/src/services/mcp_server/tools/integrations.py`
- Modify: `api/src/services/mcp_server/tools/data_providers.py`
- Modify: `api/src/services/mcp_server/tools/sdk.py`

**Step 1: For each file, apply the same pattern as Task 3**

1. Update imports (remove old, add `ToolResult`)
2. Change return types from `CallToolResult` to `ToolResult`
3. Remove `@system_tool` decorators
4. Add `TOOLS` list and `register_tools()` function

**Step 2: Run type check on all**

Run: `cd api && pyright src/services/mcp_server/tools/`
Expected: Pass

**Step 3: Commit**

```bash
git add api/src/services/mcp_server/tools/
git commit -m "refactor: update all tool files to use direct FastMCP registration"
```

---

## Task 5: Update tools __init__.py to export register functions

**Files:**
- Modify: `api/src/services/mcp_server/tools/__init__.py`

**Step 1: Update to export register functions**

```python
# api/src/services/mcp_server/tools/__init__.py
"""
MCP System Tools

Each module provides a register_tools(mcp, get_context_fn) function.
"""

from src.services.mcp_server.tools import (
    agents,
    apps,
    code_editor,
    data_providers,
    execution,
    forms,
    integrations,
    knowledge,
    organizations,
    sdk,
    tables,
    workflow,
)

TOOL_MODULES = [
    agents,
    apps,
    code_editor,
    data_providers,
    execution,
    forms,
    integrations,
    knowledge,
    organizations,
    sdk,
    tables,
    workflow,
]


def register_all_tools(mcp, get_context_fn) -> None:
    """Register all system tools with FastMCP."""
    for module in TOOL_MODULES:
        module.register_tools(mcp, get_context_fn)
```

**Step 2: Commit**

```bash
git add api/src/services/mcp_server/tools/__init__.py
git commit -m "refactor: add register_all_tools to tools __init__"
```

---

## Task 6: Update server.py to use new registration

**Files:**
- Modify: `api/src/services/mcp_server/server.py`

**Step 1: Remove old imports**

```python
# Remove:
# from src.services.mcp_server.generators import register_fastmcp_tools
# from src.services.mcp_server.tool_registry import get_all_tool_ids
```

**Step 2: Add new import**

```python
from src.services.mcp_server.tools import register_all_tools
```

**Step 3: Update get_fastmcp_server method**

Replace `register_fastmcp_tools(...)` call with:

```python
register_all_tools(mcp, get_context_fn)
```

**Step 4: Add get_system_tools function**

```python
def get_system_tools() -> list[dict[str, str]]:
    """
    Get system tool metadata from FastMCP.

    Returns list of dicts with id, name, description for each tool.
    Used by /api/tools endpoint.
    """
    # This will be called after tools are registered
    # We need to access the global FastMCP instance
    # For now, return from tool modules directly
    from src.services.mcp_server.tools import TOOL_MODULES

    tools = []
    for module in TOOL_MODULES:
        if hasattr(module, "TOOLS"):
            for tool_id, name, description in module.TOOLS:
                tools.append({
                    "id": tool_id,
                    "name": name,
                    "description": description,
                })
    return tools
```

**Step 5: Commit**

```bash
git add api/src/services/mcp_server/server.py
git commit -m "refactor: update server.py to use register_all_tools"
```

---

## Task 7: Update /api/tools endpoint

**Files:**
- Modify: `api/src/routers/tools.py`

**Step 1: Update imports**

```python
# Remove:
# from src.services.mcp_server.tool_registry import get_all_system_tools, get_all_tool_ids

# Add:
from src.services.mcp_server.server import get_system_tools
```

**Step 2: Update get_system_tools function**

```python
def get_system_tools_for_api() -> list[ToolInfo]:
    """Get system tools for API response."""
    from src.services.mcp_server.server import get_system_tools

    return [
        ToolInfo(
            id=tool["id"],
            name=tool["name"],
            description=tool["description"],
            type="system",
        )
        for tool in get_system_tools()
    ]
```

**Step 3: Update SYSTEM_TOOLS lazy list**

Replace `get_system_tools()` calls with `get_system_tools_for_api()`.

**Step 4: Commit**

```bash
git add api/src/routers/tools.py
git commit -m "refactor: update /api/tools to use FastMCP tool metadata"
```

---

## Task 8: Update tool_access.py

**Files:**
- Modify: `api/src/services/mcp_server/tool_access.py`

**Step 1: Update imports**

```python
# Remove:
# from src.routers.tools import SYSTEM_TOOLS

# Add:
from src.services.mcp_server.server import get_system_tools
```

**Step 2: Update _SYSTEM_TOOL_MAP**

```python
# Change from class attribute to computed property or function
def _get_system_tool_map() -> dict[str, ToolInfo]:
    return {tool["id"]: ToolInfo(
        id=tool["id"],
        name=tool["name"],
        description=tool["description"],
        type="system",
    ) for tool in get_system_tools()}
```

**Step 3: Remove is_restricted check (lines 91-95)**

```python
# Remove these lines:
# if tool_info.is_restricted and not is_superuser:
#     logger.debug(...)
#     continue
```

**Step 4: Commit**

```bash
git add api/src/services/mcp_server/tool_access.py
git commit -m "refactor: update tool_access.py, remove is_restricted check"
```

---

## Task 9: Remove is_restricted from middleware

**Files:**
- Modify: `api/src/services/mcp_server/middleware.py`

**Step 1: Remove is_restricted check (lines 124-135)**

```python
# Remove these lines:
# tool_metadata = get_system_tool(tool_name)
# if tool_metadata and tool_metadata.is_restricted and not is_superuser:
#     logger.warning(...)
#     raise ToolError(...)
```

**Step 2: Remove unused import**

```python
# Remove:
# from src.services.mcp_server.tool_registry import get_system_tool
```

**Step 3: Commit**

```bash
git add api/src/services/mcp_server/middleware.py
git commit -m "refactor: remove is_restricted check from middleware"
```

---

## Task 10: Remove is_restricted from ToolInfo

**Files:**
- Modify: `api/src/models/contracts/agents.py`

**Step 1: Remove is_restricted field from ToolInfo**

Find the `ToolInfo` class and remove the `is_restricted` field.

**Step 2: Commit**

```bash
git add api/src/models/contracts/agents.py
git commit -m "refactor: remove is_restricted from ToolInfo"
```

---

## Task 11: Delete obsolete files

**Files:**
- Delete: `api/src/services/mcp_server/tool_decorator.py`
- Delete: `api/src/services/mcp_server/tool_registry.py`

**Step 1: Delete files**

```bash
rm api/src/services/mcp_server/tool_decorator.py
rm api/src/services/mcp_server/tool_registry.py
```

**Step 2: Remove any remaining imports of deleted files**

Search for and remove:
- `from src.services.mcp_server.tool_decorator import`
- `from src.services.mcp_server.tool_registry import`

**Step 3: Commit**

```bash
git add -A
git commit -m "refactor: delete tool_decorator.py and tool_registry.py"
```

---

## Task 12: Update tests

**Files:**
- Modify: `api/tests/unit/services/mcp_server/test_tool_registry.py` (delete or update)
- Modify: `api/tests/unit/services/mcp_server/test_tool_decorator.py` (delete)
- Modify: `api/tests/unit/services/mcp_server/test_middleware.py`

**Step 1: Delete obsolete test files**

```bash
rm api/tests/unit/services/mcp_server/test_tool_registry.py
rm api/tests/unit/services/mcp_server/test_tool_decorator.py
```

**Step 2: Update middleware tests**

Remove tests for `is_restricted` behavior.

**Step 3: Run all MCP tests**

Run: `./test.sh tests/unit/services/mcp_server/ -v`
Expected: All pass

**Step 4: Commit**

```bash
git add -A
git commit -m "test: update tests for simplified MCP tool structure"
```

---

## Task 13: Run full verification

**Step 1: Type check**

Run: `cd api && pyright src/services/mcp_server/`
Expected: Zero errors

**Step 2: Lint**

Run: `cd api && ruff check src/services/mcp_server/`
Expected: Zero errors

**Step 3: Unit tests**

Run: `./test.sh tests/unit/services/mcp_server/ -v`
Expected: All pass

**Step 4: Integration tests**

Run: `./test.sh tests/integration/ -v`
Expected: All pass

**Step 5: Manual MCP test**

1. Start dev stack: `./debug.sh`
2. In Claude Code, call: `mcp__bifrost__search_content` with pattern "halopsa"
3. Verify output is plain text (not JSON blob)

**Step 6: Verify /api/tools endpoint**

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:3000/api/tools
```
Expected: Returns list of tools with id, name, description

**Step 7: Final commit**

```bash
git add -A
git commit -m "chore: MCP tool simplification complete"
```

---

## Summary

| Deleted | Lines |
|---------|-------|
| `tool_decorator.py` | ~100 |
| `tool_registry.py` | ~100 |
| `ToolCategory` enum | ~15 |
| `is_restricted` checks | ~20 |
| `input_schema` on decorators | ~500 |

| Added | Lines |
|-------|-------|
| `register_tool_with_context()` | ~40 |
| `TOOLS` lists in each file | ~100 |

**Net result:** ~600 lines deleted, ~140 lines added.

**Access control unchanged:** Agents still have `system_tools = ["search_content", ...]`, roles still filter agent access, middleware still filters tool access.
