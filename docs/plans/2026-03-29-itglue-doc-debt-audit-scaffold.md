# IT Glue Documentation Debt Audit → HaloPSA Tasking (Scaffold)

**Date:** 2026-03-29

## 1) Repo readout: structure and implementation patterns

This repo already follows a consistent integration shape that is useful for this feature:

- Vendor API clients live in `modules/{vendor}.py` (example: IT Glue and Autotask).
- Org-mapping workflows live in `features/{vendor}/workflows/sync_*.py`.
- Mapping pickers live in `features/{vendor}/workflows/data_providers.py`.
- Integration mapping dedupe is typically done with `integrations.list_mappings(...)` and `integrations.upsert_mapping(...)`.

Concrete references:

- `modules/itglue.py` already contains IT Glue auth/bootstrap helpers and endpoint wrappers.
- `features/itglue/workflows/sync_organizations.py` and `features/itglue/workflows/data_providers.py` show the current IT Glue organization sync pattern.
- `modules/autotask.py` shows a clean vendor-focused client boundary with normalization helpers and retry behavior.
- Event scheduling supports cron-backed schedule sources (`source_type = schedule`) at the platform level.

## 2) IT Glue API endpoint audit (documentation review)

Primary source reviewed: <https://api.itglue.com/developer/>

Confirmed from API docs + existing module:

- Organizations: available and already implemented in `modules/itglue.py`.
- Configurations: available and already implemented (`list_configurations`, etc.).
- Contacts: available and already implemented (`list_contacts`, etc.).
- Passwords: available and already implemented (`list_passwords`, etc.).
- Documents: available in IT Glue docs (resource listed with index/show/create/update/etc.), **but not currently wrapped in `modules/itglue.py`**.
- Checklists: listed as API resources in IT Glue docs (index/show/update/bulk operations); no first-class wrapper currently in `modules/itglue.py`.

Notable accessibility/shape caveats to validate during build:

- The specific timestamp field used for "password last rotated" may vary by record type/tenant data model; defaulting to `updated-at` is likely but should be validated against live payloads.
- Checklist completeness and checklist item age depend on checklist task fields available via include/relationships payloads.
- IT Glue docs include "complete document information" endpoint shape for document sections; we should use this where needed for staleness/content checks.

## 3) NinjaOne API inventory cross-reference review

Sources reviewed:

- NinjaOne public API operations article: <https://www.ninjaone.com/docs/application-programming-interface-api/public-api-operations/>
- Existing generated SDK in repo: `workflows/ninjaone/sdk.py`.

Practical conclusion:

- For this repo, the most reliable implementation source is the generated NinjaOne SDK currently in-tree.
- The SDK exposes inventory endpoints needed for cross-reference:
  - `list_devices` (`GET /v2/devices`)
  - `list_devices_detaileds` (`GET /v2/devices-detailed`)
  - `get_devices` by organization (`GET /v2/organization/{id}/devices`)
  - `list_organizations` (`GET /v2/organizations`)
- NinjaOne public docs are partially navigational and point to console-hosted API references for full parameter detail; endpoint behavior should be verified with integration test payloads in your tenant.

## 4) Proposed data model

Create a userland feature package: `features/itglue_doc_audit/`.

### 4.1 Config model (per client)

Store per-client policy in integration/org config (or a dedicated table if platform changes are approved):

```yaml
enabled: true
required_configuration_fields:
  - ip_address
  - os_version
  - assigned_contact
  - location
password_rotation_days: 90
document_staleness_days: 180
required_contact_roles:
  - primary_technical
  - primary_business
checklist_incomplete_age_days: 30
documentation_owner:
  type: halopsa_agent
  id: "<agent-id>"
```

### 4.2 Normalized audit entities (internal)

Define internal typed shapes (in feature layer) produced **only** by boundary adapters:

- `NormalizedAsset`: `external_id`, `name`, `client_id`, `last_seen_at`, `status`.
- `NormalizedConfigItem`: `external_id`, `client_id`, `name`, `updated_at`, `fields` (normalized dictionary).
- `NormalizedPassword`: `external_id`, `client_id`, `name`, `updated_at`, `last_rotated_at?`.
- `NormalizedContact`: `external_id`, `client_id`, `name`, `roles[]`, `is_key_contact`.
- `NormalizedDocument`: `external_id`, `client_id`, `title`, `updated_at`.
- `NormalizedChecklist`: `external_id`, `client_id`, `name`, `updated_at`, `items[]` where items include completion + age metadata.

### 4.3 Gap + run state model

- `AuditRun`: run metadata per client (`run_id`, timestamps, score).
- `GapRecord`: deterministic gap identity + status.
  - `gap_key` (deterministic hash: `client|category|stable_resource_key|rule_version`)
  - `category`
  - `severity`
  - `payload`
  - `first_seen_run_id`, `last_seen_run_id`, `status` (`open|resolved`)
- `GapTaskLink`: maps `gap_key` to HaloPSA task ID/status for dedupe.

## 5) Proposed audit pipeline

1. Resolve enabled clients via org mappings (IT Glue + NinjaOne + HaloPSA).
2. Load per-client policy.
3. Collect source snapshots through adapters:
   - NinjaOne device inventory snapshot.
   - IT Glue configs/passwords/contacts/documents/checklists snapshot.
4. Execute checks (pure functions) and emit normalized gaps.
5. Deduplicate against prior open gaps by `gap_key`.
6. Create/update HaloPSA tasks for net-new gaps only.
7. Close/annotate tasks for resolved gaps (configurable behavior).
8. Compute and store health score and trend delta vs prior run.
9. Emit per-client and global summary.

## 6) Deduplication logic

Use deterministic identity, not title matching:

- Gap identity key composition:
  - `client_id`
  - `gap_category` (e.g., `missing_config_item_for_ninja_device`)
  - stable subject key (e.g., Ninja device ID, IT Glue document ID, checklist ID+item ID)
  - `rule_version` (lets future logic changes intentionally fork identities)

Task creation rule:

- If `gap_key` has open linked task => do not recreate.
- If `gap_key` existed but task closed while gap still present => either reopen existing task (preferred) or create new and relink.

## 7) Health scoring model

Per client, compute both weighted and raw score:

- For each check category:
  - `pass_count`, `fail_count`
  - category pass % = `pass_count / (pass_count + fail_count)`
- Overall score (default weighted equally by category):
  - `score_pct = mean(category_pass_pct)`
- Store prior run score and compute:
  - `delta_pct = current - previous`
  - 4-week trend direction (`up|flat|down`) by simple linear slope or moving average delta.

Include in summary report:

- Score now, previous score, delta.
- Open gaps by category.
- Newly created tasks count.
- Resolved gaps count.

## 8) Shared Bifrost components to reuse

- `integrations.get/list_mappings/upsert_mapping` for client/entity mapping patterns.
- Existing vendor workflow/data-provider shape under `features/*/workflows`.
- Existing IT Glue and NinjaOne modules for auth/bootstrap and baseline endpoint access.
- Existing HaloPSA integration module for task creation calls.
- Event schedule source support (cron) for weekly execution.

## 9) Phased build order

### Phase 1 — Foundation and seam

- Add normalized boundary interfaces + DTOs.
- Add IT Glue adapter implementation inside single dedicated module boundary.
- Add NinjaOne adapter abstraction for device inventory retrieval.
- Add HaloPSA task sink abstraction.

### Phase 2 — Core checks + dry-run

- Implement two highest-value checks first:
  - Ninja device missing IT Glue configuration.
  - Missing required configuration fields.
- Implement deterministic gap keys and persistence.
- Add dry-run workflow returning report without creating tasks.

### Phase 3 — Tasking + dedupe

- Implement HaloPSA task create/reopen/dedupe link behavior.
- Add weekly scheduler wiring.
- Add summary output payload and persisted run history.

### Phase 4 — Remaining checks + scoring/trend

- Password rotation age.
- Missing required contact roles.
- Document staleness.
- Checklist incompleteness aging.
- Final health scoring and trend reporting.

### Phase 5 — Hardening

- Unit tests for each check function and gap key stability.
- Integration tests for adapters with mocked payloads.
- Backfill/migration strategy for run history and gap/task links.

## 10) Explicit IT Glue ↔ future Hudu seam (required)

All IT Glue-specific API behavior must stay in one module and expose only normalized, vendor-neutral contracts.

### 10.1 Boundary module contract

Create a dedicated module (example):

- `modules/doc_platform_adapter.py` (interface)
- `modules/doc_platform_itglue.py` (IT Glue implementation)
- future: `modules/doc_platform_hudu.py` (Hudu implementation)

### 10.2 Interface (vendor-neutral)

```python
class DocumentationPlatformAdapter(Protocol):
    async def list_clients(self) -> list[DocClient]: ...
    async def list_config_items(self, client_ref: str) -> list[DocConfigItem]: ...
    async def list_passwords(self, client_ref: str) -> list[DocPassword]: ...
    async def list_contacts(self, client_ref: str) -> list[DocContact]: ...
    async def list_documents(self, client_ref: str) -> list[DocDocument]: ...
    async def list_checklists(self, client_ref: str) -> list[DocChecklist]: ...
```

Rules:

- No IT Glue field names (kebab-case attrs, endpoint names, resource names) outside `doc_platform_itglue.py`.
- No IT Glue resource IDs outside adapter outputs except opaque `source_ref` strings.
- Audit engine consumes only normalized dataclasses.

### 10.3 Migration path to Hudu

When Midtown switches to Hudu:

- Implement `doc_platform_hudu.py` to same interface.
- Switch adapter binding in one place (factory/config).
- Leave audit checks, gap models, dedupe, scoring, and HaloPSA task logic untouched.

## 11) Security/auth notes

- IT Glue auth should continue to source API key from integration config; if centrally sourced from Azure Key Vault, inject into integration config or runtime secret provider without changing audit logic.
- Keep key usage global (Midtown-level) while enforcing per-client filtering in query layer.

## 12) Ambiguities to resolve before coding

1. **Task reopen policy:** reopen old HaloPSA task vs create new task when a historical gap reappears?
2. **Task assignment source:** is documentation owner always a HaloPSA agent ID, or can it be role/team-based?
3. **Required-field mapping:** canonical mapping for `ip_address`, `os_version`, `assigned_contact`, `location` across IT Glue configuration types.
4. **Password rotation field:** preferred timestamp source (`last_rotated_at` equivalent vs fallback `updated-at`).
5. **Checklist aging definition:** age measured from checklist update date, checklist item due date, or item created date?
6. **Health scoring weights:** equal category weighting vs business-priority weighting.
7. **Disabled clients behavior:** should disabling a client auto-resolve/close prior open gap tasks?
8. **Run failure semantics:** if one check category fails API retrieval, should score be partial, fail-closed, or carry-forward previous?

## 13) Suggested first implementation slice

Start with a single workflow:

- `features/itglue_doc_audit/workflows/run_weekly_audit.py`

Initial scope:

- load enabled clients
- run first two checks
- produce dry-run summary with deterministic gap keys
- no task creation yet (feature flag)

This gives a safe baseline for validating data contracts and seam design before introducing task side effects.
