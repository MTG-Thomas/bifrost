"""Tests for worker loading code from S3 via Redis cache."""

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(autouse=True)
def preserve_virtual_import_state():
    """Importing the worker must not leak its global finder across unit tests."""
    from src.services.execution import virtual_import

    original_meta_path = list(sys.meta_path)
    original_finder = virtual_import.get_virtual_finder()
    try:
        yield
    finally:
        virtual_import.remove_virtual_import_hook()
        sys.meta_path[:] = original_meta_path
        virtual_import._finder = original_finder  # noqa: SLF001


class TestGetModuleSyncFromCache:
    """Test get_module_sync returns cached modules from Redis."""

    @pytest.fixture(autouse=True)
    def stable_workspace_generation(self, monkeypatch):
        monkeypatch.setattr(
            "src.core.module_cache_sync.workspace_generation_for_import",
            lambda: "generation-1",
        )

    def test_worker_loads_code_from_redis_cache(self):
        """Worker should load workflow code from Redis cache using path."""
        from src.core.module_cache_sync import get_module_sync

        with patch(
            "src.core.module_cache_sync._get_sync_redis"
        ) as mock_redis_factory:
            content = "from bifrost import workflow\n@workflow\ndef test(): return {}"
            content_hash = hashlib.sha256(content.encode()).hexdigest()
            cached = json.dumps(
                {
                    "content": content,
                    "path": "workflows/test.py",
                    "hash": content_hash,
                    "generation": "generation-1",
                }
            )
            mock_redis = MagicMock()
            mock_redis.get.return_value = cached
            mock_redis_factory.return_value = mock_redis

            result = get_module_sync("workflows/test.py")
            assert result is not None
            assert result["content"] == content
            assert result["path"] == "workflows/test.py"
            assert result["hash"] == content_hash

    def test_worker_batch_loads_cached_modules_in_one_round_trip(self):
        """Worker hash validation should batch Redis reads for loaded modules."""
        from src.core.module_cache_sync import get_modules_sync

        first_content = "x = 1"
        second_content = "x = 2"
        first = {
            "content": first_content,
            "path": "modules/a.py",
            "hash": hashlib.sha256(first_content.encode()).hexdigest(),
            "generation": "generation-1",
        }
        second = {
            "content": second_content,
            "path": "modules/b.py",
            "hash": hashlib.sha256(second_content.encode()).hexdigest(),
            "generation": "generation-1",
        }
        with patch(
            "src.core.module_cache_sync._get_sync_redis"
        ) as mock_redis_factory:
            mock_redis = MagicMock()
            mock_redis.mget.return_value = [json.dumps(first), json.dumps(second)]
            mock_redis_factory.return_value = mock_redis

            result = get_modules_sync(["modules/a.py", "modules/b.py"])

        assert result == {"modules/a.py": first, "modules/b.py": second}
        mock_redis.mget.assert_called_once_with(
            ["bifrost:module:modules/a.py", "bifrost:module:modules/b.py"]
        )
        mock_redis.get.assert_not_called()

    def test_cache_miss_returns_none(self):
        """When both Redis and S3 miss, should return None."""
        from src.core.module_cache_sync import get_module_sync

        with (
            patch(
                "src.core.module_cache_sync._get_sync_redis"
            ) as mock_redis_factory,
            patch("src.core.module_cache_sync._get_s3_module") as mock_s3,
        ):
            mock_redis = MagicMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis
            mock_s3.return_value = None

            result = get_module_sync("workflows/nonexistent.py")
            assert result is None

    def test_s3_fallback_on_redis_miss(self):
        """When Redis misses but S3 has the module, should return it and re-cache."""
        from src.core.module_cache_sync import get_module_sync

        code_content = "from bifrost import workflow\n@workflow\ndef run(): pass"
        code_bytes = code_content.encode("utf-8")

        with (
            patch(
                "src.core.module_cache_sync._get_sync_redis"
            ) as mock_redis_factory,
            patch("src.core.module_cache_sync._get_s3_module") as mock_s3,
        ):
            mock_redis = MagicMock()
            mock_redis.get.return_value = None  # Redis miss
            mock_redis_factory.return_value = mock_redis
            mock_s3.return_value = code_bytes  # S3 hit

            result = get_module_sync("workflows/s3_test.py")
            assert result is not None
            assert result["content"] == code_content
            assert result["path"] == "workflows/s3_test.py"
            # Verify it was cached back to Redis
            mock_redis.setex.assert_called_once()


class TestWorkerCodeLoadingBranches:
    """Test the worker's three-way branching for code loading."""

    @patch("src.services.execution.module_loader.load_workflow_from_db")
    def test_existing_path_uses_workflow_code(self, mock_load):
        """When workflow_code is provided, use it directly (backwards-compatible)."""
        mock_func = MagicMock()
        mock_metadata = {"name": "test"}
        mock_load.return_value = (mock_func, mock_metadata, None)

        # Simulate the branch logic from worker.py
        workflow_code = "def run(): pass"
        function_name = "run"
        file_path = "workflows/test.py"

        if workflow_code and function_name and file_path:
            from src.services.execution.module_loader import load_workflow_from_db

            result = load_workflow_from_db(
                code=workflow_code,
                path=file_path,
                function_name=function_name,
            )
            assert result[0] == mock_func
            assert result[2] is None  # no error

        mock_load.assert_called_once_with(
            code="def run(): pass",
            path="workflows/test.py",
            function_name="run",
        )

    @patch("src.core.module_cache_sync.get_module_sync")
    @patch("src.services.execution.module_loader.load_workflow_from_db")
    def test_cache_path_used_when_no_workflow_code(self, mock_load, mock_cache):
        """When workflow_code is None but file_path exists, try cache."""
        mock_func = MagicMock()
        mock_metadata = {"name": "test"}
        mock_load.return_value = (mock_func, mock_metadata, None)
        mock_cache.return_value = {
            "content": "from bifrost import workflow\n@workflow\ndef run(): pass",
            "path": "workflows/test.py",
            "hash": "abc123",
        }

        # Simulate the branch logic from worker.py
        workflow_code = None
        function_name = "run"
        file_path = "workflows/test.py"
        load_error = None

        if workflow_code and function_name and file_path:
            pytest.fail("Should not take the workflow_code branch")
        elif function_name and file_path:
            from src.core.module_cache_sync import get_module_sync
            from src.services.execution.module_loader import load_workflow_from_db

            cached = get_module_sync(file_path)
            if cached:
                workflow_func, metadata, load_error = load_workflow_from_db(
                    code=cached["content"],
                    path=file_path,
                    function_name=function_name,
                )
                assert workflow_func == mock_func
                assert load_error is None
            else:
                pytest.fail("Cache should have returned a result")
        else:
            pytest.fail("Should not take the else branch")

        mock_cache.assert_called_once_with("workflows/test.py")
        mock_load.assert_called_once_with(
            code="from bifrost import workflow\n@workflow\ndef run(): pass",
            path="workflows/test.py",
            function_name="run",
        )

    @patch("src.core.module_cache_sync.get_module_sync")
    def test_cache_miss_sets_no_workflow_func(self, mock_cache):
        """When cache also misses, workflow_func stays None (triggers error)."""
        mock_cache.return_value = None

        workflow_code = None
        function_name = "run"
        file_path = "workflows/test.py"
        workflow_func = None

        if workflow_code and function_name and file_path:
            pytest.fail("Should not take the workflow_code branch")
        elif function_name and file_path:
            from src.core.module_cache_sync import get_module_sync

            cached = get_module_sync(file_path)
            if cached:
                pytest.fail("Cache should not have returned a result")
            else:
                # This mirrors worker.py behavior: workflow_func stays None
                pass

        # workflow_func is still None, which would trigger the error path in worker.py
        assert workflow_func is None

    def test_missing_fields_falls_to_else_branch(self):
        """When both function_name and file_path are missing, falls to else."""
        workflow_code = None
        function_name = None
        file_path = None
        reached_else = False

        assert workflow_code is None
        assert function_name is None
        assert file_path is None

        if workflow_code and function_name and file_path:
            pytest.fail("Should not take the workflow_code branch")
        elif function_name and file_path:
            pytest.fail("Should not take the cache branch")
        else:
            reached_else = True

        assert reached_else

    def test_generation_is_validated_before_entry_source_loading(self):
        from src.core.module_cache_sync import WorkspaceGenerationChangedError
        from src.services.execution.worker import _load_workspace_workflow

        with (
            patch(
                "src.core.module_cache_sync.assert_workspace_generation",
                side_effect=WorkspaceGenerationChangedError("stale"),
            ) as validate,
            patch("src.core.module_cache_sync.get_module_sync") as get_module,
            patch(
                "src.services.execution.module_loader.load_workflow_from_db"
            ) as load_workflow,
        ):
            with pytest.raises(WorkspaceGenerationChangedError, match="stale"):
                _load_workspace_workflow(
                    file_path="workflows/test.py",
                    function_name="run",
                    workspace_generation="generation-1",
                )

        validate.assert_called_once_with("generation-1")
        get_module.assert_not_called()
        load_workflow.assert_not_called()

    def test_generation_is_validated_after_entry_source_loading(self):
        from src.core.module_cache_sync import WorkspaceGenerationChangedError
        from src.services.execution.worker import _load_workspace_workflow

        with (
            patch(
                "src.core.module_cache_sync.assert_workspace_generation",
                side_effect=[None, WorkspaceGenerationChangedError("stale")],
            ) as validate,
            patch(
                "src.core.module_cache_sync.get_module_sync",
                return_value={
                    "content": "x = 1",
                    "path": "workflows/test.py",
                    "hash": "h",
                },
            ),
            patch(
                "src.services.execution.module_loader.load_workflow_from_db",
                return_value=(MagicMock(), None, None),
            ) as load_workflow,
        ):
            with pytest.raises(WorkspaceGenerationChangedError, match="stale"):
                _load_workspace_workflow(
                    file_path="workflows/test.py",
                    function_name="run",
                    workspace_generation="generation-1",
                )

        assert validate.call_count == 2
        load_workflow.assert_called_once()

    @patch("src.core.module_cache_sync.get_module_sync")
    def test_cache_exception_sets_load_error(self, mock_cache):
        """When cache raises an exception, load_error should be set."""
        mock_cache.side_effect = Exception("Redis connection refused")

        workflow_code = None
        function_name = "run"
        file_path = "workflows/test.py"
        load_error = None

        if workflow_code and function_name and file_path:
            pytest.fail("Should not take the workflow_code branch")
        elif function_name and file_path:
            try:
                from src.core.module_cache_sync import get_module_sync

                get_module_sync(file_path)  # will raise
            except Exception as e:
                load_error = f"Cache load failed: {e}"

        assert load_error is not None
        assert "Redis connection refused" in load_error


@pytest.mark.asyncio
async def test_worker_main_pins_generation_before_execution():
    from src.services.execution import worker

    context = {"name": "test"}
    redis_client = AsyncMock()
    result = {"status": "Success", "metrics": None}
    seen = {}

    async def record_context(_execution_id, execution_context):
        seen["generation"] = execution_context.get("workspace_generation")
        return result

    with (
        patch("src.config.get_settings", return_value=SimpleNamespace(redis_url="redis://test")),
        patch("redis.asyncio.from_url", return_value=redis_client),
        patch.object(worker, "_setup_signal_handlers"),
        patch.object(
            worker,
            "_read_execution_context",
            new=AsyncMock(return_value=context),
        ),
        patch.object(worker, "_run_execution", new=AsyncMock(side_effect=record_context)) as run,
        patch.object(worker, "_write_execution_result", new=AsyncMock()),
        patch(
            "src.core.module_cache_sync.wait_for_workspace_generation_sync",
            return_value="generation-1",
        ) as wait_generation,
    ):
        await worker.worker_main("execution-1")

    wait_generation.assert_called_once_with()
    run.assert_awaited_once()
    assert seen["generation"] == "generation-1"
    redis_client.aclose.assert_awaited_once()
