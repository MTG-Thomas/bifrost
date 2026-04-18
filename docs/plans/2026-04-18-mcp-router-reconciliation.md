# MCP Router Reconciliation

**Created:** 2026-04-18
**Status:** Deferred — filed during Task 6 of
`2026-04-18-cli-mutation-surface-and-mcp-parity.md`, to be executed after
that plan ships.
**Owner:** TBD
**Blocking:** nothing immediate; follow-up housekeeping for the MCP
surface's long-term consistency.

## Why this plan exists

Task 6 of the CLI mutation surface + MCP parity plan filled in the *missing*
MCP tool surface (roles, configs, integration create/update, integration
mappings, org update/delete, workflow lifecycle) as **thin wrappers** that
call the REST API via an in-process HTTP bridge.

What it explicitly did **not** do: reconcile the *existing* MCP tools
(`agents`, `forms`, `tables`, `apps`, `events`) with their REST routers.
Those tools duplicate the router logic, and the drift is material — see
the Risks section #6 of the cli-mutation-surface plan (lines 626-627) for
the drift audit summary. That consolidation is a behaviour-reconciliation
project with product decisions in the critical path, not a mechanical
refactor. This plan sequences it.

## Goals

1. Decide, per entity, **whose permission model wins** (REST router vs.
   existing MCP tool).
2. Decide whether missing REST-side side effects (RepoSyncWriter, role
   sync, cache invalidation, audit) should run from both paths or be
   consolidated into a shared service that both the router handler and
   the MCP tool call.
3. Decide whether the MCP tools' warn-and-continue patterns (e.g.
   "role not found, skipping") should become hard `422`s to match REST
   behaviour.
4. Migrate each existing tool to the thin-wrapper shape used by Task 6.
5. Remove duplicated logic, write migration tests, ship.

## Drift inventory (summarised from the audit)

The audit was conducted during the cli-mutation-surface plan's design
pass. Each row below is a single entity's drift; full details live in
the source files referenced.

| Entity | Router (canonical) | MCP tool (current) | Drift |
|---|---|---|---|
| agents | `api/src/routers/agents.py` | `api/src/services/mcp_server/tools/agents.py` | Different permission model (router: scoped repo via `AgentsRepository`; MCP: direct ORM with ad-hoc org filter); MCP lacks RepoSyncWriter emit on create/update; MCP's delegation/tool sub-resource endpoints have divergent validation. |
| forms | `api/src/routers/forms.py` | `tools/forms.py` | Role sync: router calls `sync_form_roles_to_workflows` on update; MCP does not. Cache invalidation divergent. `created_by` semantics differ (router: ctx.user.email; MCP: ctx.user_email with fallback). |
| tables | `api/src/routers/tables.py` | `tools/tables.py` | Router enforces application scope on rename; MCP does not. Router emits `RepoSyncWriter`; MCP emits nothing. Soft-delete semantics differ. |
| apps | `api/src/routers/applications.py` | `tools/apps.py` | Dependencies column handling; MCP tool writes the DB directly without going through the bundler's cache invalidation hooks. |
| events (subscriptions / sources) | `api/src/routers/events.py` | `tools/events.py` | Scheduler wiring: router's `upsert` calls `schedule_cron_source`; MCP direct-writes the row but does not call the scheduler. |

## Per-entity merge decisions (to be made)

For each entity the following three questions must be answered **before**
migration. Estimated effort assumes the answer is "yes, adopt router
behaviour" — flip if the decision lands the other way.

1. **Permission model** — does the router's scoping (repo with `org_id` /
   `user_id` / `is_superuser` injection) become canonical, or is the
   MCP's looser model intentional?
   - Default answer (proposed): router wins. The drift is accidental;
     MCP tools predate the repo scoping refactor.
2. **Missing side effects** — for each side effect the router runs that
   the MCP tool skips (`RepoSyncWriter`, role-sync-to-workflows, cache
   invalidation, audit), does the MCP tool adopt it or stay as a
   fast-path?
   - Default answer (proposed): adopt all side effects. A "fast path"
     that silently skips audit rows is a security / compliance bug, not
     a feature.
3. **Warn-and-continue vs. 422** — MCP tools currently swallow failures
   (e.g. missing role) and log a warning. Should those become hard `422`
   errors matching the router?
   - Default answer (proposed): yes, hard error. The caller is an LLM;
     silent partial success is worse than a loud failure it can retry.

## Sequencing

Four phases, one entity at a time. Each phase is a PR.

1. **Extract shared service** — lift the REST handler body into a
   `services/<entity>_service.py` pure function that takes a session
   and a `Context`-like object. Router calls the service; existing MCP
   tool also calls the service (unchanged behaviour for now). No
   functional changes in this phase; it's a pure refactor that makes
   phase 2 safe.
2. **Flip MCP tool to the service** — replace the MCP tool's direct
   ORM code with a call to the new service function. Run the existing
   MCP E2E test suite (`tests/e2e/mcp/test_mcp_scoped_lookups.py` and
   the parity tests added by Task 6) to catch regressions.
3. **Re-do as a thin wrapper** (optional) — replace the service call
   with an HTTP bridge call, matching Task 6's shape. Keeps the surface
   uniform but doubles the auth handshake; decide per entity whether
   the uniformity is worth it.
4. **Delete duplicated code** — remove the MCP tool's ORM imports,
   ad-hoc scoping, and duplicated validation. Tests stay; they should
   pass with no changes.

## Order of entities (proposed)

`forms` first (smallest drift, well-understood role sync behaviour),
then `tables` (similar shape), then `agents` (largest drift, most
product decisions), then `apps` and `events` last (bundler and
scheduler wiring has enough extra state that they deserve their own
small design docs).

## Non-goals

- Changing the MCP tool parameter surface (tool names, argument names)
  without a deprecation window. Agents and humans already have these
  names bound in prompts / scripts.
- Adding new MCP tools for surface currently not exposed. That work
  belongs in its own plan.

## Open questions

- Do any callers rely on the MCP tools' warn-and-continue behaviour? The
  agent-executor logs both paths; the answer lives in production logs
  and should be checked before deciding on question 3 above.
- Should the service-extraction phase ship under a feature flag? The
  refactor is supposed to be behaviour-preserving, but the audit found
  non-obvious side-effect drift, so a flagged rollout is defensible.

## References

- Task 6 architectural constraint:
  `docs/plans/2026-04-18-cli-mutation-surface-and-mcp-parity.md`
  (lines 350-390).
- Drift audit summary:
  `docs/plans/2026-04-18-cli-mutation-surface-and-mcp-parity.md`
  (Risks section, #6 — lines 626-627).
- Task 6 parity tools (the thin-wrapper exemplar):
  `api/src/services/mcp_server/tools/roles.py`,
  `api/src/services/mcp_server/tools/configs.py`,
  `api/src/services/mcp_server/tools/_http_bridge.py`.
