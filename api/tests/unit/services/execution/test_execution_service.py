"""Tests for execution service functions."""

import base64
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4


class _Context:
    execution_id = "exec-context"
    solution_deployment_id = None


class _Module(SimpleNamespace):
    def __dir__(self):
        return list(self.__dict__)


class TestGetWorkflowForExecution:
    """Test get_workflow_for_execution with optional session."""

    @pytest.mark.asyncio
    async def test_uses_provided_session(self):
        """Should use provided session instead of creating new one."""
        from src.services.execution.service import get_workflow_for_execution

        workflow_id = str(uuid4())
        mock_session = AsyncMock()

        # Create mock workflow record
        mock_workflow = MagicMock()
        mock_workflow.name = "test_workflow"
        mock_workflow.function_name = "run"
        mock_workflow.path = "workflows/test.py"
        mock_workflow.timeout_seconds = 300
        mock_workflow.time_saved = 5
        mock_workflow.value = 10.0
        mock_workflow.execution_mode = "async"
        mock_workflow.organization_id = uuid4()

        # Single execute: select(Workflow) -> returns workflow
        mock_wf_result = MagicMock()
        mock_wf_result.one_or_none.return_value = (mock_workflow, True)
        mock_session.execute = AsyncMock(return_value=mock_wf_result)

        result = await get_workflow_for_execution(workflow_id, db=mock_session)

        assert result["name"] == "test_workflow"
        assert mock_session.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_creates_session_when_not_provided(self):
        """Should create own session when none provided."""
        from src.services.execution.service import get_workflow_for_execution

        workflow_id = str(uuid4())

        mock_workflow = MagicMock()
        mock_workflow.name = "test_workflow"
        mock_workflow.function_name = "run"
        mock_workflow.path = "workflows/test.py"
        mock_workflow.timeout_seconds = 300
        mock_workflow.time_saved = 5
        mock_workflow.value = 10.0
        mock_workflow.execution_mode = "async"
        mock_workflow.organization_id = uuid4()

        # Single execute: select(Workflow) -> returns workflow
        mock_wf_result = MagicMock()
        mock_wf_result.one_or_none.return_value = (mock_workflow, True)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_wf_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        # Patch at src.core.database since it's imported inside the function
        with patch("src.core.database.get_session_factory", return_value=mock_factory):
            result = await get_workflow_for_execution(workflow_id)

        assert result["name"] == "test_workflow"
        mock_factory.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_metadata_keys(self):
        """Should return expected metadata keys and no 'code' key."""
        from src.services.execution.service import get_workflow_for_execution

        workflow_id = str(uuid4())
        mock_session = AsyncMock()
        org_id = uuid4()

        mock_workflow = MagicMock()
        mock_workflow.name = "test_workflow"
        mock_workflow.function_name = "run"
        mock_workflow.path = "workflows/test.py"
        mock_workflow.timeout_seconds = 300
        mock_workflow.time_saved = 5
        mock_workflow.value = 10.0
        mock_workflow.execution_mode = "async"
        mock_workflow.organization_id = org_id
        mock_workflow.solution_id = None
        mock_workflow.type = "workflow"
        mock_workflow.cache_ttl_seconds = 0

        mock_wf_result = MagicMock()
        mock_wf_result.one_or_none.return_value = (mock_workflow, True)
        mock_session.execute = AsyncMock(return_value=mock_wf_result)

        result = await get_workflow_for_execution(workflow_id, db=mock_session)

        expected_keys = {
            "name", "function_name", "path", "timeout_seconds",
            "time_saved", "value", "execution_mode", "organization_id",
            "solution_id", "can_access_global_repo", "type", "cache_ttl_seconds",
        }
        assert set(result.keys()) == expected_keys
        assert "code" not in result
        assert result["name"] == "test_workflow"
        assert result["function_name"] == "run"
        assert result["path"] == "workflows/test.py"
        assert result["timeout_seconds"] == 300
        assert result["organization_id"] == str(org_id)
        assert result["type"] == "workflow"
        assert result["cache_ttl_seconds"] == 0

    @pytest.mark.asyncio
    async def test_workflow_not_found_raises(self):
        """Should raise WorkflowNotFoundError when workflow doesn't exist."""
        from src.services.execution.service import (
            get_workflow_for_execution,
            WorkflowNotFoundError,
        )

        workflow_id = str(uuid4())
        mock_session = AsyncMock()

        # Execute returns None (workflow not found)
        mock_wf_result = MagicMock()
        mock_wf_result.one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_wf_result)

        with pytest.raises(WorkflowNotFoundError, match=workflow_id):
            await get_workflow_for_execution(workflow_id, db=mock_session)


class TestWorkflowMetadataOnly:
    @pytest.mark.asyncio
    async def test_returns_cached_metadata(self, monkeypatch):
        from src.services.execution import service

        redis = AsyncMock()
        redis.get_workflow_metadata_cache.return_value = {
            "id": "wf-1",
            "name": "Cached Workflow",
            "file_path": "workflows/cached.py",
            "timeout_seconds": 90,
            "time_saved": 3,
            "value": 42.5,
            "execution_mode": "async",
        }
        monkeypatch.setattr("src.core.redis_client.get_redis_client", lambda: redis)

        metadata = await service.get_workflow_metadata_only("wf-1")

        assert metadata.id == "wf-1"
        assert metadata.name == "Cached Workflow"
        assert metadata.source_file_path == "workflows/cached.py"
        assert metadata.timeout_seconds == 90
        assert metadata.time_saved == 3
        assert metadata.value == 42.5
        assert metadata.execution_mode == "async"
        redis.set_workflow_metadata_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_miss_populates_cache_with_defaults(self, monkeypatch):
        from src.services.execution import service

        redis = AsyncMock()
        redis.get_workflow_metadata_cache.return_value = None
        monkeypatch.setattr("src.core.redis_client.get_redis_client", lambda: redis)

        workflow = MagicMock()
        workflow.id = "wf-2"
        workflow.name = "DB Workflow"
        workflow.path = "workflows/db.py"
        workflow.timeout_seconds = None
        workflow.time_saved = None
        workflow.value = None
        workflow.execution_mode = None

        result = MagicMock()
        result.scalar_one_or_none.return_value = workflow

        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(return_value=session)
        monkeypatch.setattr("src.core.database.get_session_factory", lambda: session_factory)

        metadata = await service.get_workflow_metadata_only("wf-2")

        assert metadata.id == "wf-2"
        assert metadata.timeout_seconds == 1800
        assert metadata.time_saved == 0
        assert metadata.value == 0.0
        assert metadata.execution_mode == "sync"
        redis.set_workflow_metadata_cache.assert_awaited_once_with(
            workflow_id="wf-2",
            name="DB Workflow",
            file_path="workflows/db.py",
            timeout_seconds=1800,
            time_saved=0,
            value=0.0,
            execution_mode="sync",
        )

    @pytest.mark.asyncio
    async def test_db_miss_without_record_raises(self, monkeypatch):
        from src.services.execution import service

        redis = AsyncMock()
        redis.get_workflow_metadata_cache.return_value = None
        monkeypatch.setattr("src.core.redis_client.get_redis_client", lambda: redis)

        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(return_value=session)
        monkeypatch.setattr("src.core.database.get_session_factory", lambda: session_factory)

        with pytest.raises(service.WorkflowNotFoundError, match="wf-missing"):
            await service.get_workflow_metadata_only("wf-missing")


class TestEnqueueHelpers:
    @pytest.mark.asyncio
    async def test_enqueue_workflow_async_returns_pending_without_wait(self, monkeypatch):
        from src.models.enums import ExecutionStatus
        from src.services.execution import service

        enqueue = AsyncMock(return_value=("exec-queued", False))
        monkeypatch.setattr(
            "src.services.execution.async_executor.enqueue_workflow_execution_once",
            enqueue,
        )

        response = await service._enqueue_workflow_async(
            context=_Context(),
            workflow_id="wf-1",
            workflow_name="Workflow",
            parameters={"x": 1},
            form_id="form-1",
            sync=False,
        )

        assert response.execution_id == "exec-queued"
        assert response.status is ExecutionStatus.PENDING
        assert enqueue.await_args.kwargs["workflow_id"] == "wf-1"
        assert enqueue.await_args.kwargs["parameters"] == {"x": 1}
        assert enqueue.await_args.kwargs["form_id"] == "form-1"
        assert enqueue.await_args.kwargs["execution_id"] == "exec-context"
        assert enqueue.await_args.kwargs["sync"] is False

    @pytest.mark.asyncio
    async def test_enqueue_workflow_sync_waits_with_timeout_buffer(self, monkeypatch):
        from src.models.enums import ExecutionStatus
        from src.services.execution import service

        enqueue = AsyncMock(return_value=("exec-sync", False))
        monkeypatch.setattr(
            "src.services.execution.async_executor.enqueue_workflow_execution_once",
            enqueue,
        )

        redis = AsyncMock()
        redis.wait_for_result.return_value = {
            "status": "Success",
            "result": {"ok": True},
            "duration_ms": 15,
        }
        monkeypatch.setattr("src.core.redis_client.get_redis_client", lambda: redis)

        metadata = MagicMock()
        metadata.timeout_seconds = 45
        monkeypatch.setattr(service, "get_workflow_metadata_only", AsyncMock(return_value=metadata))

        response = await service._enqueue_workflow_async(
            context=_Context(),
            workflow_id="wf-1",
            workflow_name="Workflow",
            parameters={},
            sync=True,
        )

        assert response.execution_id == "exec-sync"
        assert response.status is ExecutionStatus.SUCCESS
        assert response.result == {"ok": True}
        assert response.duration_ms == 15
        redis.wait_for_result.assert_awaited_once_with("exec-sync", timeout_seconds=105)

    @pytest.mark.asyncio
    async def test_enqueue_workflow_sync_maps_timeout_and_unknown_status(self, monkeypatch):
        from src.models.enums import ExecutionStatus
        from src.services.execution import service

        monkeypatch.setattr(
            "src.services.execution.async_executor.enqueue_workflow_execution_once",
            AsyncMock(return_value=("exec-timeout", False)),
        )
        metadata = MagicMock()
        metadata.timeout_seconds = 0
        monkeypatch.setattr(service, "get_workflow_metadata_only", AsyncMock(return_value=metadata))

        redis = AsyncMock()
        redis.wait_for_result.return_value = None
        monkeypatch.setattr("src.core.redis_client.get_redis_client", lambda: redis)

        timeout_response = await service._enqueue_workflow_async(
            context=_Context(),
            workflow_id="wf-1",
            workflow_name="Workflow",
            parameters={},
            sync=True,
        )

        assert timeout_response.status is ExecutionStatus.TIMEOUT
        assert timeout_response.error_type == "TimeoutError"
        redis.wait_for_result.assert_awaited_once_with("exec-timeout", timeout_seconds=86400)

        redis.wait_for_result.reset_mock()
        redis.wait_for_result.return_value = {
            "status": "Unexpected",
            "error": "bad",
            "error_type": "RuntimeError",
        }

        failed_response = await service._enqueue_workflow_async(
            context=_Context(),
            workflow_id="wf-1",
            workflow_name="Workflow",
            parameters={},
            sync=True,
        )

        assert failed_response.status is ExecutionStatus.FAILED
        assert failed_response.error == "bad"
        assert failed_response.error_type == "RuntimeError"

    @pytest.mark.asyncio
    async def test_run_code_encodes_source_and_returns_pending(self, monkeypatch):
        from src.models.enums import ExecutionStatus
        from src.services.execution import service

        enqueue = AsyncMock(return_value="exec-code")
        monkeypatch.setattr(
            "src.services.execution.async_executor.enqueue_code_execution",
            enqueue,
        )

        response = await service.run_code(
            _Context(),
            "print('hello')",
            script_name="snippet.py",
            input_data={"name": "Ada"},
        )

        assert response.execution_id == "exec-code"
        assert response.workflow_name == "snippet.py"
        assert response.status is ExecutionStatus.PENDING
        assert enqueue.await_args.kwargs["code_base64"] == base64.b64encode(
            b"print('hello')"
        ).decode()
        assert enqueue.await_args.kwargs["parameters"] == {"name": "Ada"}


class TestRunWorkflowAndTool:
    @pytest.mark.asyncio
    async def test_run_workflow_wraps_validation_errors(self, monkeypatch):
        from src.services.execution import service

        monkeypatch.setattr(
            service,
            "get_workflow_metadata_only",
            AsyncMock(side_effect=RuntimeError("redis down")),
        )

        with pytest.raises(service.WorkflowNotFoundError, match="redis down"):
            await service.run_workflow(_Context(), "wf-1")

    @pytest.mark.asyncio
    async def test_run_workflow_enqueues_after_validation(self, monkeypatch):
        from src.services.execution import service

        metadata = MagicMock()
        metadata.name = "Runnable"
        monkeypatch.setattr(service, "get_workflow_metadata_only", AsyncMock(return_value=metadata))
        enqueue = AsyncMock(return_value="response")
        monkeypatch.setattr(service, "_enqueue_workflow_async", enqueue)

        response = await service.run_workflow(
            _Context(),
            "wf-1",
            input_data={"x": 1},
            form_id="form-1",
            sync=True,
        )

        assert response == "response"
        assert enqueue.await_args.kwargs["workflow_id"] == "wf-1"
        assert enqueue.await_args.kwargs["workflow_name"] == "Runnable"
        assert enqueue.await_args.kwargs["parameters"] == {"x": 1}
        assert enqueue.await_args.kwargs["form_id"] == "form-1"
        assert enqueue.await_args.kwargs["sync"] is True

    @pytest.mark.asyncio
    async def test_execute_tool_builds_context_and_generates_execution_id(self, monkeypatch):
        from src.services.execution import service

        settings = MagicMock()
        settings.public_url = "https://bifrost.example"
        monkeypatch.setattr("src.config.get_settings", lambda: settings)
        enqueue = AsyncMock(return_value="tool-response")
        monkeypatch.setattr(service, "_enqueue_workflow_async", enqueue)

        response = await service.execute_tool(
            workflow_id="wf-tool",
            workflow_name="Tool",
            parameters={"arg": "value"},
            user_id="user-1",
            user_email="user@example.com",
            user_name="User One",
            org_id="org-1",
            org_name="Org One",
            is_platform_admin=True,
            is_agent=True,
        )

        assert response == "tool-response"
        context = enqueue.await_args.kwargs["context"]
        assert context.user_id == "user-1"
        assert context.email == "user@example.com"
        assert context.scope == "org-1"
        assert context.organization.id == "org-1"
        assert context.is_platform_admin is True
        assert context.is_agent is True
        assert context.workflow_name == "Tool"
        assert context.public_url == "https://bifrost.example"
        assert context.execution_id
        assert enqueue.await_args.kwargs["sync"] is True


class TestGetWorkflowById:
    def _workflow_record(self, *, path="workflows/tool.py", function_name="run_tool"):
        record = MagicMock()
        record.id = "wf-load"
        record.name = "Loadable"
        record.path = path
        record.function_name = function_name
        return record

    def _patch_db(self, monkeypatch, workflow_record):
        result = MagicMock()
        result.scalar_one_or_none.return_value = workflow_record
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=None)
        session_factory = MagicMock(return_value=session)
        monkeypatch.setattr("src.core.database.get_session_factory", lambda: session_factory)
        return session

    @pytest.mark.asyncio
    async def test_loads_workflow_from_module_cache(self, monkeypatch):
        from src.services.execution import service
        from src.services.execution.module_loader import WorkflowMetadata

        self._patch_db(monkeypatch, self._workflow_record())
        monkeypatch.setattr(
            "src.core.module_cache.get_module",
            AsyncMock(return_value={"content": "cached code"}),
        )

        metadata = WorkflowMetadata(name="Decorated")

        def run_tool():
            return "ok"

        run_tool._executable_metadata = metadata
        module = _Module(not_callable=object(), run_tool=run_tool)
        exec_from_db = MagicMock(return_value=module)
        monkeypatch.setattr(service, "exec_from_db", exec_from_db)

        func, loaded_metadata = await service.get_workflow_by_id("wf-load")

        assert func is run_tool
        assert loaded_metadata is metadata
        assert loaded_metadata.id == "wf-load"
        assert loaded_metadata.source_file_path == "workflows/tool.py"
        exec_from_db.assert_called_once_with(
            code="cached code",
            path="workflows/tool.py",
            function_name="run_tool",
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_repo_storage_and_converts_data_provider(self, monkeypatch):
        from src.services.execution import service

        self._patch_db(
            monkeypatch,
            self._workflow_record(path="providers/options.py", function_name="options"),
        )
        monkeypatch.setattr(
            "src.core.module_cache.get_module",
            AsyncMock(return_value=None),
        )

        repo = MagicMock()
        repo.read = AsyncMock(return_value=b"repo code")
        monkeypatch.setattr("src.services.repo_storage.RepoStorage", MagicMock(return_value=repo))

        metadata = MagicMock()
        metadata.type = "data_provider"
        metadata.name = "Options"
        metadata.description = "Option source"
        metadata.category = "Forms"
        metadata.parameters = []
        metadata.timeout_seconds = 25

        def options():
            return []

        options._executable_metadata = metadata
        module = _Module(options=options)
        monkeypatch.setattr(service, "exec_from_db", MagicMock(return_value=module))

        func, loaded_metadata = await service.get_workflow_by_id("wf-load")

        assert func is options
        assert loaded_metadata.type == "data_provider"
        assert loaded_metadata.name == "Options"
        assert loaded_metadata.timeout_seconds == 25
        assert loaded_metadata.id == "wf-load"
        assert loaded_metadata.source_file_path == "providers/options.py"
        repo.read.assert_awaited_once_with("providers/options.py")

    @pytest.mark.asyncio
    async def test_raises_when_record_missing_or_code_unavailable(self, monkeypatch):
        from src.services.execution import service

        self._patch_db(monkeypatch, None)
        with pytest.raises(service.WorkflowNotFoundError, match="wf-missing"):
            await service.get_workflow_by_id("wf-missing")

        self._patch_db(monkeypatch, self._workflow_record())
        monkeypatch.setattr(
            "src.core.module_cache.get_module",
            AsyncMock(side_effect=RuntimeError("redis broken")),
        )
        repo = MagicMock()
        repo.read = AsyncMock(side_effect=RuntimeError("s3 broken"))
        monkeypatch.setattr("src.services.repo_storage.RepoStorage", MagicMock(return_value=repo))

        with pytest.raises(service.WorkflowLoadError, match="has no code"):
            await service.get_workflow_by_id("wf-load")

    @pytest.mark.asyncio
    async def test_raises_for_exec_errors_and_missing_decorated_function(self, monkeypatch):
        from src.services.execution import service

        self._patch_db(monkeypatch, self._workflow_record())
        monkeypatch.setattr(
            "src.core.module_cache.get_module",
            AsyncMock(return_value={"content": "bad code"}),
        )
        monkeypatch.setattr(
            service,
            "exec_from_db",
            MagicMock(side_effect=ImportError("import failed")),
        )
        with pytest.raises(service.WorkflowLoadError, match="import failed"):
            await service.get_workflow_by_id("wf-load")

        monkeypatch.setattr(
            service,
            "exec_from_db",
            MagicMock(return_value=MagicMock(__dir__=lambda self=None: ["other"])),
        )
        with pytest.raises(service.WorkflowLoadError, match="No decorated function"):
            await service.get_workflow_by_id("wf-load")
