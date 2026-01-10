# Test Skip Cleanup Plan

## Overview

Review of all skipped tests identified **89 skips** total. Of these, **55 are fixable issues** in our code (not genuine limitations), and **34 are legitimate** skips for external dependencies or platform differences.

---

## Phase 1: Remove Redundant Websocket Import Checks

**Impact:** 33 tests currently skip unnecessarily

### Context
Every websocket test has this pattern:
```python
try:
    from websockets.asyncio.client import connect
except ImportError:
    pytest.skip("websockets library not installed")
```

But `websockets` is already in `requirements.txt` (line 97). If the import fails, our environment is broken and we should fix it, not skip.

### Tasks

- [ ] **1.1** `api/tests/e2e/api/test_websocket.py` - Remove all `ImportError` try/except skip blocks
  - Lines: 32-35, 62-65, 96-99, 133-136, 167-170, 256-259, 324-327, 370-373, 400-403, 434-437, 471-474, 507-510, 547-550
  - Keep the actual test logic, just remove the defensive import checks

- [ ] **1.2** `api/tests/e2e/api/test_coding_mode.py` - Remove all `ImportError` try/except skip blocks
  - Lines: 60-63, 96-99
  - Same pattern as websocket tests

### Verification
```bash
./test.sh api/tests/e2e/api/test_websocket.py api/tests/e2e/api/test_coding_mode.py -v
```

---

## Phase 2: Remove Defensive Workflow Checks

**Impact:** 16 tests currently skip unnecessarily

### Context
The workflow fixtures have assertions that fail if discovery doesn't work:
```python
# In fixture:
assert workflow is not None, "Workflow not discovered after file write"
```

But tests still defensively check:
```python
# In test:
if not sync_workflow["id"]:
    pytest.skip("Sync workflow not discovered")
```

This is redundant - if the fixture passes, the workflow exists. If discovery fails, let it fail loudly.

### Tasks

- [ ] **2.1** `api/tests/e2e/api/test_executions.py` - Remove all defensive workflow ID checks
  - `test_execute_sync_workflow` (line 109-110)
  - `test_sync_execution_returns_result` (line 131-132)
  - `test_execute_async_workflow` (line 157-158)
  - `test_async_execution_eventually_completes` (line 175-176)
  - `test_cancel_running_workflow` (line 303-304)
  - `test_cancel_execution_returns_cancelled_status` (similar pattern)
  - All `TestExecutionPagination` tests (6 tests)
  - All `TestExecutionAsyncOperation` tests (2 tests)
  - `TestExecutionConcurrency::test_concurrent_executions_not_blocking` (line 727)

### Verification
```bash
./test.sh api/tests/e2e/api/test_executions.py -v
```

---

## Phase 3: Fix Temp Directory Test Skips

**Impact:** 5 tests currently skip on HTTP 500

### Context
CLI tests skip when the API returns 500:
```python
if response.status_code == 500:
    pytest.skip("Temp directory not available in test environment")
```

A 500 error indicates a server bug, not a skip condition. The temp directory should work in tests.

### Tasks

- [ ] **3.1** Investigate temp directory configuration in test environment
  - Check `docker-compose.test.yml` volume mounts
  - Verify `api/src/core/paths.py` temp directory initialization
  - Ensure temp directory exists and is writable in test containers

- [ ] **3.2** `api/tests/e2e/api/test_cli.py` - Remove all 500 → skip handling
  - `test_create_file_via_cli` (line 135-136)
  - `test_list_files` (line 179-180, 193-194)
  - `test_delete_file` (line 226-227, 236-237)

### Verification
```bash
./test.sh api/tests/e2e/api/test_cli.py -v
```

---

## Phase 4: Fix Event Delivery Timeout Skip

**Impact:** 1 test skips when it should fail or pass

### Context
```python
result = poll_until(find_event_with_deliveries, max_wait=5.0)
if result is None:
    pytest.skip("No deliveries created within timeout")
```

This test verifies event delivery works. If deliveries aren't created, that's either a real bug or the timeout is too short.

### Tasks

- [ ] **4.1** Investigate event delivery timing in `api/tests/e2e/api/test_events.py`
  - Check `test_retry_delivery_with_pending` (line 956-957)
  - Determine if 5 seconds is sufficient or if event delivery is broken
  - Either increase timeout to 15-30 seconds, or fix underlying delivery issue

- [ ] **4.2** Remove the skip - let the test fail if delivery doesn't work

### Verification
```bash
./test.sh api/tests/e2e/api/test_events.py::TestEventDeliveryRetry -v
```

---

## Legitimate Skips (DO NOT CHANGE)

These skips are correct and should remain:

| File | Test/Fixture | Reason |
|------|-------------|--------|
| `test_llm_config.py` | All 6 tests | Requires `ANTHROPIC_API_TEST_KEY`, `OPENAPI_API_TEST_KEY` |
| `llm_setup.py` | 2 fixtures | Same API key requirements |
| `github_setup.py` | 4 fixtures | Requires `GITHUB_TEST_PAT` and `PyGithub` |
| `knowledge_setup.py` | 1 fixture | Requires `EMBEDDINGS_API_TEST_KEY` |
| `test_github.py` | `test_create_repository` | Creates real GitHub repos - manual only |
| `test_sdk_credentials.py` | 2 tests | Unix permissions not applicable on Windows |
| `test_executions.py` | `test_concurrent_executions_not_blocking` | Timing-sensitive, flaky in CI |

---

## Summary

| Phase | Tests Fixed | Effort |
|-------|-------------|--------|
| Phase 1 | 33 websocket tests | Low - just remove try/except blocks |
| Phase 2 | 16 execution tests | Low - just remove if checks |
| Phase 3 | 5 CLI tests | Medium - needs infrastructure check |
| Phase 4 | 1 event test | Medium - needs investigation |
| **Total** | **55 tests** | |

---

## Final Verification

After all phases complete:
```bash
# Run all E2E tests - should have no unexpected skips
./test.sh --e2e -v 2>&1 | grep -E "SKIPPED|PASSED|FAILED"

# Expected skips (legitimate):
# - 6 LLM config tests (API keys)
# - 1 GitHub create repo (manual)
# - 2 permission tests (Windows only)
# - 1 concurrent test (CI only)
# - ~10 fixtures for external services
```
