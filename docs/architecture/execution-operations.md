# Execution operations contract

## Status and purpose

This document inventories the execution systems that exist before the
execution-operations epic. It is the migration baseline for making delivery,
retry, admission, attempt, and operator-control policy explicit.

The inventory describes current code, including behavior that should be
changed later. It does not turn accidental behavior into a permanent contract.
The invariants in [Preserved invariants](#preserved-invariants), however, are
requirements for every migration in the epic.

The word **job** means one logical, user- or system-visible unit of work.
**Delivery** means one broker delivery of a message. **Attempt** means one
admitted execution of a logical job by a runner. A retry schedules another
delivery or attempt for the same logical job. A replay is an operator-initiated
delivery of a previously poisoned message. Delivery, attempt, retry, and replay
identities are not interchangeable.

## Execution systems

Bifrost currently has three related execution systems.

| System | Work | Durable authority | Dispatch and runner |
| --- | --- | --- | --- |
| Workflow execution | Tenant workflows and inline code | PostgreSQL `executions`; immutable runtime and dispatch evidence on the row | Minimal RabbitMQ message, Redis pending context, worker `ProcessPoolManager`, fresh one-shot child |
| Agent execution | Agent runs, summaries, backfills, and tuning turns | PostgreSQL `agent_runs` and conversation/tuning records; Redis carries pending runner context | RabbitMQ queue consumers in the execution worker; agent calls may invoke workflow execution |
| Platform jobs | Durable non-workflow platform operations | PostgreSQL `platform_jobs`, including deduplication, progress, attempts, lease fencing, and result/error | Scheduler replicas claim with `FOR UPDATE SKIP LOCKED` and execute a registered handler in an isolated child process |

APScheduler is a trigger host, not a fourth durable job system. The elected
trigger leader performs short maintenance or enqueues durable work. Future
workflow executions remain PostgreSQL `Scheduled` rows until the deferred
execution promoter publishes them.

## RabbitMQ delivery inventory

All ordinary queues use durable RabbitMQ queues with a poison exchange and four
TTL retry queues. Messages are persistent and carry `message_id`, correlation
metadata, schema version, origin queue, enqueue time, retry count, replay count,
and an idempotency key. The package-install exchange is intentionally different:
each online worker owns an exclusive auto-delete fanout queue.

| Queue or exchange | Producer | Consumer | Durable authority and identity | Current acknowledgement boundary | Current retry and duplicate behavior |
| --- | --- | --- | --- | --- | --- |
| `workflow-executions` | `services/execution/async_executor.py` | `jobs/consumers/workflow_execution.py` | `Execution.id`; Redis pending context is required runner input but is not terminal authority | The broker handler returns after durable `Pending -> Running` claim, metadata/context assembly, process-pool admission, and dispatch to a one-shot child. Result persistence happens later through the pool callback. | `execution_id` is the idempotency key. Non-`Pending` durable rows are duplicate deliveries. Admission and transient preparation failures explicitly raise `RetryableConsumerError`; deterministic domain failures are recorded and acknowledged. Stale accepted work is recovered by execution cleanup rather than an unacked broker delivery. |
| `agent-runs` | `services/execution/agent_run_service.py` | `jobs/consumers/agent_run.py` | `AgentRun.id`; Redis stores pending context | The consumer owns the agent run until its handler records a terminal or retryable outcome. | `run_id` is the idempotency key. Terminal and fresh-running rows are duplicate acknowledgements. A stale running row may be returned to `queued` when its Redis context still exists. |
| `agent-summarization` | `services/execution/run_summarizer.py` | `jobs/summarize_worker.py` | `AgentRun.id` and its summary status | After the summarizer has durably updated summary state or classified the delivery failure | `run_id` is the idempotency key. Deterministic summarizer failures are domain outcomes; transient dependencies may retry. |
| `agent-summarization-backfill` | `jobs/platform/summary_backfill.py` | `jobs/summarize_worker.py` | Parent `PlatformJob.id` plus `AgentRun.id`; backfill tracker owns aggregate completion | After per-run summary/backfill accounting is durably updated | `run_id:backfill_job_id` is the idempotency key. The PlatformJob moves to `waiting` after fan-out and is completed by durable child tracking. |
| `agent-tuning-chat` | `services/execution/tuning_service.py` | `jobs/summarize_worker.py` | Stable tuning `turn_id` and durable conversation state | After the turn/reply outcome is committed | `turn_id` is the idempotency key. Redelivery must not append a duplicate user turn and may resume reply generation. |
| `package-installations` fanout | Package mutation path | `jobs/consumers/package_install.py` on every online worker | Operation ID plus Redis per-worker completion/progress | Each online worker acknowledges after its local install/uninstall and template drain/recycle outcome | `operation_id` makes repeated delivery to the same worker a no-op. Offline workers receive no backlog; later reconciliation, not fanout durability, must establish convergence. |

The ordinary queue retry schedule is 10 seconds, 60 seconds, 5 minutes, and
30 minutes. `RetryableConsumerError` publishes a new message to the applicable
retry queue and acknowledges the original only after that publish succeeds.
`PermanentConsumerError` and `MalformedMessage` publish to poison and then
acknowledge. `DuplicateMessage` and `DomainFailureHandled` acknowledge without
another attempt. Cancellation during consumer shutdown retries or requeues the
delivery.

An unclassified exception currently goes directly to poison. This is important:
`docs/message-delivery.md` still describes unhandled exceptions as retryable,
but `jobs/rabbitmq.py` is authoritative. The policy registry must make the
intended default explicit rather than preserving this documentation/code split.

RabbitMQ QoS is the current reservation limiter. Workflow prefetch is capped at
`min(max_concurrency, max_workers)`, agent and tuning consumers use configured
concurrency, and summarization/package fanout default to one where configured.
There is no additional base-consumer semaphore despite the older description
in `docs/message-delivery.md`.

## Workflow execution lifecycle

The workflow path deliberately separates broker delivery from user-code
execution:

1. The API allocates an execution ID, resolves and stores immutable runtime and
   dispatch evidence, writes a PostgreSQL `Scheduled` row, and writes pending
   execution context to Redis.
2. Publication is serialized under the execution's PostgreSQL advisory lock.
   Publisher confirms are enabled. Only a confirmed publication may transition
   the row from `Scheduled` to `Pending`.
3. The consumer atomically claims only `Pending` rows as `Running`. Another
   durable state makes the broker delivery a duplicate.
4. The consumer revalidates deployment or Workspace release evidence, resolves
   tenant scope, mints execution-scoped engine credentials, and assembles the
   child context.
5. `ProcessPoolManager` applies slot and cgroup-memory admission, forks a fresh
   child from the dependency-preloaded template, and sends that one execution
   over a private pipe.
6. The child executes tenant code and exits. The parent callback durably commits
   result, buffered SDK writes, and logs before waking a synchronous caller.
   WebSocket/history fan-out and Redis cleanup occur after the authoritative
   commit.
7. Timeout, cancellation, crash, or stale-running recovery produces the same
   durable execution and history projections as an ordinary terminal result.

The broker message is not the durable lease for the full child lifetime. Once
the consumer has dispatched to the process pool, recovery depends on the
PostgreSQL execution row, pool monitoring, Redis context, and the execution
cleanup scheduler. The policy epic must preserve this fact or deliberately
change the acknowledgement boundary with failure-injection evidence.

## Agent execution lifecycle

`AgentRun` is the logical domain record for an agent invocation and already
contains token/iteration budgets, timing, result, error, and summary state.
Agent execution uses Redis context and a RabbitMQ delivery, but the durable row
decides whether a redelivery is terminal, actively running, stale and
recoverable, or invalid. Agent steps are durable user-visible reasoning/tool
history, not infrastructure attempts. The attempt model introduced by this
epic must not overload `AgentRunStep` with worker-delivery history.

Summarization, summary backfill, and tuning are operationally distinct from the
primary agent run even when they update agent-owned records. They need their own
delivery policies and workload classes so provider throttling or a backfill
cannot starve interactive agent runs.

## Platform-job inventory

Every platform job has a typed, versioned payload and an explicit
`PlatformJobPolicy`. PostgreSQL owns active deduplication, priority, resource
locks, attempts, timeout, retry-on-runner-loss behavior, progress revision,
lease owner/token/expiry, cancellation request, memory samples, and terminal
result or structured error. Scheduler replicas claim rows with
`FOR UPDATE SKIP LOCKED`; lease-token predicates fence stale runners.

The registered definitions at this baseline are:

| Job type | Timeout | Attempts | Concurrency | Memory headroom | Special behavior |
| --- | ---: | ---: | ---: | ---: | --- |
| `application.publish` | 20m | 2 | shared | 256 MiB | Runner-loss retry |
| `oauth.refresh` | 15m | 2 | 1 | 128 MiB | Singleton maintenance |
| `webhook.renew` | 30m | 2 | 1 | 128 MiB | Singleton maintenance |
| `solution.update_check` | 30m | 2 | 1 | 256 MiB | Singleton maintenance |
| `workspace.file_index_reconcile` | 60m | 2 | 1 | 256 MiB | Singleton maintenance |
| `artifact.retention_cleanup` | 30m | 2 | 1 | 128 MiB | Singleton maintenance |
| `solution.export` | 60m | 2 | shared | 512 MiB | Standard isolated handler |
| `solution.deploy` | 60m | 2 | shared | 512 MiB | Encrypted payload and learned memory profile |
| `embedding.reindex` | 4h | 2 | 1 | 512 MiB | Serialized high-memory work |
| `workspace.reimport` | 60m | 2 | 1 | 512 MiB | Serialized Workspace mutation |
| `workspace.git` | 60m | 2 | 1 | 512 MiB | Serialized git operation |
| `agent.summary_backfill` | 15m | 2 | shared | 128 MiB | Encrypted payload; may wait on RabbitMQ child fan-out |
| `workspace.release.prepare` | 15m | 2 | 2 | 512 MiB | Immutable release preparation |
| `workspace.release.lock` | 15m | 2 | 1 | 256 MiB | Serialized release lock |
| `chat.video_generation` | 30m | 1 | shared | 512 MiB | Encrypted payload, no runner-loss retry, running cancellation allowed |
| `sdk.video_generation` | 30m | 1 | shared | 512 MiB | Same policy as chat video generation |

`shared` means the definition has no job-type-specific concurrency cap; the
scheduler host's two claim loops and memory admission remain the safety ceiling.
All policies otherwise inherit admission at 85% and hard termination at 95% of
the container cgroup memory limit. Only video generation currently permits
cancellation after its handler starts.

The public `PlatformJobPublic` contract is projected identically to HTTP and
the `platform_job_updated` WebSocket event. Notifications are a projection of
the same row. The browser does not own job state, and MCP mutations must remain
thin wrappers over authorized REST endpoints.

## Scheduler and recovery inventory

One scheduler replica holds the fenced `scheduler-triggers` PostgreSQL lease.
Followers still host platform-job claim loops. The elected leader runs short
trigger and housekeeping callbacks, including workflow schedule processing,
deferred execution promotion, stuck execution and event cleanup, metric
snapshots, artifact retention, and reconciliation.

Long-running trigger work must enqueue a deduplicated PlatformJob. Future
workflow runs remain in PostgreSQL and are promoted in bounded batches with
`FOR UPDATE SKIP LOCKED`; a failed publication returns the row to `Scheduled`.
RabbitMQ ETA messages are not the durable schedule.

## Public observation and control surfaces

| Concern | Current surface | Authority |
| --- | --- | --- |
| Workflow status/history/logs | Execution REST routes, history updates, execution WebSocket channels | `executions` and `execution_logs` |
| Synchronous workflow result | Redis blocking result list after durable result commit | PostgreSQL result; Redis is wake-up transport |
| Worker presence/capacity | Redis worker heartbeats sampled into diagnostics | Live heartbeat, with sampled diagnostic history |
| Agent run and steps | Agent-run REST/UI and agent events | `agent_runs` and `agent_run_steps` |
| Platform-job status/progress/cancel | Shared REST routes, CLI polling, notification WebSocket | `platform_jobs` |
| Scheduler state | Scheduler diagnostics API/UI | PostgreSQL leases/runs plus live replica heartbeats |
| Poison messages | `src.jobs.dlq_cli` inspect/replay/discard | RabbitMQ poison queue; audit and domain linkage are incomplete |
| Workflow cancellation | Durable execution transition plus Redis cancel pub/sub | PostgreSQL status; pub/sub requests local termination |
| Template recycle | Redis worker command channel and package-install flow | Ephemeral command plus worker heartbeat/progress evidence |

There is not yet one durable operator control plane for pausing a workload
class, draining a worker, quarantining a resource, or changing intake. Those are
explicit later deliverables, not behavior to add ad hoc to an individual
consumer.

## Preserved invariants

All execution-operations changes must preserve these invariants unless a later
architecture decision explicitly replaces one with stronger, tested behavior:

1. **PostgreSQL authority.** Broker messages, Redis keys, WebSocket events, and
   notifications are transports or projections. They do not replace the
   durable workflow, agent, or PlatformJob record.
2. **Immutable runtime authorization.** Modern Solution and Workspace execution
   must run the exact deployment or release evidence authorized at dispatch.
   Redelivery, retry, and replay may not resolve unpinned current source.
3. **Tenant authorization.** Enqueue, observation, cancellation, control, and
   replay must apply the underlying organization and resource permissions.
4. **Execution-scoped credentials.** Tenant code does not inherit API,
   PostgreSQL, RabbitMQ, object-storage, refresh-token, or signing credentials
   from the worker/template environment.
5. **Fresh tenant-code process.** One user workflow executes in one fresh child
   forked from the dependency template and that child exits afterward.
6. **Admission before unsafe allocation.** Local slot and cgroup memory policy
   protect the worker before tenant code begins.
7. **Durable-before-visible completion.** Results and buffered side effects are
   committed before synchronous wake-up or terminal event projection.
8. **Fenced platform runners.** A stale lease generation cannot report progress
   or terminal state after another runner owns the job.
9. **Idempotent redelivery boundary.** A stable logical identity and durable
   claim check protect every retryable or replayable job type.
10. **One public PlatformJob contract.** REST, CLI, MCP, notification, and
    WebSocket behavior continue to derive from the shared durable job.
11. **Bounded resource termination.** Timeout, cancellation, drain, and memory
    enforcement have bounded escalation and produce an explainable durable
    outcome.
12. **No secret-bearing operational telemetry.** Events, attempts, diagnostics,
    poison inspection, and audit records must redact or protect credentials and
    sensitive payload fields.

## Baseline gaps and migration decisions

The inventory exposes the following work for the epic:

- delivery and execution policy are split between base consumers, individual
  handlers, process-pool code, settings, and `PlatformJobPolicy`;
- unclassified RabbitMQ exceptions dead-letter immediately while older docs say
  they retry, so the intended default needs an explicit policy and tests;
- older docs describe consumer semaphores that no longer exist, leaving QoS and
  local admission as the real backpressure mechanisms;
- workflow acknowledgement precedes child completion and therefore relies on
  durable stale-execution recovery rather than broker redelivery;
- `PlatformJob.attempt` is a counter, workflows have no durable attempt history,
  and `AgentRunStep` represents domain steps rather than infrastructure attempts;
- lifecycle terms and event envelopes differ between workflow, agent, and
  platform-job systems;
- retry delays are fixed and synchronized, without jitter, elapsed-time budgets,
  dependency circuits, or tenant/provider retry budgets;
- worker controls are primarily ephemeral commands and feature-specific
  cancellation paths rather than durable audited desired state;
- worker heartbeats expose useful capacity and memory data but cannot yet
  explain every queued/admission constraint or estimate drain time;
- poison queues have CLI tooling but lack durable disposition, authorization,
  domain linkage, attempt identity, and a shared UI/API workflow;
- scheduled promotion is durable but does not yet declare workload-specific
  horizons, catch-up/coalescing, overlap, or capacity-aware promotion policy;
- failure tests exist for individual components but there is no policy-indexed,
  deterministic cross-system failure matrix.

These gaps define the sequencing for the remaining execution-operations issues:
policy registry, attempt history, lifecycle events, workload classes and
backpressure, operator controls, retry/dependency protection, capacity
diagnostics, poison operations, scheduling policy, and failure injection.

## Declarative policy registry

`api/src/jobs/execution_policy.py` is the common semantic registry introduced
after this baseline was recorded. Every worker delivery channel and every
`PlatformJobDefinition` declares:

- execution mechanism and workload class;
- durable authority and completion boundary;
- idempotency and worker-loss behavior;
- retry and cancellation semantics;
- timeout and memory policy owner;
- whether operator replay is permitted.

The registry deliberately does not duplicate numeric runner settings.
`PlatformJobPolicy` remains authoritative for timeout, attempt, concurrency,
memory-ratio, and running-cancellation values; construction validates that its
runner-loss and cancellation values agree with the common semantic policy.
Workflow settings and the process pool remain authoritative for workflow
duration and cgroup values. Later issues may centralize those numeric policies
only when there is one runtime owner for them.

Production RabbitMQ consumers attach their registered policy and include its
identifier, workload class, mechanism, and completion boundary in structured
delivery-decision logs. Test-only consumers may omit a policy so the reusable
transport harness can exercise malformed and synthetic queues without
registering product execution types.

## Durable execution attempts

`execution_attempts` is the append-only operational ledger for runner claims.
It does not replace the workflow `Execution`, `AgentRun`, or `PlatformJob`
domain authorities. Each row identifies one claim of a logical job, snapshots
the policy identifier, mechanism, and workload class that governed it, and
records queue/message, worker/process, lease, retry/replay, timing, and bounded
failure metadata.

Attempt numbers are serialized with a PostgreSQL advisory transaction lock.
Workflow and agent claims create the row atomically with their durable
pending/queued-to-running transition. Platform jobs create it with their
fenced lease and canonical attempt counter; gaps are allowed for jobs that
predate the ledger, while duplicates and regressions are rejected. Terminal
domain writes transition the active attempt in the same database transaction.
Admission rejection, runner loss, cancellation, and deferred child work are
distinct outcomes so operators do not have to infer infrastructure history
from a final domain status.

The ledger contains identifiers and bounded diagnostics, never execution
payloads, results, logs, credentials, or secrets. Tenant-scoped read surfaces
must authorize against the underlying domain authority; the nullable
`organization_id` is an indexing aid, not an authorization boundary.

## Canonical implementation map

| Concern | Location |
| --- | --- |
| RabbitMQ delivery and publishers | `api/src/jobs/rabbitmq.py` |
| Common execution policy vocabulary | `api/src/jobs/execution_policy.py` |
| Durable attempt model and lifecycle service | `api/src/models/orm/execution_attempts.py`, `api/src/services/execution_attempts.py` |
| Workflow publication | `api/src/services/execution/async_executor.py` |
| Workflow consumer and durable results | `api/src/jobs/consumers/workflow_execution.py` |
| One-shot process lifecycle | `api/src/services/execution/process_pool.py` |
| Dependency template and credential scrubbing | `api/src/services/execution/template_process.py` |
| Agent consumer | `api/src/jobs/consumers/agent_run.py` |
| Summarization and tuning consumers | `api/src/jobs/summarize_worker.py` |
| Package-install fanout | `api/src/jobs/consumers/package_install.py` |
| PlatformJob policy and handler contract | `api/src/jobs/platform/base.py` |
| PlatformJob definitions | `api/src/jobs/platform/registry.py` |
| PlatformJob durable row and projections | `api/src/models/orm/platform_jobs.py`, `api/src/services/platform_jobs.py` |
| PlatformJob claiming and lease recovery | `api/src/jobs/schedulers/platform_jobs.py` |
| Trigger leadership | `api/src/scheduler/leadership.py` |
| Deferred workflow promotion | `api/src/jobs/schedulers/deferred_execution_promoter.py` |
| Current delivery runbook | `docs/message-delivery.md` |
| PlatformJob architecture | `docs/architecture/platform-jobs.md` |
