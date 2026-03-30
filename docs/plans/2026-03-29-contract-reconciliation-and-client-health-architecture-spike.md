# Contract Reconciliation + Client Health Architecture Spike (Research)

_Date: 2026-03-29_

## 1) Repository Readout (what exists today)

This proposal is grounded in the current Bifrost repository structure and patterns:

- Integration-specific workflow surfaces are already standardized under `features/<integration>/workflows/*` with `@workflow` / `@data_provider` entrypoints.
- Existing mapping strategy uses `integrations.upsert_mapping(...)` to bind a Bifrost org to external entity IDs (Pax8 company, Halo client, CIPP tenant, Ninja org, Cove customer).
- The major integrations in scope already exist as modules and can be called with org/global scoped clients:
  - Pax8 (`modules/pax8.py`, `features/pax8/...`)
  - HaloPSA (`modules/halopsa.py`, `shared/halopsa/...`, `modules/extensions/halopsa.py`)
  - CIPP (`modules/cipp.py`, `features/cipp/...`)
  - NinjaOne (`modules/ninjaone.py`, `features/ninjaone/...`)
  - Cove Data Protection (`modules/cove.py`, `features/cove/...`)
- Shared helper patterns already exist for HaloPSA write-back, including idempotent opportunity creation and account-manager-aware assignment (`modules/extensions/halopsa.py`).
- Bifrost table storage patterns for per-org operational state already exist (for example in `features/microsoft_csp/...` using `tables.query/get/upsert/delete`).

## 2) API audit findings

## 2.1 Pax8 API coverage relevant to Part A

### Confirmed in repo module (`modules/pax8.py`)

The current Pax8 client already exposes methods needed for reconciliation inputs:

- Companies: `list_companies`, `get_company`
- Subscriptions/licensing proxy: `list_subscriptions`, `get_subscription`, `get_subscription_history`, `update_subscription`, `cancel_subscription`
- Invoices/billing: `list_invoices`, `get_invoice`, `list_invoice_items`
- Usage: `list_usage_summaries`, `get_usage_summary`, `list_usage_lines`

### Confirmed in Pax8 docs nav (public API reference)

Using Pax8 API docs navigation snapshot (`https://devx.pax8.com/reference/findpartnerinvoices`), the partner endpoints include:

- Companies
- Products
- Orders
- Subscriptions
- Contacts
- Invoices (+ draft items)
- Usage summaries (+ usage lines)

This aligns with existing repo wrapper coverage for Part A seat/billing reconciliation. Note: repo wrapper currently does **not** expose draft invoice items, although docs nav shows that endpoint.

## 2.2 HaloPSA API coverage relevant to Part A + Part B

HaloPSA in this repo is backed by a very large generated module (`modules/halopsa.py`) plus extension helpers (`modules/extensions/halopsa.py`) and shared tools (`shared/halopsa/...`).

### Confirmed high-value read endpoints in module

- `list_clients`, `get_client`, `list_sites`
- `list_client_contracts`
- `list_tickets`, `get_tickets`
- `list_actions`
- `list_users`
- `list_opportunities`, `get_opportunities`
- `list_invoices`
- `list_payments`
- `list_projects`, `list_quotations`, `list_sales_orders`
- `list_appointments`

### Confirmed write endpoints in module / helpers

- `create_tickets`
- `create_actions`
- `create_opportunities`
- `create_invoice`
- `create_sales_order`
- extension helper `create_opportunity(...)` with account-manager routing + idempotency semantics

### Practical implication

The available Halo surface is sufficient to:

- Read contract metadata (where present in Halo data model/config)
- Read engagement/operations/financial-ish signals (activities/actions/tickets/opportunities/invoices/payments)
- Write reconciliation outputs (opportunities/tasks/tickets/actions)
- Route assignments to client owner / account manager

### Manual-config caveats for Halo

Before implementation, the following must be explicitly configured/validated per instance:

- Contract fields that represent "contracted user count", "contracted device count", "backup coverage target", and service tier (often custom fields or specific contract line structures).
- Ticket type/team/status IDs for AM tasks vs sales opportunities.
- Whether invoice/payment records are fully accessible through current API credentials and tenant permissions.
- QBR attendance representation (actions, appointments, ticket categories, or custom fields).

## 3) Automatable vs manual-input checks (Part A)

## Fully automatable with current integration surfaces

1. **M365 licensed seats (Pax8) vs active enabled users (CIPP)**
   - Pax8 subscriptions + quantity
   - CIPP users/tenant status
   - Deterministic delta logic.

2. **Licenses assigned to disabled accounts (CIPP)**
   - CIPP user list includes disabled-state semantics and license assignments (shape should be validated tenant-by-tenant).

3. **NinjaOne managed device count vs contracted managed devices (Halo)**
   - Ninja org/device counts available.
   - Halo contract counts automatable **if** contract count fields are consistently modeled.

4. **Cove protected device count vs contracted backup coverage (Halo)**
   - Cove customer/device stats available.
   - Halo contract-side target must be mapped consistently.

5. **Halo write-back for findings**
   - Automatable via existing opportunity/ticket/action write patterns.

## Partially automatable / needs controlled manual configuration

1. **License type mismatch (user SKU too high for role)**
   - Requires a role-to-SKU policy map not present in source systems by default.
   - Recommendation: maintain baseline policy table in Bifrost (global + segment override).

2. **"Services contracted vs Midtown recommended baseline tier"**
   - Requires external baseline definition not currently represented.
   - Recommendation: explicit baseline matrix table under provider scope.

3. **Service tier normalization across Halo and (temporary) Autotask migration path**
   - Requires isolation adapter and canonical contract model.

## 4) Proposed canonical architecture (Part A + Part B)

## 4.1 Module boundaries

Proposed new feature package:

- `features/client_success/workflows/...`
  - `run_monthly_contract_reconciliation`
  - `run_weekly_client_health_scoring`
  - `recalc_client_health_realtime_triggered`
  - `sync_contract_snapshot`
  - `process_halo_alert_transitions`

Proposed shared helpers:

- `shared/client_success/reconciliation.py`
- `shared/client_success/scoring.py`
- `shared/client_success/models.py`
- `shared/client_success/signals.py`
- `shared/client_success/halopsa_writeback.py`
- `shared/client_success/ai_narrative.py`

## 4.2 Data model (Bifrost tables)

All tables scoped by `org_id` unless noted.

1. `cs_contract_snapshot`
   - `id` = `YYYY-MM` or `snapshot_ts`
   - source-of-truth contract values (tier, user/device/bkup committed counts)
   - source metadata (`source_system`, `source_record_ids`, `confidence`)

2. `cs_consumption_snapshot`
   - `id` = `YYYY-MM`
   - observed usage counts by source (Pax8/CIPP/Ninja/Cove)

3. `cs_reconciliation_finding`
   - `id` deterministic hash (org + period + check_type + entity)
   - severity, delta, financial_impact_estimate, action_type (billing_adjustment / upsell / churn_flag / reclaim)
   - `status` (new, acknowledged, action_created, resolved, ignored)
   - `halo_record_id` if written back

4. `cs_health_score`
   - `id` = scoring period (week)
   - composite score, subscores, trend direction, risk tier, threshold snapshot
   - top drivers structured list

5. `cs_health_signal_snapshot`
   - raw normalized signals used in scoring for explainability and recalibration

6. `cs_config_global` (provider scope) + `cs_config_org` (org scope)
   - weight defaults/overrides
   - threshold config (amber/red)
   - feature toggles for Part A / Part B
   - role→SKU policy map
   - baseline service tier matrix

7. `cs_action_registry`
   - idempotency ledger for outbound Halo writes (prevents duplicate tasks/opportunities)

## 4.3 Canonical contract adapter abstraction

To support Halo as current source and Autotask during migration:

- `ContractAdapter` interface:
  - `get_service_tier(org_id)`
  - `get_committed_counts(org_id)` → `{managed_users, managed_devices, protected_devices}`
  - `get_contract_metadata(org_id)`
- `HaloContractAdapter` implementation first
- `AutotaskContractAdapter` optional during migration
- selection via global config (`contract_source = halo|autotask`) with org override if needed

This avoids scattering source-specific contract parsing across reconciliation logic.

## 4.4 Reconciliation pipeline (monthly, Part A)

1. **Gather**
   - Pull fresh source snapshots (Pax8/CIPP/Ninja/Cove + contract adapter).
2. **Normalize**
   - Convert to canonical measure set:
     - `licensed_m365_seats`
     - `active_enabled_users`
     - `disabled_users_with_license`
     - `managed_devices_actual`
     - `protected_devices_actual`
     - `contracted_*` metrics
3. **Evaluate checks**
   - Run deterministic check functions returning typed findings.
4. **Persist findings idempotently**
   - Upsert by deterministic finding key.
5. **Write-back actioning**
   - Over-consumption → Halo opportunity (billing adjustment)
   - Under-consumption → Halo opportunity (upsell) or churn risk task
   - Disabled licensed users → Halo task for reclamation
6. **Publish report artifacts**
   - Org-level monthly report row + optional markdown summary.

## 4.5 Health scoring engine (weekly + real-time triggers, Part B)

### Proposed initial category weights

- Engagement: **25%**
- Technical health: **30%**
- Service consumption/value: **20%**
- Risk: **25%**

Reasoning: technical + risk should dominate churn prediction initially, but engagement remains nearly co-equal.

### Signal normalization

Each signal normalized into 0–100 with:

- explicit direction (`higher_is_better` vs inverse)
- cap/floor clipping
- missing-data fallback (`neutral=50` + confidence penalty)
- trend-aware deltas over rolling 90 days

### Composite formula

1. category subscore = weighted mean of available signal scores in category
2. composite = weighted sum(category subscores)
3. confidence multiplier = `0.85 + 0.15 * data_completeness` (prevents overconfidence on sparse data)
4. final score = round(composite * confidence_multiplier)

### Tier thresholds (default)

- Green: `>= 75`
- Amber: `55–74`
- Red: `< 55`

Org-level overrides allowed in `cs_config_org`.

### Trend logic

- Compare current score vs prior 4 weekly points (slope + delta)
- Improving: slope positive and net +3 or more
- Declining: slope negative and net -3 or less
- Stable otherwise

## 4.6 Alert and write-back logic

State machine based on prior risk tier:

- `Green -> Amber`:
  - Bifrost AM notification
- `* -> Red`:
  - create Halo urgent outreach task/ticket assigned to account owner
- `Red -> Amber/Green`:
  - Bifrost notification
  - resolve/close open outreach task if no open critical blockers remain

All outward writes use `cs_action_registry` for idempotency.

## 4.7 AI narrative generation design (Anthropic)

Model target from requirement:

- `claude-sonnet-4-20250514`

### Prompt contract

System prompt (concise):

- You are writing account-manager briefings for MSP client health.
- Use only provided structured inputs.
- Return strict JSON with keys:
  - `top_positive_factors` (max 3)
  - `top_negative_factors` (max 3)
  - `recommended_action` (single paragraph)
  - `urgency` (`low|medium|high`)

User payload:

- org metadata
- current + prior scores/subscores
- highest-impact normalized signals with raw values
- reconciliation findings summary
- open risk indicators (renewal proximity, disputes, critical findings)

### Failure behavior

If AI call fails/timeouts/invalid JSON:

- persist `narrative_status = failed`
- still return raw score/subscores/trend/tier
- skip narrative-only alerts (but **do not** skip hard risk-tier alerts)

### Example output shape

```json
{
  "top_positive_factors": [
    "Patch compliance improved 9 points over 90 days.",
    "Backup success remained above 98% for 8 straight weeks.",
    "QBR attendance is 100% this quarter."
  ],
  "top_negative_factors": [
    "Contracted 120 managed devices but 143 are active in NinjaOne.",
    "Two critical findings have been open for more than 30 days.",
    "Invoice payment delay increased to 18 days average."
  ],
  "recommended_action": "Schedule an account-owner call this week to align on device overage true-up, assign remediation owners for critical findings, and propose a managed-device contract expansion before renewal.",
  "urgency": "high"
}
```

## 5) Proposed initial scoring signals + weights

## Engagement (25)

- QBR attendance rate (30% of category)
- Project approval rate (25%)
- Project approval velocity / decision lag (20%)
- AM outreach response rate (15%)
- Invoice payment timeliness (10%)

## Technical health (30)

- Secure Score trend (25%)
- Patch compliance trend (25%)
- Backup success trend (25%)
- Age of open critical findings (25%)

## Service consumption/value (20)

- Ticket volume anomaly score (25%)
- Ticket satisfaction trend (20%)
- Under-consumption index from Part A (35%)
- Tier fit vs recommended baseline (20%)

## Risk (25)

- Renewal proximity risk curve (30%)
- Billing dispute / invoice adjustment recency (20%)
- Unacknowledged critical findings / overdue tasks (25%)
- Engagement decline over 90 days (25%)

## 6) Shared components to reuse

1. **Integration mapping model** (`integrations.list_mappings`, `integrations.upsert_mapping`) for org↔entity linkage.
2. **Scoped integration clients** from existing modules (`get_client(scope="global"|org)`).
3. **HaloPSA extension helpers** in `modules/extensions/halopsa.py` for:
   - pagination
   - org→client resolution
   - idempotent opportunity/ticket patterns
4. **Table persistence patterns** already used in `features/microsoft_csp/workflows/*`.
5. **Data provider conventions** in `features/*/workflows/data_providers.py` for future configuration UI dropdowns.

## 7) Phased build order

## Phase 0 — sign-off gates (no production writes)

- Confirm contract field mapping in Halo.
- Confirm Halo financial endpoint access in target tenant.
- Approve initial scoring weights/thresholds and calibration plan.

## Phase 1 — Part A read-only reconciliation MVP

- Build canonical snapshot + check engine.
- Persist findings in tables.
- Produce monthly per-client report output.
- No Halo write-back yet.

## Phase 2 — Part A actioning (direct revenue impact)

- Enable Halo write-back for over/under/reclamation findings.
- Add idempotency registry and dedupe protections.
- Add AM-owner assignment routing.

## Phase 3 — Part B scoring core (no AI)

- Implement signal ingestion + normalization + scoring engine.
- Weekly schedule + real-time triggers (ticket spike, missed invoice, renewal <90d).
- Tier transition alerts + Halo urgent tasks on Red.

## Phase 4 — Part B AI narratives

- Add Anthropic prompting + strict JSON validation.
- Add failure fallback path and observability.

## Phase 5 — model calibration and governance

- Back-test against known churn/expansion outcomes.
- Adjust weights/thresholds per segment.
- Operational playbook for AM response SLAs.

## 8) Highest-risk design decisions requiring explicit sign-off

1. **Halo financial data availability**
   - Are `invoices/payments/adjustments` complete enough for payment risk signals?
   - Are permissions/scopes sufficient for automation identity?

2. **Contract-source truth model during migration**
   - Halo-only vs Halo+Autotask blended mode.
   - Conflict resolution precedence when both have values.

3. **Custom field dependency in Halo contracts**
   - Exact fields for committed counts and service tier may differ by tenant.

4. **Score calibration ownership**
   - Who approves weight and threshold changes?
   - What objective KPI defines model success (churn prediction, upsell conversion, false-alert rate)?

5. **Action policy for under-consumption**
   - When to open upsell opportunity vs churn-risk intervention.

6. **AI narrative policy**
   - Acceptable hallucination risk controls and review requirements.

## 9) Ambiguities to resolve before writing code

1. Exact Halo contract schema/field IDs for:
   - managed user commitment
   - managed device commitment
   - backup commitment
   - service tier
2. Precise Halo artifact for AM outreach task:
   - ticket type/team/status vs opportunity type.
3. QBR attendance source-of-truth in Halo:
   - appointment category? ticket action type? custom entity?
4. Definition of "ticket spike" trigger:
   - z-score threshold? absolute increase? per-seat normalization?
5. Definition of "missed invoice":
   - days past due threshold + exclusion rules.
6. License role-to-SKU policy source:
   - centrally maintained table vs per-client overrides only.
7. Baseline service tier framework:
   - exact Midtown recommendation matrix and versioning model.
8. Red-task auto-close criteria on recovery:
   - immediate on score recovery or only after manual validation.
9. Required audit trail granularity:
   - store every raw source value vs only normalized snapshots.

## 10) Implementation readiness summary

- **Part A is implementation-feasible immediately** for read-only reconciliation and most write-back, pending Halo contract-field mapping confirmation.
- **Part B is implementation-feasible in staged form** (deterministic scoring first, narrative layer second).
- Primary blockers are **schema semantics + policy decisions**, not missing technical primitives.
