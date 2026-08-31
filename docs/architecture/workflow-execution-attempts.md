# Workflow execution attempts

## Decision

`Execution` is the stable, user-visible logical workflow run.
`ExecutionAttempt` is bounded causal evidence for one durable claim of that run.
Adding attempts does not make arbitrary workflow Python replayable and does not
enable automatic workflow retry.

PostgreSQL is authoritative. RabbitMQ transports a confirmed dispatch. Redis
stores ephemeral execution context, live logs, cancellation signals, and sync
results; loss of Redis context must not leave a token-fenced attempt silently
Running.

## Operator question

Before this model, an execution left at `Running` could mean tenant code was
still active, process admission failed, a child crashed, the worker pod
disappeared after RabbitMQ acknowledgement, or result persistence failed. The
logical row could not distinguish those cases. Attempt state and stable failure
codes provide that evidence without copying workflow payloads.

Repository inspection establishes the gap but cannot honestly produce historic
production counts for categories that were never recorded. Production counts
remain an operational follow-up after the schema is deployed.

## State ownership

| Transition | Owner | Durable evidence | Fence |
| --- | --- | --- | --- |
| accepted → scheduled/dispatching | API dispatch | `executions` row, attempt 1, plus immutable dispatch/runtime hashes | advisory transaction lock keyed by execution ID |
| scheduled/dispatching → pending/published | confirmed publisher, including deferred promotion | logical status, attempt publication timestamp, Redis context, and broker message | execution identity and status under dispatch lock |
| pending/published → running/claimed | workflow consumer | logical status plus worker identity and a fresh secret on the existing active attempt | unique active-attempt index and random claim token |
| claimed → running | consumer before child routing | attempt start/heartbeat timestamps and policy/source references | active claim token |
| running → terminal | result callback | attempt terminal status/code/resources and logical execution result/status | active claim token in the same database transaction |
| running → admission rejected | consumer admission handler | terminal pre-child attempt; logical execution returns to pending | active claim token plus execution advisory lock |
| running → cancelled | authorized cancellation plus pool callback | logical cancellation and terminal attempt | execution lock and active claim token |
| running → timed out/worker lost | process-pool synthetic result | terminal attempt with stable failure code | captured active claim token |
| result with missing Redis context | result callback | `result_context_missing`; logical execution fails closed | captured active claim token |

Duplicate broker delivery cannot create a second active attempt. A delayed
result carrying an old token cannot update a terminal attempt or overwrite the
logical run. Claim tokens are internal capabilities: they are never returned by
REST, WebSocket, logs, audit records, or generated clients.

## Attempt data contract

Attempt rows store only:

- ordinal, status, phase, and stable failure code;
- stable worker slot, per-start worker incarnation, and process identity for
  platform administrators;
- runtime mode and immutable runtime-evidence digest;
- policy version and dispatch/publication/claim/start/heartbeat/terminal timestamps;
- bounded resource-summary scalars.

They never store parameters, results, variables, execution context, credentials,
raw exception messages, or logs. Attempts cascade with their logical execution
and follow the execution retention policy. There is no independent attempt
deletion that could manufacture partial history.

The unique `(execution_id, attempt_number)` constraint is also the ordered
detail-query index; no redundant secondary index is maintained. A partial
unique index on `execution_id WHERE completed_at IS NULL` enforces one active
attempt. Rollout verification records `EXPLAIN (ANALYZE, BUFFERS)` for
`WHERE execution_id = ? ORDER BY attempt_number` and the stale-heartbeat scan
against representative production cardinality before enabling cleanup alerts.

`GET /api/executions/{id}` preserves the existing owner/platform-admin
authorization. It adds `attempt_history`:

- `recorded`: attempt tracking was enabled; the list may be empty when a new
  execution failed or was cancelled before queue claim;
- `legacy_unavailable`: the execution predates attempt tracking or used an
  unreconciled persisted inline path; no synthetic history is invented.

Worker/process identity, evidence digests, and attempt resource metrics are
redacted for non-admin owners.

## Failure windows and current boundaries

- Broker publication is confirmed before a normal run becomes `PENDING`.
- The consumer creates a durable active attempt before it acknowledges the
  delivery after child routing.
- The token stays in the trusted parent `ProcessHandle`; the pool overwrites
  child-supplied identity fields and attaches the token to both real and
  synthetic timeout, cancellation, crash, and orphan results.
- Terminal callbacks update attempt and logical execution transactionally. A
  stale callback performs no SDK flush, event-delivery update, metrics update,
  terminal notification, or Redis cleanup.
- A missing Redis pending record is recorded as a result-persistence failure,
  not ignored.

Attempt 1 is created with the durable execution pin in `dispatching`, advances
to `published` only after RabbitMQ confirmation, and receives its unexposed
claim token when the consumer owns it. Run-now and deferred publication use the
same execution-keyed lock and only transition to Pending after confirmation.

Worker heartbeats publish a per-start incarnation UUID, and attempts persist
that UUID as their lease owner. Cleanup compares the exact incarnation rather
than a reusable hostname before classifying a stale active attempt as
`worker_lost`. This is intentionally terminal failure, not automatic retry;
restarting arbitrary workflow code remains prohibited.

## Non-goals

- replaying workflow Python;
- retrying a whole workflow after tenant code may have produced side effects;
- replacing RabbitMQ, PlatformJob, or Bifrost's process sandbox;
- creating a second execution history or visibility service;
- using hostnames, PIDs, or public attempt IDs as fencing credentials.

## Verification contract

Focused tests cover bounded public data, explicit legacy coverage,
helper-level monotonic transitions, stale-token rejection, failure
classification, process-pool result propagation, and core PostgreSQL
constraints (one active attempt, state shape, ordinal reuse after a terminal
attempt, token/execution pairing, rollback atomicity, and cascade deletion).
Broader concurrent-claim, authorization/redaction, broker-redelivery,
migration upgrade/downgrade, and full reconnect E2E coverage remain rollout
gates. The repository's supported Docker/PostgreSQL test stack must execute
both the authored tests and those remaining scenarios before rollout.

The Milestone 1 validation run on 2026-08-31 used the isolated
`bifrost-platform-test-debian13-01` VM stack. It verified a clean migration,
an explicit downgrade to `20260827_event_criteria`, and upgrade back to
`20260831_execution_attempts`; the downgrade removed both the workflow attempt
table and parent tracking marker. PostgreSQL selected
`uq_workflow_execution_attempt_number` for ordered execution-detail lookup and
`ix_workflow_execution_attempts_active_heartbeat`
for the active heartbeat scan. Focused backend, workflow E2E, generated-client
typecheck, and attempt-history component tests passed. Production-cardinality
`EXPLAIN (ANALYZE, BUFFERS)` and post-deployment failure counts remain rollout
observability tasks because the isolated VM contains synthetic test data only.
