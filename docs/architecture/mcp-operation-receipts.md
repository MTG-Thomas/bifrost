# MCP operation receipts

MCP gateway mutations use a permanent, hashed receipt tombstone to guarantee
that a caller retry cannot redispatch an operation after a lost response. The
database never stores the caller's raw `operation_id` or an unhashed copy of
the caller/tenant scope. `scope_key` and the request fingerprint are
deterministic SHA-256 digests.

Replay responses and errors are a separate, short-lived concern. Before the
first response is returned and stored they are recursively redacted for common
credential fields, structurally bounded, and capped at 64 KiB. Terminal replay
payloads are retained for seven days. The scheduler clears expired response,
error, and durable-handle fields hourly while retaining the receipt row,
digests, terminal status, and timestamps permanently. Reads enforce the expiry
timestamp immediately, even before that physical cleanup runs. An expired
retry fails closed and is never dispatched again.

If a process loses the terminal receipt write after dispatch, deterministic
workflow and agent-run IDs—and a platform-job handle persisted immediately
after dispatch—allow an authorized retry to reconcile against the canonical
REST lifecycle. Effects with no durable handle remain ambiguous and fail
closed. A platform administrator may mark such a receipt failed through
`POST /api/mcp/operation-receipts/{receipt_id}/resolve`; the action is audited
with a hash of the operator's reason so audit storage does not duplicate the
incident text.
