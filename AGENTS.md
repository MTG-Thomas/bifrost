# Bifrost Agent Guide (MTG)

Tool-neutral guidance for AI coding agents in `MTG-Thomas/bifrost`. This fork tracks [upstream](https://github.com/gobifrost/bifrost); `CLAUDE.md` remains the detailed **platform** playbook for Persona B work.

**New teammates and workspace-only work:** start with MTG onboarding, not this file alone.

- [Develop at MTG (Windows)](https://github.com/MTG-Thomas/bifrost-ops/blob/main/docs/develop-at-mtg-windows.md) — Persona A (default) and Persona B overview (`MTG-Thomas/bifrost-ops`)
- [Bifrost workspace agent guide](https://github.com/MTG-Thomas/bifrost-workspace/blob/main/AGENTS.md) — automation authoring against `https://dev.bifrost.midtowntg.com` (`MTG-Thomas/bifrost-workspace`)

## Personas

| Persona | Repo | Runtime | Verification |
| --- | --- | --- | --- |
| **A — Workspace author** | `bifrost-workspace` | `https://dev.bifrost.midtowntg.com` | Scoped sync, dev execution, PR CI — **no local Docker** |
| **B — Platform engineer** | `bifrost` (this repo) | Shared Linux dev VM on `pve-t340` or CI | `./test.sh` on the VM; see [Proxmox shared dev roadmap](https://github.com/MTG-Thomas/bifrost-ops/blob/main/docs/proxmox-shared-dev-roadmap.md) |

Assume **Persona A** unless the task clearly changes `api/`, `client/`, migrations, or platform MCP/CLI surfaces.

## Read First (Persona B)

- Work from the current checkout state. Do not revert user or other-agent changes unless explicitly asked.
- Use `rg` for search, inspect nearby code before editing, and follow existing patterns.
- Keep changes narrow and reviewable. Avoid drive-by refactors, dead code, commented-out code, and unrequested fallback paths.
- Treat `.bifrost/` as export-only. Mutate platform entities through the CLI or MCP tools — not by hand-editing manifest YAML.
- New MCP tools must be thin HTTP wrappers around REST endpoints. Do not add direct ORM/repository access in MCP tools.
- When merging upstream `gobifrost/bifrost` changes into this fork, do not recreate or re-enable DigitalOcean deployment CI/CD for `MTG-Thomas/bifrost`. MTG deploys from `bifrost-infra` to Azure. Keep the `.github/workflows/ci.yml` `deploy-dev` job guarded with `github.repository == 'gobifrost/bifrost'`; CI runs `api/scripts/check_mtg_ci_boundaries.py` to fail fast if that guard is removed or weakened.

## Long-running platform jobs (CRITICAL)

`PlatformJob` is the canonical system for durable, non-workflow work that can
outlive an HTTP request. Read
[`docs/architecture/platform-jobs.md`](docs/architecture/platform-jobs.md)
before adding or changing background work.

- Use a platform job for user-initiated platform operations that need durable
  status, progress, retries, cancellation, deduplication, or resource
  protection.
- Do **not** create a feature-specific job table, worker/container, status
  contract, status endpoint, WebSocket event, or browser polling loop. Extend
  the platform-job definition, registry, runner policy, and shared transports.
- The UI receives platform-job progress through the existing notification
  WebSocket system. The CLI may poll the shared
  `/api/platform-jobs/{job_id}` endpoint with short requests.
- Workflow and agent execution still use the execution worker infrastructure.
  Short request-scoped work remains synchronous. Recurring scheduler triggers
  may enqueue platform jobs when their units of work need this durability.
- Existing bespoke job systems are migration candidates, not templates for new
  work. If the shared platform-job system cannot express a requirement, propose
  an extension to it instead of building a parallel system.

Keep this section identical in `AGENTS.md` and `CLAUDE.md`.

## Development Environment (Persona B only)

Platform work runs in **Docker on a Linux host** (shared `pve-t340` dev guest or CI). Do **not** ask Windows teammates to install Docker Desktop for routine work.

2. **Connect via the CLI** from an isolated scratch directory outside the repo (not the worktree, not `~`). This keeps `.env`/credentials out of the source tree and lets you install the API-matched CLI without disturbing the user's global `bifrost` install:
   ```bash
   mkdir -p /tmp/bifrost-cli-<name>
   cd /tmp/bifrost-cli-<name>
   python3 -m venv .venv
   .venv/bin/pip install --quiet --upgrade pip
   .venv/bin/pip install --quiet "<API_URL>/api/cli/download/bifrost-cli.tar.gz"
   ```
   The download endpoint serves the build matching the running API; installing this version avoids the version-mismatch warning that otherwise short-circuits subcommand output.

3. **Log in** inside the scratch directory using password-grant. This writes `.env` (with `BIFROST_API_URL`, `BIFROST_ACCESS_TOKEN`, `BIFROST_REFRESH_TOKEN`) so subsequent commands in this directory pick the tokens up automatically:
   ```bash
   ./.venv/bin/bifrost login --url <API_URL> --email dev@gobifrost.com --password password
   ```
   Default credentials are `dev@gobifrost.com` / `password` (MFA off) — password-grant works only because the dev stack has MFA disabled.

4. **Drive the API** with `./.venv/bin/bifrost <entity> <command> ...`. Use `--help` on any subcommand. Browser testing goes against the URL from step 1.

Tips:
- If a CLI command appears to succeed silently, run the matching `list` to verify — version-mismatch warnings can otherwise mask failures (do not pipe to `/dev/null` blindly).
- The netbird sidecar can transiently drop the peer; if connections start failing, re-check `./debug.sh status` and re-fetch the URL.
- For seeding entities to exercise a UI change (e.g. enough apps/agents to make a page overflow), use `bifrost apps create`, `bifrost agents create`, etc., from this same scratch venv.

## Technologies

-   **Backend**: Python 3.11 (FastAPI), SQLAlchemy, Pydantic, PostgreSQL, RabbitMQ, Redis
-   **Frontend**: TypeScript 4.9+, React, Vite
-   **Storage**: PostgreSQL (data), Redis (cache/sessions), RabbitMQ (message queue)
-   **Infrastructure**: Docker, Docker Compose, GitHub Actions for CI/CD

## Development Environment (CRITICAL - READ FIRST)

### Everything Runs in Docker

**Development happens inside Docker containers, not on the host machine.**

Start the development stack (per-worktree isolated):
```bash
./debug.sh             # Boot stack, print URL + login
./debug.sh status      # Show URL, login, mode (port or netbird)
./debug.sh down        # Tear down + remove volumes for THIS worktree
./debug.sh logs api    # Follow logs for one service
```

`./debug.sh` derives its Compose project name from the worktree path, so multiple worktrees can run debug stacks in parallel. URL and login are printed at the end of `up` (default: `dev@gobifrost.com` / `password`, MFA off).

The default mode allocates a free local port for the client (deterministic per worktree, in 30000-39999). If `NETBIRD_SETUP_KEY` is set in `~/.config/bifrost/debug.env`, the stack boots with a Netbird sidecar instead and is reachable at `http://<bifrost-debug-WORKTREE>` over the Netbird mesh — no host ports.

Stack contains: API (port 8000 internal), Client (port 80 internal), Scheduler, Worker, Postgres, RabbitMQ, Redis, SeaweedFS. All Bifrost services build from `api/Dockerfile.dev` / `client/Dockerfile.dev` (source build, not public images).

### Hot Reload is Automatic

All services have hot reload - **DO NOT restart containers for code changes**:
- **API**: Uvicorn watches `/app/src` and `/app/shared` - restarts automatically
- **Client**: Vite HMR - updates instantly in browser
- **Scheduler/Worker**: watchmedo monitors Python files - restarts automatically

### Vite Proxies All API Requests

The client at `localhost:3000` proxies `/api/*` to the API container. This means:
- Access the app at `http://localhost:3000` (not 8000)
- Type generation works from `client/` directory while stack is running
- No CORS issues - everything goes through Vite

### Container Management Rules

| Scenario | Correct Action | Wrong Action |
|----------|---------------|--------------|
| Code changes | Do nothing (hot reload) | Restart containers |
| New Python dependency | `docker compose restart api` | Rebuild everything |
| Database migration | Restart `bifrost-init` (runs alembic), then restart `api` | Restart entire stack |
| Schema changes | Restart api, then `npm run generate:types` | Manual type updates |
| Something broken | Check logs first: `docker compose logs api` | Nuke and restart |

### DO NOT

- ❌ Run `docker compose up` if containers are already running
- ❌ Run pytest directly on host - use `./test.sh`
- ❌ Start a separate uvicorn/vite process outside Docker
- ❌ Restart the entire stack for single-service changes
- ❌ Manually write TypeScript types for API responses

## File Operation Model

| Content type | Write path | Read path | Cache strategy |
|-------------|-----------|----------|----------------|
| All files | S3 `_repo/` via `RepoStorage` | S3 `_repo/` via `RepoStorage` | S3 is source of truth |
| Text files | + `file_index` DB | `file_index` (search only) | Search index, never read content |
| Python modules/workflows | + generation-stamped Redis via `set_module()` | current-generation Redis via `get_module()` → object-storage fallback | Write-through; stale generations are misses and self-heal |
| Compiled app files | S3 `_apps/{id}/preview/` | Redis render cache → S3 fallback | Invalidate on write, lazy rebuild |

**`get_module()` must NOT be used for non-Python files.** App source reads (TSX, YAML, etc.) go to S3 directly via `RepoStorage.read()`.
The complete contract and release-repair semantics are documented in
[`docs/architecture/workspace-source-coherence.md`](docs/architecture/workspace-source-coherence.md).

### Form & agent inline manifest content — portability design

Form and agent **content lives inline under each UUID** in `.bifrost/forms.yaml` and `.bifrost/agents.yaml`. There are no per-UUID `.form.yaml` / `.agent.yaml` files anymore — the manifest is the source of truth for both identity and content.

The same portability rule still applies: the inline content intentionally **excludes environment-specific fields** (`access_level`, `organization_id`, `roles`, `created_by`, timestamps). Those live on the DB record and on the manifest entry alongside (but distinct from) the portable content. The portable content (fields, prompts, tool bindings, etc.) can be shared with the community or imported across environments without carrying org/role/access assumptions. **Do not add environment-specific fields to the inline manifest serialization** — they belong on the DB entity record / the manifest entry's env-specific section, not the portable content.

## Manifest Serialization & Git Sync (Integration Data)

The git sync system uses a **manifest** (`.bifrost/*.yaml`) to round-trip platform entities between the database and git. The `_resolve_*` methods in `github_sync.py` handle importing manifest data into the DB.

### Key files

| File | Purpose |
|------|---------|
| `api/src/services/manifest.py` | Pydantic models for manifest data (`ManifestIntegration`, `ManifestIntegrationMapping`, `ManifestConfig`, etc.) |
| `api/src/services/manifest_generator.py` | DB → manifest serialization (generates `.bifrost/*.yaml` from DB state) |
| `api/src/services/github_sync.py` | Manifest → DB import (`_resolve_integration`, `_resolve_config`, etc.) + stale-entity cleanup |

### Integration sync: what gets serialized

| Entity | Manifest model | DB model | Natural key for upsert |
|--------|---------------|----------|----------------------|
| Integration | `ManifestIntegration` | `Integration` | `name` or `id` |
| Config schema | `ManifestConfigSchemaItem` (list in integration) | `IntegrationConfigSchema` | `(integration_id, key)` |
| Mappings | `ManifestIntegrationMapping` (list in integration) | `IntegrationMapping` | `(integration_id, organization_id)` |
| Config values | `ManifestConfig` (separate `configs` dict) | `Config` | `id` (UUID) |

### App sync

`ManifestApp` in `.bifrost/apps.yaml` carries all app metadata (name, description, dependencies, access_level, roles). The `path` field points to the app source directory (e.g. `apps/my-app`), which contains only TSX/TS/CSS source code — no metadata files. App npm dependencies are stored in the `Application.dependencies` JSON column in the DB.

### Critical: non-destructive upsert pattern

`_resolve_integration` syncs config schema and mappings using **upsert-by-natural-key** (not delete-all + re-insert):

-   **Why**: `IntegrationConfigSchema` rows are referenced by `Config` rows via FK (`config_schema_id`). Deleting schema rows cascades to Config values set by users in the UI.
-   **Why**: `IntegrationMapping` rows carry `oauth_token_id` set by users via OAuth flow. Deleting and re-creating mappings loses this.
-   **Pattern**: Query existing rows → update matching → insert new → delete removed.
-   **Stale-entity cleanup**: Config rows with a `config_schema_id` (user-set integration values) are excluded from the "delete configs not in manifest" sweep, since their lifecycle is managed by IntegrationConfigSchema cascade.

### When adding new fields to manifest models

1. Add the field to the Pydantic model in `manifest.py` (e.g., `ManifestIntegrationMapping`)
2. Add serialization in `manifest_generator.py` (DB → manifest)
3. Add deserialization in `github_sync.py` `_resolve_*` method (manifest → DB)
4. **For upserted entities**: ensure the new field is included in BOTH the update-existing AND insert-new code paths
5. Write a round-trip unit test in `tests/unit/test_manifest.py` and an E2E test in `tests/e2e/platform/test_git_sync_local.py`

## Keeping CLI, MCP, and manifest in sync

Entity mutations have three parallel surfaces: **CLI** (`bifrost <entity> ...`), **MCP** (`api/src/services/mcp_server/tools/`), and **manifest export** (`.bifrost/*.yaml` via `bifrost sync` / `bifrost export`). All three read from the same `XxxCreate` / `XxxUpdate` Pydantic DTOs via `api/bifrost/dto_flags.py`, so drift is caught by tests rather than review discipline.

**When a DTO changes:**

1. Run the DTO-parity test: `./test.sh tests/unit/test_dto_flags.py`. If it fails, either add the new field to the appropriate CLI command / MCP tool, or add it to `DTO_EXCLUDES` in `api/bifrost/dto_flags.py` with a one-line comment explaining why (UI-managed, out-of-scope, etc.).
2. If the field should round-trip in portable exports, update `api/bifrost/manifest.py` (`ManifestXxx` pydantic models) and the scrub rules in `api/bifrost/portable.py`.
3. If the field changes a command or tool, regenerate the skill appendices via `python api/scripts/skill-truth/generate.py` (CI enforces freshness).
4. **Run the CLI-contract tripwire: `./test.sh tests/unit/test_contract_version.py`.** It fingerprints every CLI/SDK-consumed DTO (command DTOs + all `src.models.contracts.cli` SDK DTOs, pulled in programmatically). If one changed, the test forces a decision: for a **CLI-impacting/breaking** change (field removed/renamed/retyped, a response shape the CLI parses), raise `MIN_CLI_VERSION` in `api/shared/version.py` to the release containing the compatible CLI, then refresh `EXPECTED_CONTRACT_FINGERPRINT`; for a **compatible additive/cosmetic** change, refresh only the fingerprint. The runtime hard gate is the server-provided minimum CLI version. `CONTRACT_VERSION = 10` is a frozen one-release bridge for older CLIs that shipped while minimum gating was absent; do not continue bumping it. (Pure route renames aren't fingerprinted — they 404 loudly rather than corrupt; the DTO layer catches silent shape breakages.)

**When renaming or reassigning an entity (workflow, table, config):** grep the codebase before committing. Workflows are referenced by `path::func` in forms; tables are referenced by name in workflow SDK calls (`sdk.tables.get("...")`); configs are referenced by key. `bifrost tables update --name` warns on renames but does not block — the author is responsible for a full-workspace search (`rg -n '\b<old-name>\b' apps/ workflows/`) before pushing.

**`.bifrost/` is export-only.** Watch only syncs code (`apps/`, `workflows/*.py`); it does NOT push `.bifrost/` content. Entity mutations go through the CLI (`bifrost orgs create`, `bifrost roles update`, etc.) or MCP. To distribute a packaged set of entities into an org, use **Solutions** (`bifrost solution deploy` / `solution install <zip>`) — the install-scoped, lifecycle-aware successor to the older `bifrost export --portable` / `bifrost import` flow. **`bifrost export` / `bifrost import` were REMOVED** (they predated Solutions and the `--portable` scrub never actually stripped org IDs). For raw `_repo/` workspace movement across environments, use git sync.

**MCP vs REST routers (existing drift):** the MCP tools for `agents`, `forms`, `tables`, `apps`, `events` re-implement router logic and have diverged (different permission models, missing side effects, divergent validation). See `docs/plans/2026-04-18-mcp-router-reconciliation.md` for the catalog and reconciliation sequence. **New MCP tools must be thin HTTP wrappers that call the REST endpoints** (see `api/src/services/mcp_server/tools/roles.py` / `configs.py` / `_http_bridge.py` for the pattern) — no direct ORM access, no repository imports. A unit test (`api/tests/unit/test_mcp_thin_wrapper.py`) enforces this.

## Project Structure

```
api/
├── src/              # FastAPI application
│   ├── handlers/     # HTTP endpoint handlers (thin layer)
│   ├── models/       # SQLAlchemy models
│   ├── jobs/         # Background job workers
│   └── main.py       # FastAPI app entry point
├── shared/           # Business logic, utilities
│   ├── models.py     # Pydantic models (source of truth)
│   └── ...
├── alembic/          # Database migrations
└── tests/            # Unit and E2E tests

client/
├── src/
│   ├── services/     # API client wrappers
│   └── lib/
│       └── v1.d.ts   # Auto-generated TypeScript types
└── ...
```

## Project-Specific Rules

### No dead code, no unrequested fallbacks

Never leave dead code, commented-out code, unused helpers, or "just in case" fallback branches behind. Never add a fallback, compatibility shim, or alternate code path that the user did not explicitly ask for — these get forgotten and become latent bugs (see the `multiprocessing.spawn` fallback in `process_pool.py` that silently leaked ~800 MB per pod in production). If you think a fallback might be warranted, **ask first**. When removing a code path, also remove everything that was only reachable from it in the same change.

### Agent summary cost lives in `total_cost_7d`

Summarizer-generated `AIUsage` rows roll up into `AgentStats.total_cost_7d` (the Spend (7d) card). A backfill of N runs will move the card by roughly `N × (avg summarizer cost per run)`. The backfill endpoint (`POST /api/agent-runs/backfill-summaries`) shows a cost estimate up front — runbook at `docs/runbooks/agent-summary-backfill.md`.

### Backend (Python/FastAPI)

-   **Models**: All Pydantic models MUST be defined in `api/shared/models.py`
-   **Routing**: Create one handler file per base route (e.g., `/discovery` → `discovery_handlers.py`)
    -   Sub-routes and related functions live in the same file
-   **Request/Response**: Always use Pydantic Request and Response models
-   **Business Logic**: MUST live in `api/shared/`, NOT in `api/src/handlers/`
    -   Handlers are thin HTTP handlers only
    -   Complex logic, algorithms, business rules go in shared modules
    -   Example: User provisioning logic lives in `shared/user_provisioning.py`

### Frontend (TypeScript/React)

-   **Type Generation**: Run `npm run generate:types` in `client/` after API changes
    -   Must run while API is running
    -   Types are auto-generated from OpenAPI spec based on `models.py`
    -   Never manually write TypeScript types for API endpoints
-   **API Services**: Create service files in `client/src/services/` for new endpoints
-   **Scrollable content sizing**: Prefer "content-sized until constrained" for lists, tables, drawers, panels, and other scrollable regions. Let the region size to its contents when short; cap it with available-space `max-height` and apply `overflow-auto` only after it reaches that cap. Avoid `flex-1` / forced full-height scroll containers for short content unless the design explicitly calls for a fill-height workspace. In drawers and dialogs, keep fixed headers/metadata outside the scroller and put `overflow-auto` on the long content region itself.

Example service pattern:
```typescript
import { apiClient } from "@/lib/api-client";
import type { components } from "@/lib/v1";

// Re-export types for convenience
export type DataProvider = components["schemas"]["DataProviderMetadata"];
export type DataProviderResponse = components["schemas"]["DataProviderResponse"];

export async function getDataProviders() {
  return apiClient.get<DataProviderResponse>("/api/data-providers");
}
```

### Testing & Quality

-   **Tests**: All work requires tests. Backend logic → unit tests in `api/tests/unit/`. Endpoint/workflow/integration changes → e2e tests in `api/tests/e2e/`. React components → sibling `*.test.tsx` (vitest). User-facing features → happy-path spec in `client/e2e/` (Playwright).
    -   **Functional frontend modules require vitest coverage.** New or modified `.ts` files under `client/src/lib/**` and `client/src/services/**` that export functions (auth helpers, storage adapters, API wrappers, formatters, etc.) need a sibling `*.test.ts` covering the public API. Pure type/constant re-export files and files that only import and re-configure third-party SDKs are exempt. If the module has a cross-tab, cross-window, or storage-boundary concern (like `auth-token.ts`), the test MUST exercise that boundary — a regression that only reproduces with two tabs open is one a future refactor will silently re-introduce otherwise.
    -   **Scoped verification is the local default.** Run the tests that directly exercise the changed behavior, its known consumers, and any affected contract boundaries. Full backend, Vitest, and Playwright suites are broad integration-gate checks, not a routine local completion requirement. Until every suite is wired into that gate, targeted coverage for changed behavior remains mandatory; never assume an unrun suite is covered elsewhere. Report exactly what ran and which broader suites did not run.
    -   **Keep CI dependency edges explicit.** CI selects focused API and client tests from repo-local imports, their transitive reverse dependency closure, and literal FastAPI route calls in backend e2e tests. New runtime registration or string-dispatch mechanisms must either add a modeled edge in `api/scripts/plan_affected_tests.py` with regression coverage or remain comprehensive. Every source file in an affected closure needs direct or transitive test ownership; do not add fake ownership metadata or hide an import to force a fast lane.
    -   **Uncertain changes intentionally stay comprehensive.** Deletions, dependency locks, API/ORM contracts, migrations, Docker/runtime inputs, CI/planner code, mixed backend-client source changes, excessive fan-out, parse errors, and unowned downstream source run the broad gates. Review the uploaded `affected-test-plan` JSON when a plan is surprising.
    -   **A known failure must receive a durable disposition.** If a broader local or CI run finds a failure outside the scoped set, determine whether it is a regression, product race, leaked state, harness defect, overcomplicated test, or obsolete coverage. Fix the cause, simplify the test, or delete genuinely redundant coverage with a documented replacement. "Unrelated" or "flaky" alone is not a disposition.
    -   **Never rerun until green or mask instability.** Do not add retries, longer timeouts, `skip`, or `xfail`. Reproduce the exposing condition, fix the hypothesized cause, then use repetition only to validate the fix. See `.claude/skills/bifrost-testing/SKILL.md` for the full protocol.
    -   **Prefer simple, durable tests.** Keep one observable contract per test, minimal fixtures, deterministic state, explicit cleanup, and only one useful end-to-end happy path. Move edge cases down to unit/component tests; complexity is not evidence of rigor.
    -   **IMPORTANT**: Always use `./test.sh` — it manages the Dockerized test stack (PostgreSQL, Redis, RabbitMQ, SeaweedFS, API, worker). Running pytest directly on the host will FAIL for anything touching DB/queue/cache.
    -   **Stack lifecycle is separate from test execution.** Boot once per worktree, run tests many times. See the Commands section below.
    -   **Test results**: `./test.sh` writes JUnit XML to `/tmp/bifrost/test-results.xml` — parse this for pass/fail details instead of grepping stdout.
    -   **Logs**: Container logs are exported to `/tmp/bifrost-<project>/*.log` after test runs (per-worktree, so parallel worktrees don't clobber each other).
-   **Type Checking**: Must pass `pyright` (API) and `npm run tsc` (client)
-   **Linting**: Must pass `ruff check` (API) and `npm run lint` (client)

### Commands

```bash
# Dev stack (per-worktree)
./debug.sh                                         # Boot dev stack (hot reload)
./debug.sh status                                  # URL + login
./debug.sh down                                    # Tear down + wipe volumes
./debug.sh logs api                                # Follow one service's logs

# Test stack lifecycle (per worktree, long-lived)
./test.sh stack up                                 # Boot the test stack for this worktree
./test.sh stack down                               # Tear it down + remove volumes
./test.sh stack reset                              # Fast state reset (<2s) — DB clone + redis flush + object storage wipe
./test.sh stack status                             # Is the stack up? What project name?

# Backend tests (stack must be up; state auto-reset before each run)
./test.sh                                          # Unit tests only (fast default)
./test.sh unit                                     # Same
./test.sh e2e                                      # Backend e2e
./test.sh all                                      # All backend tests, including slow tests
./test.sh tests/unit/test_foo.py::test_bar -v      # Passthrough to pytest

# Client tests
./test.sh client unit                              # Vitest on host (no stack needed)
./test.sh client e2e                               # Playwright in containers
./test.sh client e2e --screenshots                 # Capture screenshots for every test (UX review)
./test.sh client e2e e2e/auth.unauth.spec.ts       # Passthrough to Playwright

# CI (one-shot: boot → all tests → tear down)
./test.sh pre-pr                         # Required clean-commit gate before opening/queueing a PR
./test.sh ci

# Type Generation (requires dev stack running via ./debug.sh)
(cd client && npm run generate:types)     # Regenerate TypeScript types from API

# Quality Checks
./test.sh quality api                     # Type check + lint Python in Docker
(cd client && npm run tsc)                # Type check TypeScript
(cd client && npm run lint)               # Lint TypeScript
```

Hot reload is expected. Do not restart containers for ordinary code changes. Use targeted restarts only when dependencies, migrations, or generated API types require them.

Do not start host-side FastAPI, Vite, pytest, Postgres, Redis, RabbitMQ, or MinIO **on Windows** for platform verification.

## Local Coordination (optional)

Multiple Codex threads on one machine may use the local coordinator (warning-only, not in git):

**After adding a Python dependency:**
1. Add to `pyproject.toml` (root)
2. Regenerate the lock: `docker run --rm -v "$PWD":/repo -w /repo python:3.14-slim sh -c "pip install --quiet --require-hashes -r requirements-piptools.lock && pip-compile --generate-hashes --output-file=requirements.lock pyproject.toml"`
3. Rebuild and restart: `docker compose -f docker-compose.dev.yml up --build api`

**Merging main into a long-lived branch:** Two specific gotchas:

1. **`client/src/lib/v1.d.ts` always conflicts.** It's a generated file. Resolve by taking main's version, then regenerating against your running API:
   ```bash
   git checkout --theirs client/src/lib/v1.d.ts
   cd client && OPENAPI_URL=<your-dev-url>/openapi.json npm run generate:types
   cd .. && git add client/src/lib/v1.d.ts && git commit --no-edit
   ```
   Get the URL from `./debug.sh status`. The regen must happen *after* the merge is in place, because it reflects the merged code's schema.

2. **Newly-added Python deps on main break the dev container silently.** If main introduced a new `import` (e.g. `defusedxml`) and your container was built before that dep landed in `requirements.lock`, the API will start failing with `ModuleNotFoundError` after the next restart, and `/openapi.json` will 502. Symptom: regen suddenly can't fetch the schema. Fix:
   ```bash
   docker logs bifrost-debug-<project>-api-1 --tail 20   # confirm ModuleNotFoundError
   docker exec bifrost-debug-<project>-api-1 pip install <missing-pkg>
   docker restart bifrost-debug-<project>-api-1
   ```
   This is a per-container quick fix. The lasting fix is `./debug.sh down` and a fresh `./debug.sh` (which rebuilds against the new lock), but the in-container `pip install` unblocks you in 10 seconds.

## Pre-Completion Verification (REQUIRED)

Before marking work complete, select verification from the actual change surface. The examples below are choices, not a command list to run wholesale:

```bash
# Backend source changed
./test.sh quality api
./test.sh tests/unit/test_relevant_behavior.py -v
./test.sh tests/e2e/path/test_relevant_boundary.py -v  # when a live boundary changed

# Client source changed
(cd client && npm run tsc && npm run lint)
./test.sh client unit src/path/RelevantComponent.test.tsx
./test.sh client e2e e2e/relevant-flow.admin.spec.ts  # when a user journey changed

# API contract changed: regenerate types against this worktree's running debug stack
./debug.sh status | grep -q "Status:   UP" || ./debug.sh up
(cd client && npm run generate:types)
```

The selected tests must cover the changed behavior, known consumers, and contract tripwires. During iteration, do not run `./test.sh all`, the full Vitest suite, or the full Playwright suite by default; use targeted coverage to get fast, relevant feedback.

Before opening or queueing a PR, the worktree must be clean and based on current `origin/main`, and `./test.sh pre-pr` must pass for the exact `HEAD`. This is separate from scoped completion verification: it runs every locally reproducible required PR/merge-queue check, including the full backend E2E and critical browser-smoke suites. GitHub still owns non-local boundaries such as the synthetic merge ref, registry publication, signing, and attestation.

In the final handoff, list the exact checks and tests run, state which broader suites were not run, and disclose any known failure. A known out-of-scope failure must be permanently fixed in the current change or split into a dedicated blocking repair change; it cannot be waived as flaky or left as an unowned follow-up.

Release claims after meaningful work. Skip entirely if the operator does not use Codex.

## Code Boundaries

- Backend API is FastAPI/Python. Keep HTTP handlers thin and put business logic in the shared/service layer.
- Request and response contracts use Pydantic models in `api/shared/models.py`.
- Frontend API types are generated from OpenAPI. Do not hand-write endpoint response types.
- If API contracts change, run type generation from `client/` while the dev stack is running.
- Manifest, CLI, and MCP surfaces must stay in sync when DTOs change. Run the DTO parity test in `CLAUDE.md`.

## Testing And Verification (Persona B)

- Primary bar: `./test.sh` on the **shared Linux dev environment** or **GitHub Actions** on `MTG-Thomas/bifrost`.
- Dedicated VM target: when a task needs a remote test VM, hardware-backed validation, long-running suites off the workstation, or says "test it on the VM", use the dedicated Bifrost test VMs hosted on the Dell T340 Proxmox host `pve-t340.netbird.cloud`.
- Do **not** fall back to the legacy laptop Proxmox host named `pve` or its VM unless the user explicitly asks for that legacy target.
- SSH key material and Proxmox access details for `pve-t340` live in Keeper. Retrieve them at the time of use; never copy secrets into this repo, docs, shell history snippets, or coordination notes.
- If Keeper or `pve-t340.netbird.cloud` is unavailable, report that blocker instead of silently testing somewhere else.
- When reporting VM-backed test results, include the actual target (`pve-t340.netbird.cloud`, VM name/id if known, and repo/worktree path) so accidental legacy-VM testing is visible.
- Do not treat a Windows laptop without the stack as the platform verification environment.

Match verification to the change (see `CLAUDE.md` for detail):

- Backend logic: `api/tests/unit/`
- Endpoint, queue, DB, or workflow behavior: `api/tests/e2e/`
- React components: sibling `*.test.tsx`
- Functional frontend modules under `client/src/lib/**` or `client/src/services/**`: sibling `*.test.ts`
- User-facing UI flows: Playwright in `client/e2e/`

Before calling significant platform work complete, run the relevant checks from `CLAUDE.md` and report anything skipped with the reason.

Use [docs/dev/delivery-lanes.md](docs/dev/delivery-lanes.md) to choose the
lightest verification and review path that protects the actual risk. Do not run
the full pre-completion matrix for tiny docs, narrow tests, or throwaway spikes
unless the task specifically depends on stack behavior.

Default PR review to one accountable reviewer. Add extra human or AI reviewers
only for sensitive paths, broad ownership boundaries, unfamiliar code, or
explicit user request; stacked generic reviewers are delivery drag, not maturity.

For Codex-authored PRs, treat automated review as a velocity-preserving safety
net. Address concrete correctness, security, auth, execution, deployment, and
coverage findings; summarize or dismiss duplicated, stale, style-only, or
scope-expanding bot feedback. Do one focused stewardship pass by default, then a
second pass only if real defects remain or the user asks.

## Security And Sensitive Paths

Use extra care around auth, execution, multi-tenancy filters, migrations, secrets, manifest round-trips, and audit logging. Call out sensitive-path changes in summaries and PR descriptions.

Do not expose secret values in chat, logs, test fixtures, screenshots, or committed files.

## Useful References

- `CLAUDE.md` — upstream-style platform commands, manifest rules, verification checklist
- `CONTRIBUTING.md` — human-facing PR expectations, DCO, and governance links
- [docs/dev/delivery-lanes.md](docs/dev/delivery-lanes.md) — right-sized verification and reviewer budget
- [GOVERNANCE.md](./GOVERNANCE.md) — maintainer roles, culture, continuity
- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) — community standards
- [docs/security/openssf-best-practices-badge.md](docs/security/openssf-best-practices-badge.md) — BadgeApp tiers and submit flow
- [Develop at MTG (Windows)](https://github.com/MTG-Thomas/bifrost-ops/blob/main/docs/develop-at-mtg-windows.md) — team onboarding (Windows-first)
- [Proxmox shared dev roadmap](https://github.com/MTG-Thomas/bifrost-ops/blob/main/docs/proxmox-shared-dev-roadmap.md) — shared lab + Entra direction
- `.claude/skills/` — upstream maintainer workflows (release, issues); **ignore for MTG day-to-day**
- `api/tests/README.md` — test structure and fixture notes
