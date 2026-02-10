# Workspace Architecture Redesign

> **Goal:** Replace the current discovery-based, polymorphic workspace model with a manifest-driven architecture where `.bifrost/metadata.yaml` is the source of truth for all platform entities, S3 is the durable file store, and git sync becomes actual git operations.

---

## Core Principles

1. **Declaration over discovery** — The manifest says what exists, not AST parsing.
2. **UUIDs for all cross-references** — Manifest keys are human-readable, all references between entities use UUIDs. No exceptions.
3. **Entity files are portable artifacts** — A `.form.yaml` or `.py` file has no org, roles, or instance config. Cross-references use UUIDs which are remapped on import into a new environment (recipient builds their own manifest from exported artifacts).
4. **Manifest is the instance binding** — Org assignment, roles, runtime config, UUIDs all live in the manifest.
5. **DB is the runtime store** — DB tables keep all columns and work independently. The manifest is only relevant during git sync operations — when sync is triggered, the manifest is reconciled to the DB. No implicit mode switching.
6. **S3 is the durable file store** — All files live in `_repo/` in S3. No code or content stored in DB columns.

---

## S3 Bucket Layout

Single bucket, prefix-separated zones:

```
bifrost-{env}/
├── _repo/                        # Git workspace (platform-managed, reserved)
│   ├── .git/                     # Full git history (when git is connected)
│   ├── .bifrost/
│   │   └── metadata.yaml         # The manifest
│   ├── workflows/
│   │   └── onboard_user.py
│   ├── forms/
│   │   └── onboarding.form.yaml
│   ├── agents/
│   │   └── support.agent.yaml
│   ├── apps/
│   │   └── dashboard/
│   │       └── app.yaml
│   └── modules/
│       └── shared/
│           └── utils.py
│
├── _tmp/                         # Platform-managed temp files (reserved)
├── exports/                      # SDK file operations (user space)
├── uploads/                      # Form uploads (convention)
├── data/                         # User data files
└── [any user-chosen prefix]/     # Free-form SDK file operations
```

**Reserved prefixes** (rejected by SDK file operations):
- `_repo/` — the git workspace
- `_tmp/` — platform-managed temp files
- Future: any `_` prefix reserved for platform use

**SDK file operations** accept any string as a location/prefix except reserved names. The current static enum (`workspace`, `temp`, `uploads`) is replaced with free-form strings validated against the reserved list.

---

## The Manifest: `.bifrost/metadata.yaml`

The manifest is a flat file at `_repo/.bifrost/metadata.yaml`. It declares every platform entity, its UUID, file path, org binding, roles, and runtime config.

### Structure

```yaml
organizations:
  - id: 9a3f2b1c-...
    name: Contoso

roles:
  - id: b7e2a4d1-...
    name: helpdesk
    organization_id: 9a3f2b1c-...
  - id: c4d1e8f2-...
    name: admin
    organization_id: 9a3f2b1c-...

workflows:
  onboard_user:
    id: f8a1b3c2-...
    path: workflows/onboard_user.py
    function_name: onboard_user
    type: workflow
    organization_id: 9a3f2b1c-...
    roles: [b7e2a4d1-..., c4d1e8f2-...]
    access_level: role_based
    endpoint_enabled: true
    timeout_seconds: 300

  ticket_classifier:
    id: a2b4c6d8-...
    path: workflows/ticket_classifier.py
    function_name: classify
    type: tool
    organization_id: 9a3f2b1c-...
    roles: []
    access_level: authenticated

  fetch_clients:
    id: e1f2a3b4-...
    path: data_providers/clients.py
    function_name: fetch_clients
    type: data_provider
    organization_id: null  # global
    roles: []

forms:
  onboarding_form:
    id: d2e5f8a1-...
    path: forms/onboarding.form.yaml
    organization_id: 9a3f2b1c-...
    roles: [b7e2a4d1-...]

agents:
  support_agent:
    id: c3d4e5f6-...
    path: agents/support.agent.yaml
    organization_id: 9a3f2b1c-...
    roles: [b7e2a4d1-..., c4d1e8f2-...]

apps:
  dashboard:
    id: a1b2c3d4-...
    path: apps/dashboard/app.yaml
    organization_id: 9a3f2b1c-...
    roles: [b7e2a4d1-...]
```

### Rules

- **Manifest keys** are the human-readable entity names (resolved name from decorator for workflows, name from config for forms/agents/apps).
- **All cross-references** use UUIDs — in the manifest and in entity files. No exceptions.
- **No instance-specific secrets** — `api_key_hash` and similar stay DB-only, never in the manifest.
- **Transient state stays DB-only** — `last_seen_at`, `created_at`, `updated_at`, execution history.
- **Cached/derived data stays DB-only** — `parameters_schema`, computed at parse time.
- Both humans and the platform can edit the manifest. Platform appends entries when entities are created via UI; humans edit it for local SDK development.

### What lives where

| Data | Location | Examples |
|------|----------|---------|
| Entity identity | Manifest | key, id, path, function_name, type |
| Org/role binding | Manifest | organization_id, roles |
| Runtime config | Manifest | endpoint_enabled, timeout_seconds, access_level |
| Portable definition | Entity file | form fields, agent prompt, workflow code |
| Cross-references | Entity file | workflow UUID in form YAML, tool UUIDs in agent YAML |
| Secrets | DB only | api_key_hash, credentials |
| Transient state | DB only | last_seen_at, updated_at, created_at |
| Derived/cached data | DB only | parameters_schema |

---

## Entity Files

Each non-code entity (forms, agents, apps) has a YAML file containing its portable definition. Workflows are `.py` files — their definition is the code itself, with identity metadata in the decorator.

### Workflow (`.py`)

```python
from bifrost import workflow

@workflow(name="onboard_user", category="Admin", tags=["m365", "user"])
async def onboard_user(email: str, license_type: str = "E3"):
    """Onboard a new M365 user."""
    user = await create_account(email)
    await assign_license(user, license_type)
    return {"user": user}

# Helper functions are fine — unlimited per file
async def create_account(email):
    ...

async def assign_license(user, license_type):
    ...
```

Multiple `@workflow`/`@tool`/`@data_provider` decorators per file are allowed. Each decorated function is a separate entry in the manifest with the same `path` but different `function_name`.

### Form (`.form.yaml`)

```yaml
name: Onboarding Form
description: New employee onboarding request
workflow: f8a1b3c2-...          # UUID reference to workflow
launch_workflow: null
fields:
  - name: employee_name
    type: text
    label: Employee Name
    required: true
  - name: department
    type: select
    label: Department
    options: [Engineering, Sales, Support]
  - name: license_type
    type: select
    label: M365 License
    default: E3
    options: [E1, E3, E5]
```

### Agent (`.agent.yaml`)

```yaml
name: Support Agent
description: Handles tier 1 support tickets
system_prompt: You are a helpful support agent...
llm_model: claude-sonnet-4-5-20250929
tools:
  - a2b4c6d8-...               # UUID reference to ticket_classifier
  - e1f2a3b4-...               # UUID reference to fetch_clients
```

### App (`app.yaml`)

```yaml
name: Dashboard
description: Client overview dashboard
```

App pages and components are sibling files in the same directory. The platform discovers them by convention from the app.yaml location.

---

## Database Changes

### Drop

- **`workspace_files` table** — Replaced by `file_index` (search only) and S3 (storage).
- **`workflows.code` column** — Code lives in `.py` files in S3.
- **`workflows.code_hash` column** — No longer needed.
- **`workflows.portable_ref` computed column** — Replaced by manifest keys.

### Add

```sql
CREATE TABLE file_index (
    path VARCHAR(1000) PRIMARY KEY,
    content TEXT,
    content_hash VARCHAR(64),
    updated_at TIMESTAMPTZ
);
```

`file_index` is a search index for text content in `_repo/`. Populated via dual-write whenever files are written to S3. Only indexes text-searchable files (`.py`, `.yaml`, `.md`, `.txt`, etc.). No entity routing, no polymorphic references.

### Keep as-is

- `workflows` — keeps all columns except `code`, `code_hash`, `portable_ref`. Still stores metadata, runtime config, secrets, derived data like `parameters_schema`.
- `forms`, `form_fields` — unchanged.
- `agents`, `agent_tools` — unchanged.
- `applications`, `app_files` — unchanged.
- All other tables — unchanged.

---

## Worker Execution Flow

Workers no longer read code from the DB. The execution flow becomes:

1. **Consumer receives job** from RabbitMQ.
2. **Consumer looks up workflow** in DB — gets `path`, `function_name`, timeout, org, etc. (metadata only, no code).
3. **Consumer routes** to ProcessPoolManager.
4. **Worker needs code** — virtual import hook:
   - Check Redis cache for `_repo/{path}`
   - Cache miss → fetch from S3 `_repo/{path}`
   - Cache to Redis with TTL
   - Return code
5. **Worker executes** the function.

Module imports follow the same path — Redis cache → S3 fallback → cache to Redis.

**Removed:**
- `warm_cache_from_db()` — no prewarming needed.
- `_sync_module_cache()` — importer is self-sufficient.
- DB reads for workflow code — S3 via Redis cache.

The virtual importer manages its own cache lifecycle: check Redis → pull from S3 if miss/expired → cache to Redis. No prewarming, no DB dependency in workers.

---

## MCP Content Tools

### Reads

- `get_content(path)` → `SELECT content FROM file_index WHERE path = ?`
- `search_content(pattern)` → `SELECT path, content FROM file_index WHERE content LIKE ?` then grep for line numbers/context
- `list_content()` → `SELECT path FROM file_index` (optionally filtered by path prefix)

No `entity_type` parameter needed. The manifest tells you what a file is if you need to know.

### Writes

- `replace_content(path, content)` → write to S3 `_repo/{path}` AND upsert `file_index`
  - If path is referenced in manifest as a workflow → re-parse AST, update `parameters_schema` in `workflows` table
  - If path is a form/agent YAML → re-parse, update corresponding DB table
  - Invalidate Redis cache for that path
- `patch_content(path, old_string, new_string)` → read from `file_index`, apply patch, then `replace_content`
- `delete_content(path)` → delete from S3 `_repo/{path}` AND delete from `file_index`
  - If referenced in manifest → surface error (manifest says it should exist)
  - Invalidate Redis cache

### Deactivation Protection (simplified)

If someone edits a `.py` file and removes a `@workflow` decorator, the manifest still references it. Reconciliation detects the mismatch (manifest declares entity, file no longer has the decorator) and surfaces it as an error rather than silently deactivating.

---

## Git Sync

With `_repo/` being a real git repo in S3, sync becomes actual git operations.

### Connecting Git (Existing Platform, Empty Repo)

1. Create temp dir.
2. `git init && git remote add origin <url>`
3. Serialize platform state:
   - Query all active workflows → write `.py` files
   - Query all forms + fields → serialize to `.form.yaml`
   - Query all agents + tools → serialize to `.agent.yaml`
   - Query all apps → serialize to `app.yaml` + page files
   - Generate `.bifrost/metadata.yaml` from all entities
4. `git add -A && git commit -m "Initial export" && git push -u origin main`
5. Copy temp dir to `_repo/` in S3.

### Connecting Git (Existing Platform, Repo With Data)

User chooses one of three strategies:

1. **Platform wins** — Serialize platform state, force push to remote, overwriting repo content.
2. **Git wins** — Pull remote state, reconcile manifest to DB, overwriting platform entities.
3. **Manual merge** — Preview diff between platform state and remote state:
   - Entities in both → user picks which version wins per entity
   - Entities only in remote → pull candidates
   - Entities only in platform → push candidates
   - User confirms resolutions, then commit and push.

### Pulling Changes

1. Copy `_repo/` from S3 to temp dir.
2. `git pull origin main` (git handles merge).
3. If conflicts → surface to user in UI (standard git conflict markers).
4. Read `.bifrost/metadata.yaml`.
5. Reconcile manifest against DB:
   - New entries → create entities in DB
   - Removed entries → prompt user before deleting
   - Changed files → update DB entities
   - Broken cross-references → surface as errors
6. Copy temp dir back to `_repo/` in S3.
7. Update `file_index` for changed files.

### Pushing Changes

1. Copy `_repo/` from S3 to temp dir.
2. Ensure files in temp dir reflect current DB/S3 state.
3. `git add -A && git commit && git push`
4. Copy temp dir back to `_repo/` in S3 (updated `.git/`).

### Deletion Protection

When reconciling after a pull, entities present in the DB but missing from the manifest are **pending deletes**. These always prompt the user unless "git wins" mode was selected. The platform can detect likely renames by matching on function_name or path similarity and surface those as "did you rename X → Y?" suggestions.

---

## No-Git Workspaces

When no git repo is connected:

- DB is the source of truth for entity metadata and config (same as today).
- `_repo/` in S3 still exists as the file store for code and entity files.
- `.bifrost/metadata.yaml` is not involved — no manifest exists until git is connected.
- The platform reads/writes entity config directly to/from DB tables.
- Connecting git for the first time generates the manifest from DB state (one-time export).

---

## What Gets Deleted From the Codebase

- **`workspace_files` table + model** — replaced by `file_index` + S3.
- **`workflows.code` / `workflows.code_hash` columns** — code lives in S3 files.
- **`workflows.portable_ref` computed column** — replaced by manifest keys.
- **`github_sync_virtual_files.py`** — no more virtual files, everything is a real file.
- **`entity_detector.py`** — discovery replaced by manifest declaration. AST parsing demoted to validation/parameter extraction.
- **`build_workflow_ref_map()` / `build_ref_to_uuid_map()`** — replaced by manifest key + UUID lookups.
- **`warm_cache_from_db()` / `_sync_module_cache()`** — virtual importer is self-sufficient via Redis → S3.
- **Most of `github_sync.py`** — manual SHA comparison replaced by git operations (via GitPython).
- **`WorkspaceFile` polymorphic routing** — no more `entity_type`/`entity_id` indirection.
- **Multi-decorator handling complexity** — recent `0add8fe3` commit's MCP editing workarounds become unnecessary (manifest handles identity regardless of file layout).

---

## Migration Path

### One-Time Migration

1. **Generate manifest** from existing DB state (workflows, forms, agents, apps, roles, orgs).
2. **Export entity files** — serialize forms to `.form.yaml`, agents to `.agent.yaml`, apps to `app.yaml`.
3. **Write workflow `.py` files** to S3 from `workflows.code` column.
4. **Populate `file_index`** from all text files.
5. **Write `_repo/` structure** to S3 with `.bifrost/metadata.yaml`.
6. **Convert existing `.form.json` / `.agent.json`** to `.form.yaml` / `.agent.yaml`.

### Schema Migration

1. Create `file_index` table.
2. Drop `workspace_files` table.
3. Drop `workflows.code`, `workflows.code_hash`, `workflows.portable_ref` columns.

### Git Sync Migration

Existing GitHub-connected workspaces need re-initialization:
1. Generate manifest from DB.
2. Commit to repo (may require merge with existing content).
3. Subsequent syncs use the new git-based flow.

---

## Operational Concerns

### Write Consistency (S3 + DB + Redis)

The write path touches three stores: S3, `file_index` (DB), and Redis cache. If one write fails mid-flight, state can diverge.

**Approach:** Best-effort dual write with a background reconciler.
- Write path: S3 first → `file_index` upsert → Redis invalidation. If the DB or Redis step fails, log it.
- **Reconciler** runs periodically (and on API startup): lists `_repo/` in S3, compares against `file_index`, heals any drift (missing rows, stale content, orphaned index entries).
- This avoids the complexity of an outbox/two-phase commit while still guaranteeing eventual consistency.

### Execution Reproducibility

When a job is dispatched, the worker loads code from Redis/S3. If the file changes between dispatch and load, the execution could use inconsistent code.

**Approach:** Pin execution to a content hash.
- At dispatch time, the consumer records the `content_hash` (from `file_index`) on the execution record.
- Worker fetches code from Redis/S3, computes hash, validates it matches.
- Hash mismatch → re-fetch from S3 (bypass Redis cache) and re-validate. If still mismatched, fail the execution with a clear error ("code changed during dispatch").

### Git Sync Concurrency

Git sync copies `_repo/` from S3 to a temp dir, operates on it, then copies back. Two concurrent syncs would corrupt state.

**Approach:** Redis distributed lock.
- Acquire `bifrost:sync:lock` (with TTL to prevent deadlocks) before any sync operation.
- If lock is held, return "sync already in progress" to the caller.
- Lock covers the entire copy-in → git operations → reconcile → copy-out cycle.

### Migration Safety

Dropping `workspace_files` and `workflows.code` is destructive. A phased approach reduces risk:

1. **Phase 1:** Create `file_index` table. Add dual-write to all mutation paths (write to both old and new stores). Deploy and validate.
2. **Phase 2:** Migrate read paths to use `file_index` and S3. Old tables still populated but no longer read. Deploy and validate.
3. **Phase 3:** Drop old columns/tables once Phase 2 is stable.

---

## Dependencies

- **GitPython** (`gitpython`) — Python wrapper for git CLI operations. Handles clone, pull, push, diff, merge without manual CLI output parsing.
- **PyYAML** (`pyyaml`) — YAML parsing/serialization for manifest and entity files. (Likely already a transitive dependency.)
