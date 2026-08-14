# GitHub Merge Queue

`main` uses GitHub's native merge queue to validate the exact candidate commit
before it lands. Pull-request runs remain change-aware for fast feedback;
`merge_group` runs deliberately force the comprehensive test plan.

## Required checks

The queue-safe required checks are:

- `Lint & Type Check`
- `Unit Tests`
- `E2E Tests`
- `Analyze (python)`
- `Analyze (javascript-typescript)`

The CI and CodeQL workflows must retain their `merge_group` triggers and stable
job names. `api/scripts/check_mtg_ci_boundaries.py` guards this contract.

CodeRabbit and SonarCloud remain visible on pull requests, but are not required
queue checks. They are GitHub App checks whose availability on the synthetic
`merge_group` ref is not controlled by this repository; requiring them can
leave the queue waiting for a status that never starts.

## Queue policy

- merge method: squash
- simultaneous merge-group builds: 1
- minimum entries to merge: 1
- maximum entries to build: 5
- maximum entries to merge: 5
- queue wait: 5 minutes
- status-check timeout: 60 minutes

The active `main` repository ruleset still requires pull requests, signed
commits, resolved conversations, and passing required checks, and it blocks
force pushes and deletions. Its Bypass list is empty, so repository
administrators and other privileged actors remain subject to the rules. Strict
branch freshness is disabled only while the merge queue is enabled: the
synthetic queue commit is the current-base proof.

## Verification and rollback

After changing queue settings, enqueue a small PR and verify that CI and both
CodeQL jobs run on a `gh-readonly-queue/main/...` ref. Confirm the resulting
`main` push publishes the dev/test images without repeating unit or E2E tests.

If any required queue check is absent, re-enable strict branch freshness and
disable the merge queue before investigating. The workflow triggers are safe to
leave in place while the queue is disabled.
