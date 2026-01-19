# Entity Display and Editor Simplification

**Date:** 2026-01-19
**Status:** Design

## Summary

Simplify the code editor to only show actual code files (workflows, modules). Remove forms, agents, and apps from the file tree entirely. Enhance the git sync UI to show these entities with display names, icons, and proper grouping.

## Problem

Currently:
- Forms/agents appear in the code editor as `.form.json` / `.agent.json` files
- Users can edit these JSON files directly, risking breaking the entities
- Forms/agents are written to S3 and indexed in `workspace_files` - unnecessary complexity
- Apps have a separate code editor but the architecture is inconsistent
- The file tree mixes "actual files" with "virtual entity representations"

## Solution

### 1. Code Editor: Only Actual Code

The file tree shows only:
- `workflows/*.py` - workflow files
- `data_providers/*.py` - data provider files
- `modules/*.py` - shared Python modules

**Not shown:**
- Forms (use form editor)
- Agents (use agent editor)
- Apps (use dedicated app code editor)

### 2. Git Sync UI: Entity-Centric Display

Transform the sync preview from file paths to entity display:

**Current:**
```
forms/a1b2c3d4-e5f6-7890-abcd-ef1234567890.form.json
agents/f9e8d7c6-b5a4-3210-fedc-ba0987654321.agent.json
apps/dashboard/app.json
apps/dashboard/pages/index.tsx
apps/dashboard/components/Header.tsx
```

**Proposed:**
```
📝 Customer Intake Form
   [Keep Local] [Keep Remote]

🤖 Support Bot
   [Keep Local] [Keep Remote]

🔲 Dashboard
   [Keep Local] [Keep Remote]
   ▶ 3 files (expandable)

⚡ process_payment.py
   [Keep Local] [Keep Remote]
```

Key changes:
- **Display names** instead of file paths (for forms, agents, apps)
- **Entity icons** from existing icon set (AppWindow, Bot, FileText, Workflow)
- **App grouping** - app files collapsed under app header by default
- **Sync actions at entity level** - not per-file for apps

### 3. Remove Virtual File Infrastructure for Editor

**Remove from `workspace_files`:**
- Form entries (entity_type="form")
- Agent entries (entity_type="agent")
- Never add app entries

**Remove S3 writes:**
- Forms should not be written to S3 on create/update
- Agents should not be written to S3 on create/update
- These are serialized on-the-fly only for git sync

**Fields to deprecate/remove:**
- `forms.file_path` - no longer needed
- `agents.file_path` - no longer needed

### 4. Git Sync Architecture (Unchanged)

Virtual file provider continues to work as-is:
- Serializes forms/agents/apps on-the-fly for git push
- Generates paths like `forms/{id}.form.json`, `agents/{id}.agent.json`, `apps/{slug}/...`
- Parses incoming files on git pull using indexers
- Matches by UUID embedded in JSON, not by path

## Implementation

### Phase 1: Git Sync UI Enhancement

1. **Backend: Enrich sync preview response**
   - Add `display_name` and `entity_type` to `SyncAction` model
   - Parse display names from JSON content (already available in temp clone)
   - Group app files by app slug

2. **Frontend: Update SourceControlPanel**
   - Render entities with display names and icons
   - Collapse app files under app header
   - Move sync actions to entity level for apps

### Phase 2: Remove Forms/Agents from Editor

1. **Stop writing forms/agents to S3**
   - Remove `_write_form_to_file()` calls in `api/src/routers/forms.py`
   - Remove equivalent agent S3 write calls

2. **Stop creating workspace_files entries**
   - Remove code that creates workspace_files records for forms/agents
   - Forms/agents become fully virtual (only in entity tables)

3. **Migration: Delete existing workspace_files entries**
   - `DELETE FROM workspace_files WHERE entity_type IN ('form', 'agent')`
   - One-time cleanup

4. **Remove dead code**
   - `_write_form_to_file()`, `_update_form_file()`, `_deactivate_form_file()`
   - Equivalent agent functions
   - Any workspace_files creation for forms/agents

5. **Deprecate file_path fields**
   - Remove `forms.file_path` column (migration)
   - Remove `agents.file_path` column (migration)

## Data Model Changes

### SyncAction (API Response)

```python
class SyncAction(BaseModel):
    path: str                          # Git path (for reference)
    action: SyncActionType
    sha: str | None = None

    # New fields for UI
    display_name: str | None = None    # Entity name (forms, agents, apps)
    entity_type: str | None = None     # "form", "agent", "app", "app_file", "workflow"
    parent_entity: str | None = None   # For app_files: the app slug
```

### SyncPreviewResponse (API Response)

```python
class SyncPreviewResponse(BaseModel):
    to_pull: list[SyncAction] | None = None
    to_push: list[SyncAction] | None = None
    conflicts: list[ConflictInfo] | None = None
    # ... existing fields ...

    # New: grouped structure for UI convenience
    entities: EntitySyncSummary | None = None
```

## Migration Path

1. Git sync UI can be enhanced independently (no breaking changes)
2. Removing forms/agents from editor requires:
   - Coordinated backend + frontend changes
   - Data migration to clean up workspace_files
   - Schema migration to remove file_path columns
3. Can be done incrementally - git sync UI first, then editor cleanup

## Benefits

1. **Simpler code editor** - only actual code files
2. **No risk of breaking entities** - can't edit JSON directly
3. **Cleaner architecture** - entities have dedicated editors
4. **Less storage** - no S3 writes for forms/agents
5. **Better UX** - git sync shows meaningful names, not UUIDs
6. **Consistent** - apps, forms, agents all treated the same way

## Decisions

1. **Delete all form/agent entries from `workspace_files`** - Migration to remove them entirely
2. **No S3 cleanup needed** - Just stop writing to S3, existing files can rot (or be cleaned up later)
3. **Forms/agents become fully virtual** - Only exist in their entity tables, serialized on-the-fly for git sync
