# Rapid Workspace Promotion

Status: immutable artifact and operator channels under implementation. The
existing exact-path reviewed release remains the production fallback.

## Goal

Make the common Workspace loop feel like local development without allowing a
laptop or mutable cache to become production authority:

1. Author a workflow and its private helpers locally.
2. Record local-run evidence and an immutable local-only draft.
3. Execute and iterate locally with `bifrost run`; local uploads remain inert.
4. Merge the proven bytes through protected Git.
5. Preview the exact current protected commit/tree and complete effective snapshot.
6. Optionally execute that reviewed artifact as a bounded canary with no
   registration or trigger/public surface.
7. Atomically activate by candidate ID, then prove one release ID across
   runtime, source reads, registration, and history.

Local evidence is useful but never activatable. Production activation never
accepts source bytes; it accepts an immutable reviewed candidate plus base CAS
and idempotency guards.

## Ownership

- The CLI stores local-only drafts, displays complete graph evidence, and reads
  reviewed source directly from exact Git objects for production preview.
- The API verifies protected source, reconstructs the complete effective
  snapshot, computes forward/reverse impact, classifies effects, binds
  registry/access state, and creates the immutable candidate.
- The Workspace repository owns authored source, effect declarations, focused
  tests, and protected-main provenance before Live activation.
- Infrastructure owns feature enablement, migration-before-rollout, encrypted
  artifact storage policy, and GitHub credentials. It does not classify or
  activate a candidate.

Solutions use the same artifact, release, validation, activation, rollback,
and status state machine. They remain a separate target kind and namespace.

## Interactive file impact checks

The lower-level `bifrost files` surface has a guarded loop for quick iteration
on authored Workspace Python without pretending that an edit is a release:

```bash
# Inspect the durable file as it exists now.
bifrost files graph features/vendor/workflows/report.py --direction both

# Inspect proposed local bytes without writing them.
bifrost files graph features/vendor/workflows/report.py \
  --from-file features/vendor/workflows/report.py

# Preview, then write only the exact graph candidate the server re-verifies.
bifrost files write features/vendor/workflows/report.py \
  --from-file features/vendor/workflows/report.py --check-impact
```

The graph combines repo-local Python imports with literal workflow registry
references. It reports the selected file's transitive forward dependencies,
transitive reverse dependents, exact hashes, edge types, and the paths that need
validation. Traversal is never truncated or blocked merely because a shared
module has many consumers. The response states how many paths and edges were
fully traversed; a large graph is an informational diagnostic, not a safety
failure.

Checked-write diagnostics are change-aware. Syntax errors and uncertainty in
the changed file or its forward closure block. Newly introduced uncertainty,
an ambiguous registry edge to the changed workflow, star imports across a
removed module surface, and removal of a symbol that a reverse dependent
imports also block. An unrelated issue that already existed in a connected
consumer remains visible as a warning, but does not falsely claim that the
proposed edit created it. Graph inspection without proposed bytes always
returns the evidence even when the connected source already contains warnings.

`--check-impact` performs two server calls. The first returns a content-addressed
candidate over the proposed bytes and the complete durable Python inventory.
The write call recomputes that candidate while holding the global Python-source
writer barrier. If any source or graph edge changed in between, it returns a
conflict and writes nothing. Agents normally do not handle the candidate ID;
the CLI carries it between the two calls.

`traversal_complete` means every statically known transitive edge was returned
without a fan-out cutoff. It does not mean that Python can prove arbitrary
dynamic behavior. Those limitations remain explicit diagnostics, and only
diagnostics relevant to the proposed change make `ready_to_write` false.

This is a development safety primitive, not promotion:

- it does not register, activate, execute, test, commit, or push anything;
- it does not make a direct production edit an accepted release mechanism;
- reverse dependents are surfaced for targeted validation, not rewritten or
  executed automatically;
- the checked-write lane is currently limited to platform-admin writes of
  instance Workspace Python in durable cloud storage;
- Solution file storage remains policy-scoped application data. Solution
  workflow source continues through the Solution bundle/artifact model and
  will consume the same graph primitive when checked Solution source editing is
  introduced.

SDK callers can perform the same compare-and-write sequence explicitly:

```python
from bifrost import files

impact = await files.impact(
    "features/vendor/workflows/report.py",
    content=proposed_source,
)
if impact["ready_to_write"]:
    await files.write(
        "features/vendor/workflows/report.py",
        proposed_source,
        impact_candidate_id=impact["candidate_id"],
    )
```

## Operator-channel contract

The v1 operator loop is:

```bash
bifrost run <path> -w <function> --promotion-evidence .promotion-run.json
bifrost promote draft <path> -w <function> \
  --run-evidence .promotion-run.json
# branch, PR, CI, protected main
bifrost promote preview <path> -w <function> \
  --run-evidence .promotion-run.json
bifrost promote canary <reviewed-artifact-id>
bifrost promote prepare <reviewed-artifact-id> \
  --candidate-id <candidate-id>
bifrost promote activate <prepared-release-row-id> \
  --artifact-id <reviewed-artifact-id> \
  --candidate-id <candidate-id> \
  --workspace-release-id <workspace-release-id> \
  --expected-base-release-id <current-live-release-id> \
  --expected-active-release-id <current-live-release-id> \
  --prepared-evidence-id <prepared-evidence-id> \
  --canary-execution-id <successful-canary-execution-id>
bifrost promote status --live
```

For the first activation, where no immutable release owns Live yet, replace
`--expected-active-release-id` with `--expect-no-active-release` and use the
`repo-v1:...` base ID emitted by preview. The two forms are mutually exclusive:
the operator must explicitly assert which CAS state is expected.

`prepare` follows the durable platform job to completion and prints the exact
release-row, release, artifact, candidate, and prepared-evidence IDs returned by
the server. `activate` requires those identities plus the exact successful
canary execution; it does not infer a candidate or canary from mutable "latest"
state. `status <release-row-id>` reads one release, while `status --live` reads
the global Live pointer. JSON mode returns the exact server response, including
the complete platform-job receipt for `prepare`.

Draft compilation:

- reads tracked and untracked, non-ignored Python files twice;
- rejects traversal, symlinks, case/Unicode collisions, ambiguous module names,
  size limits, and a changing snapshot;
- stores an immutable private local artifact with exact bytes;
- binds local-run evidence to the selected entry and forward-closure ID rather
  than the entire repository, so unrelated later merges do not invalidate it;
- computes complete forward/reverse graph evidence; and
- marks the artifact `authority=local_only`, `activatable=false`.

An optional server draft upload stores the same local-only bundle for 24 hours,
but it is inert: it cannot prepare, canary, register, schedule, subscribe,
expose an endpoint/API key, or move Live state. Use `bifrost run` for local
iteration, then commit and build a reviewed preview. Only an exact reviewed
artifact may enter the bounded server canary lane.

Reviewed preview first refreshes authoritative `origin/main`, reads Git blobs
directly from that exact protected source, and calls
`POST /api/workspace-promotions/preview` with bundle schema v2:

- exact commit SHA and tree SHA;
- full snapshot and selected forward-closure path/hash identities;
- optional matching local/canary evidence;
- optional expected-base and supersession CAS; and
- no client-supplied production source bytes. The server fetches the reviewed
  blobs and validates them against protected Git.

The server:

- derives organization and authoritative active base from authenticated context;
- verifies protected commit/tree and fetches exact blobs;
- reconstructs the complete effective snapshot using content-addressed reuse;
- rejects missing, mixed, extra, ambiguous, or dynamically unprovable closure;
- scans the effective snapshot for all reverse importers;
- blocks a changed helper when a live consumer is outside the candidate;
- binds workflow create/reactivate/preserve intent and existing access/trigger
  state fingerprints, including registration-only candidates;
- computes declared, static, and combined effects plus enforced bounds;
- scans high-confidence secret patterns before storing source;
- writes source and manifest to create-only, content-addressed object keys;
- stores immutable artifact metadata separately from the atomic Live pointer;
- commits a strict, source-free audit event with candidate, snapshot, entry,
  risk, policy, and closure hashes;
- returns content, closure, candidate, release, base/effective manifest,
  registration, source, lifecycle, and exact per-path hash evidence.

Activation sends no source. Status distinguishes candidate validation,
activation, runtime coherence, and signed-history projection. In particular,
`Live / runtime coherent / history pending` is not an activation failure.

## Candidate identity

The model separates stable content from release evidence:

- `closure_id` binds entry path/function plus exact forward path/hash members;
- `content_id` binds the deployable behavior independent of evidence;
- `effective_manifest_id` binds every path/hash in the resulting Workspace;
- `candidate_id` also binds organization, protected source, active base,
  registration fingerprints, validation/effect evidence, and policy version;
- `release_id` identifies the immutable activation target;
- protected source always includes full commit and tree SHA;
- declared, statically inferred, and computed effects;
- enforced resource bounds;
- create/reactivate/preserve registration intent;
- existing workflow identity, organization, active/type/access/role/API/endpoint
  state;
- local-run evidence;
- CLI, SDK, and contract versions;
- policy version.

Presentation text, timestamps, and diagnostic wording do not affect content
identity. Activation accepts only candidate/base/idempotency evidence; it does
not accept replacement source or policy claims.

## Dependency and reverse-dependency invariant

Forward closure answers which helpers the selected workflow requires. Reverse
closure answers which already-live code can be affected by a helper change.
Both are required.

For the first R0 activation policy:

- a leaf workflow edit may qualify;
- a new private helper may qualify when no live source imports it outside the
  candidate;
- an unchanged live dependency may be included and verified;
- any changed helper with a live importer outside the candidate is R2 and must
  use reviewed promotion;
- unresolved, ambiguous, dynamic, or failed graph analysis is never R0.

This is the direct regression guard for the AutoTask incident where an
unrelated release rolled `_ticket_lifecycle.py` back to an older parser. The
test fixture changes the shared parser while leaving `ticket_webhook.py`
outside the candidate and requires a fail-closed result.

The Cove restoration incident is the corresponding multi-root case: a commit
claiming one reviewed source revision rewrote 96 unrelated paths and rolled
`helpers/autotask_ticket_migration.py` backward. A rapid candidate therefore
binds one coherent snapshot, contains only the selected workflow's exact
closure, and may not source a closure member from a different revision. Broad
multi-root convergence remains a reviewed, ordered operation—not an implicit
side effect of promoting one workflow.

## Effect policy

R0 starts narrowly: manually invoked `@workflow` only, with explicit effects
and platform-recognized positive integer bounds. Tools and data providers are
excluded because other entities may invoke them automatically.

Direct networking, subprocess execution, dynamic code/imports, writes, secrets,
schedules, webhooks, public/API-key endpoints, access changes, global entities,
and unknown behavior are R2. A declaration cannot weaken a static or observed
lower bound. Runtime enforcement and server-issued run evidence are required
before activation is introduced; decorator metadata alone is not enforcement.

## Activation rollout prerequisites

The activation route must remain feature-disabled in production until tests
prove all of the following:

- candidate-backed production loader smoke has no fallback to live or local
  files and blocks top-level effects;
- server-issued run evidence binds the same candidate and enforced budgets;
- relevant reverse-graph and registry state are rechecked under activation CAS;
- source/registry/current-artifact pointer switch is atomic and read back;
- failure can restore the exact previous artifact without clobbering a newer
  release;
- registration-only create/reactivate works without manufacturing a source
  change;
- preserve-only exact live state returns `already_live` with no candidate/job;
- duplicate or competing activation is idempotent/fail-closed;
- activation performs no Git or GitHub operation;
- signed-history projection performs no activation, workflow execution,
  webhook replay, or vendor mutation;
- Git ref delay or later branch advancement affects only history projection,
  never Live activation state;
- Meraki array-query, AutoTask parser, Cove shared-helper, and DMA
  registration-only incident fixtures all pass against the same release model.

## Durable state

`workspace_promotion_artifacts` is immutable candidate metadata pointing to
create-only source and manifest objects. The active Workspace release pointer
is a separate CAS-updated record, and signed history is a projection of that
release. Keeping these states separate prevents history synchronization
failures from being misreported as source activation failures.
