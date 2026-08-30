# Serialized Merge Queue

`main` keeps strict branch freshness and the repository-owned queue serializes
eligible pull requests under the `merge-queued` label. This provides an exact
current-base integration proof without GitHub's organization-only native merge
queue.

## Invariants

- The oldest labeled pull request targeting `main` advances first.
- A same-repository head that does not contain current `main` is merged with
  current `main` and updated using an SSH deploy key plus `--force-with-lease`.
- A fork head may merge only while it already contains current `main`; the queue
  cannot write to fork branches, so it removes stale fork heads and fails closed.
- Every required check must pass on the exact refreshed head. The policy file
  records both the context and producing GitHub App where the Checks API exposes
  it. CodeRabbit uses its legacy commit status context.
- The merge API is bound to the validated head SHA and uses squash merge.
- After merge, the queue verifies that `main` is the returned merge SHA, its sole
  parent is the validated base, and its tree exactly matches the validated head.
- Strict required-status freshness remains enabled, protecting direct merges
  outside the queue.

Required contexts are `Lint & Type Check`, `Unit Tests`, `E2E Tests`, both
CodeQL `Analyze` language jobs, `CodeRabbit`, and `SonarCloud`. CI cancels superseded
pull-request heads but never cancels authoritative push runs. The queue has a
single non-cancelling concurrency group.

## Credentials and recovery

Create a repository deploy key with write access and store only its private key
as the Actions secret `SERIALIZED_MERGE_QUEUE_SSH_KEY`. The workflow pins
GitHub's Ed25519 host key, creates a mode-0700 temporary directory under umask
077, writes the key as mode 0600, and deletes the directory when the step exits.
The key is used only to update a queued same-repository branch. GitHub's scoped
workflow token performs API reads, labels, comments, and the protected merge.
Because merges made with that token do not emit a downstream push workflow, the
queue verifies the exact merge first and then dispatches `CI` on that exact
`main` ref with `queue_post_merge=true`. That lane publishes images, signatures,
and provenance without rerunning the already validated unit or E2E suites.

CI and CodeQL completions wake the queue. Label changes wake it immediately, and
the five-minute schedule is the recovery path for external checks and interrupted
runs. Main-branch workflow completions are ignored to avoid a redundant wakeup.

If the queue cannot refresh a branch, first verify the deploy key and secret are
the matching pair and that the key still has repository write access. Never
weaken strict branch protection to clear the queue. Remove `merge-queued` to
pause a pull request; use `merge-queue-blocked` to record a failed candidate.
