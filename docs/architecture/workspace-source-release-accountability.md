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

## Remaining repository integration

This platform contract does not install the producer in
`MTG-Thomas/bifrost-workspace`. That repository must add the protected-main
workflow, its authentication, executable-path classification, SHA-256
calculation, and a required operational alert for declaration failures before
the tracking state can be considered active.

The OIDC endpoint is disabled until all five settings are configured:

- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_ID`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_REPOSITORY_OWNER_ID`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_WORKFLOW_REF`
- `BIFROST_WORKSPACE_SOURCE_RELEASE_OIDC_ORGANIZATION_ID`
