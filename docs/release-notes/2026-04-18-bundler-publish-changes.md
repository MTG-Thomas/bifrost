# Bundler-driven publish

Date: 2026-04-18
Affects: anyone running Bifrost with published apps

## What changed

Publish now drives the esbuild bundler explicitly. Previously `POST /api/applications/{id}/publish` invoked the legacy per-file compiler to populate `preview/`, then promoted `preview/` → `live/`. The per-file compiler is no longer the runtime; publish now calls `BundlerService.build(mode='preview')` to produce a fresh bundle that matches the source being published, then promotes it to live. A failed bundle fails the publish — we will not promote a partial or stale preview.

## Before upgrading

If you have draft changes to any app that you haven't yet published, **either publish them or revert them before upgrading**. On first live view after upgrading, any app whose last publish was under the legacy per-file compiler will be rebundled automatically from current source. If your current source differs from what was last published (because of unpublished drafts), those drafts will become live the moment any viewer opens the app.

## After upgrading

- First live view of a legacy-published app: the server sees no `live/manifest.json`, runs the bundler against current source, writes the live manifest, and serves it. End users see the app load normally. No admin action required.
- Newly-published apps work the same — publish writes a bundle manifest and live serves it directly.
- Apps using the old `import { X } from "bifrost"` grab-bag keep working. The bundler synthesizes a `node_modules/bifrost/` shim that re-exports platform components, Lucide icons, and React Router primitives. You'll see a `console.warn` in DevTools listing deprecated imports; runtime behavior is unchanged.

## Recommended follow-up

Run `bifrost migrate-imports` on your workspace to rewrite app source from the legacy grab-bag to standard imports (`lucide-react`, `react-router-dom`, relative user-component paths). Not required now — the shim handles it — but required before a future release that deletes the shim. The command is diff-first by default; review and apply.
