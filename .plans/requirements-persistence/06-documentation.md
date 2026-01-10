# Phase 6: Documentation

Update documentation to explain requirements.txt persistence behavior.

## Overview

Document:
1. How packages persist across restarts
2. Where requirements.txt is stored
3. How workers install on startup

## Tasks

### Task 6.1: Update AI coding documentation
- [x] Update `api/shared/docs/how-to-guides/local-dev/ai-coding.txt`
- [x] Add section about package persistence
- [x] Explain the flow: install → database → cache → worker startup

Add section similar to:

```
## Package Persistence

When you install packages via `/api/packages/install`, they persist across container restarts:

1. **During Installation:**
   - Package is installed to user site-packages
   - requirements.txt is updated in the database (`workspace_files` table)
   - Redis cache is updated for fast access
   - Workers are recycled to pick up new packages

2. **On Container Restart:**
   - Init container warms the Redis cache from database
   - Worker processes read requirements.txt from Redis on startup
   - Packages are installed before the worker starts accepting executions

3. **Storage Locations:**
   - Database: `workspace_files` table with `entity_type='requirements'`
   - Redis: `bifrost:requirements:content` key
   - Workers: User site-packages at `~/.local/lib/python3.11/site-packages`

This means your installed packages will survive:
- Worker restarts (via API or automatic)
- Container restarts
- Full stack restarts
```

### Task 6.2: Add inline code comments
- [x] Add comments in `requirements_cache.py` explaining the architecture
- [x] Add comments in `package_install.py` explaining persistence flow
- [x] Add comments in `simple_worker.py` explaining startup installation

Example comments to add:

```python
# In requirements_cache.py:
"""
Requirements.txt Persistence Architecture
========================================

This module is part of the package persistence system that ensures
installed packages survive container restarts.

Flow:
1. User installs package via /api/packages/install
2. package_install.py consumer calls save_requirements_to_db()
3. requirements.txt is stored in workspace_files table + Redis cache
4. On container restart, init_container.py calls warm_requirements_cache()
5. Worker processes call _install_requirements_from_cache_sync() at startup
6. pip install runs from cached requirements.txt

Related files:
- api/src/jobs/consumers/package_install.py - Saves after install
- api/src/init_container.py - Warms cache on startup
- api/src/services/execution/simple_worker.py - Installs on worker startup
"""
```

## Verification

```bash
# Check documentation is accessible
cat api/shared/docs/how-to-guides/local-dev/ai-coding.txt | grep -A 20 "Package Persistence"

# Verify code comments exist
grep -n "persistence" api/src/core/requirements_cache.py
```

## Checklist

- [x] AI coding documentation updated
- [x] Code comments added to `requirements_cache.py`
- [x] Code comments added to `package_install.py`
- [x] Code comments added to `simple_worker.py`
- [x] No broken links in documentation
