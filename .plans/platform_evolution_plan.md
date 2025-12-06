# Platform Evolution Plan

## Objectives
- Establish global+org inheritance for configs and OAuth with clear UI surfacing of effective values and overrides.
- Stabilize workflow identity to survive renames/moves while keeping Git as source of truth.
- Enable agent-callable workflows with schema-driven slot-filling, role gating, and optional approvals.
- Provide a pluggable approvals SDK with audit trails and delivery adapters.
- Keep secrets in DB, workspace holds code/metadata; improve portability via manifests.
- Add minimal observability for runs, approvals, and delivery attempts.

## 1) Config & OAuth Inheritance
- Identity: keep canonical slug (current name) and add `display_name` for UI.
- Scopes: `global -> org` (last-write-wins). Org-only entries allowed.
- UI: show all entries in selected org scope with columns `Name/Slug`, `Display Name`, `Type` (Global | Override | Org-only), `Effective Value`, `Overrides Stack` (global value + org override when present).
- Behavior: renames are slug changes + code find/replace; consider optional alias support later.
- Data: metadata in workspace is fine; secret values remain only in DB.

## 2) Workflow Identity & Versioning
- Extend `@workflow` with `unique_id` (slug/UUID). If absent, generate on sync and persist back to DB and workspace so future edits keep identity across renames/moves.
- Keep `name` as display only. "Latest wins" from Git remains the model.
- Store `unique_id` in DB for cross-references; ensure discovery watcher reads/writes it.

## 3) Agent-Enabled Workflows
- Extend `@workflow` with `ai_agent=True` and `ai_instructions="..."`.
- Inputs reuse `@param` schemas for validation; chat layer performs slot-filling until required params are satisfied or fails fast.
- Role gating: reuse form roles/assignments; only expose agent-callable workflows permitted for the user’s roles.
- Tool registry: workflows with `ai_agent=True` are registered as tools (by `unique_id` + display name + instructions + param schema).
- System prompt composition: platform-level system prompt + workflow name + `ai_instructions`.

## 4) Chat UX (minimal viable flow)
- Detect candidate tools by role + intent (instructions/name matching).
- If required params missing/ambiguous, ask clarifying questions (slot-filling); then validate against schema.
- Optional confirmation step for high-risk actions.
- Render approval status inline when a workflow is blocked pending approval.
- Persist light session context per user+org for a short TTL to support multi-turn slot-filling; avoid heavy PII storage.

## 5) Approvals SDK
- State machine: `requested -> pending -> approved | denied | expired`.
- Storage: Postgres tables for approvals + events (audit trail) owned by platform.
- Policies defined in workspace code (developers write policy functions or decorators). Later: allow org overrides if needed.
- Delivery adapters: pluggable interfaces (Slack/Teams/email/webhook); platform handles retries with backoff and logs delivery attempts.
- API surface: SDK helper to request approval, await/subscribe to result, and inject into workflow execution.
- Optional tie-in: agent-invoked workflows can declare a policy; approvals must resolve before execution continues.

## 6) Export/Import & Secrets
- Workspace/Git remains source for workflows/forms and non-secret metadata.
- Secret values for configs/OAuth live only in DB (KMS/envelope encryption). Workspace may include non-secret manifests.
- Provide a "secrets manifest" (non-secret) listing required secrets/keys to re-provision when restoring a workspace.
- Discovery watcher continues to sync workspace -> DB; scanner warns on missing configs/OAuth referenced in code.

## 7) Observability & Governance (minimal)
- Log workflow runs, agent invocations, approval lifecycle events, and channel delivery attempts.
- Retry failed deliveries with backoff; surface status in UI.
- Later expansions: per-workflow lineage, richer analytics, and policy evaluations.

## 8) UI Adjustments (high level)
- Config/OAuth: combined view per org scope; show effective value, override stack, and Type badge (Global/Override/Org-only). Org switch retains full list visibility.
- Agent console: role-aware tool list with `ai_instructions` description, slot-filling prompts, approval/confirmation indicators, and execution results.

## 9) Suggested Implementation Order
1) Add `unique_id` support to `@workflow`, DB, and discovery sync (auto-generate if missing).
2) Implement config/OAuth inheritance model and UI (effective value + Type/override stack).
3) Build approvals SDK: Postgres tables, state machine, adapter interfaces, and sample adapters (Slack/email/webhook) in workspace code; retries + audit events.
4) Implement agent flags (`ai_agent`, `ai_instructions`), tool registry, role gating, and schema-based slot-filling chat flow.
5) Integrate approvals with agent-invoked workflows (optional per-workflow policy).
6) Add secrets manifest + ensure secrets stay DB-only; scanner warnings stay active.
7) Add observability hooks/logs for runs, approvals, and delivery attempts.
