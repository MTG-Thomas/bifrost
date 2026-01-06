# App Builder Improvements Plan

Generated from CRM POC vibe coding session (2026-01-06).

## Overview

During a session enhancing the CRM app using MCP tools, several limitations and bugs were discovered. This document captures findings and proposes improvements to make the App Builder more flexible and production-ready.

---

## Critical: Versioning System Incomplete

### Problem: Mixed Legacy and New Versioning

The codebase has TWO versioning systems running simultaneously, causing inconsistency:

**Legacy System (still in use):**
- `Application.live_version` / `draft_version` - numeric counters
- `AppPage.is_draft` - boolean flag
- Code paths still use `is_draft=True/False` for queries

**New System (migration added but not adopted):**
- `app_versions` table - UUID-based version snapshots
- `Application.active_version_id` / `draft_version_id` - UUID pointers
- `AppPage.version_id` - links pages to versions
- Migration: `20260106_151003_app_builder_versioning.py`

**Current Symptoms:**
- Some pages have `version_id` set (migrated from existing data)
- New pages created via MCP have `version_id: null` (code still uses `is_draft`)
- `get_app` returns pages with mixed `version_id` values
- Numeric `draft_version` counter increments but `draft_version_id` isn't updated

### Decision Required

**Option A: Complete the Migration to UUID Versioning**
1. Update all service code to use `version_id` instead of `is_draft`
2. Set `version_id` when creating new pages
3. Update publish logic to create new versions properly
4. Deprecate numeric version counters

**Option B: Rollback to Legacy System**
1. Run migration downgrade to remove versioning columns
2. Remove all `version_id` references from code
3. Keep using `is_draft` boolean and numeric counters
4. Simpler but loses version history capability

### Files Affected (for Option A)

| File | Changes Needed |
|------|----------------|
| `app_builder_service.py` | Use `version_id` in all queries, create versions on page creation |
| `app_pages.py` router | Query by `version_id` instead of `is_draft` |
| `pages.py` MCP tool | Set `version_id` when creating pages |
| `applications.py` MCP tool | Update `draft_version_id` when pages change |
| `AppBuilderService.create_page_with_layout()` | Accept and set `version_id` |
| `AppBuilderService.publish_with_versioning()` | Already exists but may need fixes |

### Code Paths to Audit

```python
# These patterns need updating:
AppPage.is_draft == True   # Should query by version_id
page.is_draft = True       # Should set version_id
app.draft_version += 1     # Should also update draft_version_id
```

Search command:
```bash
grep -rn "is_draft" api/src/services/
grep -rn "draft_version\b" api/src/services/
```

**Current Usage Counts:**
- `is_draft`: 154 occurrences in services/routers
- `version_id`: 84 occurrences in services/routers

Both systems are heavily intertwined - this is not a trivial fix.

---

## Bug Fixes Completed This Session

### 1. `update_page_layout` Unique Constraint Violation

**Problem:** When updating a page layout via MCP, the operation failed with:
```
duplicate key value violates unique constraint "ix_app_components_unique"
```

**Root Cause:** Components were marked for deletion but not flushed before new components with the same `component_id` were inserted.

**Fix Applied:** Added `await self.session.flush()` after deleting components in `app_builder_service.py:697`

---

## Missing Components

### 1. Textarea Component

**Use Case:** Notes fields, descriptions, multi-line content

**Current Workaround:** Using `text-input` which only supports single line

**Proposed Implementation:**
```json
{
  "type": "textarea",
  "props": {
    "fieldId": "notes",
    "label": "Notes",
    "rows": 4,
    "maxLength": 1000,
    "placeholder": "Enter notes..."
  }
}
```

### 2. Date Picker Component

**Use Case:** Activity dates, contract expiration, follow-up dates

**Current Workaround:** Text input with `placeholder: "YYYY-MM-DD"`

**Proposed Implementation:**
```json
{
  "type": "date-input",
  "props": {
    "fieldId": "expiration_date",
    "label": "Expiration Date",
    "minDate": "{{ today }}",
    "maxDate": "2030-12-31"
  }
}
```

### 3. Rich Text Editor (Future)

**Use Case:** Formatted notes, email templates, descriptions

---

## Missing CRUD Workflows

The CRM has full CRUD for Clients but only Create + List for other entities:

| Entity | List | Get | Create | Update | Delete |
|--------|------|-----|--------|--------|--------|
| Clients | ✅ | ✅ | ✅ | ✅ | ✅ |
| Contacts | ✅ | ❌ | ✅ | ❌ | ❌ |
| Contracts | ✅ | ❌ | ✅ | ❌ | ❌ |
| Activities | ✅ | ❌ | ✅ | ❌ | ❌ |

**Recommendation:** Add missing workflows to complete CRUD for all entities.

---

## UI/UX Improvements

### 1. Detail Pages for All Entities

Currently only Clients have a detail page. Add:
- `/contacts/:id` - Contact detail
- `/contracts/:id` - Contract detail
- `/activities/:id` - Activity detail

### 2. Modal Forms

For quick-add scenarios without full page navigation:
```json
{
  "type": "modal",
  "props": {
    "triggerLabel": "Quick Add",
    "title": "Add Contact",
    "content": { /* form layout */ },
    "footerActions": [
      {"label": "Cancel", "variant": "outline"},
      {"label": "Save", "actionType": "submit", "workflowId": "..."}
    ]
  }
}
```

### 3. Dashboard with Stats

Add a dashboard page showing:
- Total clients by status (stat cards)
- Recent activities (mini table)
- Expiring contracts (alerts)
- Quick action buttons

---

## Documentation Fixes Applied

### MCP Tool Documentation

**Issue:** Documentation suggested using `read_file` to explore workflow source code, but workflow source is not accessible via MCP.

**Files Updated:**
1. `.claude/skills/bifrost_vibecode_debugger/SKILL.md`
2. `bifrost-docs/src/content/docs/how-to-guides/local-dev/ai-coding.md`

**Change:** Clarified that the `path` field in workflow metadata is informational only. Use `get_workflow` for metadata or `execute_workflow` to test behavior.

---

## Implementation Priority

### Phase 1: Versioning Decision (High Priority)
- [ ] **Decision:** Keep UUID versioning (Option A) or rollback to legacy (Option B)?
- [ ] If Option A:
  - [ ] Audit all `is_draft` usages in services and routers
  - [ ] Update `create_page_with_layout()` to set `version_id` from app's `draft_version_id`
  - [ ] Update MCP `create_page` to pass `version_id`
  - [ ] Update MCP `update_page` to ensure `version_id` consistency
  - [ ] Test publish flow creates new version correctly
- [ ] If Option B:
  - [ ] Run migration downgrade: `alembic downgrade aa1d28178057^`
  - [ ] Remove all `version_id` references from ORM and services
  - [ ] Clean up `active_version_id`/`draft_version_id` columns

### Phase 2: Core Components (Medium Priority)
- [ ] Add `textarea` component
- [ ] Add `date-input` component
- [ ] Test cascading dropdown (`dependsOn`) behavior

### Phase 3: CRM Completion (Medium Priority)
- [ ] Add Get/Update/Delete workflows for Contacts
- [ ] Add Get/Update/Delete workflows for Contracts
- [ ] Add Get/Update/Delete workflows for Activities
- [ ] Add detail pages for each entity

### Phase 4: Polish (Low Priority)
- [ ] Add dashboard page with stats
- [ ] Test modal forms for quick-add
- [ ] Add rich text editor component

---

## Testing Notes

- Preview draft at: `/apps/crm?draft=true`
- CRM is at draft version 74
- Only publish (`publish_app`) after review

---

## References

- Session date: 2026-01-06
- Bug fix commit: `app_builder_service.py` line 697
- Doc fixes: SKILL.md, ai-coding.md
