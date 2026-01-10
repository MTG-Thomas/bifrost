# Phase 3: Init Container Integration

Update `api/src/init_container.py` to warm requirements cache on startup.

## Overview

The init container runs once before API and workers start. It currently:
1. Runs database migrations
2. Warms module cache from database

We need to add step 3: warm requirements cache.

## File: `api/src/init_container.py`

## Dependencies

- Phase 1 must be complete (`warm_requirements_cache()` function exists)

## Tasks

### Task 3.1: Add import
- [ ] Import `warm_requirements_cache` from `src.core.requirements_cache`

```python
from src.core.requirements_cache import warm_requirements_cache
```

### Task 3.2: Add requirements cache warming step
- [ ] Add step after module cache warming
- [ ] Call `await warm_requirements_cache()`
- [ ] Print status message

```python
# After module cache warming:

# Warm requirements cache
requirements_found = await warm_requirements_cache()
print(f"✓ Requirements cache: {'cached' if requirements_found else 'empty'}")
```

### Task 3.3: Update output format (optional)
- [ ] Ensure consistent output format with other steps
- [ ] Match existing print style (✓ prefix)

## Example Final Output

```
=== Bifrost Init Container ===
✓ Migrations: Applied
✓ Module cache: 42 modules loaded
✓ Requirements cache: cached
=== Init Complete ===
```

Or if no requirements.txt exists:

```
=== Bifrost Init Container ===
✓ Migrations: Applied
✓ Module cache: 42 modules loaded
✓ Requirements cache: empty
=== Init Complete ===
```

## Verification

```bash
# Manual test in dev environment
docker compose exec api python -m src.init_container

# Or run the full stack and check logs
./debug.sh
docker compose logs init-container
```

## Checklist

- [ ] Import statement added
- [ ] `warm_requirements_cache()` call added after module cache
- [ ] Output message matches existing style
- [ ] No type errors: `cd api && pyright src/init_container.py`
- [ ] No lint errors: `cd api && ruff check src/init_container.py`
- [ ] Manual test passes
