# Workspace source coherence

## Contract

Object storage under `_repo/` is the durable source of truth. Every Python path
also has a content version (`sha256`) and is served to workflow imports through
the Redis module cache. The workspace generation is an execution fence, not a
second source revision.

A cached global workspace module is usable only when all of these are true:

- its cached content hashes to its recorded `sha256`;
- that hash is the byte version written from `_repo/`;
- its cache generation equals the current ready workspace generation; and
- its path is present in the module index stamped with that generation.

Legacy entries without a generation and entries from an older generation are
cache misses. The reader loads exact `_repo/` bytes and repopulates Redis. A
cold read captures the generation before object-storage access and rechecks it
afterward, so a read that overlaps a source write cannot label old bytes as the
new generation.

Python writes run behind `workspace_source_update`. The writer first publishes
an `updating:<token>` barrier, writes durable/index/cache state, publishes the
same token as the ready generation, and then proves the changed paths coherent.
The operation does not report ordinary completion before that proof. A durable
changeset activation whose final runtime proof fails remains `activated` and
returns `failure_detail.phase=runtime_propagation`; callers must not retry the
source mutation.

## Release behavior

`GET /api/workspace-repo-changesets/state` reports runtime mismatches separately
from durable `file_hashes`. Exact-byte `verify` mutations are activatable when
the durable bytes are already correct but Redis content, generation, or index is
not. Validation binds those runtime-repair paths into the immutable candidate;
activation rotates the generation, reconstructs the exact cache entries from
object storage, and records runtime evidence in `activation_evidence`.

This is the supported repair for “durable source current, runtime stale.” It
must not manufacture a source change or a Git history commit.

## Outage recovery boundary

Execution orphan cleanup remains short, idempotent scheduler housekeeping in
`api/src/jobs/schedulers/execution_cleanup.py`. It already uses worker
heartbeats and bounded age thresholds, and should not become a workflow or a
bespoke job table. Running PlatformJobs are already fenced and recovered after
lease expiry by `api/src/jobs/schedulers/platform_jobs.py`.

Queued Git jobs retain operator intent and therefore must not be discarded only
because execution capacity was unavailable. Operators may cancel obsolete jobs
through the shared PlatformJob API. If the product later needs automatic
abandonment, add an explicit expiry/supersession contract to PlatformJob rather
than a workspace-specific cleanup job.
