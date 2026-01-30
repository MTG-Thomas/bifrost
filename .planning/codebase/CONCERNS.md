# Codebase Concerns

**Analysis Date:** 2026-01-30

## Tech Debt

**Datetime Handling Standardization In Progress:**
- Issue: Mixed usage of timezone-aware vs naive UTC datetimes across the codebase. Some code uses `datetime.utcnow()` (naive UTC) while database columns were historically timestamptz (timezone-aware).
- Files: Multiple routers (`api/src/routers/auth.py`, `api/src/routers/forms.py`, `api/src/routers/github.py`, `api/src/routers/oauth_connections.py`, etc.)
- Impact: Potential timezone confusion, inconsistent data representation, and debugging difficulties
- Status: Active mitigation - migration `20260130_000000_standardize_datetime_columns_to_naive_utc.py` converting all 103+ columns from timestamptz to timestamp (naive UTC)
- Fix approach: Complete the migration and ensure all new datetime operations use `datetime.utcnow()` consistently

**Webhook Integration Database Loading (TODO):**
- Issue: Custom adapter discovery from database not yet implemented
- Files: `api/src/services/webhooks/registry.py:153` (explicit TODO comment)
- Impact: Cannot dynamically load custom webhook adapters from workspace_files; only built-in adapters work
- Fix approach: Implement database query in `_load_custom_adapters()` to discover and load `adapters/*.py` files from workspace_files

**Events Integration Loading (TODO):**
- Issue: Integration loading on webhook events commented out and not implemented
- Files: `api/src/routers/events.py:360` (explicit TODO comment)
- Impact: Webhook event handling cannot access integration-specific credentials/context
- Current state: Adapter requires integration but integration is not fetched from database
- Fix approach: Implement `get_integration()` call to load integration when adapter.requires_integration is True

**Events Webhook Verification (TODO):**
- Issue: Workflow existence verification on events not implemented
- Files: `api/src/routers/events.py:613` (explicit TODO comment)
- Impact: Events can be routed to non-existent workflows without validation
- Fix approach: Implement workflow lookup before routing events to workflows

**System Logs Router - Stub Implementation:**
- Issue: Logs router returns empty list and 404 for all requests
- Files: `api/src/routers/logs.py` (lines 29-94)
- Impact: Cannot query system logs; endpoints are non-functional placeholders
- Fix approach: Implement proper PostgreSQL logging backend with category/date filtering and pagination

## Known Bugs

**GitHub Virtual Files Sync Anomaly:**
- Symptoms: App appears in BOTH to_pull AND to_push lists simultaneously
- Files: `api/tests/e2e/api/test_github_virtual_files.py:354` (commented as "BUG")
- Trigger: Specific sequence of file modifications during sync
- Workaround: Code handles the case but logs it as anomalous
- Status: Functional workaround in place; root cause not fully understood

**Memory Test Flakiness After Sequential Large File Writes:**
- Symptoms: Test marked with `@pytest.mark.xfail` for cleanup issues
- Files: `api/tests/integration/platform/test_large_file_memory.py:97-99`
- Trigger: Running `test_many_files_no_accumulation` after `test_sequential_writes_memory_bounded`
- Impact: Cannot run full test suite for large file memory behavior reliably
- Status: Partial mitigation - `db_session.expire()` releases memory after each write, but event loop cleanup issues remain
- Improvement path: Investigate asyncio event loop cleanup in test teardown

## Performance Bottlenecks

**Large Module File Memory Accumulation (Mitigated but Limited):**
- Problem: Sequential writes of 4MB+ Python modules accumulate in SQLAlchemy session causing OOM (experienced 512MB limit breach in scheduler)
- Files: `api/src/services/file_storage.py`, `api/tests/integration/platform/test_large_file_memory.py`
- Cause: SQLAlchemy session holds references to all written file records; parsing large modules creates multiple copies (AST, decoding)
- Current mitigation: `db_session.expire()` after each write releases session memory
- Improvement path: Consider streaming writes, chunked processing, or temporary file handling for modules >10MB

**N+1 Query Patterns - Prevalent but Manageable:**
- Problem: Multiple `.all()` and `.first()` queries in routers without aggressive eager loading
- Files: Multiple routers (`api/src/routers/forms.py`, `api/src/routers/executions.py`, `api/src/routers/oauth_connections.py`, etc.)
- Impact: 20-50+ queries for complex operations (e.g., list with related data)
- Mitigation: Used throughout but not consistently optimized with `selectinload()` or `joinedload()`
- Improvement path: Add relationship eager loading, implement query pagination limits, add query count monitoring

**Data Provider Cache Stampede Protection:**
- Problem: Simultaneous requests for same data provider can trigger multiple executions
- Files: `api/src/core/cache/data_provider_cache.py:28-29` (TTL_LOCK = 10 seconds)
- Current mitigation: Redis SETNX lock with 10-second TTL prevents stampede
- Risk: Lock timeout of 10 seconds may be too short for complex data providers
- Improvement path: Dynamic lock duration based on execution timeout, or use probabilistic early expiration

## Fragile Areas

**Execution Engine - Complex Module Loading and Isolation:**
- Files: `api/src/services/execution/engine.py` (1095 lines), `api/src/services/execution/module_loader.py`, `api/src/services/execution/simple_worker.py`, `api/src/services/execution/process_pool.py` (1633 lines)
- Why fragile:
  - Multiple import mechanisms (direct, discovery, from workflow)
  - Dynamic code execution with eval/exec patterns
  - Complex context management (bifrost SDK, write buffer, execution context)
  - Process pool scaling with signal handling and graceful shutdown
  - Timeout management across multiple layers
- Safe modification: Ensure all test coverage exists before refactoring; integration tests critical
- Test coverage: Exists but concentrated in specific areas; gaps in edge cases

**Process Pool Manager - Concurrent Process Management:**
- Files: `api/src/services/execution/process_pool.py` (1633 lines)
- Why fragile:
  - Multiple asyncio loops coordinating with multiprocessing queues
  - Timeout handling with SIGTERM -> SIGKILL escalation
  - Process crash detection and replacement
  - Heartbeat publishing while managing pool lifecycle
- Safe modification: Changes to process lifecycle must be tested with timeouts, crashes, and recovery scenarios
- Test coverage: `api/tests/unit/execution/test_process_pool.py` (966 lines) - good coverage but async edge cases possible

**OAuth Token Refresh with Retry Logic:**
- Files: `api/src/services/oauth_provider.py`, `api/src/routers/oauth_connections.py`
- Why fragile:
  - Exponential backoff retry logic (1s, 2s, 4s)
  - Different handling for client_credentials vs authorization_code flows
  - Token URL template placeholder substitution ({entity_id})
  - Both sync and async paths
- Safe modification: Test with various OAuth providers; ensure retry logic doesn't exceed request timeouts
- Test coverage: Unit tests exist; integration with real OAuth providers needed

**Distributed Lock Service:**
- Files: `api/src/core/locks.py` (130+ lines)
- Why fragile:
  - Default TTL of 300 seconds (5 minutes) may be too long or too short depending on operation
  - No automatic retry on lock acquisition failure
  - Simple Redis implementation without Redlock algorithm
- Safe modification: Test lock contention scenarios; verify TTL is appropriate for all uses
- Test coverage: Used for upload locking; no dedicated unit tests visible

## Scaling Limits

**Process Pool Static Worker Count:**
- Current capacity: min_workers, max_workers configured statically in ProcessPoolManager
- Limit: No automatic scaling based on load; requires manual configuration
- Impact: Cannot handle variable execution demand without restart
- Scaling path: Implement dynamic scaling based on queue depth and execution time metrics

**Redis-Based Caching and PubSub:**
- Current capacity: Single Redis instance (appears to be localhost in dev)
- Limit: Single point of failure; no replication visible in config
- Impact: Any Redis outage stops execution tracking, caching, and websocket broadcasts
- Scaling path: Implement Redis Sentinel or Cluster; add circuit breaker for degraded Redis

**PostgreSQL Session Connection Pool:**
- Current capacity: Not explicitly configured in visible code; using SQLAlchemy defaults
- Limit: Database connection pool exhaustion during concurrent execution spikes
- Impact: Cascading failures when database connections exhausted
- Scaling path: Explicit pool size configuration, connection pooling middleware (pgbouncer), query optimization

**File Storage Operations on Large Modules:**
- Current capacity: ~512MB scheduler memory limit observed in tests with 4MB+ modules
- Limit: Sequential large file writes cause peak memory at 400MB+ (near OOM)
- Impact: Writing 10+ large files in succession can trigger OOM, especially with AST parsing
- Scaling path: Implement streaming writes, defer AST parsing, chunked file uploads

## Dependencies at Risk

**Datetime/Timezone Handling Across Database:**
- Risk: Ongoing migration from timestamptz to timestamp (naive UTC) creates temporary inconsistency window
- Impact: Mixed datetime formats in database until all code updated consistently
- Migration status: Active (migration created 2026-01-30)
- Timeline: Critical to complete migration and audit all datetime operations

**Process Isolation via Multiprocessing:**
- Risk: Python multiprocessing behavior differs across OS (Windows vs Linux/Mac); pickling unreliable for complex objects
- Impact: Process pool may not work correctly on all platforms
- Current handling: subprocess-based worker fallback available
- Mitigation needed: Platform-specific testing; consider ProcessPoolExecutor fallback

## Missing Critical Features

**System Logging Infrastructure:**
- Feature gap: No persistent system logging; /api/logs endpoints are stubs
- Blocks: Cannot audit user actions, troubleshoot system issues, or provide admin visibility
- Impact: Compliance and debugging severely limited
- Implementation status: Not started (ticket in backlog)

**Webhook Custom Adapter Discovery:**
- Feature gap: Cannot load custom adapters from database at runtime
- Blocks: Customer-specific webhook handlers cannot be deployed without code changes
- Implementation status: Scaffolding in place; database loading not implemented

**Dynamic Integration Context Loading:**
- Feature gap: Events handler cannot fetch integration context for webhook processing
- Blocks: Webhooks requiring integration credentials cannot execute properly
- Implementation status: TODO comment exists; implementation pending

## Test Coverage Gaps

**Webhook Custom Adapters - Runtime Loading:**
- What's not tested: Dynamic discovery and loading of workspace file adapters
- Files: `api/src/services/webhooks/registry.py`
- Risk: New custom adapters won't work without explicit testing
- Priority: High - affects production webhook execution

**Event Routing to Workflows:**
- What's not tested: Workflow existence validation before routing events
- Files: `api/src/routers/events.py`
- Risk: Events silently dropped for non-existent workflows without error visibility
- Priority: High - affects event-driven automation reliability

**Large File Memory Behavior Under Load:**
- What's not tested: Concurrent large file writes; sequential writes in specific order
- Files: `api/tests/integration/platform/test_large_file_memory.py`
- Risk: OOM conditions not detected until production; test marked xfail
- Priority: High - potential production outages

**Execution Engine Timeout Handling:**
- What's not tested: Multiple timeout paths (process pool timeout, execution timeout, worker timeout)
- Files: `api/src/services/execution/engine.py`, `api/src/services/execution/process_pool.py`
- Risk: Hanging executions, incomplete cleanup, resource leaks
- Priority: High - core execution stability

**OAuth Token Refresh Failures:**
- What's not tested: Token refresh with various failure modes (network, invalid token, provider down)
- Files: `api/src/services/oauth_provider.py`
- Risk: Stale tokens, failed integrations without visibility
- Priority: Medium - affects integration stability

**Distributed Lock Contention:**
- What's not tested: Multiple concurrent lock acquisition attempts; lock timeout scenarios
- Files: `api/src/core/locks.py`
- Risk: Race conditions in upload operations, orphaned locks
- Priority: Medium - affects data consistency

---

*Concerns audit: 2026-01-30*
