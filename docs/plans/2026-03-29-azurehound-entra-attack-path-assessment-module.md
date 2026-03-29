# AzureHound-Powered Entra ID Attack Path Assessment Module (Pre-Implementation Plan)

## Skill usage note
Used skills: `bifrost-build` (repo architecture + authored surfaces), `bifrost-integration-authoring` (module/workflow patterns), and `bifrost-app-authoring` (app UX/routing patterns), because this request spans integration, workflows, and app UI planning.

## 1) BiFrost repo structure review (before coding)

### What appears to be the current authored pattern

- **Integration/client logic** lives under `modules/` (e.g., `modules/microsoft/*`, `modules/googleworkspace.py`).
- **Integration workflows and data providers** live under `features/{vendor}/workflows/` with decorators (`@workflow`, `@data_provider`).
- **Reusable workflow SDK wrappers** for some vendors live in `workflows/{vendor}/`.
- **UI apps** live under `apps/{slug}/` using:
  - `pages/index.tsx` as the entry view
  - `_layout.tsx` root layout wrapper
  - `components/*` for app-local components
  - `app.yaml` metadata
- **Platform/runtime API** lives under `api/src/` with typed FastAPI routers in `api/src/routers/*`.
- **Manifest-like metadata** exists in `.bifrost/integrations.yaml` and `.bifrost/workflows.yaml` and is still used tactically for workflow/integration registration in this fork.

### Routing/UI conventions observed

- App pages use Bifrost hooks (`useWorkflowQuery`, `useWorkflowMutation`) with **workflow UUIDs** as route/action bindings.
- App root layout is a fixed-height shell pattern (`<Outlet />`, `h-full`, overflow control).
- Configuration and table-heavy screens are normal and use shared UI primitives like `MultiCombobox` and dialog components.

### Integration pattern conventions observed

- Module clients encapsulate vendor API details.
- Sync workflows:
  1. list vendor entities,
  2. match/create Bifrost orgs,
  3. upsert `IntegrationMapping`.
- Data providers return UI-friendly `{ value, label }` lists.

---

## 2) Minimum Graph/Azure permissions for per-tenant app registration

For **full AzureHound collection coverage** (Entra + AzureRM), baseline should be:

1. **Entra Directory Readers role** (directory role assignment, active).
2. **Microsoft Graph application permission**: `Directory.Read.All` (admin consent).
3. **Microsoft Graph application permission**: `RoleManagement.Read.All` (admin consent).
4. **Azure RBAC Reader** on all subscriptions (prefer management-group/root scope).

Rationale: SpecterOps explicitly documents the need for both Directory Reader role and `Directory.Read.All`/`RoleManagement.Read.All` for complete collection in AzureHound deployments.

> Note: Tenant-specific edge cases can require additional read scopes depending on hardening and API evolution; module should preflight and report missing permissions explicitly before scan execution.

---

## 3) Proposed ephemeral BloodHound CE Docker Compose config

### Compose model

Use one ephemeral stack **per assessment run**, with unique project name and isolated volumes/network.

```yaml
# generated at runtime as /tmp/bifrost-bhce-{assessment_id}.yml
services:
  neo4j:
    image: neo4j:5
    container_name: bhce-neo4j-${ASSESSMENT_ID}
    environment:
      - NEO4J_AUTH=neo4j/${NEO4J_PASSWORD}
    healthcheck:
      test: ["CMD", "cypher-shell", "-u", "neo4j", "-p", "${NEO4J_PASSWORD}", "RETURN 1"]
      interval: 10s
      timeout: 5s
      retries: 20
    volumes:
      - neo4j_data:/data
      - neo4j_logs:/logs

  bloodhound:
    image: specterops/bloodhound:latest
    container_name: bhce-app-${ASSESSMENT_ID}
    depends_on:
      neo4j:
        condition: service_healthy
    environment:
      # Final env keys should be aligned to CE image docs/version
      - BH_HOST=0.0.0.0
      - BH_PORT=8080
      - NEO4J_URI=bolt://neo4j:7687
      - NEO4J_USER=neo4j
      - NEO4J_PASSWORD=${NEO4J_PASSWORD}
    ports:
      - "127.0.0.1:${BH_PORT}:8080"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/api/v2/version"]
      interval: 10s
      timeout: 5s
      retries: 30

volumes:
  neo4j_data:
  neo4j_logs:
```

### Lifecycle contract in Bifrost

- `up`: `docker compose -p bhce-{assessment_id} -f ... up -d`
- health poll (`/api/v2/version`)
- run ingest/query/report
- `down`: `docker compose ... down --volumes --remove-orphans`
- post-check: assert no leftover containers/volumes by project label

If teardown fails, assessment transitions to `cleanup_required` (operator-visible) and includes orphan artifact identifiers.

---

## 4) Mapping requirements to existing BiFrost module/routing patterns

## Proposed authored surfaces

- `modules/azurehound.py`:
  - app credential resolution from AKV reference
  - AzureHound command builder/invocation
  - structured error normalization
- `modules/bloodhound_ce.py`:
  - ephemeral compose lifecycle manager
  - API auth/token handling
  - ingest + query execution client
- `features/entra_attack_path/workflows/`:
  - `run_assessment.py` (full orchestration)
  - `list_assessments.py` (history)
  - `get_assessment.py` (details/report link)
  - `save_client_config.py`, `get_client_config.py`
  - `data_providers.py` (org + AKV reference picker)
- `apps/entra-attack-path/`:
  - `pages/index.tsx` (config + manual run)
  - `pages/history.tsx` (assessment history)
  - `pages/report.tsx` (markdown viewer/download)
  - shared dialogs/components in `components/`
- Tactical `.bifrost/*` updates for integration/workflow registration.

### API/platform usage

Prefer workflow execution and Bifrost schedule entities rather than introducing new ad hoc API routers unless unavoidable.

---

## 5) Full module architecture proposal

## 5.1 Data model

### Client assessment config (per Bifrost org)

```json
{
  "client_id": "org_uuid",
  "tenant_id": "entra_tenant_guid",
  "akv_credential_reference": "akv://vault/secret/version-or-key",
  "scan_schedule": "0 3 * * 1",
  "enabled": true,
  "severity_threshold": "high",
  "last_assessment_id": "assessment_uuid | null",
  "assessment_history": ["assessment_uuid", "..."]
}
```

### Assessment run record

```json
{
  "assessment_id": "uuid",
  "client_id": "org_uuid",
  "triggered_by": "schedule|operator",
  "status": "queued|running|complete|failed|cleanup_required",
  "started_at": "ISO8601",
  "completed_at": "ISO8601|null",
  "finding_summary": {
    "critical": 0,
    "high": 0,
    "medium": 0,
    "low": 0,
    "informational": 0
  },
  "report_path": "repo/file-store path",
  "halo_ticket_id": "string|null",
  "query_results": [
    {
      "query_name": "All Global Administrators",
      "query_source": "prebuilt",
      "query_text": "MATCH ...",
      "row_count": 4,
      "result": {}
    }
  ],
  "errors": []
}
```

## 5.2 Orchestration workflow (`run_assessment`)

1. Validate client config + enabled status.
2. Create assessment run row (`queued` -> `running`).
3. Create temporary workspace + compose file.
4. Start BH CE stack + health check.
5. Resolve AKV credential reference into service principal credentials.
6. Run AzureHound to generate JSON output.
7. Upload collection to BH CE ingest API.
8. Poll ingest/analysis completion.
9. Execute v1 built-in query set and persist structured query results.
10. Build markdown report (with prior-run delta if present).
11. Persist report artifact path.
12. Evaluate threshold and create HaloPSA ticket when required.
13. Update assessment status (`complete`/`failed`).
14. Always attempt teardown in `finally`; if teardown fails, set `cleanup_required`.

## 5.3 AzureHound execution recommendation

### Recommended: run AzureHound as a subprocess inside Bifrost worker container

**Why this fits Bifrost better now**
- Matches existing workflow execution model (single workflow process controls all steps).
- Avoids coupling collector runtime to ephemeral BH stack startup.
- Easier secret handling (AKV -> memory/env -> subprocess), fewer cross-container hops.
- Simpler failure attribution in one execution log stream.

**Tradeoffs**
- Requires AzureHound binary availability in worker image or mounted tool cache.
- Larger worker image if bundled.

### Alternative: sidecar in ephemeral compose stack

**Pros**: stronger runtime isolation, self-contained per-assessment runtime.
**Cons**: more complex secret injection and log collection, more compose orchestration states, harder debugging.

## 5.4 Error handling map

- **AzureHound auth/permission/API failures**: fail fast; no ingest attempt.
- **BH CE startup/health failure**: fail assessment + cleanup attempt; if partial cleanup fails -> `cleanup_required`.
- **Ingestion timeout/incomplete**: fail + teardown.
- **Report generation failure**: fail but retain captured `query_results`.
- **Teardown failure**: explicit `cleanup_required` state + surfaced container/volume names.

---

## 6) Shared BiFrost components/patterns to reuse

1. **Org selection pattern** used by integration mapping/data providers (`{value,label}` provider outputs).
2. **Workflow hook pattern** (`useWorkflowQuery`/`useWorkflowMutation`) with UUID binding.
3. **Scheduling primitives** through existing schedule entities/workflow triggers.
4. **HaloPSA integration wrappers** under `workflows/halopsa/*` for ticket creation.
5. **Shared UI primitives** (multi-select comboboxes, dialogs, table/status patterns) used in existing apps.
6. **Integration config schema mechanics** in `.bifrost/integrations.yaml` for secrets/string fields.

---

## 7) Built-in BloodHound CE Cypher query availability + programmatic executability

## What can be executed programmatically

- **Yes**: Cypher can be executed via REST using `POST /api/v2/graphs/cypher`.
- **Yes**: Saved/public queries can be listed/exported/imported via `/api/v2/saved-queries*` endpoints.

## Important nuance

- There is **no single API endpoint labeled “list all built-in queries”** in CE docs.
- Practical enumeration path for built-ins:
  1. Use SpecterOps Query Library release artifact (contains all existing pre-built + community queries).
  2. Filter `prebuilt=true` and `platforms` includes `Azure` for Entra-focused v1 set.

### Current observed enumeration (from latest Query Library release artifact)

- Total queries: **199**
- Prebuilt queries: **96**
- Azure-platform queries (all): **47**
- Azure + prebuilt queries: **28**

Azure+prebuilt names currently include:

1. Synced Entra Users with Entra Admin Role approval (direct)
2. On-Prem Users synced to Entra Users with Entra Group Membership
3. All service principals with Microsoft Graph App Role assignments
4. Synced Entra Users with Entra Admin Role direct eligibility
5. Devices with unsupported operating systems
6. Synced Entra Users with Entra Admin Role approval (group delegated)
7. All members of high privileged roles
8. Entra Users with Entra Admin Roles group delegated eligibility
9. On-Prem Users synced to Entra Users with Azure RM Roles (direct)
10. Foreign principals in Tier Zero / High Value targets
11. Synced Entra Users with Entra Admin Roles group delegated eligibility
12. All Global Administrators
13. Shortest paths from Entra Users to Tier Zero / High Value targets
14. All service principals with Microsoft Graph privilege to grant arbitrary App Roles
15. Shortest paths to privileged roles
16. On-Prem Users synced to Entra Users with Entra Admin Roles (group delegated)
17. On-Prem Users synced to Entra Users that Own Entra Objects
18. Entra Users with Entra Admin Role approval (direct)
19. Entra Users with Entra Admin Role approval (group delegated)
20. Entra Users synced from On-Prem Users added to Domain Admins group
21. Disabled Tier Zero / High Value principals (AZ)
22. Shortest paths from Azure Applications to Tier Zero / High Value targets
23. Entra Users with Entra Admin Role direct eligibility
24. Tier Zero / High Value external Entra ID users
25. On-Prem Users synced to Entra Users with Entra Admin Roles (direct)
26. Shortest paths to Azure Subscriptions
27. On-Prem Users synced to Entra Users with Azure RM Roles (group delegated)
28. Tier Zero AD principals synchronized with Entra ID

### Programmatic vs GUI-only assessment

- **Programmatic**:
  - run cypher query endpoint,
  - saved query CRUD/import/export,
  - attack path findings endpoints.
- **GUI-only for v1 assumption**:
  - none required for executing queries/report generation.
  - UI convenience features (manual explore interactions) are not required for this module.

---

## 8) Proposed phased build order (earliest value first)

### Phase 0 — Foundations
- Define config schema and assessment run schemas.
- Implement AKV credential resolution abstraction and validation/preflight.

### Phase 1 — Core pipeline (no UI)
- Implement ephemeral BH CE lifecycle manager.
- Implement AzureHound invocation + ingest + fixed small query pack execution.
- Persist raw query results + markdown report.

### Phase 2 — Operationalization
- Add schedule-triggered runs.
- Add threshold logic + HaloPSA ticket creation.
- Add cleanup-required detection and operator-visible errors.

### Phase 3 — UI
- Config screen, manual run CTA with confirm, live status panel.
- Assessment history with status/finding summary/report/ticket links.
- Report viewer + markdown download.

### Phase 4 — Hardening
- Retry policies/timeouts tuning.
- Guardrails for orphan cleanup reconciliation job.
- Comparative reporting quality improvements and remediation text tuning.

---

## 9) Gaps / ambiguities to resolve before implementation

1. **Final BH CE image/env contract**: confirm exact compose env keys for chosen CE tag.
2. **AKV secret schema**: define canonical JSON shape for per-tenant app credentials.
3. **Storage location** for report artifacts and structured query blobs (table vs file + pointer).
4. **Severity mapping rubric** from query outputs to low/medium/high/critical.
5. **HaloPSA target defaults** (board/queue/priority/status) and override hierarchy.
6. **Live status transport** in UI (polling interval vs websocket events).
7. **Concurrency policy**: whether one org may run concurrent assessments.
8. **Retention policy** for assessment history and raw query payload size controls.
9. **Permissions drift strategy**: whether to auto-run preflight checks outside scheduled window.

---

## External references consulted

- BloodHound API overview and endpoint reference: `https://bloodhound.specterops.io/reference/overview`
- BloodHound Cypher endpoints:
  - list saved queries: `https://bloodhound.specterops.io/reference/cypher/list-saved-queries`
  - run cypher: `https://bloodhound.specterops.io/reference/cypher/run-a-cypher-query`
- AzureHound CE docs: `https://bloodhound.specterops.io/collect-data/ce-collection/azurehound`
- AzureHound service principal requirement reference (enterprise doc, used as permission baseline):
  `https://bloodhound.specterops.io/install-data-collector/install-azurehound/system-requirements`
- Query enumeration source: `https://github.com/SpecterOps/BloodHoundQueryLibrary/releases/latest/download/Queries.json`
