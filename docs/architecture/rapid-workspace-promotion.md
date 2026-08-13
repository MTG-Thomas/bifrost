# Rapid Workspace Promotion

Status: preview-only dark launch. Production activation is intentionally absent.

## Goal

Make the common Workspace loop feel like local development:

1. Author a workflow and its private helpers locally.
2. Run it against the selected Bifrost data plane.
3. Compile one immutable preview that names every byte and behavioral claim.
4. Activate that exact candidate quickly when it qualifies for the bounded R0 lane.
5. Let durable background work lock the already-live artifact into protected Git.

Git review remains the durable system of record, but GitHub queue latency is not
on the future activation critical path. A Git failure must never replay an
activation or turn a verified Live release into an activation failure.

## Ownership

- The CLI constructs a coherent local snapshot and sends the selected
  workflow's complete forward Python closure.
- The API independently reconstructs that closure, computes reverse-dependency
  risk against live Workspace bytes, classifies effects, binds registry/access
  state, and creates the immutable candidate.
- The Workspace repository owns authored source, effect declarations, focused
  tests, and the eventual lock-in pull request.
- Infrastructure owns feature enablement, migration-before-rollout, encrypted
  artifact storage policy, and GitHub credentials. It does not classify or
  activate a candidate.

Solutions should ultimately use the same artifact, release, validation,
activation, rollback, and lock-in state machine. They remain a separate target
kind and namespace.

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
validation. Syntax errors, missing repo-local imports, ambiguous module names,
computed dynamic imports, and excessive fan-out block the checked write.

`--check-impact` performs two server calls. The first returns a content-addressed
candidate over the proposed bytes and the complete durable Python inventory.
The write call recomputes that candidate while holding the global Python-source
writer barrier. If any source or graph edge changed in between, it returns a
conflict and writes nothing. Agents normally do not handle the candidate ID;
the CLI carries it between the two calls.

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

## Shipped dark-launch contract

The dark-launch operator loop is:

```bash
bifrost run <path> -w <function> --promotion-evidence .promotion-run.json
bifrost promote <path> -w <function> --preview \
  --run-evidence .promotion-run.json
```

`bifrost promote` then:

- reads tracked and untracked, non-ignored Python files twice;
- rejects traversal, symlinks, case/Unicode collisions, ambiguous module names,
  size limits, and a changing snapshot;
- sends a complete path/hash inventory plus exact forward-closure bytes;
- optionally binds local-run evidence to that exact snapshot;
- calls `POST /api/workspace-promotions/preview`.

The server:

- derives organization from authenticated context;
- rebuilds imports from submitted bytes and rejects missing, mixed, or extra
  closure members;
- never fills a missing dependency from live storage, Git, another checkout, or
  another candidate, and never expands a selected closure into unrelated roots;
- scans authoritative live source to find reverse importers;
- blocks a changed helper when a live consumer is outside the candidate;
- binds workflow create/reactivate/preserve intent and existing access/trigger
  state;
- computes declared, static, and combined effects plus enforced bounds;
- scans high-confidence secret patterns before storing source;
- writes source and manifest to create-only, content-addressed object keys;
- stores immutable artifact metadata separately from future mutable release
  state;
- commits a strict, source-free audit event with candidate, snapshot, entry,
  risk, policy, and closure hashes;
- always returns `preview_only=true` and `ready_to_activate=false`.

The capability flag `BIFROST_WORKSPACE_RAPID_PROMOTION_PREVIEW_ENABLED` defaults
to false. There is no activation endpoint or CLI activation option in this
slice.

## Candidate identity

The SHA-256 candidate binds behaviorally meaningful inputs:

- organization and production target;
- entry path and function;
- full snapshot identity and exact closure path/hash/size/relation;
- source revision when available;
- declared, statically inferred, and computed effects;
- enforced resource bounds;
- create/reactivate/preserve registration intent;
- existing workflow identity, organization, active/type/access/role/API/endpoint
  state;
- local-run evidence;
- CLI, SDK, and contract versions;
- policy version.

Presentation text, timestamps, and diagnostic wording do not affect candidate
identity. Activation will eventually accept only a candidate ID plus an
idempotency/confirmation token; it will not accept replacement source or policy
claims.

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

## Activation prerequisites

The activation route must remain absent until tests prove all of the following:

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
- the later lock-in job performs no activation, workflow execution, webhook
  replay, or vendor mutation;
- Git ref delay, later branch advancement, or CI failure affects only lock
  state, never Live activation state.

## Durable state

`workspace_promotion_artifacts` is immutable candidate metadata pointing to
create-only source and manifest objects. `workspace_promotion_releases` is a
separate future state machine with independent activation and Git lock states.
Keeping them separate prevents history synchronization failures from being
misreported as source activation failures.
