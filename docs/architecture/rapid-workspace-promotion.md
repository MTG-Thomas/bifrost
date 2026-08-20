# Rapid Workspace Promotion

Status: implemented behind default-off platform feature flags. The existing
exact-path reviewed release remains authoritative for paths that have not yet
entered an immutable Live release.

## Goal

Make the common Workspace loop feel like local development without allowing a
laptop or mutable cache to become production authority:

1. Author a workflow and its private helpers locally.
2. Record local-run evidence and an immutable local-only draft.
3. Execute and iterate locally with `bifrost run`; local uploads remain inert.
4. Merge the proven bytes through protected Git.
5. Preview the exact current protected commit/tree and complete effective snapshot.
6. Prepare the immutable release and receive its exact activation challenge.
7. For R0, execute that exact reviewed artifact as a bounded canary. For R1/R2,
   explicitly acknowledge the exact classified effects without claiming a
   canary or effect-execution sandbox.
8. Atomically activate by candidate ID, then prove one release ID across
   runtime, source reads, registration, and history.

Local evidence is useful but never activatable. Production activation never
accepts source bytes; it accepts an immutable reviewed candidate plus base CAS
and idempotency guards.

## Ownership

- The CLI stores local-only drafts, displays complete graph evidence, and reads
  reviewed source directly from exact Git objects for production preview.
- The API verifies protected source, reconstructs the complete effective
  snapshot, computes forward/reverse impact, classifies effects, binds exact
  registry and exposure state, and creates the immutable candidate.
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

# Preview, then write only an ungoverned legacy path using the exact graph
# candidate the server re-verifies.
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
- once a path is governed by an immutable Live release, reads resolve the
  release object and every legacy write/delete/editor/signed-URL path fails
  closed with a direction to use `bifrost promote`;
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
bifrost promote prepare <reviewed-artifact-id> \
  --candidate-id <candidate-id>
# R0 only
bifrost promote canary <reviewed-artifact-id>
bifrost promote activate <prepared-release-row-id> \
  --artifact-id <reviewed-artifact-id> \
  --candidate-id <candidate-id> \
  --workspace-release-id <workspace-release-id> \
  --expected-base-release-id <current-live-release-id> \
  --expected-active-release-id <current-live-release-id> \
  --prepared-evidence-id <prepared-evidence-id> \
  --canary-execution-id <successful-canary-execution-id>
# R1/R2 instead use the exact class printed by prepare
bifrost promote activate <prepared-release-row-id> \
  <same immutable IDs and base CAS> \
  --acknowledge-risk R2
bifrost promote status --live
```

For the first activation, where no immutable release owns Live yet, replace
`--expected-active-release-id` with `--expect-no-active-release` and use the
`repo-v1:...` base ID emitted by preview. The two forms are mutually exclusive:
the operator must explicitly assert which CAS state is expected.

`prepare` follows the durable platform job to completion and prints the exact
release-row, release, artifact, candidate, prepared-evidence, protected-main,
risk, computed-effect, and activation-challenge identities returned by the
server. `activate` reads and revalidates that challenge. R0 requires a successful
exact reviewed canary. R1/R2 require `--acknowledge-risk` matching the prepared
class and record the decision `activate_without_canary_or_effect_execution`.
They never reuse or claim the canary lane. `status <release-row-id>` reads one
release, while `status --live` reads the global Live pointer. JSON mode returns
the exact server response, including the complete platform-job receipt for
`prepare`.

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
- reconstructs the complete hybrid base from a generation-stable RepoStorage
  snapshot plus the current immutable governed overlay;
- materializes a complete immutable effective snapshot while accumulating only
  the selected forward closure as governed paths;
- rejects missing, mixed, extra, ambiguous, or dynamically unprovable closure;
- scans the effective snapshot for all reverse importers;
- binds every statically known affected reverse consumer as an immutable
  validation target; unresolved, ambiguous, or dynamic edges remain blockers;
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
- `effective_manifest_id` binds every path/hash in the resulting immutable
  runtime snapshot;
- `governed_manifest_id` binds the cumulative paths whose reads, imports,
  mutations, cache, compatibility projection, and history are now owned by the
  release system;
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

For reviewed promotion:

- a leaf workflow edit may qualify;
- a new private helper may qualify when no live source imports it outside the
  candidate;
- an unchanged live dependency may be included and verified;
- every statically known reverse consumer of a changed helper becomes a bound
  validation target and is compile/import-checked from the same immutable
  snapshot;
- unresolved, ambiguous, dynamic, or failed graph analysis is never R0.

This is the direct regression guard for the AutoTask incident where an
unrelated release rolled `_ticket_lifecycle.py` back to an older parser. The
test fixture changes the shared parser and requires `ticket_webhook.py` to be
bound as an affected validation target from the same protected source. Missing
or ambiguous coverage fails closed.

The Cove restoration incident is the corresponding multi-root case: a commit
claiming one reviewed source revision rewrote 96 unrelated paths and rolled
`helpers/autotask_ticket_migration.py` backward. A rapid candidate therefore
binds one coherent full snapshot and one exact selected forward closure, and
may not source a closure member or affected consumer from a different
revision. Broad multi-root convergence remains a reviewed operation—not an
implicit mutable rewrite caused by promoting one workflow.

## Effect policy and activation authorization

R0 starts narrowly: manually invoked `@workflow` only, with explicit effects
and platform-recognized positive integer bounds. Tools and data providers are
excluded because other entities may invoke them automatically.

Direct networking, subprocess execution, dynamic code/imports, writes, secrets,
schedules, webhooks, public/API-key endpoints, broad access, global entities,
and unknown behavior are R2. R2 describes what an invocation may do. It does
not force reviewed source bytes back through the mutable exact-path lane.
Preparation compiles R2 source without invoking the workflow or claiming that
its effects ran. Activation requires the exact R2 acknowledgement.

An immutable source release may preserve an existing access level, role set,
endpoint, public endpoint, and API-key state. Preview binds those values into
the registration manifest, activation CAS-checks them, and runtime pinning
rejects any mismatch. The release cannot create or widen those exposure values.
Operators change exposure through its separate reviewed control-plane path.
A declaration cannot weaken a static or observed lower bound. The runtime
enforces duration and output limits for reviewed canaries and Live release
executions. External-call, record, and effect declarations remain
classification evidence, not a generic sandbox claim. Decorator metadata alone
never proves that an effect was safely exercised.

Every prepared release exposes a content-addressed activation challenge bound
to the candidate, prepared evidence, effective and governed manifests,
registration manifest, protected commit/tree, policy, risk class, and canonical
computed-effect digest. R0 accepts only a successful exact reviewed-canary
attestation. R1/R2 accept only a canonical explicit risk acknowledgement for
that challenge. Preparation reports their effect execution as `not_performed`;
compile/import-integrity checks are not evidence that vendor or platform effects
were exercised safely.

## Activation rollout prerequisites

The activation route must remain feature-disabled in production until tests
prove all of the following:

- R0 credentialless import validation has no fallback to mutable Workspace
  files, while R1/R2 preparation executes no candidate source and claims no
  effect sandbox;
- server-issued run evidence binds the same candidate and enforced budgets;
- relevant reverse-graph and registry state are rechecked under activation CAS;
- source/registry/current-artifact pointer switch is atomic and read back;
- failure can restore the exact previous artifact without clobbering a newer
  release;
- registration-only create/reactivate works without manufacturing a source
  change;
- registration-only and source-already-target releases still produce an
  immutable candidate, atomic registration outcome, durable projection job,
  and signed content-addressed release ledger without rewriting source bytes;
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
