"""Tests for pure helper functions in workflow_validation service."""

import asyncio
from dataclasses import dataclass
from datetime import datetime

import pytest


from src.services.workflow_validation import (
    _convert_workflow_metadata_to_model,
    _extract_relative_path,
    _write_temp_workflow,
    _write_temp_workflow_cancellation_safe,
    validate_workflow_file,
)
from src.services import workflow_validation


@dataclass
class MockParam:
    name: str
    type: str
    required: bool
    label: str | None = None
    default_value: str | None = None


@dataclass
class MockWorkflowMetadata:
    name: str
    description: str
    category: str
    tags: list[str] | None
    parameters: list[MockParam] | None
    execution_mode: str
    timeout_seconds: int | None
    time_saved: int | None
    value: float | None
    source_file_path: str | None


class TestExtractRelativePath:

    def test_returns_path_as_is(self):
        result = _extract_relative_path("features/ticketing/workflows/create_ticket.py")
        assert result == "features/ticketing/workflows/create_ticket.py"

    def test_returns_none_for_none_input(self):
        result = _extract_relative_path(None)
        assert result is None

    def test_returns_none_for_empty_string(self):
        result = _extract_relative_path("")
        assert result is None


class TestConvertWorkflowMetadataToModel:

    def test_basic_conversion_with_all_fields(self):
        metadata = MockWorkflowMetadata(
            name="my_workflow",
            description="A test workflow",
            category="Testing",
            tags=["test", "unit"],
            parameters=[
                MockParam(name="input", type="string", required=True),
            ],
            execution_mode="sync",
            timeout_seconds=600,
            time_saved=10,
            value=5.5,
            source_file_path="workflows/my_workflow.py",
        )

        result = _convert_workflow_metadata_to_model(metadata)

        assert result.id == "pending-my_workflow"
        assert result.name == "my_workflow"
        assert result.description == "A test workflow"
        assert result.category == "Testing"
        assert result.tags == ["test", "unit"]
        assert len(result.parameters) == 1
        assert result.parameters[0].name == "input"
        assert result.parameters[0].type == "string"
        assert result.parameters[0].required is True
        assert result.execution_mode == "sync"
        assert result.timeout_seconds == 600
        assert result.time_saved == 10
        assert result.value == 5.5
        assert result.source_file_path == "workflows/my_workflow.py"
        assert result.relative_file_path == "workflows/my_workflow.py"
        assert result.retry_policy is None
        assert result.endpoint_enabled is False
        assert result.disable_global_key is False
        assert result.public_endpoint is False
        assert isinstance(result.created_at, datetime)

    def test_empty_parameters_list(self):
        metadata = MockWorkflowMetadata(
            name="empty_params",
            description="No params",
            category="General",
            tags=["demo"],
            parameters=[],
            execution_mode="async",
            timeout_seconds=300,
            time_saved=5,
            value=1.0,
            source_file_path="workflows/empty.py",
        )

        result = _convert_workflow_metadata_to_model(metadata)
        assert result.parameters == []

    def test_none_parameters(self):
        metadata = MockWorkflowMetadata(
            name="none_params",
            description="Null params",
            category="General",
            tags=None,
            parameters=None,
            execution_mode="sync",
            timeout_seconds=1800,
            time_saved=0,
            value=0.0,
            source_file_path=None,
        )

        result = _convert_workflow_metadata_to_model(metadata)
        assert result.parameters == []

    def test_parameters_with_optional_label_and_default_value(self):
        metadata = MockWorkflowMetadata(
            name="labeled_params",
            description="Has labels",
            category="General",
            tags=[],
            parameters=[
                MockParam(
                    name="email",
                    type="string",
                    required=False,
                    label="User Email",
                    default_value="user@example.com",
                ),
                MockParam(
                    name="count",
                    type="int",
                    required=True,
                    label=None,
                    default_value=None,
                ),
            ],
            execution_mode="sync",
            timeout_seconds=1800,
            time_saved=0,
            value=0.0,
            source_file_path=None,
        )

        result = _convert_workflow_metadata_to_model(metadata)
        assert len(result.parameters) == 2

        # First param has label and default_value
        assert result.parameters[0].label == "User Email"
        assert result.parameters[0].default_value == "user@example.com"

        # Second param has None for label and default_value (they were not set)
        assert result.parameters[1].label is None
        assert result.parameters[1].default_value is None

    def test_tags_default_to_empty_list_when_none(self):
        metadata = MockWorkflowMetadata(
            name="no_tags",
            description="Tags are None",
            category="General",
            tags=None,
            parameters=None,
            execution_mode="sync",
            timeout_seconds=1800,
            time_saved=0,
            value=0.0,
            source_file_path=None,
        )

        result = _convert_workflow_metadata_to_model(metadata)
        assert result.tags == []

    def test_timeout_defaults_to_1800_when_none(self):
        metadata = MockWorkflowMetadata(
            name="default_timeout",
            description="No timeout set",
            category="General",
            tags=[],
            parameters=None,
            execution_mode="sync",
            timeout_seconds=None,
            time_saved=0,
            value=0.0,
            source_file_path=None,
        )

        result = _convert_workflow_metadata_to_model(metadata)
        assert result.timeout_seconds == 1800

    def test_time_saved_defaults_to_zero_when_none(self):
        metadata = MockWorkflowMetadata(
            name="no_time_saved",
            description="No time saved",
            category="General",
            tags=[],
            parameters=None,
            execution_mode="sync",
            timeout_seconds=1800,
            time_saved=None,
            value=0.0,
            source_file_path=None,
        )

        result = _convert_workflow_metadata_to_model(metadata)
        assert result.time_saved == 0

    def test_value_defaults_to_zero_when_none(self):
        metadata = MockWorkflowMetadata(
            name="no_value",
            description="No value",
            category="General",
            tags=[],
            parameters=None,
            execution_mode="sync",
            timeout_seconds=1800,
            time_saved=0,
            value=None,
            source_file_path=None,
        )

        result = _convert_workflow_metadata_to_model(metadata)
        assert result.value == 0.0

    def test_id_format_is_pending_name(self):
        metadata = MockWorkflowMetadata(
            name="create_ticket",
            description="Creates a ticket",
            category="Ticketing",
            tags=["ticket"],
            parameters=None,
            execution_mode="async",
            timeout_seconds=300,
            time_saved=15,
            value=10.0,
            source_file_path="workflows/create_ticket.py",
        )

        result = _convert_workflow_metadata_to_model(metadata)
        assert result.id == "pending-create_ticket"

    def test_relative_file_path_matches_source_file_path(self):
        path = "features/ticketing/workflows/create_ticket.py"
        metadata = MockWorkflowMetadata(
            name="ticket_workflow",
            description="Ticket",
            category="General",
            tags=[],
            parameters=None,
            execution_mode="sync",
            timeout_seconds=1800,
            time_saved=0,
            value=0.0,
            source_file_path=path,
        )

        result = _convert_workflow_metadata_to_model(metadata)
        assert result.relative_file_path == path
        assert result.source_file_path == path


class TestValidateWorkflowFile:

    @pytest.mark.asyncio
    async def test_valid_workflow_returns_metadata_with_best_practice_warnings(self):
        content = """
from src.sdk.decorators import workflow

@workflow(name="valid_workflow", description="Does useful work")
async def valid_workflow():
    return "ok"
"""

        result = await validate_workflow_file("workflows/valid.py", content)

        assert result.valid is True
        assert result.metadata is not None
        assert result.metadata.name == "valid_workflow"
        assert result.metadata.source_file_path is not None
        assert [issue.severity for issue in result.issues] == ["warning", "warning"]
        assert "category other than 'General'" in result.issues[0].message
        assert "adding tags" in result.issues[1].message

    @pytest.mark.asyncio
    async def test_syntax_error_returns_line_issue(self):
        result = await validate_workflow_file("workflows/bad.py", "def broken(:\n    pass\n")

        assert result.valid is False
        assert result.metadata is None
        assert len(result.issues) == 1
        assert result.issues[0].line == 1
        assert result.issues[0].severity == "error"
        assert "Syntax error" in result.issues[0].message

    @pytest.mark.asyncio
    async def test_import_error_returns_issue(self):
        content = """
import module_that_does_not_exist_for_validation
"""

        result = await validate_workflow_file("workflows/import_error.py", content)

        assert result.valid is False
        assert result.metadata is None
        assert len(result.issues) == 1
        assert result.issues[0].severity == "error"
        assert "Import error" in result.issues[0].message

    @pytest.mark.asyncio
    async def test_missing_workflow_decorator_returns_issue(self):
        content = """
async def helper():
    return "not discoverable"
"""

        result = await validate_workflow_file("workflows/no_decorator.py", content)

        assert result.valid is False
        assert result.metadata is None
        assert len(result.issues) == 1
        assert result.issues[0].severity == "error"
        assert "No @workflow decorator found" in result.issues[0].message

    @pytest.mark.asyncio
    async def test_invalid_workflow_metadata_collects_all_validation_errors(self, monkeypatch):
        from types import SimpleNamespace

        from src.services.execution.module_loader import WorkflowMetadata, WorkflowParameter

        metadata = WorkflowMetadata(
            name="Invalid Name",
            description=" ",
            parameters=[WorkflowParameter(name="unsupported", type="tuple", required=True)],
            timeout_seconds=90000,
        )
        metadata.execution_mode = "later"

        def fake_workflow():
            return None

        fake_workflow._executable_metadata = metadata

        monkeypatch.setattr(
            "src.services.execution.module_loader.import_module",
            lambda path: SimpleNamespace(fake_workflow=fake_workflow),
        )

        content = "def placeholder():\n    return None\n"

        result = await validate_workflow_file("workflows/invalid_metadata.py", content)

        assert result.valid is False
        messages = [issue.message for issue in result.issues]
        assert any("Invalid workflow name" in message for message in messages)
        assert any("Workflow description is required" in message for message in messages)
        assert any("Invalid execution mode" in message for message in messages)
        assert any("Invalid timeout" in message for message in messages)
        assert any("Invalid parameter type" in message for message in messages)
        assert any("category other than 'General'" in message for message in messages)
        assert any("adding tags" in message for message in messages)

    @pytest.mark.asyncio
    async def test_file_storage_not_found_when_content_is_omitted(self, monkeypatch):
        class FakeStorage:
            def __init__(self, db):
                self.db = db

            async def read_file(self, path):
                raise FileNotFoundError(path)

        class FakeDbContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(
            "src.core.database.get_db_context",
            lambda: FakeDbContext(),
        )
        monkeypatch.setattr(
            "src.services.file_storage.FileStorageService",
            FakeStorage,
        )

        result = await validate_workflow_file("missing.py")

        assert result.valid is False
        assert result.metadata is None
        assert len(result.issues) == 1
        assert result.issues[0].message == "File not found in database: missing.py"


@pytest.mark.asyncio
async def test_cancelled_temp_workflow_write_removes_completed_file(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    removed = asyncio.Event()

    class TempPath:
        def unlink(self):
            return None

    temp_path = TempPath()

    async def fake_to_thread(func, *args):
        if func is _write_temp_workflow:
            started.set()
            await release.wait()
            return temp_path
        removed.set()

    monkeypatch.setattr(workflow_validation.asyncio, "to_thread", fake_to_thread)

    task = asyncio.create_task(_write_temp_workflow_cancellation_safe("VALUE = 1"))
    await started.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert removed.is_set()
