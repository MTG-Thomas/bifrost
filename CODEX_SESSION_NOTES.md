# Codex Session Notes

**Last updated:** 2026-03-30

This file is a Codex-friendly distillation of the top-level `CLAUDE.md`,
selected `.claude/` behavior, and the current integration-session context.
It exists so future sessions do not need to reconstruct operating assumptions
from Claude-specific config files.

## Scope

These notes describe how to work in the `bifrost` repo, not the separate
workflow/content repos.

## Fast Bootstrap

- Repo root for platform/source work: `~/mtg-bifrost/bifrost`
- Preferred repo-local CLI wrapper: [`scripts/bifrost-local.sh`](/home/thomas/mtg-bifrost/bifrost/scripts/bifrost-local.sh)
- Preferred repo-local sync helper: [`scripts/bifrost-local-sync.sh`](/home/thomas/mtg-bifrost/bifrost/scripts/bifrost-local-sync.sh)
- Repo-local watch helper for fast iteration: [`scripts/bifrost-local-watch.sh`](/home/thomas/mtg-bifrost/bifrost/scripts/bifrost-local-watch.sh)
- Watchable repo-local dev-run helper: [`scripts/bifrost-local-devrun.sh`](/home/thomas/mtg-bifrost/bifrost/scripts/bifrost-local-devrun.sh)
- Default credentials backend should be `pass`
- If the Bifrost instance is only reachable through NetBird and uses a private
  or self-signed certificate chain, run the repo-local CLI with
  `BIFROST_SSL_NO_VERIFY=1` unless a proper CA bundle has been installed

Bootstrap commands:

```bash
cd ~/mtg-bifrost/bifrost
export BIFROST_CREDENTIALS_BACKEND=pass
export BIFROST_SSL_NO_VERIFY=1
./scripts/bifrost-local.sh api GET /health
```

Preferred day-to-day directory sync flow:

```bash
cd ~/mtg-bifrost/bifrost
export BIFROST_CREDENTIALS_BACKEND=pass
export BIFROST_SSL_NO_VERIFY=1
./scripts/bifrost-local-sync.sh features/autotask
```

Preferred repo-local Python test flow:

```bash
cd ~/mtg-bifrost/bifrost/api
../.venv/bin/python -m pytest
```

Notes:

- This repo now has a repo-local virtualenv at `~/mtg-bifrost/bifrost/.venv`
- Prefer `../.venv/bin/python -m pytest` over host `pytest`
- On this machine, that avoids host package drift and stale console-script shebangs

Do not use the sync/watch wrappers against `.bifrost` in normal development.
Those wrappers now refuse `.bifrost` unless you explicitly opt in with
`BIFROST_ALLOW_MANIFEST_SYNC=1` for a one-off tactical repair.

If you have a trusted CA bundle instead of using insecure mode:

```bash
export BIFROST_CA_BUNDLE=/path/to/ca.pem
./scripts/bifrost-local.sh api GET /health
```

Notes:

- The pipx-installed `bifrost` binary may not match the repo-local SDK/CLI code
- Future sessions should prefer the repo-local wrapper above when they need
  predictable behavior from the current checkout
- The local SDK/CLI honors:
  - `BIFROST_CREDENTIALS_BACKEND=pass`
  - `BIFROST_PASS_ENTRY=bifrost/credentials`
  - `BIFROST_SSL_NO_VERIFY=1` or `BIFROST_INSECURE=1`
  - `BIFROST_CA_BUNDLE=/path/to/ca.pem`

Important CLI behavior difference:

- The pipx-installed `bifrost` on this machine uses older `run` behavior:
  interactive/browser-backed by default, standalone only when `--params` is used
- The repo-local CLI in this checkout changed on 2026-02-26:
  direct local execution is now the default, and browser-backed execution
  requires `--interactive`
- The repo-local wrapper now hard-blocks `run` unless
  `BIFROST_ALLOW_LOCAL_RUN=1` is set
- If you want runs to appear in Bifrost's UI/Workbench, use:
  - `./scripts/bifrost-local-devrun.sh <file> -w <workflow>`
  - or `BIFROST_ALLOW_LOCAL_RUN=1 ./scripts/bifrost-local.sh run <file> --interactive -w <workflow>`
- If you want workflows to be runnable from the normal Bifrost UI and show up in
  standard execution history, sync them first with:
  - `./scripts/bifrost-local-sync.sh` as the default
  - `./scripts/bifrost-local-watch.sh` when iterating very quickly

Recommended workflow default:

- Prefer `sync` for normal userland changes so server state stays intentional and reviewable
- Use `watch` only when making rapid repeated edits and you need fast feedback
- Use `run --interactive` / `bifrost-local-devrun.sh` only when the user
  explicitly asked for local-file execution through the Workbench UI

## Autotask Primitives Added

Current reusable Autotask workflows added in this session:

- `features/autotask/workflows/review_timesheet.py`
  - `review_autotask_timesheet_coverage`
- `features/autotask/workflows/get_task.py`
  - `get_autotask_task`
- `features/autotask/workflows/work_item_index.py`
  - `sync_autotask_work_item_index`
  - `search_autotask_work_item_index`

Watchable invocation pattern:

```bash
cd ~/mtg-bifrost/bifrost
export BIFROST_CREDENTIALS_BACKEND=pass
export BIFROST_SSL_NO_VERIFY=1
./scripts/bifrost-local-devrun.sh features/autotask/workflows/work_item_index.py \
  -w sync_autotask_work_item_index
```

Notes on the work-item index:

- Table name: `autotask_work_items`
- The index stores normalized metadata, not raw Autotask payloads
- Included fields are intended for lookup/reporting:
  - item IDs/type, task or ticket number, title, status
  - company ID/name
  - project ID
  - assigned resource ID/name
  - contact ID/name/email when present on tickets
  - created/updated/completed timestamps
  - `is_closed`
- It intentionally does **not** store descriptions, notes, internal notes, or
  raw payload blobs
- Tasks do not reliably carry company/contact directly; task company enrichment
  currently comes from the linked Autotask project record when available

## Repo State

- Repo: `~/mtg-bifrost/bifrost`
- Active branch at time of writing: `main`
- The platform is FastAPI + React and is developed primarily through Docker
- `.bifrost/*.yaml` manifests are currently committed in this fork, but that is
  a fork-local model under active review rather than a stable upstream pattern

## Core Operating Rules

### Development Environment

- Treat Docker as the normal development environment
- Start the stack with `./debug.sh`
- Access the app through `http://localhost:3000`
- Do not assume host-level Python or frontend processes are the intended path
- Hot reload is expected; do not restart the whole stack for normal file edits

### Testing and Verification

- Use `./test.sh` for backend tests rather than host `pytest`
- If full Docker-backed test execution is unavailable, at minimum run targeted
  syntax/import sanity checks and say so explicitly
- Standard quality gates from `CLAUDE.md` remain:
  - backend: `pyright`, `ruff check`
  - frontend: `npm run generate:types`, `npm run tsc`, `npm run lint`

### File and Architecture Rules

- Cross-feature workspace logic belongs in top-level `shared/`, not in thin HTTP handlers
- The repo no longer has `api/shared/`; Docker and local tooling should reference `shared/`
- Frontend API types should be generated, not handwritten
- S3/RepoStorage is the source of truth for repo content in the platform
- For manifests and git sync, use non-destructive upsert patterns for
  integrations, config schema, and mappings
- The fork currently carries committed `.bifrost/*.yaml`, but upstream treats
  `.bifrost/` as generated/system-managed workspace state
- Do not treat manual `.bifrost/*.yaml` editing as a normal development path
- If `.bifrost/*.yaml` must be edited at all, limit it to tactical repair of
  already-broken generated state that is blocking manifest import or sync, and
  document the reason for the exception
- See `docs/plans/2026-03-26-upstream-convergence-plan.md` before doing more
  repo-model work around manifests or dev image workflows
- Use `docs/plans/2026-03-27-manifest-transition-guidance.md` as the current
  operating rule: `.bifrost/` is a discovery surface and transitional sync
  artifact, not a safe long-term authored source-of-truth
- The built-in Git/GitHub integration should now be treated as deprecated for
  this fork's day-to-day workflow; see
  `docs/plans/2026-03-27-post-github-integration-workflow.md`

## Useful `.claude` Behavior That Matters

### Environment Detection Hook

The hook at `.claude/hooks/bifrost-detect.sh` tries to detect:

- whether local Bifrost source exists
- whether the `bifrost` CLI is installed
- whether local Bifrost credentials are present
- whether a Bifrost MCP server is configured
- Python version / install command hints

That detection is Claude-specific, but the practical takeaway is:

- future sessions should quickly determine whether they have local source,
  working platform credentials, and a usable CLI before assuming SDK-first
  workflows are available

### Build Skill Guidance

The main reusable ideas from `.claude/skills/bifrost-build/SKILL.md` are:

- prefer local-repo discovery over remote platform discovery when source is
  available
- fetch platform docs once per session if needed and reuse them
- treat `.bifrost/*.yaml` as a current discovery surface, but not as a safe
  long-term source-of-truth assumption
- avoid teaching or reinforcing manual `.bifrost/*.yaml` authoring as the
  default workflow
- when broken generated state forces a manifest repair, keep the edit minimal
  and treat it as an exception to be documented, not a reusable pattern
- generate UUIDs before writing cross-referenced manifest entities
- rely on `bifrost watch` only when intentionally using SDK/watch workflows

For this repo specifically, those ideas are secondary to the repo-level Docker
and test rules above.

## Integration Work Pattern Used In This Session

For first-class vendor integrations, the working pattern has been:

1. `modules/{vendor}.py`
   - focused async `httpx` client
   - auth handling
   - normalized customer/entity helpers
   - `get_client(scope: str | None = None)` that reads Bifrost integration config
2. `features/{vendor}/workflows/data_providers.py`
   - returns sorted `{value, label}` options for org mapping
3. `features/{vendor}/workflows/sync_*.py`
   - lists vendor entities
   - matches or creates Bifrost orgs
   - upserts `IntegrationMapping`
4. `.bifrost/integrations.yaml`
   - add integration entry and config schema
5. `.bifrost/workflows.yaml`
   - add workflow + data provider metadata
6. `api/tests/unit/test_{vendor}_integration.py`
   - config contract
   - sorting/normalization
   - sync behavior

## Post-GitHub Workflow

Jack plans to deprecate the in-app Git/GitHub integration. For this fork,
assume that workflow is going away and prefer the direct platform sync path.

### Source Control

- local git is the source of truth for code review, branching, rebasing, and
  merges
- GitHub remains for repo hosting and collaboration
- do not depend on Bifrost's `/api/github/*` workflow for normal delivery

### Userland Changes

For workspace-level changes that the platform can load from RepoStorage:

- `features/`
- `modules/`
- `shared/`
- `helpers/`
- `workflows/`
- `apps/`
- current fork-local `.bifrost/` manifests

Use the direct CLI/API sync path instead of GitHub sync:

- `bifrost push [path]`
- `bifrost sync [path]`
- `bifrost watch [path]`

These commands write files through `/api/files/*` and run manifest import via
`/api/files/manifest/import`.

### Platform Code Changes

Changes under these paths are not "userland" and require an image rebuild or
deployment update to affect the running platform:

- `api/`
- `client/`
- `docker-compose*.yml`
- image/build/deployment files

For dev, use the SSH + k3s rollout path on `10.1.23.114` rather than relying on
workspace sync.

### Current Dev Server Baseline

- dev GitHub config was switched back to `MTG-Thomas/bifrost` on branch `main`
  on 2026-03-27
- treat that as a temporary compatibility setting, not the preferred workflow

## Current Integration Coverage

As of this note, `integrations.yaml` includes:

- CIPP
- Microsoft CSP
- Microsoft
- GoToConnect
- DNSFilter
- Meraki
- VIPRE
- Quoter
- ConnectSecure
- Pax8
- Huntress
- HaloPSA
- AutoElevate
- NinjaOne
- Cove Data Protection
- Datto SaaS Protection
- Google Workspace Reseller
- Google Workspace
- Datto RMM
- Datto Networking
- IT Glue
- Keeper MSP

## Current Architectural Decisions Worth Preserving

### Keeper

- Keeper is integrated as `Keeper MSP` through Commander Service Mode over HTTP
- Keeper is treated as a system Bifrost manages, not as Bifrost's primary
  secrets backend

### Microsoft

- `Microsoft CSP` and `Microsoft` are intentionally separate integrations
- `Microsoft CSP` is the partner-side delegated OAuth connection for Partner
  Center, tenant discovery, GDAP, and consent workflows
- `Microsoft` is the Bifrost customer-tenant app identity used for Graph and
  Exchange after tenants are linked and consented
- The Microsoft CSP app expects both integrations to be configured; one does not
  replace the other
- Preferred security model: `Microsoft` should be a dedicated Bifrost Entra app
  / service-principal style identity, while `Microsoft CSP` remains the
  delegated partner-admin connection
- Detailed rationale is in
  `docs/plans/2026-03-26-microsoft-integration-boundaries.md`
- Additional service-account guidance is in
  `docs/plans/2026-03-26-microsoft-service-account-model.md`
- For Bifrost runtime secrets, prefer an external store such as Azure Key Vault
- See `docs/plans/2026-03-25-keeper-msp-integration-design-note.md`

### Vendor Order-Lifecycle Research

Current conclusion:

- Amazon Business is the cleanest fit for order / shipment / delivery events
- Dell appears workable but requires partner onboarding and webhook setup
- TD SYNNEX is clearly workable for StreamOne cloud orders, but physical-order
  shipment tracking is not verified from public API docs
- LuxSci remains deferred pending business relevance

See `docs/plans/2026-03-25-order-lifecycle-vendor-api-research.md`

## Operational Constraints Observed In This Session

- Docker was not available in the execution environment, so `./test.sh` could
  not be run here
- local `pytest` was blocked by missing dependencies such as `pytest_asyncio`
- in that constrained environment, lightweight validation used:
  - `python3 -m py_compile`
  - YAML parse checks
  - import sanity checks with adjusted `sys.path`

Future sessions should still prefer the full Docker-backed validation path when
available.

## Suggested Start-of-Session Checklist

1. Read this file
2. Check branch and `git status`
3. Check whether Docker and `./test.sh` are usable
4. Read any note in `docs/plans/` that matches the feature area being touched
5. Confirm whether the task is:
   - platform/repo work in `bifrost`
   - community contribution work in `bifrost-workspace-community`
   - external integration research in `~/agents/integrations`

## When In Doubt

- prefer repository source over stale handoff assumptions
- prefer focused vendor clients over committing large generated SDKs unless the
  broader surface is actually needed
- document architectural calls in normal repo docs, not only in ephemeral chat
