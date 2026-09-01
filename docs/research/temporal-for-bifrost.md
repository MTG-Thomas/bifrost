# Temporal Learnings for Bifrost

Date: 2026-08-31

## Question

Should Bifrost embed or outsource workflow execution to Temporal, or should it
adopt selected durable-execution patterns while retaining Bifrost's execution
and product model?

## Executive conclusion

Bifrost should not replace its workflow engine with Temporal or put Temporal
behind the existing workflow API as an invisible queue. Temporal's reliability
comes from a different programming contract: Workflow code is deterministic
orchestration reconstructed by replay, while external I/O happens in separately
retried Activities. Bifrost workflows are ordinary tenant Python programs that
may perform I/O through the SDK and run once in isolated child processes.
Transparently replaying or retrying them could duplicate external side effects.

Embedding Temporal would also introduce a second durable execution authority,
task-queue and worker topology, namespace/security model, deployment-versioning
contract, history store, visibility system, CLI, and UI. Bifrost would still
need its PostgreSQL execution records, organization and Solution authorization,
source pinning, sandbox, logs, notifications, and product-specific state. The
integration would therefore retain most of Bifrost while adding most of
Temporal.

Bifrost should adopt five Temporal lessons inside its existing model:

1. **A logical execution is distinct from its attempts.** Preserve one stable
   execution identity and record every dispatch/claim/start/terminal attempt,
   including worker-loss evidence. The current `Execution` row compresses that
   history into one mutable status.
2. **Retry safety belongs at the side-effect boundary.** Never infer that a
   failed or lost arbitrary workflow is safe to rerun. Operations that may be
   retried need an explicit idempotency contract and stable operation key.
3. **Durable interaction is a first-class protocol.** Temporal Signals,
   Updates, and Queries demonstrate a clean separation among asynchronous
   commands, validated synchronous commands, and read-only state. Bifrost
   should use one typed command/observation vocabulary for cancellation,
   approval, pause/resume, and future long-lived executions.
4. **Deployed code compatibility is execution data.** Temporal's replay and
   worker versioning discipline reinforces Bifrost's existing runtime-evidence
   pins. Worker compatibility, source identity, and policy version should be
   visible on every attempt and verified before execution.
5. **History must be bounded and payload-aware.** Temporal records inputs and
   results in event history and uses Continue-As-New for long histories.
   Bifrost should keep large values in its existing storage planes and impose
   explicit event/log/progress retention budgets rather than copying payloads
   into a new event-sourcing system.

The best first pilot is a durable workflow-attempt ledger with no new automatic
workflow retry behavior. It closes an observability and correctness gap without
claiming arbitrary user code is replay-safe. Automatic runner-loss retry should
be a later, separately gated capability available only to a deliberately narrow
execution contract.

## Operator recommendation

Fund Milestone 1 as an execution-evidence pilot. Do not approve a Temporal
service deployment, transparent workflow retry, or a wholesale durable-workflow
rewrite.

This is worthwhile for reasons visible in current Bifrost code:

1. `Execution` is both logical run and current attempt. It has one set of
   start/completion/error/resource fields and no durable attempt child rows.
   Process crashes, queue redelivery, publication recovery, cancellation races,
   and operator retries cannot be presented as one causal attempt history.
2. Dispatch is already stronger than the older execution README suggests:
   PostgreSQL stores immutable dispatch and runtime evidence before RabbitMQ
   publication, and a caller-supplied execution ID is an idempotency boundary.
   Attempt records complete that design instead of replacing it.
3. Bifrost already has the right model for durable platform work. PlatformJob
   separates job identity from attempts, uses PostgreSQL leases and fencing,
   declares retries and runner-loss policy, and requires retry-enabled handlers
   to be idempotent. Workflow execution should reuse that vocabulary and proven
   invariants without merging the two domain types.
4. The one-shot workflow child and immutable source pin remain valuable. An
   attempt ledger makes their failures explainable while preserving the
   sandbox, tenant context, and source authorization that Temporal does not
   replace.

### Relative value and cost

| Investment | Expected value | Relative cost/risk | Recommendation |
| --- | --- | --- | --- |
| Attempt ledger and projection | High diagnostic and correctness value | Medium; migration, worker transitions, API/UI projection | Fund now as the pilot. |
| Unified retry/idempotency contract | High safety value | Medium; requires explicit product semantics | Fund after the ledger proves the real failure categories. |
| Durable pause/message protocol | Potentially high for approvals and agents | Medium-high; auth, ordering, timeout, and lifecycle semantics | Defer until a named long-lived workflow use case exists. |
| Restricted durable-orchestration mode | High for some long-running processes | Very high; new authoring contract and migration burden | Research only after concrete demand; do not make it the default. |
| Temporal service as Bifrost substrate | Duplicates state and operations while not preserving arbitrary workflow semantics | Very high | Reject. |

### Preconditions

Before implementing the pilot, inventory production execution states and the
worker paths that can create, publish, claim, cancel, timeout, or terminalize an
execution. Reconcile the execution README with the current PostgreSQL-first
dispatch implementation so the migration is based on current behavior.

Name one operator question that current data cannot answer, such as whether an
execution was published twice, reclaimed after worker loss, or failed before
tenant code started. Define the bounded retention and authorization contract
for attempt evidence before adding rows.

### Stop conditions

Stop after the preflight or pilot if:

- every supported workflow execution is demonstrably single-attempt and the
  additional history cannot answer an operator question;
- attempt recording would require Temporal, event sourcing, or another state
  service instead of PostgreSQL;
- the design weakens immutable runtime evidence, organization/Solution scope,
  or one-shot process isolation;
- attempt rows copy parameters, results, credentials, or unbounded logs;
- an implementation silently enables whole-workflow retry;
- operators cannot distinguish dispatch failure, admission failure, runner
  loss, timeout, cancellation, tenant-code failure, and result-report failure.

## Temporal architecture in Bifrost terms

| Temporal concept | Responsibility | Closest Bifrost concept | Assessment |
| --- | --- | --- | --- |
| Workflow Execution | Stable, durable orchestration instance reconstructed from history | Workflow `Execution` | Similar identity, different semantics: Bifrost executes arbitrary Python once and does not replay orchestration. |
| Workflow Task | Replay deterministic code to advance orchestration state | No direct equivalent | Do not emulate for existing workflows. It requires a restricted deterministic authoring contract. |
| Activity | Retryable external side effect with timeout and heartbeat policy | SDK operation, integration call, workflow/tool invocation, PlatformJob handler | Adopt the explicit side-effect and idempotency boundary; do not relabel all workflow calls as safe Activities. |
| Activity attempt | One scheduled/started/completed try | PlatformJob attempt; implicit workflow process try | Add explicit workflow attempt evidence before considering retry. |
| Event History | Append-only decisions and results used for replay | Execution status/logs/runtime evidence/audit records | Adopt causal evidence, not full event sourcing or replay payload duplication. |
| Task Queue | Routes work to compatible pollers; does not hold workflow state | RabbitMQ queue plus Bifrost worker pools | Keep Bifrost routing. Queue replacement alone provides none of Temporal's replay guarantees. |
| Worker | Hosts Workflow and Activity implementations | Execution worker, one-shot child, scheduler job host, agent worker | Preserve domain-specific isolation and credential boundaries. |
| Namespace | Isolation and retention boundary | Organization/provider scope plus deployment environment | Bifrost authorization is richer and remains authoritative. |
| Signal | Durable asynchronous message to a Workflow | Cancellation/event command; no general workflow inbox | Adapt through a typed, authorized command ledger only when a consumer exists. |
| Update | Durable, validated command with result | No single equivalent | Useful model for pause/resume/approval, but needs explicit ordering and idempotency semantics. |
| Query | Read-only inspection of live Workflow state | Execution/job detail APIs | Prefer durable projections; do not depend on an in-memory tenant process answering. |
| Schedule | Durable policy that starts Workflow Executions | Workflow schedules and scheduler trigger leader | Keep Bifrost. Reuse overlap/catch-up/pause policy ideas where current schedules lack them. |
| Continue-As-New | New run with fresh bounded history | No direct equivalent | Apply the bounded-history principle; only add run chaining for a real long-lived orchestration model. |
| Worker/version deployment | Routes histories to compatible code during change | Runtime evidence, Solution/Workspace release pin, runtime generation | Strong validation of Bifrost's existing source-pinning direction. |
| Visibility/search attributes | Indexed operational projection separate from history | Execution history, metrics, logs, diagnostics | Keep PostgreSQL projections and add attempt facets rather than a second visibility store. |

## Why Temporal cannot transparently run existing Bifrost workflows

Temporal Workflow code must be deterministic because a worker may replay the
recorded history to reconstruct state. The Python guide forbids ordinary
network I/O, process calls, system time, randomness, threading, and global-state
mutation in Workflow logic. External effects belong in Activities, whose
results are recorded in history.

Bifrost intentionally offers a different contract. A tenant workflow is loaded
from pinned source and executed as ordinary Python in a fresh, isolated child.
The workflow can call Bifrost SDK operations and integrations during that
process. Its stack and local variables are not durable orchestration state. If
the process disappears after an external call but before reporting its result,
Bifrost cannot know from the Python stack whether replaying the function would
repeat the side effect.

Wrapping the whole Bifrost function in one Temporal Activity would provide
durable scheduling and Activity retry controls, but it would not make the
function resumable. Retrying that Activity would restart from the beginning.
Disabling retry would reduce Temporal to an expensive second queue and history
UI. Splitting the function into Activities would require a new user-visible
authoring model, not an infrastructure swap.

## What Temporal genuinely provides

- durable, replay-based orchestration that survives worker and service failure;
- explicit separation of deterministic decisions from retryable side effects;
- stable workflow identity, event history, timers, child workflows, and
  Continue-As-New;
- Signals, Updates, and Queries for interacting with long-lived state machines;
- Activity timeouts, retry policy, heartbeats, cancellation, and asynchronous
  completion;
- task-queue routing and worker deployment/version compatibility;
- schedule overlap, pause, backfill, and catch-up semantics;
- mature testing support, including replay tests and time-skipping;
- self-hosted or managed service options and an operational Web UI.

These are strong capabilities. Most arise from Temporal's programming model,
not from its transport.

## What Bifrost should preserve

### Arbitrary Python and one-shot isolation

Bifrost workflows are approachable Python functions rather than deterministic
state machines. Fresh child processes, bounded runtime, cgroup-aware admission,
credential scrubbing, import controls, and source verification are core tenant
execution guarantees. Temporal's Python Workflow sandbox primarily enforces
determinism; it is not a substitute for hostile or multi-tenant code isolation.

### Product authorization and source authority

Organization, caller, form, API-key, Solution deployment, Workspace release,
and runtime evidence are resolved and pinned before dispatch. Temporal
namespaces and task queues do not replace those product rules.

### PlatformJob for platform-owned durable work

PlatformJob already owns typed/encrypted payloads, deduplication, leases,
fencing, progress, cancellation, retry policy, resource protection, and shared
REST/CLI/WebSocket observation. Moving those jobs to Temporal would create a
second authority without a demonstrated gap.

### Existing data planes

PostgreSQL is the durable authority; RabbitMQ transports workflow work; Redis
supports ephemeral coordination, results, and live logs; object storage holds
source and large artifacts. Attempt evidence should strengthen these boundaries
rather than copy sensitive or large payloads into an event history.

## Adopt / adapt / reject

### Adopt

- stable logical execution identity with explicit attempt history;
- typed retry policy with explicit non-retryable failures;
- mandatory idempotency declaration/key for retryable external effects;
- heartbeat semantics that report liveness and bounded progress, not merely
  worker presence;
- worker/source/policy compatibility evidence on every attempt;
- replay-style compatibility tests for any future deterministic orchestration
  contract;
- bounded history, retention, and large-payload references.

### Adapt

- Signals/Updates/Queries into authorized command and durable read projections;
- Activity cancellation and heartbeat into cooperative SDK operation contracts;
- schedule overlap, catch-up, pause, and backfill policies into Bifrost's
  scheduler;
- Continue-As-New into explicit run chaining only for long-lived state machines;
- task-queue routing into capability-aware Bifrost worker admission;
- Temporal's data-converter/encryption concerns into Bifrost's existing secret
  and storage classification.

### Reject

- embedding Temporal as an invisible replacement for RabbitMQ;
- treating a whole arbitrary Bifrost workflow as a safely retryable Activity;
- replaying existing workflow Python after worker loss;
- making Temporal history a second execution source of truth;
- mapping organizations directly to Temporal namespaces as authorization;
- replacing PlatformJob with Temporal without an evidenced missing guarantee;
- exposing Temporal Web UI or APIs as Bifrost's operator/product surface.

## Pilot gate

The workflow-attempt pilot succeeds only if:

- one `Execution` remains the stable user-visible logical run;
- each dispatch/claim/run creates or advances a uniquely identified attempt;
- attempt transitions are transactional, monotonic, and protected from stale
  worker result writes;
- the attempt records pinned runtime/source/policy identity by reference or
  digest, without copying execution parameters or credentials;
- failure phase and reason distinguish publication, admission, worker loss,
  timeout, cancellation, tenant code, and result reporting;
- existing execution APIs remain compatible, with an additive authorized
  attempt projection;
- retention is bounded and execution deletion has an explicit cascade policy;
- focused tests prove duplicate publication, stale completion, worker loss,
  cancellation race, and normal completion behavior;
- no new service, broker, automatic workflow retry, or deterministic-workflow
  restriction is introduced.

## Primary sources

- Temporal server repository and MIT license: <https://github.com/temporalio/temporal>
- Python SDK developer guide: <https://docs.temporal.io/develop/python>
- Python Workflow basics and determinism: <https://docs.temporal.io/develop/python/workflows/basics>
- Python Activities: <https://docs.temporal.io/develop/python/activities/basics>
- Python Activity execution and retry: <https://docs.temporal.io/develop/python/activities>
- Python Workflow message passing: <https://docs.temporal.io/develop/python/workflows/message-passing>
- Python Workflow versioning: <https://docs.temporal.io/develop/python/workflows/versioning>
- Python Workflow schedules: <https://docs.temporal.io/develop/python/workflows/schedules>
- Python SDK sandbox: <https://docs.temporal.io/develop/python/best-practices/python-sdk-sandbox>
- Temporal architecture: <https://docs.temporal.io/temporal-service/temporal-server>
- Temporal encyclopedia: <https://docs.temporal.io/encyclopedia>
