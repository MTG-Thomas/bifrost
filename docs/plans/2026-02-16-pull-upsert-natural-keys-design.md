# Git Pull Upsert: Use Natural Keys Instead of ID

## Problem

All `_import_*` methods in `github_sync.py` use `ON CONFLICT (id)` for upserts during git pull. But several tables have unique constraints on natural keys (e.g., `(path, function_name)` for workflows). When the manifest contains a different ID for the same natural key, the `ON CONFLICT (id)` doesn't fire, the INSERT violates the natural key constraint, and the pull fails with `IntegrityError`.

**Observed in production:** `_import_workflow` failed with `duplicate key value violates unique constraint "workflows_path_function_key"`.

## Principle

The manifest is the source of truth after a successful git merge. Upserts should conflict on the **natural unique key**, and the manifest ID should overwrite the DB row's ID when they differ.

## Changes

### 1. `_import_workflow` — ON CONFLICT on natural key

Change `index_elements=["id"]` → `constraint="workflows_path_function_key"`.
Add `"id": UUID(mwf.id)` to the `set_` dict.

Natural key: `(path, function_name)`

### 2. `_import_integration` — ON CONFLICT on name

Change `index_elements=["id"]` → `index_elements=["name"]`.
Add `"id": integ_id` to the `set_` dict.

Natural key: `name` (unique=True)

### 3. `_import_event_source` subscriptions — ON CONFLICT on (source, workflow)

Change EventSubscription upsert from `index_elements=["id"]` → `index_elements=["event_source_id", "workflow_id"]`.
Add `"id": UUID(msub.id)` to the `set_` dict.

Natural key: `(event_source_id, workflow_id)` via `ix_event_subscriptions_unique_source_workflow`

### 4. `_import_app` — Two-step SELECT + upsert

Partial unique indexes (`ix_applications_org_slug_unique`, `ix_applications_global_slug_unique`) can't be ON CONFLICT targets. Use two-step:
1. SELECT existing app by `(organization_id, slug)`
2. If found: UPDATE (including setting id to manifest id)
3. If not found: INSERT

### 5. `_import_config` — Two-step SELECT + upsert

Partial unique index (`ix_configs_integration_org_key`) can't be ON CONFLICT target. Use two-step:
1. SELECT existing config by `(integration_id, organization_id, key)`
2. If found: UPDATE
3. If not found: INSERT
4. Preserve existing secret-skip logic

### Not changing

- **`_import_table`**: Already has try/except IntegrityError fallback that updates by name.
- **`_import_form` / `_import_agent`**: Use `on_conflict_do_nothing` + delegate to indexers. Different pattern — YAML content is source of truth, not manifest entry.
- **`EventSource`, `ScheduleSource`, `WebhookSource`**: No natural key conflicts beyond PK/FK which are already correct.

## Testing

For each fixed entity type, add a test that:
1. Creates a row with natural key X and ID A
2. Imports via manifest with natural key X and ID B
3. Verifies ID B wins and no IntegrityError
4. Verifies only one row exists (no duplicates)
