# Bundler-driven publish + deterministic live serving

Date: 2026-04-18
Affects: anyone running Bifrost with published apps

## What changed

Two changes to the app publish / live pipeline:

1. **Publish now drives the esbuild bundler explicitly.** Previously `POST /api/applications/{id}/publish` invoked the legacy per-file compiler to populate `preview/`, then promoted `preview/` → `live/`. The per-file compiler is no longer the runtime; publish now calls `BundlerService.build(mode='preview')` to produce a fresh bundle that matches the source being published, then promotes it to live. A failed bundle fails the publish — we will not promote a partial or stale preview.

2. **Live serving is publish-only; no lazy rebuild from draft.** `GET /api/applications/{id}/bundle-manifest?mode=live` used to fall back to `BundlerService.build(mode='live')` from current draft source when `live/manifest.json` was absent. It no longer does. On a missing live manifest it returns `409 Conflict`:

   ```
   App has not been published under the current runtime. Publish the app to generate a live bundle.
   ```

   Live = what publish produced. Period. (`mode=preview` still builds on demand for new apps.)

## Before upgrading

If you have draft changes to any app that you haven't yet published, **either publish them or revert them before upgrading**. After the upgrade, the first live view of any app whose last publish was under the legacy per-file compiler will 409 until the owner re-publishes — there is no `live/manifest.json` in that slot.

## After upgrading

- First live view of a legacy-published app: end users see the 409 error (clear, actionable) instead of silently getting draft code promoted under them. Owner re-publishes and the app is back to normal.
- Newly-published apps work without any migration — publish writes a bundle manifest and live serves it directly.

## Not in this release

`bifrost migrate-imports` is still the separate step for rewriting app source from the legacy `import { X } from "bifrost"` grab-bag to standard imports (`lucide-react`, `react-router-dom`, relative user-component paths). This release note is about the publish/live story, not imports. Run `bifrost migrate-imports` on your workspace separately if you haven't already.
