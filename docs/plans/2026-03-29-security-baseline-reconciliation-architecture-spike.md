# Security Baseline Reconciliation Module — Architecture & Research Spike

**Date:** 2026-03-29  
**Status:** Proposal (no implementation yet)

## 1) Repository Recon Summary

This spike is grounded in current Bifrost patterns visible in repo source:

- **Integration clients and org mapping pattern** already exist per vendor in `modules/*` and `features/*/workflows/sync_*`.  
- **Execution + scheduling primitives** already exist via workflows, async execution queueing, and event schedules (`event_sources` + `schedule_sources`).  
- **Audit primitives** already exist (`audit_logs`, execution logs), but today they are generic and not yet a first-class control-remediation ledger.

The proposed module should therefore be implemented as a **new cross-platform feature layer** that reuses these primitives rather than inventing a parallel runner.

---

## 2) Platform Automation Capability Audit (Repo-Evidenced)

> Scope note: This section is based on currently committed module clients and wrappers. It is an audit of what Bifrost can call today (or has clear method affordances for), not a legal/compliance guarantee that every tenant has API rights enabled.

### Entra ID / M365 (via CIPP + Graph integration)

- **Read available in repo (CIPP wrapper):** users, groups, licenses, alerts, incidents, Defender state, standards, CA policies, devices.  
- **Write in current CIPP wrapper:** no dedicated write helpers are currently exposed in `modules/cipp.py`; it has a generic `call(..., method="POST")` path but no strongly-typed remediation methods yet.
- **Implication:** For phase 1/2, treat M365 controls as mostly **observe/task-only** unless we add explicit, testable write operations (either via CIPP API functions or direct Graph client module).

### NinjaOne

- **Read/write breadth:** generated NinjaOne client includes extensive PATCH/POST/DELETE operations and a scoped wrapper for org updates.
- **Likely low-risk automation candidates:** policy assignment, scripted actions, patch scans/applies, agent-related operations where the endpoint already exists and has idempotent semantics.
- **Implication:** strongest early platform for low-risk closed-loop remediation.

### Huntress

- **Read available:** organizations, agents, incidents/remediations, signals/reports.
- **Write available:** incident resolution and remediation approval/rejection endpoints, plus org update endpoint.
- **Gap for this project:** no existing helper for “deploy/enable agent” in Huntress module; coverage remediation will likely require a cross-tool action (e.g., launch via NinjaOne script) rather than Huntress-native enrollment.

### Cove Data Protection

- **Read available:** partners/customers, devices, vaults.
- **Write available:** create/update customer, and `EnableRecoveryTesting` for device-level recovery testing.
- **Not yet explicit in wrapper:** no direct “assign backup job policy to arbitrary device” helper in current module API surface.
- **Implication:** coverage checks are straightforward; auto-remediation may require extending client methods and validating safe idempotency.

### ConnectSecure

- **Read available:** companies, assets.
- **Write available in wrapper:** company metadata update.
- **Gap for this project:** no existing helper for findings lifecycle actions in current module surface.
- **Implication:** treat as **signal source** first (observe/task), automate only after endpoint-level validation for finding state transitions.

### HaloPSA (for task escalation)

- Repo has mature ticket creation/update tooling in shared extensions/tools, suitable as the default task sink for non-automated/high-risk gaps.

---

## 3) Proposed Baseline Schema (Versioned, Human-Editable, Extensible)

## Format decision

Use **versioned JSON document stored in config/system config**, with optional YAML editor UX in app layer:

- Canonical storage: JSON (DB-safe, schema-validated, diffable).
- Operator editing: YAML-friendly UI transpiles to JSON.
- Versioning: immutable revision records with semantic version + checksum + created_by/created_at.

### Top-level schema (v1)

```yaml
schema_version: 1
baseline_id: "baseline-client-acme"
baseline_version: "2026.03.29"
client_scope:
  organization_id: "<bifrost-org-uuid>"
metadata:
  name: "Acme Security Baseline"
  description: "Managed baseline for Acme"
  owner: "security@midtowntg.com"
  tags: ["hipaa", "gold-tier"]
controls:
  - control_id: "m365.mfa.enforcement"
    platform: "m365"
    domain: "identity"
    desired_state:
      mode: "all_licensed_users"
      exempt_roles: ["BreakGlassAdmin"]
      exempt_users: ["svc-legacy@acme.com"]
    severity: "critical"
    remediation:
      default_mode: "confirm"
      risk_level: "medium"
      action_ref: "m365.mfa.enforce_policy"
  - control_id: "ninja.patch.compliance"
    platform: "ninjaone"
    domain: "patching"
    desired_state:
      critical_deadline_days: 14
      high_deadline_days: 30
      compliance_target_percent: 100
    severity: "high"
    remediation:
      default_mode: "auto"
      risk_level: "low"
      action_ref: "ninja.patch.assign_policy"
  - control_id: "cove.backup.coverage.servers"
    platform: "cove"
    domain: "backup"
    desired_state:
      include_device_source: "ninjaone_managed_servers"
      require_active_job: true
    severity: "critical"
    remediation:
      default_mode: "confirm"
      risk_level: "medium"
      action_ref: "cove.backup.enable_or_assign"
```

### Why this works

- **Expressive:** each control has platform-specific `desired_state` payload.
- **Extensible:** add controls without schema rewrites.
- **Versionable:** immutable version records.
- **Human-editable:** YAML UI view.

### Validation strategy

- Global JSON schema validates envelope + common fields.
- Per-platform/per-control validator plugins validate `desired_state`.
- Invalid controls are quarantined (not executed) with explicit operator feedback.

---

## 4) Reconciliation Engine Architecture

## Pipeline

1. **Planner:** resolve enabled clients, baseline version, per-control mode overrides, schedule context.
2. **Collectors:** fetch observed state per platform (parallel with bounded concurrency).
3. **Normalizers:** convert vendor payloads into canonical control evidence docs.
4. **Diff engine:** compare normalized evidence to `desired_state` per control.
5. **Finding builder:** emit finding records with severity/remediable/action metadata.
6. **Policy router:** decide observe/task/confirm/auto per finding.
7. **Action executor:** execute auto actions or queue confirmation/task.
8. **Ledger writer:** persist reconciliation run, findings snapshot, and actions.

## Canonical entities

- `BaselineDocument` + `BaselineVersion`
- `ReconciliationRun`
- `ControlEvaluation` (per control per run)
- `Finding`
- `RemediationAction` (planned/executed)
- `ApprovalRequest` (for confirm-gated actions)
- `ComplianceLedgerEntry` (immutable audit/event trail)

## Execution model

- Implement as standard Bifrost workflows (org-scoped), scheduled via existing event schedule system.
- One "master reconcile" workflow fans out to per-platform collectors and control evaluators.
- Use existing async queueing/execution tracking for run lifecycle.

---

## 5) Remediation Taxonomy + Confirmation Gates

## Taxonomy

Each control maps to:

- `risk_level`: `low | medium | high`
- `automation_class`: `state_enforcement | assignment | incident_response | notification`
- `default_mode`: `auto | confirm | task_only | observe`
- `idempotency`: `idempotent | best_effort | non_idempotent`
- `rollback_strategy`: `none | partial | full`

## Gate policy

- **Low-risk / auto:** executes immediately if per-client override allows auto.
- **Medium-risk / confirm:** creates approval request with:
  - proposed change summary
  - before-state snapshot
  - expected after-state
  - timeout/expiry
  - approver role policy
- **High-risk / task-only:** never auto-executes; creates HaloPSA task with rich context.

## Confirmation mechanism design

- New `ApprovalRequest` record keyed to action plan.
- Approve/reject actions through workflow/API endpoint.
- Approval captures operator identity + timestamp + optional rationale.
- Expired approvals become task-only fallback.

---

## 6) Audit Trail Data Model (HIPAA-Oriented)

Existing `audit_logs` is useful but too generic for full remediation lineage. Add dedicated immutable ledger:

### `compliance_ledger`

- `id`
- `timestamp`
- `organization_id`
- `run_id`
- `finding_id` (nullable)
- `action_id` (nullable)
- `actor_type` (`automated` / `user` / `system`)
- `actor_id` (nullable user UUID)
- `platform`
- `control_id`
- `event_type` (`detected`, `proposed`, `approved`, `rejected`, `executed`, `failed`, `task_created`)
- `before_state` (JSONB)
- `after_state` (JSONB)
- `metadata` (JSONB: request IDs, API response hashes, error codes)

### Retention and integrity

- Append-only writes (no updates except redaction policy if legally required).
- Hash chain optional for tamper-evidence.
- Indexed by org/time/platform/control for audit retrieval.

---

## 7) Shared Bifrost Components to Reuse

1. **Integration + org mapping model** (`integrations`, `integration_mappings`) for per-client entity scoping.
2. **Config storage** (`configs` / `system_configs`) for baseline refs + per-client overrides.
3. **Workflow execution pipeline** (Redis + RabbitMQ + execution records) for reconciliation runs.
4. **Event schedule system** (`event_sources` + `schedule_sources`) for cron-driven reconcile cadence.
5. **Execution logs + ROI/execution metadata** for operator observability.
6. **HaloPSA shared tooling** for ticket/task escalation.

---

## 8) Proposed Phased Build Order

## Phase 0 — Data model + read-only collectors

- Baseline schema + storage/versioning.
- Per-platform collectors + normalizers.
- Reconciliation run + finding persistence.
- No remediation; output is visibility dashboard/report.

## Phase 1 — Observability-first drift detection

- Scheduled reconciliations.
- Finding severity and aging.
- HaloPSA ticket creation for selected high-risk gaps.
- SLOs for run success/freshness.

## Phase 2 — Low-risk automation only

- Enable auto for explicitly low-risk, idempotent controls (initially NinjaOne-heavy).
- Add dry-run mode and action simulation output.
- Add rollback metadata and failure retries with guardrails.

## Phase 3 — Operator-confirmed remediation

- Approval request workflow + UI/API.
- Medium-risk controls execute only with explicit approvals.
- Approval expiry + revalidation before execute.

## Phase 4 — Expand platform control coverage

- Add deeper M365 write controls once endpoint safety is validated.
- Add richer Cove/ConnectSecure remediation primitives.
- Introduce policy packs/templated baselines per vertical (HIPAA, CIS-lite, etc.).

---

## 9) Highest-Risk Design Decisions Requiring Sign-Off

1. **Single baseline document vs policy composition model** (monolith baseline vs reusable policy fragments).
2. **Where medium-risk approvals live** (Bifrost-native approval UI/workflow vs HaloPSA approval loop).
3. **Cross-platform source-of-truth for device identity** (Ninja device ID as canonical vs separate asset graph).
4. **M365 write path** (CIPP-only wrappers vs direct Graph module with typed operations).
5. **Tamper-evidence level for HIPAA** (append-only DB vs cryptographic hash-chain and external archival).
6. **Failure semantics** (partial success accepted vs transactional per-control expectations).
7. **Per-control override precedence** (global policy, client override, emergency freeze).

---

## 10) Ambiguities To Resolve Before Coding

1. **Authoritative identity joins:** how to deterministically map device/user identity across NinjaOne, Huntress, Cove, ConnectSecure.
2. **Exemption governance:** who can set exemptions and for how long (expiry required?).
3. **Secure Score actionability:** which controls are informational-only vs safely enforceable.
4. **Task schema in HaloPSA:** exact ticket type/priority/queue mappings for each severity.
5. **Approval RBAC:** which Bifrost roles can approve medium-risk actions per client.
6. **SLA expectations:** reconcile frequency per client tier and max staleness tolerated.
7. **Blast-radius controls:** max automated actions per run/day/platform before hard stop.
8. **Maintenance windows:** whether remediation honors client maintenance calendars.
9. **Evidence retention period:** exact HIPAA/legal retention timeline and export requirements.
10. **AKV secret strategy details:** whether credentials stay only in integration config, mirrored from AKV, or dynamically fetched at runtime.

---

## Suggested First Implementation Slice (post sign-off)

- Implement baseline storage/versioning + read-only reconcile for:
  - Ninja patch compliance
  - Huntress agent coverage
  - Cove backup coverage against Ninja-managed servers
- Emit findings and HaloPSA tasks only (no remediation).
- Validate data quality, false positives, and identity joins before enabling any auto-remediation.

