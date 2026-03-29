# Maester Compliance Reporting Module Proposal (Bifrost)

## Context

Goal: Wrap [Maester](https://github.com/maester365/maester) into a scheduled, multi-tenant compliance reporting service in Bifrost.

Primary requirements:
- Per-client execution (scheduled + on-demand)
- Service principal auth via per-tenant app creds stored in Azure Key Vault
- Parse structured Maester output
- Persist scan/rule results
- Generate markdown report
- Open HaloPSA ticket only when **new failures** appear and meet severity threshold

---

## 1) Bifrost Repo Structure and Reuse Patterns

### Relevant platform/runtime surfaces
- `api/src/models/orm/events.py` implements an eventing model already supporting schedule sources and org scoping, with `EventSource` + `ScheduleSource` + `EventSubscription`. This is the right primitive for scheduled triggers.
- `api/src/jobs/schedulers/cron_scheduler.py` already evaluates CRON schedules and queues event deliveries.
- `api/src/services/events/processor.py` already supports delivery fan-out and template-based `input_mapping` into workflow parameters.
- `api/src/services/execution/README.md` and execution services define async queue-backed, isolated workflow execution with persisted execution history and logs.

### Userland authoring patterns
- Existing feature workflows use `@workflow` functions under `features/<feature>/workflows/*.py`.
- Multi-tenant mapping patterns exist in `features/microsoft_csp/workflows/link_tenant.py` and table-scoped status storage (`tables.upsert/query/get`) in the same feature.
- Shared reusable integrations/utilities are placed in `modules/` and `modules/extensions/` (e.g., HaloPSA helper methods in `modules/extensions/halopsa.py`).

### Implication for this module
Use:
- **Event schedule sources + subscriptions** for recurring scans
- **Workflows** for orchestration (scan, diff, report, ticket)
- **Tables** for per-tenant scan baselines/results metadata
- **HaloPSA extension helper** for ticket creation and idempotency

---

## 2) Container Image + Headless PowerShell Invocation (Maester)

### Proposed execution topology
Use a dedicated worker image for this workflow family:

- Base image: `mcr.microsoft.com/powershell:7.4-debian-12`
- Add modules at build time:
  - `Maester`
  - `Microsoft.Graph.Authentication` (if not pulled transitively)
  - `Pester` (pin major version used by Maester + Midtown scaffold)
- Add a thin PowerShell entry script (`run-maester.ps1`) invoked by Python workflow via subprocess.

### Runtime contract
Python workflow invokes:

```bash
pwsh -NoLogo -NoProfile -NonInteractive -File /opt/bifrost/scripts/run-maester.ps1 \
  -TenantId <tenant_id> \
  -ClientId <client_id> \
  -ClientSecret <client_secret> \
  -SuiteMode <builtin|hipaa|both> \
  -OutputJson /tmp/maester/result.json
```

### `run-maester.ps1` responsibilities
1. Retrieve/receive credentials (from workflow input; workflow resolves AKV ref prior to call).
2. Authenticate non-interactively:
   - `Connect-MgGraph -TenantId ... -ClientId ... -ClientSecret ...`
3. Run selected Maester test suites (built-in, HIPAA scaffold, or both).
4. Emit **structured JSON** to known path with stable schema.
5. Exit non-zero if framework execution fails (not test failures).

### Output schema to enforce
Even if Maester/Pester native output varies by version, normalize into this JSON contract in `run-maester.ps1`:

```json
{
  "scan": {
    "started_at": "ISO-8601",
    "completed_at": "ISO-8601",
    "tenant_id": "...",
    "suite_mode": "builtin|hipaa|both",
    "maester_version": "...",
    "pester_version": "..."
  },
  "summary": {
    "total": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "inconclusive": 0
  },
  "findings": [
    {
      "id": "stable-test-id",
      "title": "...",
      "suite": "builtin|hipaa",
      "severity": "low|medium|high|critical",
      "status": "passed|failed|skipped|inconclusive",
      "message": "...",
      "evidence": {"...": "..."},
      "remediation": "..."
    }
  ]
}
```

This avoids brittle parsing in Python and makes failure-diff logic deterministic.

---

## 3) Proposed Data Model, API, Scheduling, Report Pipeline

## Data model (new)

Create a small compliance domain (likely in `api/src/models/orm/` + repository + contracts):

1. `compliance_profiles`
- `id` (UUID)
- `organization_id` (UUID, unique per client per module)
- `name`
- `enabled` (bool)
- `suite_mode` (`builtin|hipaa|both`)
- `severity_threshold` (`low|medium|high|critical`)
- `akv_secret_ref` (string; pointer, not secret)
- `event_source_id` (FK to schedule source, nullable for on-demand only)
- timestamps/audit

2. `compliance_scans`
- `id` (UUID)
- `profile_id` (FK)
- `execution_id` (FK to executions table)
- `trigger_type` (`scheduled|manual|retry`)
- `status` (`running|completed|failed`)
- `started_at`, `completed_at`
- summary counters (`total`, `passed`, `failed`, `skipped`, `inconclusive`)
- `raw_result` (JSONB, optional full normalized payload)

3. `compliance_findings`
- `id` (UUID)
- `scan_id` (FK)
- `finding_key` (stable, e.g. `${suite}:${id}`)
- `title`, `suite`, `severity`, `status`
- `message`, `evidence` (JSONB), `remediation`

4. `compliance_failure_baseline`
- `profile_id`
- `finding_key`
- `first_seen_scan_id`
- `last_seen_scan_id`
- `is_open`
- supports detection of **new failures** each run

5. `compliance_reports`
- `scan_id`
- `markdown_body`
- `report_hash`
- `storage_uri` (optional if persisted to object storage)
- `halopsa_ticket_id` / `halopsa_match_id` (nullable)

## API routes (admin/operator)

Namespace suggestion: `/api/compliance/maester`

- `POST /profiles` create per-client profile
- `GET /profiles` list profiles (org-filtered + global admin)
- `GET /profiles/{id}`
- `PATCH /profiles/{id}` (toggle enabled, cron, suite, threshold, AKV ref)
- `POST /profiles/{id}/run` on-demand scan
- `GET /profiles/{id}/scans?limit=...`
- `GET /scans/{scan_id}` summary + report + ticket status
- `GET /scans/{scan_id}/findings`
- `POST /scans/{scan_id}/report/regenerate`

## Scheduling model

Reuse existing event infrastructure instead of custom scheduler:
1. Create `event_source` with `source_type=schedule` and profile-specific CRON/timezone.
2. Add subscription to a workflow `run_maester_profile`.
3. Use `input_mapping` to pass profile id and scheduled timestamp.
4. Existing `cron_scheduler.py` handles trigger cadence and delivery queueing.

## Report generation pipeline

Workflow graph:
1. `run_maester_profile(profile_id, trigger_context)`
2. Resolve AKV secret reference → runtime credentials (never persist plain secrets)
3. Execute PowerShell Maester runner container step
4. Parse normalized JSON
5. Persist scan + findings
6. Compare against baseline/open failures
7. Generate markdown report sections:
   - Executive summary
   - Counts by severity/status
   - New failures (highlight)
   - Existing unresolved failures
   - Passed controls
   - Remediation guidance
8. If `new_failures >= threshold` (by severity policy), create HaloPSA ticket
9. Save ticket linkage and final scan status

---

## 4) Shared Bifrost Components to Reuse

- **Scheduling/eventing:** `EventSource`, `ScheduleSource`, `EventSubscription`, cron scheduler, event processor.
- **Execution/runtime:** queue-backed workflow execution engine and persisted logs/executions.
- **Org scoping/multi-tenancy:** existing organization-scoped patterns in repositories/models and workflow context.
- **State storage from workflows:** `tables` SDK for tenant state snapshots/baselines where appropriate.
- **HaloPSA automation helper:** `modules/extensions/halopsa.py` for ticket creation/idempotency and notes.
- **Microsoft tenant mapping patterns:** `features/microsoft_csp` approach for tenant↔org link and status records.

---

## 5) Phased Build Order

### Phase 0 — Validation spike (no durable schema yet)
- Build container + `run-maester.ps1`.
- Validate service principal auth and headless execution.
- Lock output normalization contract with sample payload fixtures.

### Phase 1 — Core scan orchestration
- Add profile model/routes (minimum fields).
- Add on-demand scan workflow.
- Store scans + findings.

### Phase 2 — Scheduled execution
- Wire profile CRON to event source + subscription.
- Add enable/disable behavior and schedule updates.

### Phase 3 — Diffing + report
- Add baseline/open-failure tracking.
- Add markdown generation and persisted report artifact.

### Phase 4 — HaloPSA escalation
- Add severity-threshold gating + ticket creation on *new* failures.
- Add ticket idempotency/match strategy (one open ticket per profile/finding window).

### Phase 5 — Hardening
- Unit tests for parser/diff/report logic.
- E2E for scheduled trigger to delivery to ticket flow.
- Observability/metrics, retry semantics, failure handling playbooks.

---

## 6) Ambiguities to Resolve Before Coding

1. **AKV access path:**
   - Should Bifrost API resolve AKV refs centrally, or should runner container query AKV directly?
   - If central, what service identity is used per tenant?

2. **Credential shape:**
   - Exact AKV secret schema (single JSON secret vs multiple secret refs for tenant/client/secret).

3. **Maester output contract:**
   - Preferred source of truth: Pester result object vs Maester-native object(s).
   - Required stable finding identifier for diffing.

4. **HIPAA scaffold packaging:**
   - Where does Midtown HIPAA scaffold live in repo/image? Private artifact or checked-in tests?
   - Versioning strategy across clients.

5. **Severity mapping policy:**
   - If Maester test metadata lacks severity, how is severity assigned (static map file? tags? default)?

6. **HaloPSA routing details:**
   - Which client/site/team/ticket type to use per tenant?
   - One ticket per scan vs one ticket per newly failed control?

7. **Ticket lifecycle behavior:**
   - Should resolved findings auto-close or append-note existing ticket only?

8. **Schedule ownership model:**
   - One profile per org or multiple profiles per org (e.g., different suites/thresholds)?

9. **Data retention policy:**
   - How long to keep raw scan payloads and detailed finding history?

10. **Compliance evidence expectations:**
   - Are report artifacts required in immutable storage for audits, and for how long?

