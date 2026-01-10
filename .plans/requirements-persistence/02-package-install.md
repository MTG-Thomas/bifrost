# Phase 2: Package Install Consumer Update

Update `api/src/jobs/consumers/package_install.py` to persist requirements.txt after successful installation.

## Overview

After a package is successfully installed, we need to:
1. Get the current requirements.txt content (from cache or empty)
2. Append/update the new package line
3. Save to database and update cache

## File: `api/src/jobs/consumers/package_install.py`

## Dependencies

- Phase 1 must be complete (`requirements_cache.py` module exists)

## Tasks

### Task 2.1: Add imports
- [ ] Import `get_requirements`, `save_requirements_to_db` from `src.core.requirements_cache`

```python
from src.core.requirements_cache import get_requirements, save_requirements_to_db
```

### Task 2.2: Create _get_current_requirements() helper
- [ ] Add `async _get_current_requirements(self) -> str` method to `PackageInstallConsumer`
- [ ] Read from Redis cache via `get_requirements()`
- [ ] Return content string or empty string if not found

```python
async def _get_current_requirements(self) -> str:
    """Get current requirements.txt content from cache."""
    cached = await get_requirements()
    if cached:
        return cached["content"]
    return ""
```

### Task 2.3: Create _append_package_to_requirements() helper
- [ ] Add static method `_append_package_to_requirements(current: str, package: str, version: str | None) -> str`
- [ ] Parse current requirements as lines
- [ ] Find existing line for this package (case-insensitive package name match)
- [ ] If exists, update the version; if not, append new line
- [ ] Return updated content string
- [ ] Ensure trailing newline

```python
@staticmethod
def _append_package_to_requirements(
    current: str, package: str, version: str | None
) -> str:
    """
    Append or update a package in requirements.txt content.

    Args:
        current: Current requirements.txt content
        package: Package name
        version: Optional version specifier

    Returns:
        Updated requirements.txt content
    """
    lines = current.strip().split("\n") if current.strip() else []
    package_lower = package.lower()
    package_spec = f"{package}=={version}" if version else package

    # Find and update existing entry, or append
    found = False
    for i, line in enumerate(lines):
        # Parse package name from line (handles ==, >=, etc.)
        line_package = line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
        if line_package.lower() == package_lower:
            lines[i] = package_spec
            found = True
            break

    if not found:
        lines.append(package_spec)

    # Filter empty lines and ensure trailing newline
    lines = [line for line in lines if line.strip()]
    return "\n".join(lines) + "\n" if lines else ""
```

### Task 2.4: Update process_message() to persist requirements
- [ ] After successful install (after `await send_completion("success", ...)`), add:
  - Only persist if a specific package was installed (not requirements.txt install)
  - Get current requirements
  - Append new package
  - Save to database
  - Send log message

```python
# In process_message(), after send_completion("success", ...) and before _mark_workers_for_recycle():

if package:
    # Persist to database for startup recovery
    try:
        current_requirements = await self._get_current_requirements()
        updated_requirements = self._append_package_to_requirements(
            current_requirements, package, version
        )
        await save_requirements_to_db(updated_requirements)
        await send_log("Saved requirements.txt to database")
    except Exception as e:
        logger.warning(f"Failed to persist requirements.txt: {e}")
        # Don't fail the install if persistence fails
```

### Task 2.5: Add integration tests
- [ ] Create or update `api/tests/integration/consumers/test_package_install.py`
- [ ] Test that installing a package saves to workspace_files
- [ ] Test that Redis cache is updated
- [ ] Test that installing same package twice updates version

Note: Integration tests may need to mock the actual pip install to avoid slow network calls.

```python
"""Integration tests for package_install consumer."""

import pytest
from sqlalchemy import select
from unittest.mock import AsyncMock, patch

from src.jobs.consumers.package_install import PackageInstallConsumer
from src.models.orm.workspace import WorkspaceFile
from src.core.requirements_cache import get_requirements


class TestPackageInstallPersistence:
    @pytest.fixture
    def consumer(self):
        return PackageInstallConsumer()

    def test_append_package_to_requirements_empty(self, consumer):
        """Test appending to empty requirements."""
        result = consumer._append_package_to_requirements("", "flask", "2.3.0")
        assert result == "flask==2.3.0\n"

    def test_append_package_to_requirements_existing(self, consumer):
        """Test appending to existing requirements."""
        current = "requests==2.31.0\n"
        result = consumer._append_package_to_requirements(current, "flask", "2.3.0")
        assert "requests==2.31.0" in result
        assert "flask==2.3.0" in result

    def test_append_package_updates_existing(self, consumer):
        """Test updating existing package version."""
        current = "flask==2.0.0\nrequests==2.31.0\n"
        result = consumer._append_package_to_requirements(current, "flask", "2.3.0")
        assert "flask==2.3.0" in result
        assert "flask==2.0.0" not in result
        assert "requests==2.31.0" in result

    def test_append_package_without_version(self, consumer):
        """Test appending package without version."""
        result = consumer._append_package_to_requirements("", "flask", None)
        assert result == "flask\n"

    @pytest.mark.asyncio
    async def test_process_message_persists_requirements(
        self, consumer, db_session, mock_redis
    ):
        """Test that process_message saves requirements to database."""
        # Mock pip install to succeed
        with patch.object(consumer, "_install_package", new_callable=AsyncMock):
            with patch("src.core.pubsub.manager.broadcast", new_callable=AsyncMock):
                await consumer.process_message({
                    "job_id": "test-job",
                    "package": "humanize",
                    "version": "4.0.0",
                })

        # Verify database record
        stmt = select(WorkspaceFile).where(
            WorkspaceFile.path == "requirements.txt",
            WorkspaceFile.entity_type == "requirements",
        )
        result = await db_session.execute(stmt)
        file = result.scalar_one_or_none()

        assert file is not None
        assert "humanize==4.0.0" in file.content

        # Verify Redis cache
        cached = await get_requirements()
        assert cached is not None
        assert "humanize==4.0.0" in cached["content"]
```

## Verification

```bash
./test.sh tests/integration/consumers/test_package_install.py -v

# Also run unit tests to verify helper methods
./test.sh tests/unit/ -k "package_install" -v
```

## Checklist

- [ ] Import statements added
- [ ] `_get_current_requirements()` method implemented
- [ ] `_append_package_to_requirements()` method implemented
- [ ] `process_message()` updated to persist requirements
- [ ] Integration tests created/updated
- [ ] All tests passing
- [ ] No type errors: `cd api && pyright src/jobs/consumers/package_install.py`
- [ ] No lint errors: `cd api && ruff check src/jobs/consumers/package_install.py`
