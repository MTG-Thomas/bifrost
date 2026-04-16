# esbuild-based app bundler (replaces per-file eval runtime)

Date started: 2026-04-16
Owner: Jack
Status: Phase 1 complete (PoC rendering end-to-end); Phases 2-4 pending

## Context

Bifrost apps historically compiled TSX per-file via Babel and stored each compiled string in S3 under the same key as the source. The browser then fetched a JSON blob containing every file's compiled JS string and `new Function()`-eval'd each one with platform scope (`React`, `Button`, `toast`, etc.) injected as named arguments.

Problems this caused in practice:

- No source maps / breakpoints / React DevTools component names (everything is closures inside synthetic `Function` objects)
- Hand-maintained Python copy of the platform export list (`KNOWN_BIFROST_EXPORTS`) that silently rotted — produced the "`toast` is not available" false-positive flood
- No code splitting, tree shaking, minification, browser caching, or `React.lazy`; every navigation re-parsed ~300KB of non-cacheable JSON
- `_apps/<id>/preview/pages/email/index.tsx` stored compiled JS under the `.tsx` key — led to the `_looks_like_jsx` heuristic recompile-loop bug
- Custom `import { SearchInput, Phone, Link } from "bifrost"` convention required all platform exports, Lucide icons, user components, and React Router primitives to flow through one scope-injection blob — couldn't distinguish them, couldn't tree-shake, couldn't get real source maps

Goal: move to a **normal React app** runtime. esbuild bundles the app on save; browser loads it via `<script type="module">`; real import maps; real source maps; real DevTools. Migrate existing apps off the legacy `"bifrost"`-everything pattern toward standard imports (`lucide-react`, `react-router-dom`, relative user-component paths). Delete the old runtime, no feature flag, no DB column, no backwards compatibility.

## Shared-platform design (Option B)

The existing `bifrost` platform (Buttons, Cards, hooks, etc.) stays **shared at runtime** — the host client app loads one copy, and every bundled app resolves `import { Button } from "bifrost"` via an import map pointing at the host's live copy. Same pattern as React itself: nobody ships React twice per app; we won't ship Button twice per app.

Practical implication: if a platform component changes, all apps pick up the change on next load. If that's a breaking change, we handle it with semver / deprecation like any shared library, not by vendoring.

Today this is implemented as a `globalThis.__bifrost_platform` object that the bundled `bifrost` package reads from. Phase 4 cleanup replaces that with a real ESM module exported by the host that the import map points at — same runtime behavior, cleaner plumbing.

## Architecture (what's built)

### Server — `api/src/services/app_bundler/`

- **`bundle.js`** — Node subprocess. Reads JSON config on stdin, runs esbuild with bundle + splitting + ESM + sourcemaps, returns outputs JSON on stdout.
- **`package.json`** — declares `esbuild`.
- **`__init__.py`** — `BundlerService.build(app_id, repo_prefix, mode, dependencies)`:
  1. `tempfile.TemporaryDirectory`
  2. Materialize `_repo/<repo_prefix>/**` → tempdir (skips `app.yaml` and `.tmp.*`)
  3. Synthesize `_entry.tsx` that imports `_layout.tsx` + pages and exports `mount(container, {basename})`
  4. Synthesize `node_modules/bifrost/index.js`
  5. Run `bundle.js`
  6. Upload outputs to `_apps/<id>/<mode>/`
  7. Write `manifest.json` with `{entry, css, outputs, dependencies, duration_ms}`
  8. Invalidate Redis render cache

### Server — `api/src/routers/app_code_files.py`

- `GET /applications/{id}/bundle-manifest` — returns manifest + base URL. Builds on demand if no manifest exists.
- `GET /applications/{id}/bundle-asset/{filename}` — streams bundle artifacts from S3 with correct MIME types and immutable cache headers.

### Client — `client/src/components/jsx-app/BundledAppShell.tsx`

- Fetches manifest
- Installs an **import map** of blob-URL modules that re-export the host's copies of `react`, `react-dom/client`, `react-router-dom`, `react/jsx-runtime`, `lucide-react`, and user npm deps (via esm.sh)
- Populates `globalThis.__bifrost_platform = $` (from existing `app-code-runtime.ts`) before dynamic import
- `await import(entryUrl)` → `module.mount(container, {basename})` where basename = `/apps/<slug>/preview` (or `/apps/<slug>` for live)

### Client — `client/src/components/jsx-app/JsxAppShell.tsx`

- Branches on `localStorage.bifrost.bundled`
  - `?bundled=1` sets the flag; `?bundled=0` clears it
- Flag persists across navigation so dev doesn't have to reapply `?bundled=1` every page

### The "bifrost" virtual package (`_write_bifrost_package`)

esbuild resolves `import { X } from "bifrost"` against a synthesized `node_modules/bifrost/index.js`:

1. **User components** — for each `components/**/*.tsx`, re-export the default export: `export { default as SearchInput } from "../../components/SearchInput"`
2. **Lucide icons** — names imported from `"bifrost"` that aren't user components and aren't platform → re-exported from `"lucide-react"`
3. **React Router primitives** — `Link`, `NavLink`, `Navigate`, `useNavigate` → `export from "react-router-dom"` (NOT the wrapped versions)
4. **Platform exports** — everything else → `export const X = globalThis.__bifrost_platform.X`

The bundler also emits a console warning listing deprecated patterns for the LLM skill and the migration script to target.

## Status — Phase 1 complete

- braytel-crm bundles in ~19ms via esbuild
- Browser loads via `<script type="module">` with real sourcemaps
- Navigation works (single-prefix URLs); `Link` correctly respects basename
- Platform scope resolves correctly
- localStorage flag persists across navigation
- React DevTools shows real component names
- End-to-end verified via Chrome MCP on `https://bifrostdev.musick.gg/apps/braytel-crm/preview?bundled=1`

## Remaining work

### Phase 2 — Wire the save loop

**Goal:** every file write triggers a bundle rebuild. No env guard, no flag. Legacy `AppCompilerService.compile_file` is still available but unused by the app path.

**Changes:**

- `api/src/services/file_storage/file_ops.py` lines ~326-370 ("App files: fire pubsub for real-time preview" block) — replace per-file `AppCompilerService.compile_file` + `write_preview_file` with a single call to `BundlerService.build(app_id, repo_prefix, 'preview', dependencies=app.dependencies)`.
- Same block still fires `publish_app_code_file_update` pubsub so open clients reload.
- The `ensureImportMap` `react-dom/client` etc. conflict-warning noise: once bundled is the default, this only fires once per page load. Accept.

**Verification:**

- Edit `apps/braytel-crm/pages/email/index.tsx` via UI; see `Bundler: built app=... time=...ms` API log; browser preview hot-updates without manual reload.
- Measure rebuild: aim <100ms for braytel-crm (20 files); investigate if >300ms.

**Failure surfacing (critical — today's behavior is silently broken):**

Current per-file path on compile failure: logs a warning server-side, writes raw TSX to S3, browser tries to `new Function()`-eval TSX as JS and throws a cryptic runtime error. User sees a blank page with a confusing console message and no link to the actual syntax error. Fix this as part of Phase 2:

- **Server — preserve last good bundle.** On esbuild failure, do NOT overwrite the manifest. Last good bundle stays live so the user can keep browsing pages that were working while they fix the broken one.
- **Server — publish errors through the diagnostics channel.** `_diagnostics` already exists for lint/compile errors (see the `clear_diagnostic_notification` call right above this block). Route bundler errors through the same path: file, line, column, message. This puts them in whatever UI surfaces diagnostics today.
- **Server — extend `publish_app_code_file_update` with an `error` field.** `BundledAppShell` already subscribes; on error event it shows a banner with the esbuild error and keeps rendering the last good bundle underneath. `console.error` for good measure.
- **Client — error banner.** Dismissible, red, top-of-app-shell. Clears on next successful bundle. Non-blocking (user can still click around the last good version).

Result: breakages are immediately obvious (banner + diagnostic), but non-destructive (last good bundle still works).

### Phase 3 — Migrate existing apps + update the build skill

**Goal:** rewrite all user code to use standard imports. No DB flag, no per-app flip, no partial migration. Migrate everything, commit, done.

#### 3a — Migration CLI command (`bifrost migrate-imports`)

Lives in `api/bifrost/cli.py` alongside `push`/`pull`/`watch`. Detects the user's workspace the same way other commands do (walking up from cwd, honoring `.bifrost/` markers). Supports `--dry-run` (print diff, don't write) and `--yes` (skip confirmation).

Distributed normally: users on their own workspaces run `bifrost migrate-imports` against their apps; we don't ship an external script they have to clone from our repo. Sets precedent for future platform-wide migrations.

Input: every `apps/*/` directory under the detected workspace. For each TSX/TS file:

1. Find every `import { A, B, C } from "bifrost"` (handle the one multi-line import in `margin-dashboard/_layout.tsx` via multiline regex)
2. For each imported name, classify using this **strict precedence** (first match wins):
   1. **User component** — default-exporting file exists at `components/<Name>.tsx` in the same app → rewrite to default import: `import SearchInput from "./components/SearchInput"`
   2. **React Router primitive** — name in `{Link, NavLink, Navigate, useNavigate}` → move into `import { Link } from "react-router-dom"`
   3. **Lucide icon** — name is exported by `lucide-react` (query the installed package's export list, don't hand-maintain) → move into `import { Phone, Mail } from "lucide-react"`
   4. **Platform export** — keep in `"bifrost"` import (or `@bifrost/ui` / `@bifrost/hooks` after Phase 5 reshape — see that section for the eventual shape)

   User-component precedence matters: if a user happens to name a component `Link` or `Phone`, the local file wins. The classifier should warn when this shadows a known platform/lucide/router name so the user knows.
3. Also fix missing user-component imports. Today, components are "auto-injected" (used in JSX without being imported). Find every `<PascalCase>` tag in the file, verify it resolves to an imported name or a default export on a `components/` file in the same app; for anything missing, add the relative default import.
4. Write back. `cd apps/<slug> && git diff` before committing.

Command should be safely idempotent — running it twice on already-migrated code is a no-op. (Not hard: the classifier won't find any `"bifrost"` imports for user components / icons / router primitives on the second run.)

Edge cases flagged by survey:

- `SearchInput` — braytel-crm has `components/SearchInput.tsx`, regex that caught it, migrates cleanly.
- `Checkbox`, `Textarea`, `Trash2`, `Plus` in "unknown" bucket — `Checkbox`/`Textarea` are shadcn platform exports (stay in `"bifrost"`); `Trash2`/`Plus` are Lucide (move to `"lucide-react"`). Classifier handles correctly once we query actual Lucide exports.
- 6 test apps have zero components — script ignores them (no JSX), no-ops.

#### 3b — Update `bifrost-build` skill

File: `.claude/skills/bifrost-build/SKILL.md`. Lines 268-328 teach the old patterns. Rewrite to:

- Platform imports: `import { Button, Card, useWorkflowQuery, useState } from "bifrost"`
- Icons: `import { Phone, Mail } from "lucide-react"`
- User components: `import SearchInput from "./components/SearchInput"` (explicit, default import)
- Router: `import { Link, useNavigate } from "react-router-dom"`
- Remove the "components are auto-injected — do NOT write import statements" guidance — it's now wrong
- Update the "verification" section: "every `<PascalCase>` tag has a real import"

Audit adjacent docs for the same stale guidance: `docs/app-authoring*.md`, `api/bifrost/templates/*`, anywhere that says `import X from "bifrost"` should show the new pattern.

#### 3c — Ship everything together

One commit on main: `bifrost migrate-imports` CLI command + rewrite of `bifrost-workspace/apps/*` using it + skill rewrite. Bundler keeps emitting the deprecation warning for now so any external workspace loading the platform sees the nudge.

- Every app in `bifrost-workspace` works via the new pattern
- Users with their own workspaces can run `bifrost migrate-imports` to upgrade theirs
- The bundler still supports the old pattern during Phase 4 transition so nothing breaks mid-deploy
- Deprecation warnings in the browser console nudge external users to migrate

### Phase 4 — Delete the old runtime

Only run after Phase 3 has landed and every app is confirmed working via the per-app Chrome matrix (zero console errors, all routes render). Then:

**Server:**
- Delete `api/src/services/app_compiler/` (whole package, node_modules + compile.js + tailwind.js)
- Delete `/api/applications/{id}/render` endpoint from `api/src/routers/app_code_files.py` + its related `AppRenderResponse` types
- Remove `AppCompilerService` references from everywhere (MCP tools, etc.)
- Clean up `docker-compose.dev.yml` / `api/Dockerfile.dev` — drop `app_compiler` node_modules volume and install step
- The bundler's synthesized `bifrost/index.js` stops emitting backward-compat for user components / Lucide re-exports (since user code now uses direct imports). Keeps only platform passthrough.
- Remove the deprecation warning from the bundler

**Client:**
- Delete `LegacyJsxAppShell` from `client/src/components/jsx-app/JsxAppShell.tsx`. Rename `BundledAppShell` → `JsxAppShell` (single implementation).
- Delete `client/src/lib/app-code-runtime.ts` — `wrapAsComponent`, `new Function()`, the `$` registry. Replace with:
  - New module `client/src/lib/bifrost-runtime.ts` that re-exports everything the `$` registry held — platform components, hooks, utils — as real named ESM exports.
  - Import map entry: `"bifrost": blobModuleOf(__bifrost_runtime)`. **Decision: no more `globalThis.__bifrost_platform` bridge.** The host exports a real ESM module; the import map points at it; bundled apps import from it like any other package. Kill the globalThis plumbing entirely.
  - After Phase 3 rewrites user code to use relative imports, the bundler's `node_modules/bifrost/index.js` synthesis is deleted entirely — `"bifrost"` becomes just an external that the host's import map resolves.
- Delete `transformPath`, wrapped `Link`/`NavLink`/`Navigate`/`useNavigate`/`navigate` from `client/src/lib/app-code-platform/navigation.tsx`. Platform scope exports raw React Router primitives.
- Delete the `?bundled=1` / localStorage flag logic in `JsxAppShell.tsx`. Single code path.
- Simplify `BundledAppShell` / `JsxAppShell`: drop the `setAppContext` call (was defensive for wrapped Link; Link is now from react-router-dom directly).

**Goal state after Phase 4:** `JsxAppShell` is ~50 lines. It fetches a manifest, installs an import map, dynamic-imports the entry, calls `mount()`. No Babel anywhere. No scope injection. No `new Function()`. No hand-maintained export lists. No `KNOWN_*` anything. No wrapped navigation primitives.

### Phase 5 — Aggressive platform reshape (new)

**Premise:** Phase 3 shipped `bifrost migrate-imports`. That tool is now a first-class lever for breaking platform changes — we don't need deprecation cycles or per-app migration toil. Anything that's awkward about the current `"bifrost"` surface can be rewritten across every app in one PR.

**Goal:** split the `"bifrost"` grab-bag into explicit, tree-shakeable packages and rename platform APIs to match their current best intent. Ship each reshape as one migration PR.

**Workflow for each reshape:**

1. Extend `bifrost migrate-imports` with the new rewrite (e.g. `Button` from `"bifrost"` → `Button` from `"@bifrost/ui"`).
2. Add the new host export module + import map entry.
3. Run the migration against `bifrost-workspace/apps/*`.
4. **Per-app Chrome verification** (dev is hooked up; use Chrome MCP):
   - Load `https://bifrostdev.musick.gg/apps/<slug>/preview`
   - Check console for errors
   - Click through main routes
   - Verify no "[bifrost] missing export" warnings
   - Screenshot for diff if visual
5. Commit each reshape as its own commit on main so `git bisect` is useful if something regresses.

**Candidate reshapes (in order):**

1. **Split `"bifrost"` into `@bifrost/ui` + `@bifrost/hooks` + `@bifrost/utils`.** UI components, React hooks, utility functions. Explicit surface, real tree-shaking, easier to grep for "what does the platform actually expose." Classifier knows which bucket each name belongs to.
2. **Rename stale names.** Whatever's accumulated cruft — `useWorkflowQuery` → `useWorkflow` if that's cleaner, `$` internals gone, etc. Do these case by case as you hit them.
3. **Drop shadcn re-exports that are thin passthroughs.** If `import { Checkbox } from "bifrost"` is just `export { Checkbox } from "@/components/ui/checkbox"`, consider whether apps should import from a first-party path or whether the passthrough is worth it. Lean toward first-party — reduces surface.
4. **Type safety for `useWorkflow`/`useForm`.** Now that we can rewrite callsites mechanically, consider generic parameters or codegen'd types per workflow/form. Migration tool rewrites old call sites to pass the right generic.

**Stopping rule:** the `bifrost-build` skill's import guidance should be boring. When adding a new pattern feels like "that's the obvious shape," stop. Don't reshape for novelty.

**Rollback:** each reshape is one commit; `git revert` + re-run migration backwards if something goes sideways. No flags, no per-app gates.

## Critical files

**Server:**
- `api/src/services/app_bundler/__init__.py` — bundler core
- `api/src/services/app_bundler/bundle.js` — Node subprocess
- `api/src/services/app_bundler/package.json` — esbuild dep
- `api/src/routers/app_code_files.py` — `/bundle-manifest`, `/bundle-asset` endpoints (Phase 4: delete `/render`)
- `api/src/services/file_storage/file_ops.py` — Phase 2 target, lines ~326-370
- `api/Dockerfile.dev` / `docker-compose.dev.yml` — Phase 4: drop app_compiler volume

**Client:**
- `client/src/components/jsx-app/BundledAppShell.tsx` — Phase 4: becomes `JsxAppShell`
- `client/src/components/jsx-app/JsxAppShell.tsx` — Phase 4: legacy branch deleted
- `client/src/lib/app-code-runtime.ts` — Phase 4: split into `bifrost-runtime.ts` (keep) + delete the `new Function()` machinery
- `client/src/lib/app-code-platform/navigation.tsx` — Phase 4: delete wrapper layer

**Migration (Phase 3):**
- `api/bifrost/cli.py` — add `migrate-imports` subcommand
- `api/tests/unit/test_cli_migrate_imports.py` — new test
- `.claude/skills/bifrost-build/SKILL.md` — rewrite import guidance
- All apps in `../bifrost-workspace/apps/*` (committed output of running the new command)

## Survey of existing apps

14 apps, 118 TSX files, ~60 user components. Full details in chat history but key stats:

- Zero apps use namespace imports, aliased imports, dynamic imports, or conditional imports
- Only ONE multi-line import (margin-dashboard `_layout.tsx`)
- Zero apps currently use relative imports for their own components — every app routes everything through `"bifrost"`
- All 6 current `Link` imports are from `"bifrost"` (braytel-crm only)
- 82 unique named imports across all apps — cleanly classifiable as platform / user-component / Lucide / router

Migration-script-friendly shape. No AST parser needed.

## Per-app Chrome verification matrix

Dev is hooked to `bifrostdev.musick.gg`. After each phase that changes runtime behavior (Phase 2 save loop, Phase 3 migration merge, Phase 4 delete, each Phase 5 reshape), hit every app one by one via Chrome MCP and log the result.

Apps (14): `ai-ticket-lifecycle`, `asset-offboarding`, `bifrost-grc`, `braytel-crm`, `customer-onboarding`, `dep-test`, `embed-test`, `employee-onboarding`, `lifecycle-planning`, `margin-dashboard`, `microsoft-csp`, `pm-code`, `shared-mailbox-browser`, `unifi-site-manager`.

Per app:
1. `mcp__claude-in-chrome__navigate` → `https://bifrostdev.musick.gg/apps/<slug>/preview`
2. `read_console_messages` with `pattern: "error|warning|missing"` — zero unexpected hits
3. Click main nav links (use `find` → `computer` click), verify no route errors
4. `read_network_requests` — all bundle assets 200, no 404 against stale `.tsx` keys
5. Record: ✅ clean / ⚠ warnings only / ❌ broken. ❌ blocks the phase; ⚠ gets a followup task.

Dev-test apps (`dep-test`, `embed-test`) have zero components — expect clean load of empty shell.

## Verification runbook

After Phase 4, from a fresh context:

1. `./debug.sh`
2. Open `https://bifrostdev.musick.gg/apps/braytel-crm/preview`  (note: NO `?bundled=1` — it's the default now)
3. DevTools console: zero route errors, zero deprecation warnings (all apps are migrated)
4. DevTools Sources: breakpoint in `apps/braytel-crm/pages/email/index.tsx` hits
5. React DevTools: real component names (e.g. `Clients`, not `SafeComponent`)
6. Network tab: `entry-<hash>.js` and `entry-<hash>.css` 200 with `Cache-Control: public, max-age=31536000, immutable`
7. Click around every page: single-prefix URLs, correct navigation
8. Edit `pages/email/index.tsx` via UI: `Bundler: built` log in API, page hot-reloads

## Dependencies / prerequisites

- esbuild 0.24+ (installed)
- Node 20+ in API container (installed)
- Access to esm.sh for user npm deps (verified: `react-quill-new`, `recharts` both resolve)

## Out of scope

- Persistent warm esbuild contexts / incremental builds (cold ~20ms is fine)
- Full server-side Vite dev server (different project)
- Per-app `node_modules` with real npm install (user deps via esm.sh import map)
- Renaming legacy `_apps/<id>/preview/*.tsx` keys to `.js` — those disappear in Phase 4 cleanup
