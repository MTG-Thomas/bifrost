# Requirements.txt Persistence for Worker Startup

Store requirements.txt in the database and cache in Redis so workers can install packages on startup, ensuring package persistence across container restarts.

## Problem

When containers restart, installed packages are lost because:
1. Packages are installed to user site-packages (`~/.local/lib/python3.11/site-packages`)
2. This directory is ephemeral (not in the container image)
3. No startup installation mechanism exists

## Solution Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          PACKAGE INSTALL FLOW                            │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. User installs package via /api/packages/install                      │
│                        ↓                                                 │
│  2. pip install runs (package goes to site-packages)                     │
│                        ↓                                                 │
│  3. Read requirements.txt from workspace_files (or create empty)         │
│                        ↓                                                 │
│  4. Append new package to requirements.txt content                       │
│                        ↓                                                 │
│  5. Save updated requirements.txt to workspace_files table               │
│                        ↓                                                 │
│  6. Update Redis cache: bifrost:requirements:content                     │
│                        ↓                                                 │
│  7. Mark workers for recycle (existing behavior)                         │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────┐
│                           STARTUP FLOW                                    │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Init Container (runs once before API/workers)                           │
│  1. alembic upgrade head (migrations)                                    │
│  2. warm_cache_from_db() (modules → Redis)                               │
│  3. warm_requirements_cache() (requirements.txt → Redis) ← NEW           │
│                                                                          │
│  Worker Process Startup (simple_worker.py)                               │
│  1. Setup user site-packages in sys.path                                 │
│  2. Install requirements from Redis cache ← NEW                          │
│  3. Install virtual import hook                                          │
│  4. Start main execution loop                                            │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

## Data Model

### WorkspaceFile Record
```python
WorkspaceFile(
    path="requirements.txt",
    entity_type="requirements",  # New entity type
    content="flask==2.3.0\nrequests==2.31.0\n...",
    content_hash="sha256:abc123...",
    size_bytes=1234,
    content_type="text/plain",
    git_status=GitStatus.UNTRACKED,
    is_deleted=False,
)
```

### Redis Cache
```
Key: bifrost:requirements:content
Value: {"content": "flask==2.3.0\n...", "hash": "sha256:abc123"}
TTL: 86400 (24 hours)
```

## Plan Files

| Phase | File | Description | Status |
|-------|------|-------------|--------|
| 1 | [01-requirements-cache.md](./01-requirements-cache.md) | Create requirements_cache.py module | ⬜ TODO |
| 2 | [02-package-install.md](./02-package-install.md) | Update package_install.py consumer | ⬜ TODO |
| 3 | [03-init-container.md](./03-init-container.md) | Update init_container.py | ⬜ TODO |
| 4 | [04-worker-startup.md](./04-worker-startup.md) | Update simple_worker.py startup | ⬜ TODO |
| 5 | [05-e2e-testing.md](./05-e2e-testing.md) | Add E2E tests | ⬜ TODO |
| 6 | [06-documentation.md](./06-documentation.md) | Update documentation | ⬜ TODO |

## Files Summary

| File | Action | Description |
|------|--------|-------------|
| `api/src/core/requirements_cache.py` | CREATE | Redis caching for requirements.txt |
| `api/src/jobs/consumers/package_install.py` | MODIFY | Persist requirements after install |
| `api/src/init_container.py` | MODIFY | Warm requirements cache on startup |
| `api/src/services/execution/simple_worker.py` | MODIFY | Install from cache at worker startup |
| `api/tests/unit/core/test_requirements_cache.py` | CREATE | Unit tests for cache module |
| `api/tests/integration/consumers/test_package_install.py` | MODIFY | Integration tests for persistence |
| `api/tests/e2e/api/test_executions.py` | MODIFY | E2E tests for full flow |

## Verification Commands

```bash
# Phase 1: Requirements cache module
./test.sh tests/unit/core/test_requirements_cache.py -v

# Phase 2: Package install consumer
./test.sh tests/integration/consumers/test_package_install.py -v

# Phase 3: Init container (manual)
docker compose exec api python -m src.init_container

# Phase 4: Worker startup
./test.sh tests/unit/execution/test_simple_worker.py -v

# Phase 5: E2E tests
./test.sh tests/e2e/api/test_executions.py::TestCodeHotReload -v

# Full test suite
./test.sh --e2e
```

## Dependencies

- Phase 2 depends on Phase 1 (needs cache module)
- Phase 3 depends on Phase 1 (needs warm function)
- Phase 4 depends on Phase 1 (needs get function)
- Phase 5 depends on all previous phases

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Redis unavailable at worker startup | Graceful degradation: continue without cached packages |
| Race condition on requirements.txt update | Use upsert with content hash comparison |
| pip install fails silently | Log errors, continue startup, packages will be installed on next explicit install |
| Large requirements.txt | TTL ensures cache refresh; pip handles duplicates gracefully |
