# Post-GitHub Integration Workflow

## Summary

Upstream plans to deprecate Bifrost's built-in Git/GitHub integration.
This fork should stop treating `/api/github/*` and in-app repository sync as the
primary development workflow.

The replacement path already exists:

- local git for source control
- direct `bifrost` CLI push/sync/watch for workspace content
- explicit image rebuild/deploy for platform code changes

## Immediate State

As of 2026-03-27:

- fork branch work has been merged back to `main`
- dev GitHub config was switched from `feat/autotask-cove-integrations` back to
  `main`
- the GitHub integration should be considered transitional only

## New Default Workflow

### 1. Local Git Stays Canonical

Use local git and GitHub for:

- branching
- rebasing
- merging
- code review
- upstream sync

Do not rely on Bifrost to own repo state or branch state.

### 2. Use Direct Platform Sync for Userland

For content that lives in workspace storage and can be imported into platform
state without rebuilding the running image:

- `features/`
- `modules/`
- `shared/`
- `helpers/`
- `workflows/`
- `apps/`
- current fork-local `.bifrost/` manifest files

Use:

- `bifrost push [path]`
- `bifrost sync [path]`
- `bifrost watch [path]`

These already write via `/api/files/write`, `/api/files/delete`, and run
manifest import via `/api/files/manifest/import`.

### 3. Treat Platform Code Separately

Changes under these paths require an image rebuild or deployment rollout:

- `api/`
- `client/`
- `docker-compose*.yml`
- k8s/deployment/build assets

For the dev environment, use the SSH + k3s rollout path on `10.1.23.114`.

## Practical Split

### Userland change only

Examples:

- new integration module
- workflow/data provider changes
- app page/component changes that are loaded from workspace
- manifest/content registration updates

Preferred flow:

1. Commit locally on `main` or a short-lived feature branch
2. Push to GitHub for collaboration/history
3. Run `bifrost push` or `bifrost sync`
4. Validate on dev

### Platform/runtime change

Examples:

- backend router/service changes
- auth changes
- scheduler changes
- React shell/platform client changes
- Dockerfile/build changes

Preferred flow:

1. Commit locally
2. Push to GitHub
3. Rebuild/redeploy the dev image explicitly
4. Validate against the updated running image

## Operational Notes

- `bifrost watch` is still useful, but only for intentional workspace-content
  iteration
- direct platform sync is a better fit for this fork than continuing to depend
  on a feature upstream intends to remove
- this workflow does not solve the fork's `.bifrost/` repo-model drift by
  itself; that remains tracked in
  `docs/plans/2026-03-26-upstream-convergence-plan.md`

## Next Steps

1. Stop using the in-app GitHub integration for normal dev workflows.
2. Standardize on `bifrost push/sync/watch` for userland changes.
3. Keep dev image rebuilds explicit for platform changes.
4. Continue the separate convergence work to reduce the fork's `.bifrost/`
   drift from upstream.
