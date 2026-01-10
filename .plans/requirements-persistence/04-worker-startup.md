# Phase 4: Worker Startup Package Installation

Update `api/src/services/execution/simple_worker.py` to install packages from Redis cache at startup.

## Overview

Worker processes need to install packages from the cached requirements.txt when they start. This ensures packages persist across container restarts.

Key considerations:
- Worker startup is synchronous (before event loop)
- Must use sync Redis client
- Must handle Redis unavailability gracefully
- Should log clearly for debugging

## File: `api/src/services/execution/simple_worker.py`

## Dependencies

- Phase 1 must be complete (requirements stored in Redis)
- Phase 3 should be complete (cache warmed on container start)

## Tasks

### Task 4.1: Create _install_requirements_from_cache_sync() function
- [ ] Add function at module level (not inside run_worker_process)
- [ ] Use synchronous Redis client (redis.Redis, not async)
- [ ] Fetch from `bifrost:requirements:content` key
- [ ] Parse JSON to get content
- [ ] Write content to temp file
- [ ] Run `pip install -r <temp_file>` via subprocess
- [ ] Clean up temp file
- [ ] Log success/failure
- [ ] Handle all exceptions gracefully (don't crash worker)

```python
def _install_requirements_from_cache_sync(worker_id: str) -> None:
    """
    Install packages from cached requirements.txt.

    Called at worker startup to ensure packages persist across restarts.
    Uses synchronous Redis client since we're not in async context.

    This function never raises - failures are logged and worker continues.
    """
    import json
    import os
    import subprocess
    import sys
    import tempfile
    import time

    import redis

    # Get Redis URL from environment
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    # Retry logic for Redis connection
    max_retries = 3
    retry_delay = 1.0

    for attempt in range(max_retries):
        try:
            # Connect to Redis (sync client)
            client = redis.from_url(redis_url, decode_responses=True, socket_timeout=5.0)

            # Fetch cached requirements
            data = client.get("bifrost:requirements:content")
            client.close()

            if not data:
                logger.info(f"[{worker_id}] No cached requirements.txt found")
                return

            cached = json.loads(data)
            content = cached.get("content", "")

            if not content.strip():
                logger.info(f"[{worker_id}] Cached requirements.txt is empty")
                return

            # Write to temp file and install
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                logger.info(f"[{worker_id}] Installing packages from cached requirements.txt")

                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-r", temp_path, "--quiet"],
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5 minute timeout
                )

                if result.returncode == 0:
                    # Count packages
                    pkg_count = len([line for line in content.strip().split("\n") if line.strip()])
                    logger.info(f"[{worker_id}] Installed {pkg_count} packages from requirements.txt")
                else:
                    logger.warning(
                        f"[{worker_id}] pip install failed: {result.stderr or result.stdout}"
                    )
            finally:
                # Clean up temp file
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

            return  # Success, exit retry loop

        except redis.ConnectionError as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"[{worker_id}] Redis connection failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                time.sleep(retry_delay)
            else:
                logger.warning(f"[{worker_id}] Redis unavailable after {max_retries} attempts, skipping requirements install")

        except json.JSONDecodeError as e:
            logger.warning(f"[{worker_id}] Invalid JSON in cached requirements: {e}")
            return

        except subprocess.TimeoutExpired:
            logger.warning(f"[{worker_id}] pip install timed out after 5 minutes")
            return

        except Exception as e:
            logger.warning(f"[{worker_id}] Failed to install requirements: {e}")
            return
```

### Task 4.2: Update run_worker_process() startup sequence
- [ ] Call `_install_requirements_from_cache_sync(worker_id)` after setting up user site-packages
- [ ] Must be before installing virtual import hook
- [ ] Add clear log message indicating startup phase

```python
def run_worker_process(
    work_queue: Queue,
    result_queue: Queue,
    worker_id: str,
) -> None:
    # ... existing logging setup ...

    # Ensure user site-packages is in sys.path
    # ... existing code ...

    # Install packages from cached requirements.txt
    # This ensures packages persist across container restarts
    _install_requirements_from_cache_sync(worker_id)

    # Install virtual import hook FIRST (before any workspace imports)
    from src.services.execution.virtual_import import install_virtual_import_hook
    install_virtual_import_hook()

    # ... rest of function ...
```

### Task 4.3: Add retry logic documentation
- [ ] Document retry behavior in function docstring
- [ ] Explain why we continue on failure (graceful degradation)

### Task 4.4: Add unit tests
- [ ] Create tests in `api/tests/unit/execution/test_simple_worker.py`
- [ ] Test successful install from cache
- [ ] Test graceful handling when Redis unavailable
- [ ] Test graceful handling when cache is empty
- [ ] Mock Redis and subprocess to avoid actual network/pip calls

```python
"""Unit tests for simple_worker requirements installation."""

import json
import pytest
from unittest.mock import MagicMock, patch, call

from src.services.execution.simple_worker import _install_requirements_from_cache_sync


class TestInstallRequirementsFromCache:
    @patch("src.services.execution.simple_worker.subprocess.run")
    @patch("src.services.execution.simple_worker.redis.from_url")
    def test_installs_from_cache(self, mock_redis_factory, mock_subprocess):
        """Test successful installation from cached requirements."""
        # Setup mock Redis
        mock_client = MagicMock()
        cached = {"content": "flask==2.3.0\nrequests==2.31.0\n", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client

        # Setup mock subprocess
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Run
        _install_requirements_from_cache_sync("test-worker")

        # Verify pip was called
        assert mock_subprocess.called
        call_args = mock_subprocess.call_args
        assert "-m" in call_args[0][0]
        assert "pip" in call_args[0][0]
        assert "install" in call_args[0][0]
        assert "-r" in call_args[0][0]

    @patch("src.services.execution.simple_worker.redis.from_url")
    def test_handles_redis_unavailable(self, mock_redis_factory):
        """Test graceful handling when Redis is unavailable."""
        import redis

        mock_redis_factory.side_effect = redis.ConnectionError("Connection refused")

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

    @patch("src.services.execution.simple_worker.redis.from_url")
    def test_handles_empty_cache(self, mock_redis_factory):
        """Test handling when cache is empty."""
        mock_client = MagicMock()
        mock_client.get.return_value = None
        mock_redis_factory.return_value = mock_client

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")

    @patch("src.services.execution.simple_worker.redis.from_url")
    def test_handles_empty_content(self, mock_redis_factory):
        """Test handling when cached content is empty."""
        mock_client = MagicMock()
        cached = {"content": "", "hash": "abc123"}
        mock_client.get.return_value = json.dumps(cached)
        mock_redis_factory.return_value = mock_client

        # Should not raise
        _install_requirements_from_cache_sync("test-worker")
```

## Verification

```bash
# Run unit tests
./test.sh tests/unit/execution/test_simple_worker.py -v

# Manual verification in dev:
# 1. Install a package via API
# 2. Restart workers: docker compose restart worker
# 3. Check worker logs for "Installing packages from cached requirements.txt"
# 4. Run an execution that uses the package
```

## Checklist

- [ ] `_install_requirements_from_cache_sync()` function created
- [ ] Function called in `run_worker_process()` at correct location
- [ ] Retry logic implemented (3 attempts, 1s delay)
- [ ] All exceptions handled gracefully
- [ ] Unit tests created and passing
- [ ] No type errors: `cd api && pyright src/services/execution/simple_worker.py`
- [ ] No lint errors: `cd api && ruff check src/services/execution/simple_worker.py`
- [ ] Manual verification in dev environment
