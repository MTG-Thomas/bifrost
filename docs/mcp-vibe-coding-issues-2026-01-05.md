# MCP Vibe Coding Issues - CRM POC Session (2026-01-05)

This document summarizes issues encountered while building a CRM application using only Bifrost MCP tools, simulating an end-user "vibe coding" experience.

---

## Issues Encountered

### 1. `list_workflows` MCP Tool - Attribute Error

**Error:** `'Workflow' object has no attribute 'is_tool'`

**Root Cause:** The MCP server code referenced `workflow.is_tool` but the Workflow ORM model uses a `type` field with values `'workflow'`, `'tool'`, or `'data_provider'`.

**Fix:** Changed `workflow.is_tool` to `workflow.type == "tool"` in two places in `server.py` (lines 204 and 2459).

**Instruction Update Needed:** None - this was a code bug, not a documentation issue.

---

### 2. Tables SDK Using Wrong Endpoints

**Error:** `404 Not Found` for `/api/cli/tables/documents/query`

**Root Cause:** The `bifrost/tables.py` SDK was using CLI-specific endpoints with body-based table names (e.g., `POST /api/cli/tables/documents/query` with `{"table": "clients"}`), but these endpoints don't exist. The actual tables router uses REST-style path-based endpoints at `/api/tables/{name}/documents/*`.

**Fix:** Rewrote `bifrost/tables.py` to use the REST endpoints:
- `POST /api/tables/{table}/documents` (insert)
- `GET /api/tables/{table}/documents/{id}` (get)
- `PATCH /api/tables/{table}/documents/{id}` (update)
- `DELETE /api/tables/{table}/documents/{id}` (delete)
- `POST /api/tables/{table}/documents/query` (query)
- `GET /api/tables/{table}/documents/count` (count)

**Instruction Update Needed:** The SDK documentation should specify that tables use REST-style endpoints, not body-based routing.

---

### 3. Tables Don't Auto-Create

**Error:** `404 Not Found` when inserting/querying a table that doesn't exist

**Root Cause:** The SDK docstrings claimed "Auto-creates the table if it doesn't exist" but the router's `insert_document` and `query_documents` endpoints used `get_table_or_404()` which throws 404 if the table doesn't exist.

**Fix:** Added `get_or_create_table()` helper and updated `insert_document`, `query_documents`, and `count_documents` endpoints to auto-create tables on first use.

**Instruction Update Needed:** This is now working as documented. No change needed.

---

### 4. Workflow Code - `isoformat()` on String

**Error:** `'str' object has no attribute 'isoformat'`

**Root Cause:** The workflow code I wrote assumed `doc.created_at` was a `datetime` object and called `.isoformat()` on it. However, the SDK's `DocumentData` model has `created_at: str | None` because the API returns JSON-serialized strings.

**Fix:** Removed `.isoformat()` calls from workflow code - the timestamps are already strings.

**Instruction Update Needed:** The `DocumentData` model documentation should clarify that timestamp fields are ISO-format strings, not datetime objects. Example:
```python
# Correct - timestamps are already strings
client["created_at"] = doc.created_at

# Incorrect - will fail
client["created_at"] = doc.created_at.isoformat()
```

---

### 5. `list_data_providers` MCP Tool - Attribute Error

**Error:** `'Workflow' object has no attribute 'file_path'`

**Root Cause:** Data providers were consolidated into the workflows table, but the `list_data_providers` MCP tool still referenced `provider.file_path` instead of `provider.path`.

**Fix:** Changed `provider.file_path` to `provider.path` in the MCP server.

**Instruction Update Needed:** The `list_data_providers` tool is redundant now that data providers are workflows with `type='data_provider'`. Consider removing it entirely and documenting that `list_workflows` shows all entity types (workflows, tools, data providers).

---

### 6. `list_files` MCP Tool Returns Empty

**Error:** "No files found" even though workflows exist

**Root Cause:** The `list_files` MCP tool uses `file_operations.list_directory()` which reads from the filesystem at `/tmp/bifrost/workspace`. However, platform entities (workflows, forms, apps, agents) are stored in the database, not the filesystem. The files API has been updated to merge platform entities, but the MCP tool wasn't updated.

**Current Status:** Not fixed - but determined to be out of scope for this POC since:
1. Workflows show up correctly in `list_workflows`
2. Platform entities are database-first, not filesystem-first
3. For vibe coding, we care about workflows/apps working, not filesystem listing

**Instruction Update Needed:** Document that `list_files` only shows filesystem files, not platform entities. For workflows/forms/apps, use their respective list tools (`list_workflows`, `list_forms`, `list_apps`).

---

### 7. `create_app` MCP Tool Not Registered

**Error:** Tool not available (silently missing from tool list)

**Root Cause:** The `_create_app_impl()` function existed but was never registered as an MCP tool. The tool list at line 4872 included `"create_app"` but no registration code existed.

**Fix:** Added tool registration for both SDK-style and FastMCP-style MCP servers.

**Instruction Update Needed:** When adding new MCP tools, ensure they are registered in BOTH:
1. The SDK-style registration block (~line 3468)
2. The FastMCP-style registration block (~line 4532)

---

## Recommendations for Documentation/Instructions

### 1. SDK Type Documentation
Add clear documentation that API responses return JSON-serialized types:
- Timestamps are ISO-format strings, not datetime objects
- UUIDs are strings, not UUID objects
- All nested objects are dicts, not Pydantic models

### 2. Entity Type Consolidation
Document that the platform has consolidated entity types:
- Workflows, tools, and data providers are all in the `workflows` table with `type` discriminator
- Use `list_workflows` to see all types
- Dedicated tools like `list_data_providers` are deprecated/redundant

### 3. Platform vs Filesystem
Clarify the distinction:
- **Platform entities** (workflows, forms, apps, agents): Stored in database, managed via dedicated MCP tools
- **Filesystem files**: Regular files in workspace, managed via `read_file`, `write_file`, `list_files`
- The `list_files` tool does NOT show platform entities

### 4. Tables Auto-Creation
Document that tables are auto-created on first insert/query - no need to explicitly create tables before using them.

---

## Summary

| Issue | Type | Fixed? |
|-------|------|--------|
| `is_tool` attribute error | Code bug | ✅ |
| Wrong SDK endpoints | Code bug | ✅ |
| Tables don't auto-create | Code bug | ✅ |
| `isoformat()` on string | Workflow code error | ✅ |
| `file_path` attribute error | Code bug | ✅ |
| `list_files` empty | Design issue | ⏭️ Skipped |
| `create_app` not registered | Code bug | ✅ |

Most issues were code bugs where the MCP layer wasn't aligned with the underlying API/ORM changes. The consolidation of data providers into workflows and the shift to database-first platform entities created mismatches in the MCP tools.
