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

## Status update — 2026-04-16 late afternoon

Phases 1–3 shipped. 11/11 registered apps in `bifrost-workspace` render cleanly on the bundled runtime with `?bundled=1`. The migration CLI + tests are in. One main-repo commit: Phase 2 + 3 bundler/save-loop/CLI/skill together.

**What was harder than the plan assumed:**

- **Classifier built in 6 rounds, not 1.** The migrate-imports precedence, lucide-alias handling, user-component export style, multi-line import insertion, lowercase-name inference, and not-counting-import-bodies-as-references each needed a fix after a Chrome test exposed it. Each bug was discoverable up-front from reading `app-code-runtime.ts`'s `$` registry and comparing to real apps — I didn't do that reading.
- **Bundler had two architectural bugs surfaced only in later apps.** The entry file wrapped `<BrowserRouter>` (nested-router error) and used `createRoot(container).render(...)` (sibling React root, no context inheritance from AuthProvider/QueryClient). Fixes: drop BrowserRouter, export a default React component, render it inline in `BundledAppShell`.
- **Dual-React via esm.sh.** User deps like recharts bundled their own React. Fix: `?external=react,react-dom,...` on every esm.sh URL.
- **Missing-app confusion.** Four apps in `bifrost-workspace/apps/*` weren't registered in the Applications DB table (`employee-onboarding`, `unifi-site-manager`, `dep-test`, `embed-test`). Looked like bundler failures; turned out to be "never created."

**Patchy parts Phase 3.5 must clean up** (see below).

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

### Phase 3.5 — Harden what we built

Must land before Phase 4. Phases 2–3 shipped a lot of bolt-on fixes under time pressure; 3.5 consolidates them so Phase 4's deletions don't expose the patchwork.

**1. One source of truth for platform names.**
`_PLATFORM_EXPORT_NAMES` is currently duplicated in `api/src/services/app_bundler/__init__.py` AND `api/bifrost/cli.py` with a "keep in sync" comment. This is the exact pattern that rotted the old `KNOWN_BIFROST_EXPORTS` list (the thing this whole plan is supposed to kill). Pick one location, import from the other. Either:
- `cli.py` imports from `app_bundler` at runtime (cli is already Docker-container-aware), or
- both read from a shared JSON/TOML file committed to the repo.

**2. Make the platform list complete.**
Lowercase platform exports (`cn`, `toast`, `clsx`, `twMerge`, `format`, `navigate`, `formatDate`, `formatDateShort`, `formatTime`, `formatRelativeTime`, `formatBytes`, `formatNumber`, `formatCost`, `formatDuration`) were initially missing from `_PLATFORM_EXPORT_NAMES` — the classifier handled them via a separate code path. One list, every name the runtime provides.

**3. Add a drift test.**
`tests/unit/test_platform_names_match_runtime.py` — iterate `app-code-runtime.ts`'s `$` registry (either parse the file or export a JSON manifest from the client build), assert every key is in `_PLATFORM_EXPORT_NAMES`. Prevents silent drift forever.

**4. Consolidate error surfacing.**
`BundlerService.build()` returns `BundleResult`. Today `file_ops._rebuild_app_bundle()` has two try/excepts: one for `bundler.build()` throwing (shouldn't happen now — errors come back in `BundleResult.errors`), one for the pubsub publish. Collapse into one `_report_bundle_result(result)` helper that handles both success and failure: pubsub, diagnostic create/clear, logging. Callers just call `_report_bundle_result`.

**5. Shrink the synthesized `node_modules/bifrost/index.js`.**
Current behavior: unconditionally emits platform-scope proxies (`_p["Button"]`, `_p["cn"]`, …) for every name in `_PLATFORM_EXPORT_NAMES`, even when user code doesn't import from `"bifrost"` at all. After Phase 3 migration, most apps import `Button` directly from `"bifrost"` but the bundler still generates the full proxy table. Shrink to only the names `imported_names` contains. Two knock-on benefits: smaller bundles; easier to delete this package entirely in Phase 4.

**6. Phase 4 prep file: wire it up or delete it.**
`client/src/lib/bifrost-runtime.ts.phase4-prep` exists because its `export *` statements collided with lucide-react (Badge, Sheet, Table, Command). Fix the collisions now (explicit named re-exports for the overlap) so Phase 4 can `git mv` and be done. No `.phase4-prep` suffix hiding in the tree.

**7. Clean out dead classifier helpers.**
`_JSX_NON_COMPONENT_PREFIXES`, `_NAMED_IMPORT_RE`, `_extract_jsx_tag_names` in `migrate_imports.py` are unreferenced after the inference logic was rewritten. Pyright already flags them as unused. Delete.

**8. Regression tests for the bugs we hit.**
Add test cases so the next round of classifier / bundler edits can't silently regress:
- `tests/unit/test_cli_migrate_imports.py`:
  - `Badge`, `Sheet`, `Dialog`, `Table`, `Command` stay in `"bifrost"` (platform > lucide on collision).
  - `Edit`, `AlertTriangle`, `CheckCircle`, `Loader2` move to `"lucide-react"` (alias detection via `as <Name>`).
  - User component with `export function Foo` gets named import, not default.
  - Multi-line `import {\n A,\n B,\n} from "recharts";` is not corrupted by inserting a new import after it.
  - Lowercase names (`cn`, `toast`, `format`) are inferred from usage, not only PascalCase.
  - Names that appear inside `import { ... } from` don't count as references.
- `tests/unit/test_app_bundler.py` (new):
  - `_write_entry` output contains `export default function BundledApp` and does NOT contain `BrowserRouter` or `createRoot(`.
  - `_write_bifrost_package` only emits a platform proxy entry for names present in the input's `imported_names` (after item 5).
- `tests/unit/test_bundled_app_shell_deps.py` or equivalent: esm.sh URL template includes `?external=react,react-dom,...`.

**9. Fix import-map staleness across in-tab app navigation.**
Browser import maps are immutable once installed. If a user loads app A (no deps), then navigates to app B (declares `recharts`), app B's `recharts` import 404s against the stale map. `ensureImportMap` currently just logs a warning, which users won't see.

**Decision: auto-reload on mismatch.** When `ensureImportMap` detects that the new app needs deps not in the installed map, call `location.reload()`. The page comes back up with app B's correct map installed from the start. Cost is a visible flash on cross-app navigation; users rarely chain-hop so this is fine. Three lines of code, bulletproof.

Validate by opening app A without recharts, then navigating to margin-dashboard in the same tab — should reload once and Just Work.

**10. Classifier workflow: diff before `--yes`.**
`migrate_imports.py`'s regex-based identifier scanner can't distinguish a platform name used in expression position from a local binding shadowing it (e.g. `function row({ Badge })`). No apps in `bifrost-workspace` hit this — but when external users run the tool on their own code, one could. Fix: make the CLI workflow diff-first.

- `bifrost migrate-imports` without `--yes` already prints the diff. Keep that.
- **Change `--yes` semantics.** Currently skips confirmation. Keep that, but ALSO require an explicit `--skip-diff` flag to actually suppress the diff output. Default behavior with `--yes` is: print the diff, then apply. User has something to read in their terminal scrollback if something goes sideways.
- Update the CLI's help text and `bifrost-build` skill to say "**always review the diff** — the classifier doesn't do full scope analysis, so if it added an import for a name you declared locally, reject and fix."

This is a workflow fix, not a scope-analysis implementation. Adding a real TS parser to the migration tool isn't worth it; making the user a peer reviewer of the diff is.

**11. Document known limitations in `migrate_imports.py` module docstring.**
A short header block: "This classifier uses regex, not AST. It does NOT track function parameters / destructured bindings / type-level identifiers. A user-declared PascalCase name that shadows a platform export may be incorrectly imported. Always review the diff before applying."

**Verification:** re-run the Chrome matrix on all 11 apps after 3.5. Zero regressions.

### Phase 4 — Delete the old runtime

Only run after Phase 3.5 has landed and every app is confirmed working via the per-app Chrome matrix (zero console errors, all routes render). Then:

**Commit discipline:** each bullet below is its own commit. After each commit, re-run the Chrome matrix. Don't batch deletions — Phase 3's "delete all at once" style is what makes cascading breaks hard to bisect.

**Commit 1 — flip the default.**
- Delete the `?bundled=1` / `localStorage.bifrost.bundled` flag from `client/src/components/jsx-app/JsxAppShell.tsx`. The legacy branch still renders; only the opt-in goes away. Bundled becomes default for all app loads.
- Re-run Chrome matrix. Must be clean.

**Commit 2 — rename BundledAppShell → JsxAppShell.**
- `git mv BundledAppShell.tsx JsxAppShell.tsx` (after deleting the old `JsxAppShell.tsx`).
- Update imports across the client.
- This is the structural rename before content deletes.

**Commit 3 — drop legacy server plumbing (`app_compiler`, `/render`).**
- Delete `api/src/services/app_compiler/` (package, node_modules, compile.js, tailwind.js).
- Delete `/api/applications/{id}/render` endpoint + `AppRenderResponse` types from `app_code_files.py`.
- Remove `AppCompilerService` references from `applications.py`, `mcp_server/tools/apps.py`, `app_storage.py`.
- Clean up `docker-compose.dev.yml` / `Dockerfile.dev` — drop the `app_compiler` node_modules volume and install step.
- Re-run Chrome matrix + backend tests.

**Commit 4 — drop legacy client plumbing.**
- Delete `client/src/lib/app-code-runtime.ts` (`wrapAsComponent`, `new Function()`, `$` registry).
- Delete `client/src/components/jsx-app/JsxPageRenderer.tsx`, `client/src/lib/app-code-resolver.ts`, `client/src/lib/app-code-router.ts`.
- Delete wrapped `Link`/`NavLink`/`Navigate`/`useNavigate`/`navigate`/`transformPath` from `client/src/lib/app-code-platform/navigation.tsx`. Platform scope exports raw React Router primitives.
- Delete `setAppContext` call from the shell (was defensive for wrapped Link).
- Re-run Chrome matrix.

**Commit 5 — swap `globalThis.__bifrost_platform` → real ESM module.**
- Wire up `client/src/lib/bifrost-runtime.ts` (cleaned up in Phase 3.5 item 6) as the import-map entry for `"bifrost"`.
- `BundledAppShell` stops setting `globalThis.__bifrost_platform`; the import map points `"bifrost"` at a blob module that re-exports from `bifrost-runtime.ts`.
- Bundler's `_write_bifrost_package` stops emitting platform-scope proxies (Phase 3.5 item 5 already pruned the "emit only used names" path; this step drops the file entirely and treats `"bifrost"` as an external).
- Remove `DEFAULT_EXTERNALS` entries that are now covered by the user's real import map.
- Re-run Chrome matrix.

**Commit 6 — remove the deprecation warning.**
- The bundler's `[Bifrost] Deprecated imports detected` console.warn was there to nudge external workspaces. Post-Phase-4 the "old pattern" doesn't exist anymore (no synthesized `node_modules/bifrost/` to translate it).

**Goal state after Phase 4:** `JsxAppShell` is ~150 lines (shell + state + banner + cleanup, no legacy branch). No Babel. No scope injection. No `new Function()`. No hand-maintained export lists proxy-dispatched via globalThis. No wrapped navigation primitives. No `app_compiler` package. No `/render` endpoint.

## Risks we live with

Things this plan deliberately does NOT fix. Documented so future-me doesn't re-discover them as bugs.

- **Regex-based default-export detection.** `_has_default_export` in both `migrate_imports.py` and `app_bundler/__init__.py` looks for `^\s*export\s+default\b`. Misses edge forms like `module.exports = …` (not relevant for TSX but the principle holds). Why live with it: if the regex guesses wrong, esbuild fails **loudly at build time** with a clear error — the user sees the bad import, fixes the file, done. Loud failures are cheap to fix; not worth pulling in a full TS parser for this.

- **Dual-platform-scope paths during transition.** Between Phase 3 (current) and Phase 4 commit 5, two paths feed platform names to bundled apps: the `globalThis.__bifrost_platform` bridge AND (after 3.5) the ESM `bifrost-runtime.ts` module. Why live with it: it's sequencing, not architecture. Phase 4 commit 5 collapses them. The only discipline required is "do not add new platform exports to the bridge" — a comment in the code + this plan is enough.

- **The synthesized `node_modules/bifrost/index.js` is still a code-gen glue file.** It re-exports user components, lucide icons, router primitives, and platform proxies so the legacy `import { X } from "bifrost"` pattern resolves. Why live with it: same reason as dual-platform-scope — transitional. Phase 3.5 item 5 shrinks it; Phase 4 commit 5 deletes it entirely.

### Phase 5 — Aggressive platform reshape (new)

**Premise:** Phase 3 shipped `bifrost migrate-imports`, Phase 3.5 consolidated the platform-names list into one source of truth, and Phase 4 deleted the legacy runtime. That combination is the first-class lever for breaking platform changes — we don't need deprecation cycles or per-app migration toil. Anything awkward about the current `"bifrost"` surface can be rewritten across every app in one commit.

**Prerequisite:** Phase 3.5 item 1 (one `_PLATFORM_EXPORT_NAMES`) must be done — reshape = splitting one list, not rewriting two.

**Goal:** split the `"bifrost"` grab-bag into explicit, tree-shakeable packages and rename platform APIs to match their current best intent. Ship each reshape as one commit on main.

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
