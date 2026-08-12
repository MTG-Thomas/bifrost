# Transactional workspace release and recovery

Transactional `_repo` activation and Git closure run as the durable
`workspace.repo-closure` platform job. The job is the platform-enforced global
writer from path CAS through activation, authoritative object-store snapshot,
Git commit/push, remote readback, and generation-fenced dirty reconciliation.

## Client contract

`POST /api/workspace-repo-changesets/{id}/activate` and
`POST /api/workspace-repo-changesets/{id}/retry-git-closure` return `202` with
`PlatformJobAccepted` and a `Location: /api/platform-jobs/{job_id}` header.
Exact-path release clients must poll that location until the job reaches a
terminal state. A successful job result contains the final changeset under
`result.changeset`.

Do not gate a release on `/api/github/status`: that endpoint describes only the
generated Git checkout. Use `/api/github/repo-status` and require all of:

- no active writer;
- `authoritative_converged=true`;
- `mismatch_count=0`;
- the expected remote branch and SHA;
- an explained dirty state (normally clean after a verified pushed closure).

The status comparison hashes text after CRLF-to-LF normalization and hashes
binary bytes exactly, matching CLI sync semantics.

## Recovery

`GET /api/workspace-repo-changesets/operational-status` lists the current dirty
generation, active writer and lease phase, active or abandoned changesets,
retryable Git closures, the ordered closure ledger, and authoritative-versus-
remote mismatch paths.

- Retry an `activated` Git failure or `committed_unpushed` push/readback failure
  by exact ID through `retry-git-closure`. A durable `pending` closure left by
  runner loss is also listed and retryable. Retry never replays source writes
  and refuses to close if the authoritative snapshot changed.
- Abort `open`, `staged`, `validated`, or `conflicted` records by exact ID.
  Backup restoration is path-CAS checked and is refused if any current byte
  state is neither the recorded before image nor the activation's after image,
  so recovery cannot erase a newer authoritative write.
- An abandoned `activating` record carries a durable source backup once source
  mutation begins. Exact-ID abort restores that backup through the normal
  storage facade. An earlier `activation_snapshot` record has not changed source
  and needs no backup; other missing-backup states are refused. Abort is also
  refused while another durable writer owns the workspace.

Never clear `bifrost:repo_dirty` manually based on generated-checkout status.
Transactional closure uses an atomic generation compare-and-delete; a newer
editor write remains dirty. Timestamp-only markers created by older releases
remain visible but cannot be cleared by this transactional reconciliation path.
