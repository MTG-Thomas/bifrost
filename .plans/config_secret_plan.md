# Config & Secret Handling Plan

## Scope / Decisions
- Protect against DB compromise; Redis is internal/trusted but should minimize plaintext exposure where easy.
- Single app key acceptable for now (no key separation/rotation in this pass).
- Keep docker-compose developer experience simple; avoid PKI/TLS requirements for Redis in this iteration.
- Legacy Key Vault/table-storage secret paths should be removed/redirected.

## Goals
- Reduce plaintext secret exposure footprint and blast radius.
- Add observability/audit around secret reads/writes.
- Clean up legacy secret storage code paths.

## Action Plan (for Claude)
1) Secret cache posture
   - Reduce scope of prewarmed secrets: cache only keys needed for the execution (if available from workflow metadata), otherwise keep current but shorten TTL for secret entries (e.g., 60s) while retaining 5m for non-secrets.
   - Avoid re-encrypting for Redis; if a larger change is acceptable, add an on-demand decrypt fallback path (SDK cache miss -> DB fetch + decrypt without storing plaintext).
2) Audit logging
   - Add audit hooks for `bifrost.config.get/set` (no values), capturing org_id, key, execution_id, user_id/email or "Workflow", outcome.
   - Emit rate/volume metrics for secret reads to alert on spikes.
3) Prewarm error visibility
   - Fail or loudly warn on secret decryption failures during prewarm (include org/key, avoid values). Add structured logging and optional metric.
4) Legacy cleanup
   - Inventory/remove Azure Key Vault/table-service secret handlers (`shared/handlers/secrets_handlers.py` and related table-service helpers) if unused; add feature flag or hard delete.
   - Consolidate other Fernet usages (`shared/services/oauth_storage_service.py`, `shared/services/git_integration_service.py`) to the shared crypto helper or deprecate duplicates.
5) Validation/ops (low lift)
   - Ensure Redis requires auth in non-dev envs; document expectation in settings (keep dev defaults simple). Optional: add a startup check that warns if redis_url lacks credentials.

## Deliverables
- Code changes implementing the above (or split PRs by area).
- Tests: unit coverage for audit hooks, cache TTL/behavior, and prewarm failure logging; integration happy-path to ensure SDK still reads secrets.
- Brief follow-up note indicating which legacy paths were removed or gated.
