# FOSS DMARC Landscape (March 29, 2026) and Bifrost App Plan

## Why this research

We want a self-hosted/FOSS stack that can replace the practical day-to-day value of managed services like EasyDMARC (report ingestion, visualization, sender triage, DNS/auth checks, and alerting/ops loops).

## What “EasyDMARC-like” means in practice

From EasyDMARC’s current tools/platform pages, the functional bar includes:

- DMARC aggregate (RUA) reporting and dashboards
- DMARC failure/forensic (RUF) reporting
- SPF, DKIM, and DMARC record checks/generation
- BIMI management/check tooling
- MTA-STS and TLS-RPT tooling
- Reputation/alerts and investigation aids

Reference:
- https://easydmarc.com/tools

## Shortlist of relevant FOSS projects

### 1) `domainaware/parsedmarc` (strongest foundation)

Repository: https://github.com/domainaware/parsedmarc

What it provides:

- Parser/CLI for DMARC reports
- Explicitly positions itself as a self-hosted open-source alternative to commercial DMARC reporting services
- Parses RUA aggregate reports, RUF/forensic reports, and SMTP TLS reports
- Can ingest from IMAP, Microsoft Graph, or Gmail API
- Can output to Elasticsearch/OpenSearch/Splunk for dashboarding

Signals of maturity/activity (snapshot date: 2026-03-29):

- ~1.2k stars
- Recent release (`9.5.5`) published 2026-03-27
- Recent code activity (`pushed_at` 2026-03-27)

Assessment:

- Best core ingest/normalization layer available in FOSS right now.
- Not a full polished product by itself; dashboard/UX + workflows still needed.

### 2) `cry-inc/dmarc-report-viewer` (good lightweight “single binary” option)

Repository: https://github.com/cry-inc/dmarc-report-viewer

What it provides:

- Standalone DMARC + SMTP TLS report viewer
- Built-in IMAP fetch + parser + HTTP server
- Docker-first deployment and prebuilt binaries
- Suitable for small teams wanting quick visibility without a larger data platform

Signals of maturity/activity (snapshot date: 2026-03-29):

- ~268 stars
- Latest release (`2.4.1`) published 2026-03-03
- Recent code activity (`pushed_at` 2026-03-28)

Assessment:

- Excellent for a low-ops start.
- Less extensible than a parsedmarc + data-store approach for multi-domain governance workflows.

### 3) `domainaware/checkdmarc` (critical companion service)

Repository: https://github.com/domainaware/checkdmarc

What it provides:

- SPF/DMARC DNS validation and parsing
- Checks for MTA-STS and SMTP TLS reporting records
- CLI/library shape works well as an app-side “domain posture scan” component

Signals of maturity/activity (snapshot date: 2026-03-29):

- ~310 stars
- Latest release (`5.13.4`) published 2026-02-18
- Recent code activity (`pushed_at` 2026-03-26)

Assessment:

- Not a replacement alone, but very valuable for “managed guidance” features.

### 4) `trusteddomainproject/OpenDMARC` (MTA enforcement component, not a management platform)

Repository: https://github.com/trusteddomainproject/OpenDMARC

What it provides:

- DMARC library + milter for mail-flow policy enforcement/report generation integration at the MTA layer

Signals (snapshot date: 2026-03-29):

- Last tagged release visible from GitHub API is old (`rel-opendmarc-1-4-2`, 2021-12-20)
- Still has repository activity, but this is primarily infra/protocol componentry, not end-user management UX

Assessment:

- Useful where mail infrastructure-level enforcement is in scope.
- Does not directly replicate EasyDMARC dashboard/ops experience.

### 5) Other candidates with caveats

- `debricked/dmarc-visualizer` (historically useful Elastic+Grafana pattern, but appears relatively quiet and release-light recently)
- `mailmarc/mailmarc` (promising concept, but currently appears very early-stage/placeholder, no tagged releases yet)

## Overall conclusion: are there suitable FOSS options?

Yes — if we accept a **composed stack** rather than expecting a single mature open-source clone of EasyDMARC.

Best-fit composition:

1. **Ingest/parse:** `parsedmarc`
2. **Domain auth posture checks:** `checkdmarc`
3. **Storage/search:** OpenSearch (or Elasticsearch)
4. **Visualization:** OpenSearch Dashboards / Grafana
5. **Case workflow + triage UX + alerts:** a custom Bifrost app

This combination can cover most practical EasyDMARC-like functionality for internal use, with the biggest delta being “managed convenience” and polished onboarding.

---

## Bifrost app plan: “DMARC Control Plane”

### Product goal

Provide a self-hosted operator console that sits on top of parsed data and turns DMARC operations into a repeatable workflow: identify unknown senders, validate alignment failures, generate remediation tasks, and track policy progression (`none` → `quarantine` → `reject`).

### Proposed scope tiers

#### MVP (Phase 1)

- Multi-domain inventory with current DNS posture:
  - DMARC policy, SPF validity, DKIM selectors discovered
  - MTA-STS/TLS-RPT presence check
- DMARC aggregate dashboard:
  - pass/fail trend
  - top sender IPs/sources
  - unknown/unauthorized sender candidates
- Basic triage queue:
  - “new sender seen”, “alignment failure spike”, “policy regression”
- Daily digest + threshold alerts (email/Slack/webhook)

Dependencies:

- `parsedmarc` feeding OpenSearch index(es)
- `checkdmarc` invoked by scheduled workflow for DNS/auth scans

#### Phase 2

- Forensic/RUF + SMTP TLS report-specific views
- Suggested fix snippets (SPF flatten candidate, DKIM selector checklists, DMARC policy advice)
- Change tracking / audit log for domain policy transitions
- Role-based queues for Security vs Messaging teams

#### Phase 3

- BIMI readiness and managed artifact checks
- Optional threat-intel enrichment (ASN/IP reputation, geo risk scoring)
- Tenant segmentation (if offering to multiple internal business units)

### Recommended Bifrost architecture

#### Data contracts

Define normalized entities (in Bifrost workflows/modules):

- `domain_posture_snapshot`
- `dmarc_aggregate_fact`
- `dmarc_forensic_fact`
- `sender_identity`
- `triage_finding`
- `policy_milestone`

#### Ingestion/workflows

1. **Ingest DMARC reports** (scheduled):
   - Pull from parsedmarc output index (or parsedmarc JSON artifact stream)
   - Upsert normalized `dmarc_*` entities
2. **Run DNS posture scan** (scheduled):
   - For each managed domain invoke checkdmarc
   - Persist `domain_posture_snapshot`
3. **Generate findings** (rule engine workflow):
   - New sender not allowlisted
   - SPF pass but alignment fail
   - DKIM fail spikes
   - High-volume source with DMARC fail
4. **Notify/escalate**:
   - Threshold-based notifications + digest

#### App UX (Bifrost `apps/*`)

Primary views:

1. **Overview**
   - Domain risk scorecards
   - trend spark-lines
   - policy stage distribution
2. **Senders**
   - Authorized vs unknown senders
   - source detail panel with first-seen/last-seen/pass rate
3. **Findings Queue**
   - Filter by severity/domain/status
   - Assign, snooze, resolve with note
4. **Domain Workbench**
   - DNS/auth checks
   - recommended DNS changes
   - policy promotion checklist
5. **Reports**
   - Exportable weekly/monthly security posture summary

### Implementation plan in this repo (SDK-first)

1. **Module layer**
   - Add a `modules/dmarc_*` integration surface for parsedmarc/OpenSearch query abstraction and checkdmarc execution wrappers.
2. **Workflow layer**
   - Add scheduled workflows for ingest, posture scans, finding generation, and notifications.
3. **App layer**
   - Create `apps/dmarc-control-plane/` with Overview, Senders, Findings, Domain Workbench pages.
4. **Testing**
   - Unit tests for normalization and finding rules under `api/tests/unit/`.
   - Reserve E2E for true cross-service interactions only.

### Suggested initial delivery milestones

- **Milestone A (1–2 weeks):** ingest + overview dashboard + sender list
- **Milestone B (1 week):** findings queue + basic alerts
- **Milestone C (1 week):** domain workbench + policy promotion workflow
- **Milestone D (optional):** forensic/TLS-RPT advanced views

## Decision recommendation

Proceed with a **parsedmarc + checkdmarc + OpenSearch + Bifrost app** strategy.

Why this is the best fit:

- Active and proven open-source components exist for the hardest technical parts (report parsing + DNS/auth validation).
- Bifrost can supply the missing “managed-service-like” workflow UX and organizational process integration.
- The architecture can start lean and grow without lock-in.
