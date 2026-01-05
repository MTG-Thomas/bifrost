# Change: Redesign Workflow Identity System

## Why

The current file watcher system has race conditions and design gaps that lead to orphaned workflow references:

1. **ID injection complexity**: IDs are injected into Python decorator parameters, creating race conditions when multiple containers process the same file
2. **No move detection**: File moves appear as delete+create, potentially creating duplicate workflow records
3. **Orphaned references**: Forms store workflow UUIDs that become stale when workflows are deleted or renamed
4. **No portability**: Forms can't be shared across environments because UUIDs are environment-specific
5. **Execution fragility**: Workflows execute from files, so deleted files break production apps

## What Changes

### **BREAKING**: DB-First Storage Model
- Platform entities (workflows, forms, apps, agents) are stored in the database as source of truth
- Regular files (modules, data, configs) remain S3-first
- The file system becomes a serialization layer for git sync only
- Editor operations route through `workspace_files.entity_type` to correct backend

### **BREAKING**: Execution from Database
- Workflows table stores the actual Python code in a new `code` column
- Execution runs via `exec()` from `workflow.code` with namespace injection
- `__file__` and `__package__` are injected to maintain expected Python behavior
- Imports from regular files work via sys.path (S3-synced to workspace)

### **BREAKING**: IDs Removed from Files
- IDs are no longer injected into decorator parameters
- IDs exist only in the database
- Files remain clean of platform-specific metadata
- Git sync serializes DB entities to files without IDs

### New: Unified File System View
- `workspace_files` table gains `entity_type` and `entity_id` columns
- `entity_type`: 'workflow', 'form', 'app', 'agent', or NULL (regular file)
- Editor reads/writes route to correct backend based on entity_type
- Provides seamless experience while maintaining proper storage separation

### New: Content Type Detection on Write
- Editor write detects file type from content and extension
- `.py` with `@workflow/@tool/@data_provider` → upserts `workflows` table
- `.form.json` → upserts `forms` table
- `.app.json` → upserts `applications` table
- `.agent.json` → upserts `agents` table
- Other files → writes to S3

### New: Redundant Workflow References in Forms
- Forms store `workflow_path` and `workflow_function_name` alongside `workflow_id`
- Enables cross-environment portability during git import
- Auto-resolves broken references by path+function lookup

### Removed: Hash-Based Duplicate Detection
- No longer needed because DB is source of truth
- File paths are just metadata, not identity
- Conflicts are prevented by database constraints

## Impact

- **Affected specs**: workflow-identity (new), file-indexing (new)
- **Affected code**:
  - `api/src/models/orm/workflows.py` - Add `code`, `code_hash`, rename `file_path` → `path`
  - `api/src/models/orm/forms.py` - Add redundant reference fields
  - `api/src/models/orm/workspace.py` - Add `entity_type`, `entity_id` columns
  - `api/src/services/file_storage_service.py` - Unified read/write routing
  - `api/src/services/execution/service.py` - Execute from DB code
  - `api/src/services/execution/module_loader.py` - Add exec_from_db function
  - `api/src/routers/files.py` - Route to correct table based on entity_type
  - `api/src/services/decorator_property_service.py` - Remove ID injection
  - `api/src/core/workspace_sync.py` - Backfill code column on startup
  - `api/src/services/git_integration.py` - Serialize DB entities to files
