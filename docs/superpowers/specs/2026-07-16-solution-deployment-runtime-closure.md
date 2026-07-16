# SolutionDeployment immutable runtime closure

**Date:** 2026-07-16  
**Status:** accepted architecture; implementation is intentionally sliced  
**Decision owners:** MTG Bifrost maintainers

## Decision

A `Solution` remains the stable installed product identity. A
`SolutionDeployment` is an immutable candidate or activated runtime revision.
`Solution.active_deployment_id` selects the deployment used when a new execution
is created.

The deployment's compiled manifest and resolution map are the execution-time
authority. Mutable ORM rows remain useful administrative and current-state
projections, but an execution pinned to a deployment must not resolve critical
behavior from rows that a later deployment overwrites.

This is the primary code-first deployment model. The generic
`WorkspaceChangeset` API is retained as an explicitly named `_repo`
compatibility path for loose workspace content; it is not a second Solution
deployment system.

## Why

Solutions already own bundle construction, dependency closure, scoped entity
reconciliation, app compilation, source artifacts, write locks, export/install,
and Git-connected workflows. Building those behaviors again behind a generic
workspace transaction API would create two competing deployment models.

Current Solution source and source ZIP storage are keyed only by `solution_id`,
and execution context carries only that install identity. A redeploy can
therefore replace code and definitions used by queued or running executions.
Immutable files alone do not solve this: mutable workflow, agent, form, event,
application, and dependency rows can still change underneath an old execution.

## Invariants

1. Every Solution-owned execution records a non-null
   `solution_deployment_id` before it is queued.
2. The deployment records an immutable bundle hash, compiled manifest hash,
   source artifact key, runtime storage prefix, and exact dependency deployment
   IDs.
3. Execution-critical resolution uses only the pinned deployment's compiled
   manifest, resolution map, and revision-addressed artifacts.
4. Active ORM rows may project the active deployment for admin APIs, search,
   configuration, and current-state UI, but are not authoritative for a pinned
   execution.
5. An execution never re-reads `Solution.active_deployment_id` after creation.
6. Same-Solution workflow and agent calls inherit the caller's deployment by
   default. Cross-Solution calls use a dependency deployment explicitly pinned
   in the caller's manifest.
7. Validated or activated deployment artifacts and manifests are immutable.
8. Activation compares `base_deployment_id` with the current active pointer
   while holding the existing per-Solution write lock.
9. Git activation, Git commit creation, and Git push are distinct observable
   facts. Active-but-unpushed is not reported as failure or ambiguous success.
10. Failure between durable systems terminates in a named state, including
    `recovery_required` when automatic compensation cannot restore a coherent
    pointer/artifact/projection state.

## Data model

### Solution changes

Add a nullable `active_deployment_id` foreign key. It is null for existing
installs until their current state is captured as the initial deployment.

### SolutionDeployment

Minimum durable fields:

```text
id UUID primary key
organization_id UUID null (must exactly match Solution scope; null means global)
solution_id UUID not null
parent_deployment_id UUID null
base_deployment_id UUID null
state deployment_state not null
declared_version string null
bundle_hash sha256 not null
compiled_manifest JSONB not null
compiled_manifest_hash sha256 not null
resolution_map JSONB not null
resolution_map_hash sha256 not null
source_artifact_key string not null
runtime_storage_prefix string not null
git_repository string null
git_ref string null
git_commit_sha string null
git_push_state string null
created_by UUID not null
codex_worker_id string null
validation_result JSONB null
failure_detail JSONB null
created_at timestamp not null
validated_at timestamp null
activated_at timestamp null
superseded_at timestamp null
```

Initial states:

```text
draft -> building -> validated -> ready -> activating -> active
                                              |           |
                                              |           -> superseded
                                              -> conflicted
                                              -> failed
                                              -> recovery_required
draft/validated/ready -> aborted
active -> committed_unpushed (Git-connected provenance state, still active)
```

State transitions are checked centrally. Callers cannot set deployment state
directly.

### Deployment dependencies

Use a relational edge as well as the compiled manifest representation:

```text
solution_deployment_dependencies
  deployment_id
  dependency_solution_id
  dependency_deployment_id
  declared_constraint
  resolved_bundle_hash
```

The exact `dependency_deployment_id` is immutable after validation. The
declared constraint explains selection; it is never re-evaluated during an
execution. Composite integrity requires the selected deployment to belong to
`dependency_solution_id`; an organization-scoped deployment may depend on the
same organization or a global deployment, while a global deployment may depend
only on global deployments.

### Execution changes

Persist at execution creation:

```text
solution_id
solution_deployment_id
bundle_hash
compiled_manifest_hash
workflow_source_hash
git_commit_sha
```

Queue payloads carry the same identity and hashes. Consumers reject missing or
mismatched deployment evidence for Solution-owned workflows rather than falling
back to the active install.

## Compiled deployment manifest

The compiled manifest is a canonical, versioned JSON document produced by the
existing complete Solution preflight. Canonical serialization is hashed and
stored immutably.

It contains:

```json
{
  "schema_version": 1,
  "solution_id": "...",
  "deployment_id": "...",
  "bundle_hash": "sha256:...",
  "source": {
    "artifact_key": "_solution_artifacts/<solution>/<deployment>/source.zip",
    "runtime_prefix": "_solutions/<solution>/<deployment>/"
  },
  "workflows": {},
  "agents": {},
  "forms": {},
  "events": {},
  "applications": {},
  "tables": {},
  "file_locations": {},
  "connections": {},
  "config_requirements": {},
  "dependencies": {},
  "git": {}
}
```

Each execution-critical entry includes its portable reference, deployment-local
resolved identity, source path/hash, relevant immutable definition, and required
dependency references. Secrets and mutable runtime values are not embedded.
Connection and configuration entries contain references and requirements only;
runtime secret/value lookup remains scoped and authorized by the normal
services.

## Resolution map

The resolution map is the narrow seam between execution and deployment data.
It supports these operations:

```text
resolve_workflow(deployment_id, portable_ref) -> workflow runtime definition
resolve_agent(deployment_id, portable_ref) -> agent runtime definition
resolve_form(deployment_id, portable_ref) -> form runtime definition
resolve_event(deployment_id, portable_ref) -> event runtime definition
resolve_application(deployment_id, portable_ref) -> app runtime definition
resolve_dependency(deployment_id, solution_ref) -> dependency deployment ID
resolve_source(deployment_id, source_ref) -> immutable object key + hash
```

The production adapter reads the immutable compiled manifest. Tests use an
in-memory adapter through the same interface. Execution callers do not know
about object-store key construction, active ORM projections, or dependency
selection.

The manifest contains the canonical resolution-map hash. Runtime loading
verifies both hashes and the equality of duplicated entity, dependency, and
source evidence before resolving an execution.

Active ORM rows continue to serve administrative APIs. Each projected
Solution-owned row records `projected_from_deployment_id` so drift can be
detected and repaired, but projection drift does not change pinned execution
behavior.

## Revision-addressed storage

```text
_solution_artifacts/{solution_id}/{deployment_id}/source.zip
_solution_manifests/{solution_id}/{deployment_id}/manifest.json
_solutions/{solution_id}/{deployment_id}/...
_apps/{app_id}/{deployment_id}/...
```

Draft uploads use a staging prefix. Validation finalizes content-addressed or
deployment-addressed objects. Final keys are create-only; attempting to replace
an existing finalized object is an integrity error.

Retention is independent of activation. Superseded deployments remain readable
while referenced by executions or retention policy. Garbage collection must
prove no execution, deployment dependency, rollback target, or audit retention
rule references the deployment.

## Draft and activation protocol

A Solution changeset is a draft `SolutionDeployment`:

```text
begin -> stage bundle/files -> diff -> validate -> activate | abort
```

Activation:

1. Acquire the existing per-Solution renewable write lock.
2. Lock the Solution row and compare `active_deployment_id` with the draft's
   `base_deployment_id`.
3. Return structured `409` conflict evidence when they differ.
4. Reconfirm manifest, artifact, dependency, and validation hashes.
5. Ensure revision-addressed artifacts and compiled manifest are finalized.
6. Reconcile active administrative ORM projections inside the database
   transaction and stamp `projected_from_deployment_id`.
7. Atomically move `Solution.active_deployment_id` and mark the prior deployment
   superseded.
8. Commit the database transaction.
9. Record exact Git provenance; attempt push only when explicitly requested.
10. Emit an auditable terminal result.

Rollback is a new activation operation targeting a prior valid deployment. It
rebuilds administrative projections from that deployment's manifest and moves
the active pointer under the same lock and compare-and-swap rules. It never
mutates the prior deployment.

Object storage and PostgreSQL do not form one transaction. Staged/finalized
artifacts are therefore completed before the pointer transaction. Orphaned
immutable artifacts are safe to collect later. A database failure leaves the
old active pointer unchanged. A projection or pointer state that cannot be
proven coherent is recorded as `recovery_required` with a machine-readable next
action.

## Execution protocol

At execution creation, the service resolves the requested workflow against the
selected deployment and writes all pinning evidence before enqueueing. For the
normal path, the selected deployment is `Solution.active_deployment_id`.
Candidate preview explicitly selects a validated non-active deployment and is
separately authorized/audited.

The worker receives the pinned deployment ID and uses a deployment-aware virtual
import context rooted at the immutable runtime prefix. All same-Solution child
calls inherit it. A child call cannot silently promote to the current active
deployment.

Before running, the worker verifies the compiled manifest and source hashes.
Missing or corrupt immutable artifacts fail the execution with deployment
integrity evidence; they do not fall back to mutable `_repo` or active Solution
state.

## Generic `_repo` compatibility

The existing generic transaction implementation is retained and renamed in API
and documentation as the Workspace `_repo` compatibility path. It may reuse
low-level canonical hashing, diff, compare-and-swap, and conflict contracts, but
must not duplicate Solution bundle compilation, dependency resolution, app
building, entity remapping, deploy jobs, locks, or activation.

```text
SolutionDeployment
  primary packaged code-first deployment and runtime-isolation model

WorkspaceRepoChangeset
  bounded compatibility model for loose global _repo content
```

## Git contract

Git provides durable source history and review provenance, not runtime
isolation. Store the exact repository, resolved ref, commit SHA, bundle hash,
deployment ID, and base deployment ID. Activation may succeed without a push.
The API exposes activation and push state separately, including an explicit
active/committed-but-unpushed outcome.

## Migration and delivery slices

1. Add schema and immutable artifact/manifest writers without changing current
   execution or activation behavior.
2. Compile and store manifests plus exact dependency deployment edges.
3. Pin new executions and switch execution-critical resolution to the manifest
   seam, retaining a guarded legacy path for pre-migration executions.
4. Add CAS pointer activation, projection rebuild, rollback, and recovery state.
5. Rework `cs bifrost deployment` against the stable API.
6. Explicitly expose the generic `_repo` changeset compatibility surface.
7. Migrate one low-risk Solution and prove concurrency and execution continuity.

Each slice must be independently reviewable and must not require a live Bifrost
mutation for verification.

## Required proof

- Two drafts from one base validate independently; only the first activates.
- The second receives deterministic conflict evidence and does not overwrite.
- An execution created before activation continues against the old deployment.
- An execution created after activation uses the new deployment.
- Same-Solution and dependency calls retain exact deployment pins.
- Old source, definitions, and apps remain readable while referenced.
- Rollback moves the pointer and projections without mutating deployment data.
- Failed validation/finalization leaves the prior pointer unchanged.
- Incomplete compensation produces `recovery_required`, never ambiguous success.
- Exact Git provenance and active-but-unpushed state are visible.
- `_repo` compatibility transactions remain isolated from Solution activation.

## Non-goals

- Making codex-swarm authoritative for deployment state.
- Storing Bifrost credentials in codex-swarm.
- Treating local swarm claims as platform locks.
- Versioning mutable runtime business data or secrets inside a deployment.
- Forcing all existing `_repo` content into one Solution.
- Deploying or mutating live Bifrost during this architecture refactor.
