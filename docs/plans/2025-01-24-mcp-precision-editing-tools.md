# MCP Precision Editing Tools

**Date:** 2025-01-24
**Status:** Draft

## Problem

Current MCP tools pass entire file contents on every read/write. For large files (4MB modules, complex apps), this:
- Wastes tokens (round-tripping full content for small changes)
- Slows down AI interactions
- Makes surgical edits impossible

Claude Code solves this with grep → read range → surgical edit, but that pattern doesn't exist for database-stored code in Bifrost.

## Solution

Five generic tools that work across all code entities, mirroring Claude Code's precision editing capabilities.

### New Tools

| Tool | Purpose | When to Use |
|------|---------|-------------|
| `search_content` | Regex search with context lines | Finding functions, imports, usages |
| `read_content_lines` | Read specific line range | Understanding context around a match |
| `patch_content` | Surgical old→new replacement | Making targeted edits |
| `get_content` | Get entire content | Small files, or need complete picture |
| `replace_content` | Replace entire content | When patch fails (syntax corruption), or creating new files |

### Supported Entity Types

| Entity Type | Table | Content Column | Identifiers |
|-------------|-------|----------------|-------------|
| `app_file` | `app_files` | `source` | `app_id` + `path` |
| `workflow` | `workflows` | `code` | `path` (+ optional `organization_id`) |
| `module` | `workspace_files` | `content` | `path` (global only, no org scoping) |

### Design Principles

1. **Explicit entity_type** - AI must declare what it's writing (workflow, module, app_file). This grounds the AI's understanding and catches mismatches.

2. **Path is always required** - Paths are organizational for workflows/modules and structural for app_files. Users think in terms of paths.

3. **Organization scoping** - Workflows can belong to an organization (optional `organization_id`). Modules are global (no org scoping). App files inherit scope from their parent app.

4. **Validation on write** - Workflows/modules route through `FileStorageService` for syntax validation. If declared `entity_type` doesn't match detected type (e.g., says "module" but has `@workflow` decorator), return an error.

5. **Precision by default** - Prefer `patch_content` for edits. Use `replace_content` only for new files or when patch fails.

## Tool Schemas

### search_content

Search across database-stored code using regex patterns. Returns matches with line numbers and surrounding context.

```json
{
  "id": "search_content",
  "name": "Search Content",
  "description": "Search for patterns in code files. Returns matching lines with context. Use to find functions, imports, or usages before making edits.",
  "category": "CODE_EDITOR",
  "input_schema": {
    "type": "object",
    "properties": {
      "pattern": {
        "type": "string",
        "description": "Regex pattern to search for (e.g., 'def get_.*agent', 'useWorkflow')"
      },
      "entity_type": {
        "type": "string",
        "enum": ["app_file", "workflow", "module"],
        "description": "Type of entity to search"
      },
      "app_id": {
        "type": "string",
        "description": "For app_file: the app UUID (required). For workflow/module: optional filter."
      },
      "path": {
        "type": "string",
        "description": "Filter to a specific file path (optional - searches all if omitted)"
      },
      "organization_id": {
        "type": "string",
        "description": "For workflow: limit to this organization (optional - searches accessible if omitted). Not applicable to modules."
      },
      "context_lines": {
        "type": "integer",
        "default": 3,
        "description": "Number of lines to show before and after each match"
      },
      "max_results": {
        "type": "integer",
        "default": 20,
        "description": "Maximum number of matches to return"
      }
    },
    "required": ["pattern", "entity_type"]
  }
}
```

**Example response:**
```json
{
  "matches": [
    {
      "path": "modules/halopsa.py",
      "organization_id": "org-uuid-here",
      "line_number": 45,
      "match": "async def get_agents(client_id: str) -> list[dict]:",
      "context_before": [
        "43: ",
        "44: @workflow(is_tool=True)"
      ],
      "context_after": [
        "46:     \"\"\"Get all agents for a client.\"\"\"",
        "47:     response = await integrations.halopsa.get(\"/agent\")"
      ]
    }
  ],
  "total_matches": 1,
  "truncated": false
}
```

### read_content_lines

Read a specific range of lines from a file. Use after `search_content` to get more context around a match.

```json
{
  "id": "read_content_lines",
  "name": "Read Content Lines",
  "description": "Read specific line range from a file. Use to get context around a search match without loading entire file.",
  "category": "CODE_EDITOR",
  "input_schema": {
    "type": "object",
    "properties": {
      "entity_type": {
        "type": "string",
        "enum": ["app_file", "workflow", "module"]
      },
      "app_id": {
        "type": "string",
        "description": "For app_file: the app UUID (required)"
      },
      "path": {
        "type": "string",
        "description": "File path (required)"
      },
      "organization_id": {
        "type": "string",
        "description": "For workflow: the organization UUID (optional for global). Not applicable to modules."
      },
      "start_line": {
        "type": "integer",
        "default": 1,
        "description": "First line to read (1-indexed)"
      },
      "end_line": {
        "type": "integer",
        "description": "Last line to read (defaults to start_line + 100)"
      }
    },
    "required": ["entity_type", "path"]
  }
}
```

**Example response:**
```json
{
  "path": "modules/halopsa.py",
  "organization_id": "org-uuid-here",
  "start_line": 40,
  "end_line": 60,
  "total_lines": 250,
  "content": "40: \n41: # Agent management\n42: \n43: \n44: @workflow(is_tool=True)\n45: async def get_agents(client_id: str) -> list[dict]:\n..."
}
```

### patch_content

Make surgical edits by replacing a unique string with new content. Fails if the old string is not found or matches multiple locations.

```json
{
  "id": "patch_content",
  "name": "Patch Content",
  "description": "Surgical edit: replace old_string with new_string. The old_string must be unique in the file. Include enough context to ensure uniqueness. Use replace_content if patch fails due to syntax issues.",
  "category": "CODE_EDITOR",
  "is_restricted": true,
  "input_schema": {
    "type": "object",
    "properties": {
      "entity_type": {
        "type": "string",
        "enum": ["app_file", "workflow", "module"]
      },
      "app_id": {
        "type": "string",
        "description": "For app_file: the app UUID (required)"
      },
      "path": {
        "type": "string",
        "description": "File path (required)"
      },
      "organization_id": {
        "type": "string",
        "description": "For workflow: the organization UUID (optional for global). Not applicable to modules."
      },
      "old_string": {
        "type": "string",
        "description": "Exact string to find and replace (must be unique in file)"
      },
      "new_string": {
        "type": "string",
        "description": "Replacement string"
      }
    },
    "required": ["entity_type", "path", "old_string", "new_string"]
  }
}
```

**Example success response:**
```json
{
  "success": true,
  "path": "modules/halopsa.py",
  "lines_changed": 3
}
```

**Example failure response:**
```json
{
  "success": false,
  "error": "old_string matches 3 locations. Include more context to make it unique.",
  "match_locations": [
    {"line": 45, "preview": "async def get_agents(client_id: str)..."},
    {"line": 102, "preview": "async def get_agents(ticket_id: str)..."},
    {"line": 189, "preview": "async def get_agents() -> list..."}
  ]
}
```

### get_content

Get the entire content of a file. Use for small files or when you need the complete picture.

```json
{
  "id": "get_content",
  "name": "Get Content",
  "description": "Get entire file content. Prefer search_content + read_content_lines for large files. Use this for small files or when you need the complete picture.",
  "category": "CODE_EDITOR",
  "input_schema": {
    "type": "object",
    "properties": {
      "entity_type": {
        "type": "string",
        "enum": ["app_file", "workflow", "module"]
      },
      "app_id": {
        "type": "string",
        "description": "For app_file: the app UUID (required)"
      },
      "path": {
        "type": "string",
        "description": "File path (required)"
      },
      "organization_id": {
        "type": "string",
        "description": "For workflow/module: the organization UUID (optional for global)"
      }
    },
    "required": ["entity_type", "path"]
  }
}
```

### replace_content

Replace entire file content or create new files. For workflows/modules, routes through FileStorageService for validation.

```json
{
  "id": "replace_content",
  "name": "Replace Content",
  "description": "Replace entire file content or create new file. For workflows/modules: validates syntax and confirms entity_type matches content (e.g., workflow must have @workflow decorator). Use when: (1) creating new files, (2) patch_content fails due to syntax issues, (3) file is small and full replacement is simpler. Prefer patch_content for targeted edits.",
  "category": "CODE_EDITOR",
  "is_restricted": true,
  "input_schema": {
    "type": "object",
    "properties": {
      "entity_type": {
        "type": "string",
        "enum": ["app_file", "workflow", "module"],
        "description": "Type of entity. Must match content (e.g., workflow code must have @workflow decorator)"
      },
      "app_id": {
        "type": "string",
        "description": "For app_file: the app UUID (required)"
      },
      "path": {
        "type": "string",
        "description": "File path (required)"
      },
      "organization_id": {
        "type": "string",
        "description": "For workflow: the organization UUID. Omit for global scope. Not applicable to modules."
      },
      "content": {
        "type": "string",
        "description": "New file content"
      }
    },
    "required": ["entity_type", "path", "content"]
  }
}
```

**Validation behavior for workflows/modules:**
- Routes through `FileStorageService` which parses Python and detects decorators
- If `entity_type=workflow` but no `@workflow` decorator found → error
- If `entity_type=module` but `@workflow` decorator found → error
- Syntax errors in Python code → error with details
- This gives fast feedback before content is persisted

**Example success response:**
```json
{
  "success": true,
  "path": "workflows/sync_tickets.py",
  "organization_id": "org-uuid-here",
  "entity_type": "workflow",
  "created": true
}
```

**Example validation error:**
```json
{
  "success": false,
  "error": "entity_type mismatch: declared 'module' but content contains @workflow decorator. Use entity_type='workflow' instead."
}
```

## Tools to Keep (Unchanged)

These tools remain as-is:

### Execution & Workflow Tools
- `execute_workflow` - Run workflows
- `list_workflows` - List available workflows
- `get_workflow` - Get workflow metadata (not code)
- `validate_workflow` - Validate workflow syntax

### App Management Tools
- `list_apps` - List applications
- `create_app` - Create new app with scaffold
- `get_app` - Get app metadata and file list
- `update_app` - Update app settings (name, description, navigation)
- `publish_app` - Publish draft to live

### Schema Documentation Tools
- `get_sdk_schema` - Auto-generated from SDK source
- `get_workflow_schema` - Auto-generated from Pydantic models
- `get_form_schema` - Auto-generated from Pydantic models
- `get_app_schema` - Manual documentation (acceptable, small surface area)
- `get_agent_schema` - Auto-generated from Pydantic models
- `get_table_schema` - Auto-generated from Pydantic models

### Other Tools
- `list_forms`, `get_form`, `create_form`, `update_form`
- `list_tables`, `get_table`, `create_table`, `update_table`
- `list_agents`, `get_agent`, `create_agent`, `update_agent`, `delete_agent`
- `search_knowledge`
- `list_integrations`
- `list_executions`, `get_execution`
- `list_organizations`

### Delete Tools (Keep)
- `delete_app_file` - Keep for deleting app files (no generic equivalent needed)

## Tools to Deprecate/Remove

These tools are replaced by the generic precision editing tools:

### App File Tools → Generic Tools
| Old Tool | Replacement |
|----------|-------------|
| `list_app_files` | `search_content(entity_type="app_file", app_id=X, pattern=".")` or just use `get_app` which returns file list |
| `get_app_file` | `get_content(entity_type="app_file", ...)` or `read_content_lines` |
| `create_app_file` | `replace_content(entity_type="app_file", ...)` |
| `update_app_file` | `patch_content` (preferred) or `replace_content` (fallback) |

### File Tools → Generic Tools
| Old Tool | Replacement |
|----------|-------------|
| `read_file` | `get_content(entity_type="workflow"\|"module", path=X)` |
| `write_file` | `replace_content(entity_type="workflow"\|"module", path=X, content=Y)` |
| `list_files` | `list_workflows` for workflows, or search with broad pattern |
| `delete_file` | Keep for now, or add `delete_content` generic tool |
| `search_files` | `search_content` with appropriate entity_type |
| `create_folder` | Remove (folders are implicit in paths) |

### Why Remove File Tools?
The path-based `read_file`/`write_file` tools used `FileStorageService` to auto-detect entity types. While convenient, this is problematic:
1. AI doesn't explicitly state what it thinks it's writing
2. Mismatches are silent (writes module when intending workflow)
3. No precision editing (always full content)

The new tools require explicit `entity_type` which:
1. Grounds AI understanding ("I am writing a workflow")
2. Enables validation ("workflow must have @workflow decorator")
3. Catches mismatches early with clear errors

## Implementation Plan

### Phase 1: Implement New Tools
1. Create `api/src/services/mcp_server/tools/code_editor.py`
2. Implement `search_content`:
   - Regex search across `app_files.source`, `workflows.code`, `workspace_files.content`
   - Return matches with line numbers and configurable context
   - Respect organization scoping and access control
3. Implement `read_content_lines`:
   - Line-range reads with 1-indexed lines
   - Return total line count for context
4. Implement `patch_content`:
   - Find `old_string` in content
   - Validate uniqueness (fail with locations if multiple matches)
   - Replace and persist
   - For workflows/modules: run through FileStorageService validation after patch
5. Implement `get_content`:
   - Simple full-content read
   - Include metadata (line count, entity info)
6. Implement `replace_content`:
   - For workflows/modules: route through FileStorageService for validation
   - Validate entity_type matches detected type
   - Create if not exists, update if exists
7. Add unit tests for all tools
8. Add integration tests covering:
   - Cross-entity search
   - Patch uniqueness validation
   - Entity type mismatch detection

### Phase 2: Update Default Coding Agent
1. Update default tool list:
   - Add: `search_content`, `read_content_lines`, `patch_content`, `get_content`, `replace_content`
   - Keep: `execute_workflow`, `list_workflows`, `get_workflow`, `list_apps`, `get_app`, `create_app`, `update_app`, `publish_app`, all schema tools
   - Remove from default: old file tools
2. Update agent system prompt with guidance:
   ```
   For editing code (workflows, modules, app files):
   1. Use search_content to find the code you need to modify
   2. Use read_content_lines if you need more context around a match
   3. Use patch_content for surgical edits (preferred)
   4. Use replace_content only when:
      - Creating new files
      - patch_content fails due to syntax issues
      - File is very small and full replacement is simpler
   5. Use get_content sparingly - only for small files or when you truly need the complete picture
   ```
3. Test with real coding tasks to validate the workflow

### Phase 3: Deprecation
1. Mark old tools as deprecated:
   - Add deprecation warning to tool responses
   - Log usage for monitoring
2. Update any documentation referencing old tools
3. Monitor usage metrics for 2-4 weeks
4. Remove deprecated tools:
   - `list_app_files`, `get_app_file`, `create_app_file`, `update_app_file`
   - `read_file`, `write_file`, `list_files`, `search_files`, `create_folder`
5. Keep `delete_app_file` and `delete_file` (or consolidate to `delete_content`)

### Phase 4: Remove Claude Agent SDK
1. Remove SDK-related code:
   - `api/src/services/mcp_server/generators/sdk_generator.py`
   - Any SDK container definitions
   - SDK-specific tool wrappers
2. Remove from docker-compose:
   - SDK container service
   - Related environment variables
3. Remove from UI:
   - "Use Claude Agent SDK" checkbox in agent settings
   - Any SDK-specific configuration options
4. Clean up:
   - Remove unused dependencies
   - Update documentation
   - Remove feature flags if any

## Naming Convention Cleanup

As part of this work, standardize tool naming:

**Current inconsistencies:**
- `get_app_file` vs `code_get_file` (different naming in schema docs)
- Mix of `list_*`, `get_*`, `create_*`, `update_*` patterns

**New convention:**
- New precision tools use `verb_noun` pattern: `search_content`, `patch_content`, `read_content_lines`, `get_content`, `replace_content`
- Existing entity management tools keep their names (`list_workflows`, `get_app`, `create_form`, etc.)
- Document the pattern for future tools:
  - **Entity management**: `list_X`, `get_X`, `create_X`, `update_X`, `delete_X`
  - **Content operations**: `verb_content` (search, get, read, patch, replace)

## Migration Guide

### For MCP Users

**Before (full file round-trip):**
```
1. get_app_file(app_id, path) → full 500-line file
2. [AI modifies one line]
3. update_app_file(app_id, path, full_content) → sends 500 lines back
```

**After (precision editing):**
```
1. search_content(pattern="export default", entity_type="app_file", app_id=X) → 5 matching lines with context
2. read_content_lines(entity_type="app_file", app_id=X, path=Y, start_line=45, end_line=60) → 15 lines
3. patch_content(entity_type="app_file", app_id=X, path=Y, old_string="...", new_string="...") → done
```

Token savings: ~90% reduction for typical edits on large files.

**Before (writing a workflow):**
```
1. write_file(path="workflows/sync.py", content=full_code) → FileStorageService auto-detects type
```

**After (explicit entity type):**
```
1. replace_content(entity_type="workflow", path="workflows/sync.py", organization_id=X, content=full_code)
   → Validates @workflow decorator present
   → Clear error if mismatch
```

## Open Questions

1. **Should `search_content` search across ALL accessible entities by default, or require explicit scoping?**
   - Searching all could be slow but more convenient
   - Recommendation: Require `entity_type`, optionally scope further with `app_id` or `organization_id`

2. **Should we add a `list_content` tool for browsing files without search?**
   - Current: `get_app` returns file list for apps, `list_workflows` lists workflows
   - Could add generic `list_content(entity_type, app_id?, organization_id?)` for consistency
   - Recommendation: Not needed initially - existing list tools work fine

3. **Line ending normalization?**
   - Should `patch_content` normalize line endings before matching?
   - Recommendation: Yes, normalize to `\n` internally

4. **Should `delete_content` be a generic tool?**
   - Currently keeping `delete_app_file` and `delete_file` separate
   - Could consolidate to `delete_content(entity_type, path, app_id?, organization_id?)`
   - Recommendation: Add `delete_content` for consistency, deprecate old delete tools

## Summary

**5 new tools:**
- `search_content` - Find code with regex
- `read_content_lines` - Read line ranges
- `patch_content` - Surgical edits
- `get_content` - Full content read
- `replace_content` - Full content write/create

**Works across 3 entity types:**
- `app_file` (identified by `app_id` + `path`)
- `workflow` (identified by `path` + optional `organization_id`)
- `module` (identified by `path`, global only)

**Key benefits:**
- ~90% token reduction for edits on large files
- Explicit entity types ground AI understanding
- Validation catches mismatches early
- Mirrors Claude Code's precision editing workflow
