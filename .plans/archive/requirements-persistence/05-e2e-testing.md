# Phase 5: E2E Testing

Add end-to-end tests for the complete requirements persistence flow.

## Overview

Test the full flow:
1. Install a package via API
2. Verify it's stored in database
3. Verify it works in execution
4. Recycle workers (simulate restart)
5. Verify package still works (installed from cache)

## File: `api/tests/e2e/api/test_executions.py`

## Dependencies

- All previous phases must be complete

## Tasks

### Task 5.1: Add test_package_persists_across_worker_restart
- [ ] Add test in `TestCodeHotReload` class
- [ ] Install a package (e.g., `humanize`)
- [ ] Verify package works in execution
- [ ] Recycle all workers via API
- [ ] Wait for new workers to spawn
- [ ] Run execution again with fresh workers
- [ ] Verify package still works

```python
@pytest.mark.asyncio
async def test_package_persists_across_worker_restart(
    self,
    api_client: TestClient,
    create_test_workflow,
    wait_for_execution,
):
    """
    Test that packages persist across worker restarts via requirements.txt caching.

    Flow:
    1. Install package
    2. Run execution using package
    3. Recycle all workers
    4. Run execution again - package should still work
    """
    # Create workflow that uses humanize package
    workflow_code = '''
from bifrost.decorators import workflow

@workflow
def test_humanize():
    import humanize
    return {"result": humanize.intcomma(1000000)}
'''
    workflow = await create_test_workflow(code=workflow_code)

    # Install humanize package
    install_response = api_client.post(
        "/api/packages/install",
        json={"package": "humanize", "version": "4.9.0"},
    )
    assert install_response.status_code == 200

    # Wait for package installation to complete
    await asyncio.sleep(5)

    # Run execution - should work
    exec_response = api_client.post(
        f"/api/workflows/{workflow['id']}/execute",
        json={},
    )
    assert exec_response.status_code == 200
    execution = exec_response.json()

    result = await wait_for_execution(execution["id"])
    assert result["status"] == "Success"
    assert result["result"]["result"] == "1,000,000"

    # Recycle all workers via diagnostics API
    recycle_response = api_client.post("/api/platform/workers/recycle-all")
    assert recycle_response.status_code == 200

    # Wait for workers to restart and install from cache
    await asyncio.sleep(10)

    # Run execution again - should still work with fresh workers
    exec_response2 = api_client.post(
        f"/api/workflows/{workflow['id']}/execute",
        json={},
    )
    assert exec_response2.status_code == 200
    execution2 = exec_response2.json()

    result2 = await wait_for_execution(execution2["id"])
    assert result2["status"] == "Success", f"Expected Success, got {result2['status']}: {result2.get('error_message')}"
    assert result2["result"]["result"] == "1,000,000"
```

### Task 5.2: Add test_requirements_stored_in_database
- [ ] Add test to verify database storage
- [ ] Install a package
- [ ] Query workspace_files table directly
- [ ] Verify requirements.txt record exists with correct content

```python
@pytest.mark.asyncio
async def test_requirements_stored_in_database(
    self,
    api_client: TestClient,
    db_session,
):
    """Test that installing a package creates/updates requirements.txt in database."""
    from sqlalchemy import select
    from src.models.orm.workspace import WorkspaceFile

    # Install a package
    install_response = api_client.post(
        "/api/packages/install",
        json={"package": "humanize", "version": "4.9.0"},
    )
    assert install_response.status_code == 200

    # Wait for installation to complete
    await asyncio.sleep(5)

    # Query database for requirements.txt
    stmt = select(WorkspaceFile).where(
        WorkspaceFile.path == "requirements.txt",
        WorkspaceFile.entity_type == "requirements",
        WorkspaceFile.is_deleted == False,
    )
    result = await db_session.execute(stmt)
    file = result.scalar_one_or_none()

    assert file is not None, "requirements.txt should be stored in database"
    assert file.content is not None, "requirements.txt should have content"
    assert "humanize==4.9.0" in file.content, "requirements.txt should contain installed package"
    assert file.content_hash is not None, "requirements.txt should have content hash"
```

### Task 5.3: Add test_requirements_cached_in_redis
- [ ] Add test to verify Redis cache
- [ ] Install a package
- [ ] Check Redis directly for cached content

```python
@pytest.mark.asyncio
async def test_requirements_cached_in_redis(
    self,
    api_client: TestClient,
):
    """Test that installing a package updates Redis cache."""
    from src.core.requirements_cache import get_requirements

    # Install a package
    install_response = api_client.post(
        "/api/packages/install",
        json={"package": "humanize", "version": "4.9.0"},
    )
    assert install_response.status_code == 200

    # Wait for installation to complete
    await asyncio.sleep(5)

    # Check Redis cache
    cached = await get_requirements()
    assert cached is not None, "requirements.txt should be cached in Redis"
    assert "humanize==4.9.0" in cached["content"], "Redis cache should contain installed package"
```

## Verification

```bash
# Run the E2E tests
./test.sh tests/e2e/api/test_executions.py::TestCodeHotReload::test_package_persists_across_worker_restart -v
./test.sh tests/e2e/api/test_executions.py::TestCodeHotReload::test_requirements_stored_in_database -v
./test.sh tests/e2e/api/test_executions.py::TestCodeHotReload::test_requirements_cached_in_redis -v

# Or run all hot reload tests
./test.sh tests/e2e/api/test_executions.py::TestCodeHotReload -v
```

## Checklist

- [ ] `test_package_persists_across_worker_restart` added
- [ ] `test_requirements_stored_in_database` added
- [ ] `test_requirements_cached_in_redis` added
- [ ] All E2E tests passing
- [ ] Tests run in CI (if applicable)
