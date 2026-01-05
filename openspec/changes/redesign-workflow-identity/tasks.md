# Tasks: Workflow Identity Redesign

## Phase 1: Schema Changes (Non-Breaking)

- [ ] 1.1 Create migration: Add `code` (TEXT) and `code_hash` (VARCHAR 64) columns to workflows table
- [ ] 1.2 Create migration: Rename `file_path` to `path` in workflows table
- [ ] 1.3 Create migration: Add `entity_type` (VARCHAR) and `entity_id` (UUID) columns to workspace_files table
- [ ] 1.4 Create migration: Add `workflow_path` and `workflow_function_name` columns to forms table
- [ ] 1.5 Update Workflow ORM model with new columns
- [ ] 1.6 Update WorkspaceFile ORM model with entity routing columns
- [ ] 1.7 Update Form ORM model with redundant reference columns
- [ ] 1.8 Write backfill script: populate `code` column from current files for all existing workflows
- [ ] 1.9 Write backfill script: set `entity_type` and `entity_id` for existing platform entities
- [ ] 1.10 Write tests for schema changes

## Phase 2: Unified Read (Non-Breaking)

- [ ] 2.1 Create entity type routing logic in file_storage_service
- [ ] 2.2 Implement read routing: workflow → workflows.code
- [ ] 2.3 Implement read routing: form → forms.definition (serialize JSON)
- [ ] 2.4 Implement read routing: app → applications.definition (serialize JSON)
- [ ] 2.5 Implement read routing: agent → agents.definition (serialize JSON)
- [ ] 2.6 Implement read routing: NULL → S3
- [ ] 2.7 Update workspace_sync to backfill `code` column if NULL from existing files
- [ ] 2.8 Write tests for unified read operations

## Phase 3: Unified Write (Non-Breaking)

- [ ] 3.1 Implement content type detection (parse decorators from .py files)
- [ ] 3.2 Implement write routing: .py with @workflow/@tool/@data_provider → workflows table
- [ ] 3.3 Implement write routing: .form.json → forms table
- [ ] 3.4 Implement write routing: .app.json → applications table
- [ ] 3.5 Implement write routing: .agent.json → agents table
- [ ] 3.6 Implement write routing: other files → S3
- [ ] 3.7 Handle file transition: regular file → workflow (delete from S3, create in DB)
- [ ] 3.8 Handle file transition: workflow → regular file (soft-delete in DB, create in S3)
- [ ] 3.9 Update workspace_files with correct entity_type and entity_id on write
- [ ] 3.10 Write tests for unified write operations
- [ ] 3.11 Write tests for file transition scenarios

## Phase 4: Switch Execution (Breaking)

- [ ] 4.1 Create exec_from_db function in module_loader.py
- [ ] 4.2 Implement namespace injection: `__file__`, `__package__`, `__name__`, `__builtins__`
- [ ] 4.3 Implement compile() with filename for stack traces
- [ ] 4.4 Update ExecutionService to use exec_from_db
- [ ] 4.5 Verify imports from modules/ (S3-backed files) still work
- [ ] 4.6 Write comprehensive tests comparing DB vs file execution
- [ ] 4.7 Add feature flag for rollback if needed
- [ ] 4.8 Enable in staging, monitor for issues
- [ ] 4.9 Enable in production

## Phase 5: Git Sync Updates

- [ ] 5.1 Update git push to serialize workflows from DB (no ID in decorators)
- [ ] 5.2 Update git push to serialize forms with workflow_path and workflow_function_name
- [ ] 5.3 Update git push to serialize apps and agents from DB
- [ ] 5.4 Update git pull to parse workflows and upsert to DB
- [ ] 5.5 Update git pull to parse forms and resolve workflow_id by path+function
- [ ] 5.6 Update git pull to parse apps and agents
- [ ] 5.7 Write tests for git sync serialization
- [ ] 5.8 Write tests for git import with workflow resolution

## Phase 6: Cleanup (Breaking)

- [ ] 6.1 Remove ID injection from DecoratorPropertyService
- [ ] 6.2 Remove LibCST transformer for ID injection
- [ ] 6.3 Remove `inject_ids_if_missing` and related functions
- [ ] 6.4 Remove file watcher's decorator parsing (no longer needed)
- [ ] 6.5 Remove file-based execution path from ExecutionService
- [ ] 6.6 Remove redundant S3 storage of platform entities
- [ ] 6.7 Clean up related tests
- [ ] 6.8 Update documentation

## Validation

- [ ] 7.1 Run full test suite after each phase
- [ ] 7.2 Verify pyright passes
- [ ] 7.3 Verify ruff check passes
- [ ] 7.4 Manual testing of editor read/write for all entity types
- [ ] 7.5 Manual testing of file transitions (regular ↔ workflow)
- [ ] 7.6 Manual testing of git sync (push and pull)
- [ ] 7.7 Performance testing (execution from DB vs file)
- [ ] 7.8 Import resolution testing (modules/, relative imports)
