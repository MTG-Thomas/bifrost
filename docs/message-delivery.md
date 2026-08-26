# Message Delivery Operations

Bifrost RabbitMQ consumers use explicit delivery outcomes instead of relying on
implicit `message.process(requeue=False)` behavior.

Production consumers attach the declarative policy registered in
`src.jobs.execution_policy`. Delivery-decision logs include the policy name,
workload class, mechanism, and completion boundary. The policy records existing
semantics while the consumer implementation remains responsible for executing
the acknowledged/retry/poison decision.

## Queues and Idempotency

| Queue | Idempotency key | Terminal duplicate behavior |
| --- | --- | --- |
| `workflow-executions` | `execution_id` | Existing durable execution row is acked as a duplicate. Missing Redis pending state is a no-op duplicate unless sync result delivery is waiting. |
| `agent-runs` | `run_id` | Completed, failed, cancelled, and timeout rows are acked as duplicates. Fresh running rows are acked as duplicates. |
| `agent-summarization` | `run_id` | Summary state on the run is authoritative; deterministic failures are recorded on the run rather than retried forever. |
| `agent-summarization-backfill` | `run_id:backfill_job_id` | Backfill accounting is handled by the backfill tracker; replay still uses the same run/job idempotency key. |
| `agent-tuning-chat` | `turn_id` | User turns carry a stable `turn_id`; redelivery does not append the same user turn twice and can continue reply generation. |
| `package-installations` | `operation_id` | Each worker reports package-operation progress in Redis. Delivery remains at-least-once; the ID correlates attempts but does not durably make repeated broadcasts a no-op. |

Every publisher sets:

- `message_id`
- `x-idempotency-key`
- `x-origin-queue`
- `x-schema-version`
- `x-enqueued-at`
- `x-retry-count`
- `x-replayed-count`

## Delivery Outcomes

Consumers classify work with explicit exceptions:

- `DuplicateMessage`: ack as a duplicate/no-op.
- `DomainFailureHandled`: ack after durable domain state is recorded.
- `RetryableConsumerError`: publish to a delayed retry queue, then ack the original only after retry publish succeeds.
- `PermanentConsumerError` and `MalformedMessage`: publish to poison, then ack the original.
- `ConsumerShutdown`: schedule retry or requeue before the channel closes.

Unhandled exceptions are currently poisoned by the base framework and are
logged with queue, message id, idempotency key, retry count, replay count,
redelivery flag, and processing duration. Consumers must raise
`RetryableConsumerError` explicitly for infrastructure failures that are safe
to retry. The execution-operations migration baseline and the policy decision
that will replace this implicit default are documented in
[`architecture/execution-operations.md`](architecture/execution-operations.md).

## Retry Policy

Each base queue has retry queues:

- `<queue>-retry-1`: 10 seconds
- `<queue>-retry-2`: 60 seconds
- `<queue>-retry-3`: 5 minutes
- `<queue>-retry-4`: 30 minutes

Retry queues dead-letter back to the main queue after TTL. `x-retry-count`
increments on each retry. When retry attempts are exhausted, the message is
published to `<queue>-poison` with `x-poison-reason` and `x-poisoned-at`.

Production broker-retry policies predeclare three durable TTL variants per
stage (80%, 100%, and 120%). A stable hash of idempotency identity and attempt
selects the variant, preventing synchronized retry waves without creating
unbounded queues or per-message TTL head-of-line blocking. Retries are bounded
by both count and elapsed time from the original `x-enqueued-at` value (one
hour for the standard profile). A retryable error may name a bounded
dependency; repeated failures advance one delay stage and carry circuit
evidence in headers to reduce hot loops.

Examples of retryable failures:

- Redis or Postgres connectivity failures.
- Process-pool admission rejection or memory pressure.
- RabbitMQ publish failure while processing a handler.
- Provider 429/5xx when retry is safe.

Examples of permanent failures:

- Malformed JSON.
- Missing required IDs.
- Invalid UUIDs.
- Unknown or impossible operation/state.

Workflow code failures and agent/tool errors that are already recorded in
domain state are domain failures, not broker-delivery failures.

## Backpressure and Shutdown

RabbitMQ QoS uses `prefetch_count` as the consumer reservation limit:

- `BIFROST_WORKFLOW_CONSUMER_CONCURRENCY` default `10`
- `BIFROST_AGENT_RUN_CONSUMER_CONCURRENCY` default `4`
- `BIFROST_TUNE_CHAT_CONSUMER_CONCURRENCY` default `2`
- summarization and package broadcast stay at one-at-a-time per worker where
  their consumer configuration selects a prefetch of one

There is no additional base-consumer semaphore. On shutdown, consumers cancel
their consumer tag, wait for tracked in-flight tasks up to
`BIFROST_DRAIN_DEADLINE_SECONDS` (default 300 seconds), then close their channel
and connection. A delivery that races with drain is negatively acknowledged
and requeued.

## Recovery

Workflow executions are acknowledged after the process pool accepts them.
If a worker crashes after that point, the existing
`execution_cleanup` scheduler is the durable recovery path: it marks stale
`Pending`, `Running`, and `Cancelling` executions as timed out/cancelled and
publishes normal execution/history updates. It respects workflow-specific
timeouts with grace.

Agent runs use durable `agent_runs` rows. Fresh `running` rows are treated as
active duplicates. Rows older than `BIFROST_CONSUMER_STALE_RUNNING_SECONDS`
are reset to `queued` and can be processed again if the Redis context still
exists; terminal rows are never re-run by normal redelivery.

## DLQ Tool

Run inside the API or worker environment:

```bash
python -m src.jobs.dlq_cli inspect workflow-executions --limit 10
python -m src.jobs.dlq_cli replay workflow-executions --limit 5 --dry-run --actor ops@example.com --reason "validated outage"
python -m src.jobs.dlq_cli replay workflow-executions --limit 5 --actor ops@example.com --reason "validated outage"
python -m src.jobs.dlq_cli discard workflow-executions --limit 5 --actor ops@example.com --reason "bad legacy payload"
```

Inspect shows decoded body, headers, retry/replay counts, original queue,
message id, idempotency key, and dead-letter metadata. Replay increments
`x-replayed-count`, preserves the idempotency key, and publishes back to the
original queue. Consumers still enforce normal idempotency, so replay is safe
for messages that died before a durable domain claim and harmless for already
completed duplicates.
Mutating operations require actor and reason and commit a PostgreSQL audit
receipt containing identity, counts, and a body hash—not the payload. Queue
names must exist in the policy registry, policy can forbid replay, and replay
is capped at three cycles to prevent an operator-created poison loop.

## Known Limits

Broadcast queues are exclusive auto-delete queues. They are fanout delivery
channels, not durable replay channels for workers that were offline at publish
time. Package install/uninstall safety comes from operation-level idempotency,
not from durable per-worker backlog retention.
