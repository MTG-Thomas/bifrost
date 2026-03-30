# Security Benchmarking Module — Architecture & Research Spike

**Date:** 2026-03-29  
**Status:** Proposal (no implementation)

## 1) Scope and intent

This document proposes a major new Bifrost module that benchmarks each client’s security posture against anonymized peer cohorts.

This is an architecture-first spike. It is intentionally scoped to:

- consume already-available tenant signals from existing integrations/modules
- enforce anonymization and minimum cohort rules by design
- phase delivery so percentile math and data quality ship before AI narrative generation and client-facing UX

## 2) Repo observations that shape this design

### 2.1 Organization metadata and extensibility

Organizations already have a JSONB `settings` field and the organizations API supports updating it, which provides a natural home for vertical assignment and benchmark-specific toggles without schema changes to the organization table.

### 2.2 Existing integration mapping model

Integrations are already mapped per organization via `integration_mappings` (`integration_id`, `organization_id`, `entity_id`, optional `entity_name`). This is the right resolver for joining benchmark snapshots to external system entities.

### 2.3 Existing metrics/snapshot patterns

The backend already has periodic metrics snapshot patterns (`ExecutionMetricsDaily`, `PlatformMetricsSnapshot`, `KnowledgeStorageDaily`) and scheduler job registration in APScheduler. A weekly benchmark snapshot scheduler fits this pattern.

### 2.4 Existing reporting UX building blocks

The client already has reporting pages with summary cards, trend charts, date range controls, organization filter controls, and report tables (ROI/Usage). A benchmark report UI should reuse these primitives.

### 2.5 Existing Anthropic model defaults

The LLM subsystem already supports Anthropic and defaults include `claude-sonnet-4-20250514`. Narrative generation should route through this existing abstraction and gracefully degrade when unavailable.

### 2.6 Existing vendor integration surfaces

- Microsoft integration currently uses a generic Graph client (suitable for Secure Score, MFA registration, CA policy endpoints).
- NinjaOne module contains device/patch/backup-related DTOs and organization identifiers.
- Huntress module exposes incident report and organization endpoints.
- ConnectSecure module supports company and asset listing.
- Cove module supports backup device enumeration and `last_backup_time` fields.

## 3) Proposed anonymization architecture (primary design constraint)

## 3.1 Separation of raw vs benchmarked data

Create separate storage layers:

1. **Raw signal snapshots (org-identifiable, restricted):**
   - one row per `{snapshot_week, org_id, signal_key}`
   - used for percentile calculations and trend deltas
   - not exposed directly to client-facing benchmark APIs except current org’s own rows

2. **Cohort benchmark aggregates (anonymized):**
   - one row per `{snapshot_week, cohort_key, signal_key}`
   - stores distribution summaries only (count, p10/p25/p50/p75/p90, min/max)
   - **never stores org identifiers**

3. **Client benchmark outputs (org-facing):**
   - one row per `{snapshot_week, org_id, signal_key}` with percentile + trend + basis (`vertical` vs `portfolio`)
   - optionally includes generated narrative JSON blob if AI succeeded

This follows the requirement to keep aggregated benchmark data separate from client raw data.

## 3.2 Cohort key model and k-anonymity threshold

Define cohort keys as:

- `vertical:<slug>` (e.g., `vertical:dental_oral_health`)
- `portfolio:all`

For each org + signal + snapshot:

1. attempt vertical cohort if org has assigned vertical
2. check **eligible participant count** (excludes benchmark opt-outs and missing-data orgs)
3. if count >= `min_cohort_size` (default 5), use vertical
4. else fallback to portfolio cohort

Store `cohort_size_used`, `cohort_type_used`, and `fallback_reason` in client output for auditability.

## 3.3 Anti-reidentification controls

- Do not expose min/max when cohort size is low (even above threshold, optionally suppress at n<10).
- Do not return cohort member lists.
- Round outputs to fixed precision (e.g., integer percentile, one decimal for rates).
- For narrow cohorts, prefer percentile bands (e.g., 60–70th) in narratives.
- Enforce per-org API scope: org users can only retrieve their org’s benchmark result rows.

## 3.4 Explainability contract (client defensibility)

For each report, include a machine-readable “data usage disclosure” section:

- which signals were used
- date window used
- statement that only anonymized aggregates are shared externally
- whether this org was included in benchmark pool (opt-out aware)

Suggested text baseline:

> “Your benchmark compares your organization’s signal values against anonymized cohort distributions. No other client identity or raw records are exposed.”

## 4) Vertical segmentation model (extensible)

## 4.1 Proposed org settings structure

Use `Organization.settings.security_benchmarking`:

```json
{
  "security_benchmarking": {
    "enabled": true,
    "vertical": "dental_oral_health",
    "exclude_from_aggregation": false,
    "last_reviewed_at": "2026-03-29T00:00:00Z"
  }
}
```

Initial allowed vertical slugs:

- `dental_oral_health`
- `medical_healthcare`
- `professional_services`
- `general_smb`

Keep as controlled vocabulary in config (not hardcoded DB enum) so future verticals can be added without schema migrations.

## 4.2 Validation and defaults

- default `vertical = general_smb` when missing
- default `enabled = false` until feature rollout gate is complete
- default `exclude_from_aggregation = false`

## 5) Signal ingestion plan (reuse existing modules, avoid refetch duplication)

## 5.1 Design rule

The benchmarking module should consume **normalized signal snapshots** from source workflows, not call vendor APIs directly on-demand for benchmarking.

## 5.2 Signal source mapping (current-state + gaps)

### Existing surfaces likely reusable now

- **NinjaOne**
  - patch/device/backup primitives in module models
  - can derive patch compliance and agent coverage from snapshot workflow outputs
- **Huntress**
  - incident/alert surfaces for alert frequency
- **ConnectSecure**
  - company + assets; extend source workflow to compute high/critical findings per device
- **Cove Data Protection**
  - device and `last_backup_time` support for backup coverage
- **Microsoft Graph (generic client)**
  - supports implementing secure score, MFA registration, CA policy metric collectors via Graph endpoints

### Gap identified

The repo currently has sync/data-provider workflows focused on organization mapping, but not yet a standardized “security signal snapshot” data contract for the requested metric set. Introduce a shared normalized signal schema first, then make each source workflow publish into it.

## 5.3 Normalized signal contract

Proposed canonical row:

- `org_id`
- `snapshot_week` (ISO week start date)
- `signal_key` (fixed enum-like string)
- `value` (numeric)
- `unit` (`percent`, `count`, `rate_per_device_month`, etc.)
- `direction` (`higher_is_better` / `lower_is_better`)
- `source_system`
- `data_quality` (`ok`, `partial`, `stale`, `missing`)
- `evidence` (JSON: upstream IDs/counts/time range)

## 6) Percentile model and trend calculation

## 6.1 Percentile method

For each signal and cohort on a given snapshot week:

- define normalized comparison value
- if `higher_is_better`, percentile is direct rank
- if `lower_is_better` (e.g., alerts/finding rates), invert percentile so higher percentile always means better posture

Recommended calculation:

`percentile = 100 * (count(values < x) + 0.5 * count(values == x)) / n`

This handles ties consistently.

## 6.2 Trend model

Compute trend at the same cohort basis used in both periods:

- `delta = percentile_current - percentile_previous`
- thresholds:
  - `improving` if delta >= +5
  - `declining` if delta <= -5
  - `stable` otherwise

Also store `insufficient_history` when prior period missing.

## 6.3 Quarterly report basis

- Weekly snapshots continue as ingestion cadence.
- Quarterly report uses the latest complete week in quarter and compares against prior quarter’s latest complete week.

## 7) AI narrative generation design (Anthropic)

## 7.1 Invocation policy

- Model: `claude-sonnet-4-20250514`
- Input: only org’s own percentiles + anonymized cohort summary stats (no raw peer rows)
- If API fails: return report without narrative/opportunity text (explicit `narrative_status="unavailable"`)

## 7.2 Prompt structure

System prompt (high-level):

- You are an MSP vCIO analyst.
- Use plain language and avoid exposing peer-identifying details.
- Recommend only actions grounded in provided signals.
- Output strict JSON schema.

User payload fields:

- client name (optional display)
- period and prior period
- per-signal: value, percentile, delta, trend, unit, signal definition
- cohort metadata: type used, size, fallback reason
- optional effort heuristics by signal (low/med/high)

## 7.3 Expected output schema

```json
{
  "summary": "string",
  "signal_narratives": [
    {
      "signal_key": "mfa_enrollment_rate",
      "interpretation": "string",
      "target_percentile": 75,
      "what_it_would_take": "string"
    }
  ],
  "top_opportunities": [
    {
      "rank": 1,
      "signal_key": "ca_policy_coverage",
      "expected_percentile_gain": 12,
      "estimated_effort": "medium",
      "rationale": "string"
    }
  ]
}
```

## 7.4 Example prompt input/output (abbreviated)

Input excerpt:

```json
{
  "period": "2026-Q1",
  "cohort": {"type": "vertical", "name": "medical_healthcare", "size": 12},
  "signals": [
    {"signal_key": "secure_score_percent", "value": 58, "percentile": 42, "delta": 6},
    {"signal_key": "mfa_enrollment_rate", "value": 71, "percentile": 35, "delta": -8},
    {"signal_key": "connectsecure_open_high_critical_per_device", "value": 1.9, "percentile": 28, "delta": -3}
  ]
}
```

Output excerpt:

```json
{
  "summary": "You are improving overall, but identity controls remain your main drag on peer ranking.",
  "top_opportunities": [
    {
      "rank": 1,
      "signal_key": "mfa_enrollment_rate",
      "expected_percentile_gain": 14,
      "estimated_effort": "medium",
      "rationale": "Completing MFA registration for remaining licensed users typically moves this metric quickly."
    }
  ]
}
```

## 8) Proposed module architecture

## 8.1 Backend components

1. **Signal Snapshot Ingestion Workflows** (userland)
   - one workflow per source system (or per signal family)
   - writes normalized rows for current org/week

2. **Benchmark Aggregator Service** (platform/backend)
   - reads eligible org signal snapshots by week
   - builds cohort aggregate distributions
   - computes per-org percentiles + trend
   - writes client benchmark output rows

3. **Narrative Generator Service**
   - reads computed percentiles
   - calls LLM
   - stores narrative/opportunity blobs

4. **Benchmark Report API**
   - `GET /api/reports/security-benchmarks/{org_id}?period=2026-Q1`
   - `POST /api/reports/security-benchmarks/{org_id}/generate` (on-demand)
   - scope checks: org user only their org; admins all

5. **Scheduler Jobs**
   - weekly snapshot ingestion trigger(s)
   - weekly aggregate recompute
   - quarterly report materialization task

## 8.2 Data model additions (proposed)

- `security_signal_snapshots` (org-identifiable raw normalized signals)
- `security_benchmark_cohorts` (anonymized per signal/cohort/week distribution stats)
- `security_benchmark_results` (org-specific percentile output)
- `security_benchmark_narratives` (optional AI-generated text/opportunities)
- optionally `security_benchmark_job_runs` (audit + retry tracing)

## 8.3 UI architecture

Add new page: `/reports/security-benchmarks`

Sections:

- report header (period, cohort basis, cohort size)
- per-signal percentile table + trend badges
- optional narrative card
- top opportunities table (impact vs effort)
- disclosure section (“how your data is used”)

Reuse patterns from ROI/Usage reports for date range, org filter, cards/charts/tables.

## 9) Contractual and privacy considerations requiring sign-off

1. **Client agreement alignment**
   - Confirm existing agreements permit anonymized aggregate benchmarking.
   - If not explicit, add contractual language before launch.

2. **Opt-out semantics**
   - Respect `exclude_from_aggregation` for cohort construction.
   - Clarify that opting out excludes their data from others’ benchmarks but they still receive benchmark results using remaining cohorts.

3. **Minimum cohort policy**
   - Enforce minimum size (recommended 5) as hard backend rule.

4. **Retention policy**
   - Define retention windows for raw signal snapshots vs anonymized aggregates.

5. **Access control and auditability**
   - Log report generation/access events.
   - Ensure strict tenant isolation on org-facing endpoints.

## 10) Phased build order (recommended)

### Phase 1 — Foundations: signal normalization + raw percentiles

- Define normalized signal schema
- Build ingestion workflows/jobs for available signals
- Build cohort aggregation + percentile engine
- Expose API returning raw percentile/trend payloads (no narrative)

### Phase 2 — Narrative generation

- Implement Anthropic narrative generation pipeline
- Add fallback behavior and status fields
- Add prompt templates + strict JSON validation

### Phase 3 — Client-facing report UX + on-demand generation

- Build UI page and reusable cards/tables
- Add “Generate now” action
- Add disclosure copy and export options

### Phase 4 — Hardening

- monitoring, audit logs, retry/backfill tooling
- data quality warnings and stale-snapshot indicators
- policy/legal sign-off gates for production enablement

## 11) Ambiguities to resolve before implementation

1. **Authoritative source precedence** when multiple systems can infer similar metrics (e.g., backup coverage via NinjaOne vs Cove).
2. **Exact signal formulas** (especially denominator definitions: “managed devices”, “licensed users”, “within SLA”).
3. **SLA window definitions** for patch compliance by OS/type.
4. **Quarterly period semantics** (calendar quarter vs trailing 90 days).
5. **Effort model** for opportunity ranking (static rules vs configurable weights).
6. **Narrative governance**: mandatory approval/review before client-visible publishing?
7. **Opt-out edge behavior** when cohort drops below threshold after exclusions.
8. **Treatment of missing/stale data** in percentile outputs and narrative eligibility.
9. **Contractual language ownership** (legal vs account management) and rollout dependency.
10. **UI audience/scope**: org users only, vCIO internal users, or both with different detail levels.

## 12) Suggested acceptance criteria for Phase 1

- Weekly snapshots are persisted for at least 4 signals across >= 5 organizations.
- Percentile engine produces deterministic output for ties and directionality.
- Cohort fallback logic enforces min-size threshold exactly.
- Org-facing API returns only current org results and anonymized cohort metadata.
- Unit tests cover ranking, fallback, and opt-out filtering.

