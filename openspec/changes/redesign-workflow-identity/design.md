# Design: Workflow Identity Redesign

## Context

The file watcher system currently:
1. Injects UUIDs into Python decorator parameters (e.g., `@workflow(id="...")`)
2. Uses file path + function name as identity
3. Loads and executes code from the file system
4. Has no mechanism for detecting file moves (sees delete + create)

This causes:
- Race conditions when multiple containers inject different IDs
- Orphaned form/app references when workflows are deleted
- No portability of forms between environments
- Silent failures when files are moved/renamed
- Production outages when workflow files are deleted

## Goals

1. **Stable identity**: Workflow identity survives file moves and renames
2. **Clean files**: No platform-specific IDs in user code
3. **Production safety**: Deleted files don't break running forms/apps
4. **Portability**: Forms can be shared across environments
5. **Unified experience**: Editor works seamlessly with DB-backed entities

## Non-Goals

1. Versioning of workflow code (single active version per workflow)
2. Rollback to previous code versions
3. Sandboxed execution (workflows run in worker process)
4. Workflow-to-workflow imports (use modules/ for shared code)

## Decisions

### Decision 1: DB-First for Platform Entities

**What**: Platform entities (workflows, forms, apps, agents) are stored in the database as source of truth. Regular files (modules, data, configs) remain S3-first.

**Why**:
- Database is the authoritative source - no sync issues
- File deletion doesn't break production (code persists in DB)
- No race conditions on entity creation
- Git sync is a translation layer, not the source of truth

**Alternatives considered**:
- File-first with DB cache → Still has race conditions, complexity
- Dual-mode with feature flags → Transitional complexity, maintenance burden

### Decision 2: Unified File System View via workspace_files

**What**: `workspace_files` table has `entity_type` column that routes read/write to correct backend.

```python
class WorkspaceFile(Base):
    path: str
    entity_type: str | None   # 'workflow', 'form', 'app', 'agent', None
    entity_id: UUID | None    # FK to entity table
    content_hash: str | None  # For S3-backed files only
```

**Why**:
- Seamless editor experience (all files in one tree)
- Clear routing logic (entity_type determines backend)
- Permanent architecture (not transitional dual-mode)

### Decision 3: exec() with Namespace Injection

**What**: Execute workflow code from `workflows.code` column using Python's `exec()` with injected namespace.

```python
namespace = {
    "__name__": workflow.function_name,
    "__file__": str(WORKSPACE_PATH / workflow.path),
    "__package__": str(Path(workflow.path).parent).replace("/", "."),
    "__builtins__": __builtins__,
}
code_obj = compile(workflow.code, filename=str(implicit_path), mode='exec')
exec(code_obj, namespace)
```

**Why**:
- `__file__` injection makes code behave as if loaded from file
- `compile(..., filename=path)` gives meaningful stack traces
- sys.path includes workspace, so imports from regular files work
- No need for files to exist on disk during execution

**Constraints**:
- Workflows cannot import other workflows via relative imports
- Shared code must live in `modules/` or `lib/` (S3-backed regular files)

### Decision 4: Content Type Detection on Write

**What**: Editor write detects content type and routes to correct table.

| Pattern | Detection | Backend |
|---------|-----------|---------|
| `.py` with `@workflow/@tool/@data_provider` | AST parse | `workflows` table |
| `.form.json` | Extension | `forms` table |
| `.app.json` | Extension | `applications` table |
| `.agent.json` | Extension | `agents` table |
| Other | Default | S3 |

**Why**:
- User experience unchanged (save file, it just works)
- Handles transitions (regular file becomes workflow, workflow becomes regular file)
- Clear routing logic

### Decision 5: Redundant References for Portability

**What**: Forms store `workflow_path` and `workflow_function_name` alongside `workflow_id`.

**Why**:
- Git import can resolve by path+function when UUID doesn't exist
- Self-repair during import (update workflow_id to local match)
- Graceful degradation (show helpful error if unresolved)

### Decision 6: Git Sync as Serialization Layer

**What**: Git operations serialize DB entities to files, and parse files into DB entities.

**Git Pull**: Parse files → upsert to DB tables → create workspace_files entries
**Git Push**: Serialize DB entities → write to temp dir → commit and push

**Why**:
- Files are for version control and sharing, not runtime
- Clean separation of concerns
- IDs never appear in files (generated on parse, not serialized on push)

## Risks / Trade-offs

### Risk: Large `code` column size
- **Mitigation**: TEXT column, compressed at DB level. Typical workflow <10KB.

### Risk: exec() security
- **Mitigation**: Same process as current file-based execution. No regression.
- **Note**: Imports still resolve from file system (sys.path), which is expected behavior.

### Risk: Relative imports between workflows don't work
- **Mitigation**: Document constraint clearly. Use modules/ for shared code.
- **Benefit**: Clearer separation between platform entities and utility code.

### Trade-off: Editor write complexity
- Must detect content type and route to correct backend
- Worth it for seamless user experience and proper storage separation

## Migration Plan

### Phase 1: Schema Changes (Non-Breaking)
1. Add `code`, `code_hash` columns to workflows table (nullable)
2. Add `entity_type`, `entity_id` columns to workspace_files table
3. Add `workflow_path`, `workflow_function_name` to forms table
4. Backfill: Copy current file content into `code` column for all existing workflows
5. Backfill: Set `workspace_files.entity_type` for existing platform entities

### Phase 2: Unified Read (Non-Breaking)
1. Editor read routes based on `entity_type`:
   - `workflow` → fetch `workflows.code`
   - `form` → fetch `forms.definition`
   - `app` → fetch `applications.definition`
   - `agent` → fetch `agents.definition`
   - `NULL` → fetch from S3
2. Backfill missing `code` column from existing files on workspace sync startup

### Phase 3: Unified Write (Non-Breaking)
1. Editor write detects content type and routes to correct backend
2. Handle transitions (regular file ↔ platform entity)
3. Update workspace_files with correct entity_type and entity_id

### Phase 4: Switch Execution (Breaking)
1. Change executor to use `exec()` from `workflows.code`
2. Inject `__file__`, `__package__` into namespace
3. Verify imports from modules/ still work
4. Remove file watcher's decorator parsing

### Phase 5: Cleanup
1. Remove ID injection code
2. Remove file-based execution path
3. Update git sync to serialize from DB
4. Remove redundant S3 storage of platform entities

### Rollback
- Phase 4 rollback: Revert executor to file-based loading
- Data is preserved in both locations during transition

## Open Questions

1. **Hot reload**: When DB code updates mid-execution, do running workflows see new code?
   - Likely no: function is already loaded into Python namespace
   - Acceptable: consistent with current file-based behavior
