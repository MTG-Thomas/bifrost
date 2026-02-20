# Secret Redaction in Workflow Executions

## Problem

When a workflow calls `context.get_config("secret_key")`, the decrypted plaintext can leak into:
- `executions.variables` (captured local variables via `sys.settrace`)
- `executions.result` (workflow return value)
- `executions.error_message` (exception messages)
- `execution_logs` (print/logging output, real-time WebSocket stream)

Secrets are encrypted at rest in the DB and Redis, but once decrypted for workflow use, there's no protection against persistence.

## Design

Two layers of defense:

### Layer 1: SecretString

A `str` subclass returned by `ConfigResolver._decrypt_secret()`.

```python
class SecretString(str):
    REDACTED = "[REDACTED]"

    def __repr__(self):
        return f"'{self.REDACTED}'"

    def __str__(self):
        return self.REDACTED

    def __format__(self, format_spec):
        return super().__str__().__format__(format_spec)

    def get_secret_value(self) -> str:
        return super().__str__()
```

Behavior:
- `repr(s)` / `str(s)` / `print(s)` → `[REDACTED]`
- `f"Bearer {s}"` → real value (via `__format__`)
- `"Bearer " + s` → real value (str concat uses internals)
- `s.encode()` → real bytes (inherited from str)
- `headers={"Auth": s}` → real value (libraries read str buffer)

Known edge case: `f"{s!s}"` returns `[REDACTED]` because `!s` forces `__str__`. Uncommon, fails visibly (auth error).

### Layer 2: Persist-time scrub

Before writing execution data to the DB, substring-replace all known secret values with `[REDACTED]`.

Utility function:
```python
def redact_secrets(obj: Any, secret_values: set[str]) -> Any:
    """Deep-walk a JSON-serializable object, replacing secret substrings."""
```

Recursively walks dicts/lists. For strings, does substring replacement against all known secrets. Skips secrets shorter than 4 characters to avoid false positives.

### Integration points

1. **SecretString creation** — `ConfigResolver._decrypt_secret()` (`config_resolver.py:104`). Wrap return value in `SecretString`.

2. **Secret collection** — Add `_collect_secret_values()` to `ExecutionContext`. Decrypts all secret-typed entries from `_config` to build a `set[str]` for scrubbing.

3. **Persist-time scrub** — `_process_success()` (`workflow_execution.py:231`). Before `update_execution()`, scrub `result`, `variables`, `error_message` using `redact_secrets()`.

4. **Real-time log scrub** — `WorkflowLogHandler.emit()` (`engine.py:752`). Scrub log messages before broadcasting via SignalR and before appending to log buffer.

## Testing

All unit-testable without Docker:

- **SecretString**: `repr`, `str`, `format`, concat, `encode`, `json.dumps`, dict key, equality, `get_secret_value()`
- **redact_secrets()**: nested dicts, lists, strings with embedded secrets, partial matches, short secret skip, non-string primitives pass through
- **Secret collection**: mock config dict with mixed types, verify only secrets collected, min-length filter
- **E2E**: workflow using a secret config, verify stored execution output/variables/logs contain `[REDACTED]`, not the real value

## Files to create/modify

- `api/src/core/secret_string.py` (new) — SecretString class + redact_secrets utility
- `api/src/core/config_resolver.py` — return SecretString from `_decrypt_secret()`
- `api/src/sdk/context.py` — add `_collect_secret_values()` method
- `api/src/services/execution/engine.py` — scrub logs in WorkflowLogHandler, pass secret set to handler
- `api/src/jobs/consumers/workflow_execution.py` — scrub result/variables/error before persist
- `api/tests/unit/test_secret_string.py` (new) — SecretString + redact_secrets unit tests
- `api/tests/e2e/` — E2E test for secret redaction in stored execution data
