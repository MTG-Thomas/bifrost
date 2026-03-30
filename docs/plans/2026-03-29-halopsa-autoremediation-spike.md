# HaloPSA Auto-Remediation Workflow Module — Architecture & Research Spike

**Date:** 2026-03-29  
**Status:** Research/architecture only (no implementation)  
**Scope:** HaloPSA-triggered Tier 1 triage + guarded remediation orchestration in Bifrost

---

## 1) Repo reconnaissance summary

This proposal is based on current Bifrost surfaces for:

- event ingestion and webhook routing (`/api/hooks/{source_id}` + adapter protocol),
- workflow dispatch and event subscriptions,
- existing HaloPSA/NinjaOne/IT Glue/Huntress/CIPP integration clients,
- reusable HaloPSA and NinjaOne extension helpers.

### Key reusable platform blocks identified

1. **Webhook/event framework already exists**
   - Public webhook receiver: `api/src/routers/hooks.py`
   - Event source + subscription CRUD: `api/src/routers/events.py`
   - Adapter protocol + result contract: `api/src/services/webhooks/protocol.py`
   - Event processing and delivery queueing: `api/src/services/events/processor.py`
   - Existing generic adapter pattern: `api/src/services/webhooks/adapters/generic.py`

2. **HaloPSA integration and helpers are extensive**
   - Auto-generated HaloPSA client with tickets/actions/users/sites/webhooks endpoints: `modules/halopsa.py`
   - Extension helpers for ticket creation, notes, and org→client mapping resolution: `modules/extensions/halopsa.py`
   - Existing shared HaloPSA data providers (clients/sites/open tickets): `shared/halopsa/data_providers.py`

3. **NinjaOne already has script execution utility glue**
   - `modules/extensions/ninjaone.py` provides a production-friendly fetch-and-run PowerShell pattern with polling and result capture.

4. **Other context integrations are ready as read APIs**
   - IT Glue: `modules/itglue.py`
   - Huntress: `modules/huntress.py`
   - CIPP: `modules/cipp.py`

---

## 2) HaloPSA webhook payload research (ticket creation)

### What is confirmed

- HaloPSA supports webhook resources and incoming webhook records in API (`/Webhook`, `/WebhookEvent`, `/IncomingWebhook`): `modules/halopsa.py`.
- Bifrost can already receive arbitrary webhook payloads via event source callbacks (`/api/hooks/{source_id}`).

### What is **not fully confirmed in publicly crawlable docs**

- A canonical, static “ticket created webhook JSON payload schema” from HaloPSA public docs was not retrievable without tenant-scoped interactive API docs.

### Working assumption for architecture

- Treat Halo webhook payload as potentially variable by event config/templating.
- Require a **normalization layer** that:
  1. accepts raw payload,
  2. extracts a minimal required contract (`ticket_id`, `client_id`, summary/description, requester/contact info, timestamps),
  3. falls back to Halo API enrichment (`get_tickets`) when payload is sparse.

---

## 3) API capability audit (context + remediation)

## 3.1 NinjaOne

### Context-read capabilities (present in generated client)

- Devices/orgs: list/get device and organization APIs.
- Alerts and activities: `list_alerts`, `list_activities`, `get_activities`.
- Device health and state: windows services, disks/volumes, patch reports, logged-on users, etc.

### Remediation/write capabilities

- Script execution is available via run endpoints (`create_run`) and wrapped in `modules/extensions/ninjaone.py` with polling and status persistence.
- Device patch/update endpoints exist for some scenarios.

### Fit for this module

- Strong fit for low-risk automated remediation classes (service restart, update trigger, cleanup script, verification scripts).

## 3.2 IT Glue

### Context-read capabilities

- Organizations, configurations (CIs), flexible assets, documents, related items, logs, domains/expirations.
- Good for known issue knowledge lookups, device CI context, and linkbacks for technician briefs.

### Remediation/write capabilities

- IT Glue is primarily a documentation system in this flow; no direct remediation expected.

## 3.3 HaloPSA

### Context-read capabilities

- Tickets (list/get), users, clients, sites, SLA-related entities, actions/history.

### Write-back capabilities

- Create ticket actions/notes and update tickets (status/fields).
- Webhook and webhook event endpoints exist.

### Fit for this module

- Trigger source + system of record for triage notes, remediation outcomes, escalation state, and AI-resolved tagging.

## 3.4 Huntress

### Context-read capabilities

- Organizations, agents, incident reports, remediations/signals/escalations endpoints exposed in generated client.

### Remediation/write capabilities

- Some resolution/approval APIs exist for Huntress-native flows, but this module should start read-only for Huntress context to reduce risk.

## 3.5 CIPP

### Context-read capabilities in current Bifrost client

- Tenants, users, groups, licenses, alerts, incidents, defender state, domain health, standards, conditional access, devices.

### Remediation/write capabilities in current Bifrost client

- Current first-party wrapper is predominantly read/list oriented.
- Account unlock/write actions should be routed through Graph/on-prem automation modules if introduced.

---

## 4) Proposed known issue taxonomy (initial 12 classes)

Each class below has: `risk`, `default_mode`, `min_confidence`, `verification`, `rollback`.

> **Mode definitions:**
> - `auto`: execute immediately when authorized + confidence threshold met.
> - `confirm`: queue a pending action and allow technician cancellation/approval window.
> - `brief_only`: no execution, triage brief only.

1. **Windows service stopped (non-critical service set)**
   - risk: low
   - default_mode: auto
   - min_confidence: 0.88
   - action: NinjaOne restart target service
   - verification: service running after 1–3 min + alert clears
   - rollback: restart backoff + escalate (no destructive rollback)

2. **Print spooler failure**
   - risk: low
   - default_mode: auto
   - min_confidence: 0.90
   - action: restart spooler + clear stuck queue (safe script)
   - verification: spooler running, no stuck jobs, no fresh spooler errors in short window
   - rollback: none; escalate if persistent

3. **Defender definitions out of date**
   - risk: low
   - default_mode: auto
   - min_confidence: 0.90
   - action: trigger Defender signature update via NinjaOne
   - verification: definition timestamp/version advanced
   - rollback: none

4. **Disk space critical on system volume**
   - risk: medium
   - default_mode: confirm
   - min_confidence: 0.92
   - action: curated cleanup script (temp/cache/log rotation only)
   - verification: free space increase >= configured threshold
   - rollback: delete rollback only where recoverable; otherwise escalate with artifact log

5. **Stale RMM agent / missed check-ins**
   - risk: medium
   - default_mode: confirm
   - min_confidence: 0.86
   - action: restart agent services + connectivity diagnostics
   - verification: check-in resumes within SLA window
   - rollback: none; escalation

6. **High CPU from known noisy process signature**
   - risk: medium
   - default_mode: confirm
   - min_confidence: 0.90
   - action: bounded remediation runbook (service restart/process recycle if approved list)
   - verification: CPU baseline improves within observation window
   - rollback: restart original service/process if applicable

7. **Windows Update pending reboot blocking patches**
   - risk: medium
   - default_mode: confirm
   - min_confidence: 0.93
   - action: schedule reboot under policy window
   - verification: host returns, patch compliance improves
   - rollback: cancel pending reboot pre-execution only

8. **User lockout (M365/AAD)**
   - risk: high (identity)
   - default_mode: brief_only (phase 1), confirm-only (phase 3)
   - min_confidence: 0.97
   - action: unlock + force password reset pathway (future)
   - verification: sign-in success telemetry/no immediate re-lockout
   - rollback: re-disable/revoke sessions where policy demands

9. **MFA challenge loops / user auth friction**
   - risk: high
   - default_mode: brief_only
   - min_confidence: 0.95
   - action: none initially; provide guided triage
   - verification: N/A
   - rollback: N/A

10. **Endpoint malware alert context triage (Huntress + Defender mismatch)**
    - risk: high
    - default_mode: brief_only
    - min_confidence: 0.90
    - action: no autonomous containment initially; structured escalation brief
    - verification: technician action only
    - rollback: N/A

11. **Line-of-business app service dependency down**
    - risk: medium
    - default_mode: confirm
    - min_confidence: 0.90
    - action: dependency order restart via scripted runbook
    - verification: service chain healthy + app port response check
    - rollback: reverse service state if possible

12. **Recurring known-error signature (from IT Glue KB + similar closed tickets)**
    - risk: variable (inherits mapped class risk)
    - default_mode: mapped from issue class
    - min_confidence: 0.90 + exact signature match requirement
    - action: class-specific runbook
    - verification: class-specific
    - rollback: class-specific

---

## 5) Governance model design

## 5.1 Policy layers

1. **Global defaults** (platform-level)
   - default confidence per class
   - class enabled/disabled default
   - default action mode (`auto|confirm|brief_only`)

2. **Per-client policy overrides**
   - enabled remediation classes
   - class confidence override
   - class mode override
   - global kill switch (`enabled=false`)

3. **Execution-time dynamic gates**
   - confidence gate
   - authorization gate
   - maintenance window / business hours gate
   - duplicate-action cooldown gate

## 5.2 Authorization model

Recommended per-client authorization levels:

- **Level A (Brief Only):** no automation writes
- **Level B (Low-Risk Auto):** low-risk auto, medium/high blocked
- **Level C (Guarded Medium):** low auto + medium confirm window
- **Level D (Extended):** custom allow-list including selected high-risk confirmed actions

## 5.3 Confirmation/cancel window for medium risk

- Pending action record with `execute_after` timestamp (e.g., +3 to +10 min).
- Halo internal note immediately posted: “Pending remediation: cancel link/button/ref”.
- If canceled before deadline, action transitions to `canceled` and brief is posted.

## 5.4 Audit trail (HIPAA-oriented)

Persist immutable audit records per run containing:

- trigger metadata (source, timestamp, raw payload hash)
- context retrieval evidence (which systems queried, result counts, errors)
- model input summary hash + model version (`claude-sonnet-4-20250514`)
- predicted class + confidence + rationale summary
- policy evaluation decision trace (why action allowed/blocked)
- remediation command metadata (script id, target device, parameters hash)
- verification results + final disposition
- actor transitions (auto/system vs technician override)

## 5.5 Failure escalation path

- If any remediation verification fails:
  1. mark run `verification_failed`,
  2. write rich internal note to Halo,
  3. set ticket triage state/tag for technician queue,
  4. include executed steps and artifact links.

---

## 6) AI triage prompt design

## 6.1 Prompt contract

### System prompt goals

- classify ticket to known taxonomy,
- estimate confidence,
- determine remediation eligibility and safety,
- produce technician-grade triage brief when not executing.

### Guardrails

- model cannot invent unavailable data; must cite evidence IDs from supplied context objects.
- must output strict JSON schema.
- must surface uncertainty and missing critical context.
- must not recommend high-risk action unless policy + confidence constraints are explicitly met.

## 6.2 Suggested input envelope (example)

```json
{
  "ticket": {
    "id": 12345,
    "summary": "Server print queue stuck",
    "description": "Users cannot print to \"FrontOffice-01\"",
    "client_id": 2001,
    "device_id": "NINJA-99881",
    "priority": "P3",
    "created_at": "2026-03-29T10:02:11Z"
  },
  "context": {
    "ninjaone": {...},
    "it_glue": {...},
    "halopsa": {...},
    "huntress": {...},
    "cipp": {...}
  },
  "policy": {
    "client_mode": "confirm",
    "enabled_classes": ["print_spooler_failure", "windows_service_stopped"],
    "thresholds": {
      "print_spooler_failure": 0.9
    }
  }
}
```

## 6.3 Suggested model output schema (example)

```json
{
  "classification": {
    "issue_class": "print_spooler_failure",
    "confidence": 0.94,
    "root_cause_hypothesis": "Spooler service crash after failed driver update",
    "evidence": [
      "ninja.windows_services.spooler.state=stopped",
      "ninja.alerts[0].subject=Print Spooler",
      "halopsa.similar_tickets[2].resolution=Restart spooler"
    ]
  },
  "decision": {
    "eligible_for_auto_remediation": true,
    "risk_level": "low",
    "required_mode": "auto",
    "policy_checks": {
      "class_enabled": true,
      "confidence_threshold_met": true,
      "client_mode_allows": true
    }
  },
  "remediation_plan": {
    "action_id": "restart_print_spooler",
    "verification_steps": [
      "confirm service status running",
      "confirm no active spooler alert after 3m"
    ],
    "rollback": "none"
  },
  "triage_brief": {
    "summary": "Likely spooler crash, automated restart appropriate.",
    "diagnostic_steps": [
      "check recent print driver changes",
      "review event log for spoolsv crashes"
    ],
    "documentation_links": [
      "https://itglue/.../docs/print-spooler-runbook"
    ],
    "similar_tickets": [
      {"id": 11880, "resolution": "Restarted spooler and cleared queue"}
    ]
  }
}
```

---

## 7) Proposed module architecture

## 7.1 High-level components

1. **Halo webhook adapter (`halopsa_ticket`)**
   - Parses and validates incoming webhook calls.
   - Normalizes event type to `ticket.created`.

2. **Ticket context orchestrator workflow**
   - Given normalized ticket event, gathers context from:
     - HaloPSA (ticket + related history + SLA)
     - NinjaOne (device state + alerts + patches + services + script/job history)
     - IT Glue (CI + known issue docs + related docs/assets)
     - Huntress (alerts/incidents)
     - CIPP (conditional for M365 signals)

3. **AI triage classifier**
   - Calls Anthropic model with strict JSON response schema.
   - Returns class/confidence/decision/brief.

4. **Policy engine**
   - Resolves effective policy: global + client override.
   - Enforces risk/mode/confidence/authorization.

5. **Remediation engine**
   - Executes allowed action via runbook abstraction (mostly NinjaOne phase 1/2).
   - Runs verification steps.
   - Handles rollback/escalation.

6. **Halo write-back service**
   - Posts internal notes for:
     - triage brief,
     - pending confirmation,
     - execution logs,
     - verification outcomes,
     - escalation summary.

7. **Audit logger**
   - Writes immutable audit records (table-based + optional export stream).

## 7.2 Data/state model (recommended)

Use dedicated tables (via Bifrost tables abstraction) for:

- `ticket_automation_runs`
- `ticket_automation_actions`
- `ticket_automation_policy_overrides`
- `ticket_automation_audit`

Include idempotency key: `halopsa_ticket_id + workflow_version`.

## 7.3 Sequence (target behavior)

1. Halo sends ticket-created webhook.
2. Adapter validates/signature-checks and emits `ticket.created` event.
3. Subscription launches triage workflow.
4. Context pipeline executes with timeouts + partial-failure handling.
5. AI classification runs.
6. Policy engine chooses `auto` vs `confirm` vs `brief_only`.
7. If action executes, remediation + verification occur.
8. Halo ticket gets structured note(s) and resolution updates as appropriate.
9. Audit record finalized.

---

## 8) HaloPSA write-back design details

- **Internal notes:** always include structured blocks (decision, evidence, actions, outcome).
- **AI-resolved marker:** set dedicated resolution code/field + `ai_resolved=true` tag.
- **SLA touch behavior:** default to adding an internal note that can be counted as a first-response touch only if Halo configuration treats internal actions that way.
- Add an explicit config toggle: `count_ai_note_as_first_response` with tenant-specific validation.

---

## 9) Shared Bifrost components to reuse (no reinvention)

1. Event/webhook backbone in `api/src/services/webhooks/*` and `api/src/routers/hooks.py`.
2. Event delivery/subscription model in `api/src/services/events/processor.py`.
3. HaloPSA helper utilities in `modules/extensions/halopsa.py` (client mapping, note conventions).
4. NinjaOne script execution helper in `modules/extensions/ninjaone.py`.
5. Existing integration clients in `modules/*.py` for context collection.
6. Existing LLM provider infrastructure in API (`api/src/services/llm/*`) for model config and provider abstraction.

---

## 10) Phased build order

## Phase 0 — Foundations (no remediation)

- Implement Halo webhook adapter + normalization.
- Build context gatherer with resilient partial-fetch behavior.
- Generate and post structured triage brief only.
- Build audit records + run correlation IDs.

**Exit criteria:** reliable `ticket.created -> triage brief` on test tenants.

## Phase 1 — Low-risk auto-remediation

- Enable 3–5 low-risk classes (service restart/spooler/defender update).
- Add verification checks and automatic escalation on failure.
- Add per-client class enablement + thresholds + mode controls.

**Exit criteria:** measurable deflection with low regression/error rate.

## Phase 2 — Medium-risk confirmed remediation

- Add confirm window workflow + cancel path.
- Add maintenance window/blackout controls.
- Expand taxonomy to disk cleanup, agent recovery, controlled reboots.

**Exit criteria:** technician trust + high override clarity.

## Phase 3 — High-risk governed actions (optional)

- Identity-affecting actions (unlock/reset) with strict dual-control + explicit customer authorization.
- Strong compliance reporting and QA workflows.

**Exit criteria:** explicit legal/compliance sign-off.

---

## 11) Highest-risk decisions requiring explicit sign-off

1. **Whether AI can directly trigger any identity action** (unlock/reset/session revoke).  
2. **SLA interpretation** for AI-generated/internal notes as “first response.”  
3. **Auto-close policy** (which classes can auto-resolve and with what QA holdback).  
4. **Confidence threshold policy ownership** (who can lower thresholds and audit approvals).  
5. **Rollback guarantees** for non-transactional endpoint actions (especially cleanup/reboot classes).  
6. **Data retention/encryption policy** for AI context payloads and audit artifacts.  
7. **Cross-platform trust model** when sources disagree (e.g., Ninja healthy vs Huntress active incident).

---

## 12) Ambiguities to resolve before implementation

1. **Halo ticket-created webhook payload contract**
   - Need tenant-level example payload(s) for real parser contract.

2. **Ticket→device mapping strategy**
   - Source of truth order when ticket lacks device ID (custom field? CI relation? matching heuristics?).

3. **Per-client policy storage surface**
   - Prefer Bifrost tables vs org config vs integration config extension.

4. **Technician override UX**
   - Where cancellation occurs (Halo action button, Bifrost UI, both).

5. **Resolution code taxonomy in Halo**
   - Which exact status/resolution fields are mandatory for AI-resolved tickets.

6. **CIPP identity remediation boundary**
   - Whether CIPP remains read-only context source vs write executor for any identity actions.

7. **Runbook ownership model**
   - Who approves/upgrades remediation scripts per class (and tenant-specific variants).

8. **Multi-ticket dedupe behavior**
   - Handling burst duplicates for the same device/issue in short windows.

9. **Compliance requirements depth**
   - HIPAA logging fields, retention duration, and export/audit format expectations.

---

## 13) External documentation touched during research

- IT Glue developer API docs (public index and auth/rate limits): https://api.itglue.com/developer/
- Huntress REST API overview (base URL/auth/data domains): https://support.huntress.io/hc/en-us/articles/4780697192851-Huntress-REST-API-Overview
- Halo public guides/navigation (API docs are tenant-scoped, webhook article references discovered but payload schema not publicly extractable): https://usehalo.com/halopsa/guides/2305/

