# Integration request admission

`integrations.request_slot(name, scope)` bounds one HTTP attempt across all workers.
The server resolves SDK scope and the integration identity. It reads the optional
`request_concurrency_limit` from integration-level defaults. Only platform admins
can change those defaults; organization overrides do not alter admission policy.
An unset setting preserves existing behavior. Explicit values must be integers
from 1 to 100. API or Redis errors fail admission closed.

Each integration UUID shares one Redis sorted set, even when its mappings use
different credentials. Duplicate credentials stored in distinct integration rows
are not coordinated. This is cooperative admission for participating clients,
not a sandbox for arbitrary vendor effects or a guarantee about external clients.

Acquisition is atomic and uses Redis time. Tokens belong to the authenticated
principal and expire after 60 seconds. Retrying acquisition with the same token
returns its remaining lease without extending it. The SDK bounds waiting to
90 seconds, polls at intervals of at least 3 seconds, and never retries the vendor
operation. An admitted operation has a maximum 30-second wall-clock timeout, also
limited by the remaining lease after subtracting the acquisition round trip and
five seconds of safety margin. This deadline applies only when admission is enabled.

After a successful HTTP attempt, the SDK releases that token. On cancellation,
timeout, or an exception it retains the lease until expiry because the vendor may
still be processing. A failed release after successful work is logged and expires;
it does not turn a successful vendor write into an apparent failed execution.
A vendor that continues processing more than 60 seconds after the caller vanishes
can still outlive the lease. Integrations with that behavior need a durable vendor
job protocol instead of claiming this admission API gives exactly-once effects.

Deploy the server and worker SDK before deploying callers. This is an additive
SDK contract; existing routes and DTOs are unchanged. New callers fail if the
request-slot API is unavailable and must never fall back to unguarded requests.
Set the reviewed integration policy before admitting new guarded callers. For a
three-thread vendor limit, use two slots to reserve capacity for external activity.
Do not replay failed ticket-writing executions as a concurrency verification test.
Use read-only probes and natural webhook traffic.
