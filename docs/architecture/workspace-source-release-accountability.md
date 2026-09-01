# Workspace source release accountability

## Problem

A protected Workspace merge and a production activation are separate events.
The platform cannot infer a merge from the existing `production-live` GitHub
writer. Without a durable declaration, reviewed source can sit on `main` while
production keeps running older bytes.

## Contract

The trusted `bifrost-workspace` workflow triggered by every push to protected
`main` must call `POST /api/workspace-promotions/source-releases/github` for
that commit. Human administrators may use
`POST /api/workspace-promotions/source-releases` with ordinary platform-admin
authentication. The automatic producer uses a GitHub
Actions OIDC token in `Authorization: Bearer <token>`. The platform verifies
GitHub's issuer and signing key, then requires all of these identity claims:

- the configured repository name, immutable repository ID, and immutable owner ID;
- `ref=refs/heads/main`, `ref_type=branch`, and an event name of `push` or
  `workflow_run`;
- the configured producer `workflow_ref` on `refs/heads/main`;
- for `push`, the fixed audience `bifrost-workspace-source-release` and a token
  `sha` identical to `source_commit_sha` in the request;
- for `workflow_run`, the signed audience
  `bifrost-workspace-source-release:workflow_run:<run-id>:<head-sha>`, with its
  `head-sha` identical to `source_commit_sha` in the request.

`workflow_run` covers merges performed by GitHub's serialized queue, whose
`GITHUB_TOKEN` does not recursively emit a `push` workflow. GitHub defines the
workflow-run `GITHUB_SHA` as the latest default-branch commit, which may be newer
than the triggering CI run. The declaration workflow therefore binds
`github.event.workflow_run.id` and `github.event.workflow_run.head_sha` into the
OIDC audience. It must filter the exact CI workflow, completed success, and
`main`; check out that triggering head; prove it is an ancestor of its
OIDC `sha`; and derive the base from the triggering head's first parent. A fixed
audience on `workflow_run`, or a workflow-run audience on `push`, fails closed.

The request includes:

- the protected commit and tree SHA;
- each changed executable Workspace path and its SHA-256 target;
- a null target for a deletion;
- `pending` for production-affecting writes;
- `attention_required` with a reason for a deletion or unsupported change;
- `non_production` with a reason when no production path changed.

Changes under `solutions/<slug>/` use a separate child obligation. They are not
loose Workspace files and never close against the `production-live` branch. For
each changed Solution, the declaration carries the exact slug and repository
subpath, base/source commit and tree evidence, changed paths, the full protected
Git file manifest, and a canonical `source_content_id`. A solution-only commit
may remain `non_production` in the loose-file lane while its child is
`solution_deploy_required`. Malformed descriptors, deletions, cross-Solution
renames, and unsupported Git objects are declared `attention_required` instead
of disappearing from accountability.

The endpoint is idempotent for identical evidence and returns `409 Conflict`
if the same commit is redeclared with different evidence. The producer must
fail its GitHub Actions job when declaration fails. This removes operator
memory from record creation while retaining an exact audit trail.

`GET /api/workspace-promotions/source-releases` returns
`tracking_state=not_configured` until the producer configuration begins or the
first declaration arrives. Operators and monitors must treat that state as
missing coverage, not an empty backlog. Existing installations may still
activate a release only while every producer setting remains absent. Once an
operator supplies any producer setting, partial configuration and a missing
first declaration both fail closed. This prevents a failed first producer run
from leaving activation open indefinitely.

## Completion

The platform owns completion. A successful immutable release projection marks
an eligible source record `released` only when every recorded target hash
matches both the immutable Live runtime and the verified, signed
`production-live` readback. A runtime-only activation cannot close the record.
One protected commit may be promoted through several exact-path artifacts. The
source record stays pending until one cumulative projection proves every
declared target on both surfaces. A non-production current head may still anchor
an exact reviewed catch-up or registration-only release for source merged
earlier; its own non-production disposition remains unchanged while the durable
Workspace release ledger records that activation.

Pending records default to a 30-minute deadline. The scheduler changes overdue
records to `attention_required`, does the same for a Live release whose signed
history has not converged within 15 minutes, writes a structured error log, and
notifies platform administrators.

An operator may explicitly mark a record `deferred` or `non_production`, but
must provide a reason. A later exact release may still replace `deferred` with
verified `released` evidence. Records containing deletions remain open until a
release path can prove runtime absence as well as signed-history absence.

## Solution deployment completion

Solution obligations preserve the explicit operator-approved deployment step;
the platform does not auto-deploy reviewed source. A deploy binds the exact raw
uploaded ZIP SHA-256 candidate and an order-independent candidate file-manifest
identity. The obligation closes only after all of the following succeed:

- the deploy database transaction commits;
- source-artifact and runtime-file storage finalization succeeds;
- the stored artifact reads back and matches the protected-Git file manifest;
- every deployed Python runtime path and hash reads back exactly;
- every Solution-owned entity ID reads back exactly after install-specific UUID
  remapping; and
- workflow registrations read back with the expected ID, path, function, and
  name.

A deploy failure, storage-finalization failure, artifact mismatch, runtime-file
mismatch, or registration mismatch cannot mark the obligation complete.
Mismatches become durable attention records. Pending Solution obligations use
the same 30-minute accountability sweep and administrator notification as loose
Workspace releases. Read-only status is available from
`GET /api/workspace-promotions/solution-deploy-obligations` and its ID-specific
endpoint.

## Repository producer

`MTG-Thomas/bifrost-workspace` owns the protected-main producer, exact Git-tree
classification, canonical manifest construction, and declaration-failure CI
signal. The platform API must be deployed before a producer version that emits
Solution child obligations is merged.

The OIDC endpoint is disabled until all five settings are configured:

- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_ID`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_OWNER_ID`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_WORKFLOW_REF`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_ORGANIZATION_ID`
