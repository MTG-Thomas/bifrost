# Temporal Learnings Epic

Date: 2026-08-31
Status: Milestone 1 implemented; supported-stack verification pending
Research: [Temporal Learnings for Bifrost](../research/temporal-for-bifrost.md)

## Milestone 1 implementation record

Implemented on 2026-08-31:

- durable `execution_attempts` rows beginning at dispatch pin, with
  broker-confirmed publication evidence, one-active-attempt, and ordinal
  constraints;
- internal claim-token fencing from consumer claim through real and synthetic
  process results;
- atomic attempt/logical-run terminal projection and stale-result rejection;
- bounded callback retry without relinquishing local ownership before durable
  persistence;
- attempt heartbeat and stuck-worker revocation without workflow replay;
- explicit `recorded` versus `legacy_unavailable` API coverage;
- owner/platform-admin authorization with infrastructure identity and resource
  redaction for non-admin owners;
- an execution-details attempt view and focused backend/client contracts;
- reconciled execution-engine and attempt architecture documentation.

Repository inspection proved the diagnostic gap but could not produce historic
production failure counts for categories the old schema did not record. Those
counts begin after deployment and remain an operational follow-up.

The supported Docker verification stack could not run in the authoring
environment because the Docker executable was unavailable. Python compilation
and whitespace checks passed; focused Docker pytest, API quality, generated
OpenAPI client refresh, Vitest, migration exercise, and rendered UI inspection
remain required before merge.

## Outcome

Make Bifrost workflow execution attempts explicit, causally observable, and
safe to evolve toward narrowly scoped durable behavior while retaining Bifrost
as the sole authority for identity, source, authorization, execution,
persistence, packaging, and operations.

This epic does not deploy Temporal, replace RabbitMQ, replay existing workflow
Python, or enable automatic whole-workflow retries. It integrates the lessons
that improve Bifrost's current architecture.

## Approval shape

Approve this as a gated sequence, not a blanket commitment to a
durable-orchestration rewrite:

- **Gate A — preflight:** reconcile every workflow state transition and
  inventory production failure categories. Stop if an attempt ledger cannot
  answer a concrete operator question.
- **Gate B — attempt pilot:** Issues 1–3 prove a stable execution identity,
  fenced attempt evidence, and an authorized projection without changing retry
  behavior.
- **Gate C — retry safety:** Issues 4–5 establish shared error and idempotency
  contracts. No automatic retry is approved merely because the schema exists.
- **Gate D — optional durable interaction:** approve Issues 6–8 only for named
  pause/resume, schedule, or restricted-orchestration consumers.

The investment rationale, alternatives, preconditions, and stop conditions are
in the linked research document.

## Invariants

- Existing organization, caller, Solution, Workspace, workflow, and agent
  authorization remains authoritative.
- Immutable dispatch/runtime evidence is persisted before queue publication and
  verified by the worker.
- Existing workflows remain ordinary Python executed once in fresh isolated
  children; they are not replayed.
- One logical workflow execution may have attempt evidence, but automatic
  runner-loss retry remains disabled until a separately approved safe contract
  exists.
- PlatformJob remains canonical for durable non-workflow work.
- PostgreSQL remains the durable source of truth; RabbitMQ is transport and
  Redis remains ephemeral coordination/live data.
- Attempt evidence never copies credentials, parameters, results, variables, or
  unbounded logs.
- Stale workers cannot terminalize or mutate the current attempt.

## Milestone 1 — Durable execution-attempt evidence

### Issue 1: Reconcile the current execution state machine

Document every producer and transition for workflow execution: API dispatch,
scheduled/deferred dispatch, event delivery, sync/tool call, RabbitMQ publish
and redelivery, worker claim, admission, child start, cancellation, timeout,
crash, result reporting, and recovery.

The review must reconcile `api/src/services/execution/README.md` with the
current PostgreSQL-first dispatch pin in `async_executor.py`; documentation
must not continue to describe Redis as the execution-record authority.

Acceptance:

- one state-transition table names the transaction and owner for each edge;
- every edge identifies its durable evidence and idempotency boundary;
- failure windows between database, RabbitMQ, Redis, child process, and result
  persistence are enumerated;
- production counts classify recent failures into the proposed attempt reasons;
- at least one current operator question is shown to require attempt history;
- the pilot is stopped if no such question exists.

### Issue 2: Add the workflow execution-attempt contract

Add a child attempt record under the stable `Execution` identity. Define a
bounded state machine and reasons for dispatch publication, queue claim,
admission, child execution, worker loss, timeout, cancellation, tenant-code
failure, and result-report failure.

Acceptance:

- attempt identity and ordinal are unique within an execution;
- transitions are monotonic and terminal states are immutable;
- each attempt stores timestamps, worker/pool identity, failure phase/code,
  resource summary, and runtime/policy digests only;
- parameters, results, variables, credentials, and logs remain in their
  existing authorized storage surfaces;
- existing executions receive an explicit legacy projection rather than
  fabricated detailed history;
- retention, cascade, indexes, and representative query plans are documented;
- API/CLI contract tripwires and migrations pass.

### Issue 3: Fence worker transitions and expose attempt history

Assign a unique claim token to each active attempt. Require it for start,
heartbeat/progress, and terminal updates so a delayed worker cannot overwrite a
newer state. Add an authorized additive attempt projection to execution detail
and the operator UI.

Acceptance:

- duplicate publication does not create concurrent active attempts;
- a stale result after cancellation, timeout, or a newer claim is rejected and
  recorded safely;
- worker loss is distinguishable from tenant-code failure;
- the normal execution list retains its current logical-run semantics;
- execution detail shows ordered attempts and failure phases without exposing
  payloads or credentials;
- WebSocket updates converge to the durable projection after reconnect;
- unit and E2E tests cover normal completion, duplicate publication, stale
  completion, cancellation race, timeout, and worker loss;
- no automatic workflow retry is enabled.

## Milestone 2 — Retry and side-effect safety

### Issue 4: Define one execution failure and retry vocabulary

Create a shared typed vocabulary for retryability, failure phase, timeout type,
attempt number, maximum attempts, next eligibility, and non-retryable reasons
across workflows, event deliveries, agents where applicable, and PlatformJobs.
Domain state machines remain separate; the vocabulary and operator explanation
must not diverge.

Acceptance:

- policy ownership is unambiguous for dispatch, transport, tenant code,
  integrations, agents, event delivery, and PlatformJobs;
- infrastructure retry is distinguished from business-operation retry;
- timeout fields identify queue/admission/start-to-close/heartbeat/overall
  semantics rather than using one ambiguous value;
- existing Celery/StackStorm learnings are reused, not duplicated;
- public contracts explain whether a retry creates a new attempt, delivery, or
  logical execution.

### Issue 5: Establish the idempotent-operation boundary

Define the contract by which a Bifrost-owned SDK or integration operation may
be retried: stable operation identity, scoped idempotency key, result receipt,
conflict behavior, retention, and explicit declaration of retry-safe failure
classes.

Acceptance:

- retry safety is explicit and fail-closed, never inferred from HTTP method or
  exception type alone;
- the operation key is stable across a retry but scoped to organization,
  execution, attempt, and operation definition as appropriate;
- concurrent duplicates produce one committed effect or a deterministic
  conflict/result receipt;
- secrets and full payloads are not stored in receipts;
- a reference Bifrost-owned operation proves crash-after-effect recovery;
- arbitrary workflow restart remains prohibited;
- no third-party operation is advertised as idempotent without provider-backed
  evidence.

## Milestone 3 — Optional durable interaction

### Issue 6: Typed execution commands and observations

For a named long-lived consumer, define an authorized durable command protocol
inspired by Temporal Signals and Updates, plus durable read projections inspired
by Queries. Candidate commands include cancel, approve, reject, pause, resume,
or provide-input; only commands required by the named consumer are implemented.

Acceptance:

- every command has a stable ID, schema version, caller/scope, validation
  result, ordering rule, deduplication behavior, and terminal disposition;
- authorization is evaluated at acceptance and again where delayed execution
  requires current authority;
- reads come from durable projections and do not require an in-memory tenant
  process;
- commands survive API and worker restart;
- cancellation remains monotonic and cannot be undone by a stale command;
- command payload retention and secret handling are explicit;
- no generic message bus is exposed without a concrete domain contract.

### Issue 7: Complete schedule policy semantics

Compare Bifrost schedules with Temporal's overlap, catch-up, pause, backfill,
and action semantics. Implement only evidenced gaps through the existing
scheduler leader and execution dispatch path.

Acceptance:

- overlap and missed-run behavior are explicit per schedule;
- pause/resume and backfill are authorized, idempotent, and auditable;
- leader failover cannot silently multiply a logical scheduled run;
- each scheduled execution records schedule and nominal-run identity;
- no Temporal service or parallel scheduler is introduced.

## Milestone 4 — Restricted durable orchestration decision

### Issue 8: Decide whether Bifrost needs a second authoring mode

Only after a named use case cannot be met by existing workflows, events, agents,
and PlatformJobs, spike a deliberately restricted durable-orchestration model.
Compare a small native state-machine contract, explicit step/activity API, and
Temporal integration. Existing Bifrost workflows are not migration input unless
an author explicitly rewrites them for the new contract.

Acceptance:

- the use case requires multi-step durable wait/resume or recovery, not merely a
  longer timeout or queue retry;
- deterministic versus side-effecting code boundaries are explicit;
- version compatibility, history limits, payload classification, tenant
  isolation, source pinning, and replay tests are demonstrated;
- an operational cost model compares native implementation, self-hosted
  Temporal, and Temporal Cloud;
- the spike proves how Bifrost remains the identity/auth/source authority and
  avoids dual terminal state;
- the final ADR may reject the capability or Temporal; implementation is not a
  required epic outcome.

## Delivery sequence

1. Reconcile the state machine and production failure evidence (Issue 1).
2. Add attempt rows without runtime behavior changes (Issue 2).
3. Fence worker writes and expose the projection (Issue 3).
4. Use observed attempts to define retry/failure semantics (Issue 4).
5. Prove one idempotent operation before any retry expansion (Issue 5).
6. Fund commands or schedule semantics only for named consumers (Issues 6–7).
7. Consider a restricted orchestration spike only if the earlier model cannot
   serve a demonstrated use case (Issue 8).

## Notional sizing and dependencies

Sizing is relative and must be revised after the state-machine preflight.

| Work | Size | Primary dependencies |
| --- | --- | --- |
| Issue 1 | S–M | Read-only production evidence and execution owners |
| Issue 2 | M | State-machine decision, migration, contracts, retention |
| Issue 3 | M–L | Issue 2, queue consumer, process pool, UI/WebSocket |
| Issue 4 | M | Stable attempt evidence plus Celery/StackStorm policy work |
| Issue 5 | M–L | Operation receipts, SDK/integration boundary, named operation |
| Issue 6 | L | Named consumer, authorization and payload decisions |
| Issue 7 | M | Current schedule inventory and leader-failover tests |
| Issue 8 | Research L | Named unmet use case and operational ownership |

Issues 1–3 are the recommended minimum investment. Issues 4–8 are separate
options, not implied commitments.

## Epic completion gate

The recommended pilot is complete when Issues 1–3 are evidenced by current
documentation, migrations, contracts, focused tests, representative failure
injection, authorized UI inspection, and operator sign-off that attempt history
answers the named diagnostic question.

The broader epic is complete when every funded issue meets its acceptance
criteria. A Temporal deployment, automatic workflow retry, or restricted
orchestration engine is not required for completion.
