"""
Unit tests for Workflow Orphan Service.

Tests the WorkflowOrphanService data models and basic service initialization.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.models.orm.workflows import Workflow
from src.services.workflow_orphan import (
    WorkflowOrphanService,
    OrphanedWorkflow,
    WorkflowReference,
    Replacement,
    FunctionSignature,
)


class TestWorkflowOrphanServiceInit:
    """Tests for WorkflowOrphanService initialization."""

    def test_creates_service_with_db(self):
        """Test service initialization with database session."""
        mock_db = MagicMock()
        service = WorkflowOrphanService(db=mock_db)

        assert service.db == mock_db


class TestOrphanedWorkflowModel:
    """Tests for OrphanedWorkflow data model."""

    def test_creates_orphaned_workflow(self):
        """Test OrphanedWorkflow model creation."""
        orphan = OrphanedWorkflow(
            id="wf-123",
            name="Test Workflow",
            function_name="test_func",
            last_path="workflows/test.py",
            code="def test_func(): pass",
            used_by=[],
            orphaned_at=None,
        )

        assert orphan.id == "wf-123"
        assert orphan.name == "Test Workflow"
        assert orphan.function_name == "test_func"

    def test_orphaned_workflow_with_usage(self):
        """Test OrphanedWorkflow with usage references."""
        orphan = OrphanedWorkflow(
            id="wf-123",
            name="Test Workflow",
            function_name="test_func",
            last_path="workflows/test.py",
            code="def test_func(): pass",
            used_by=[
                WorkflowReference(type="form", id="form-1", name="Test Form"),
                WorkflowReference(type="app", id="app-1", name="Test App"),
            ],
            orphaned_at=None,
        )

        assert len(orphan.used_by) == 2
        assert orphan.used_by[0].type == "form"
        assert orphan.used_by[1].type == "app"

    def test_orphaned_workflow_without_code(self):
        """Test OrphanedWorkflow can have None code."""
        orphan = OrphanedWorkflow(
            id="wf-123",
            name="Test Workflow",
            function_name="test_func",
            last_path="workflows/test.py",
            code=None,
            used_by=[],
            orphaned_at=None,
        )

        assert orphan.last_path == "workflows/test.py"


class TestWorkflowReferenceModel:
    """Tests for WorkflowReference data model."""

    def test_creates_form_reference(self):
        """Test WorkflowReference for form."""
        ref = WorkflowReference(
            type="form",
            id="form-123",
            name="Test Form",
        )

        assert ref.type == "form"
        assert ref.id == "form-123"
        assert ref.name == "Test Form"

    def test_creates_app_reference(self):
        """Test WorkflowReference for app."""
        ref = WorkflowReference(
            type="app",
            id="app-123",
            name="Test App",
        )

        assert ref.type == "app"

    def test_creates_agent_reference(self):
        """Test WorkflowReference for agent."""
        ref = WorkflowReference(
            type="agent",
            id="agent-123",
            name="Test Agent",
        )

        assert ref.type == "agent"


class TestReplacementModel:
    """Tests for Replacement data model."""

    def test_creates_exact_replacement(self):
        """Test Replacement with exact compatibility."""
        replacement = Replacement(
            path="workflows/replacement.py",
            function_name="replacement_func",
            signature="(arg1: str) -> None",
            compatibility="exact",
        )

        assert replacement.path == "workflows/replacement.py"
        assert replacement.function_name == "replacement_func"
        assert replacement.compatibility == "exact"

    def test_creates_compatible_replacement(self):
        """Test Replacement with compatible compatibility."""
        replacement = Replacement(
            path="workflows/other.py",
            function_name="other_func",
            signature="(arg1: str, arg2: int = 0) -> None",
            compatibility="compatible",
        )

        assert replacement.compatibility == "compatible"

    def test_creates_incompatible_replacement(self):
        """Test Replacement with incompatible compatibility."""
        replacement = Replacement(
            path="workflows/incompatible.py",
            function_name="incompatible_func",
            signature="(different_arg: bytes) -> int",
            compatibility="incompatible",
        )

        assert replacement.compatibility == "incompatible"


class TestFunctionSignature:
    """Tests for FunctionSignature dataclass."""

    def test_creates_function_signature(self):
        """Test FunctionSignature creation."""
        sig = FunctionSignature(
            name="test_func",
            parameters=[
                ("arg1", "str", False),
                ("arg2", "int", True),
            ],
            return_type="None",
        )

        assert sig.name == "test_func"
        assert len(sig.parameters) == 2
        assert sig.parameters[0] == ("arg1", "str", False)
        assert sig.parameters[1] == ("arg2", "int", True)
        assert sig.return_type == "None"

    def test_function_signature_without_return_type(self):
        """Test FunctionSignature without return type annotation."""
        sig = FunctionSignature(
            name="no_return_type",
            parameters=[],
            return_type=None,
        )

        assert sig.return_type is None

    def test_function_signature_with_no_type_annotations(self):
        """Test FunctionSignature with untyped parameters."""
        sig = FunctionSignature(
            name="untyped_func",
            parameters=[
                ("arg1", None, False),
                ("arg2", None, False),
            ],
            return_type=None,
        )

        assert sig.parameters[0][1] is None
        assert sig.parameters[1][1] is None


# =============================================================================
# Tests for replace_workflow (new AST-based semantics)
# =============================================================================

_WORKFLOW_CODE = """\
from src.sdk import workflow

@workflow
def my_func(name: str = "world") -> str:
    return f"hello {name}"
"""

_TOOL_CODE = """\
from src.sdk import tool

@tool
def my_func(name: str = "world") -> str:
    return f"hello {name}"
"""

_DATA_PROVIDER_CODE = """\
from src.sdk import data_provider

@data_provider
def my_func(name: str = "world") -> str:
    return f"hello {name}"
"""

_NO_DECORATOR_CODE = """\
def my_func(name: str = "world") -> str:
    return f"hello {name}"
"""


def _make_workflow(
    *,
    is_orphaned: bool = True,
    type: str = "workflow",
    path: str = "workflows/old.py",
    function_name: str = "my_func",
) -> MagicMock:
    wf = MagicMock()
    wf.id = uuid4()
    wf.name = "My Workflow"
    wf.type = type
    wf.path = path
    wf.function_name = function_name
    wf.is_orphaned = is_orphaned
    wf.is_active = not is_orphaned
    wf.updated_at = datetime.now(timezone.utc)
    return wf


def _make_service(*, workflow=None, conflict_row=None):
    """Build a WorkflowOrphanService with a mocked AsyncSession.

    workflow      — row returned by db.get(Workflow, id)
    conflict_row  — row returned by the uniqueness-conflict select
    """
    db = AsyncMock()
    db.get = AsyncMock(return_value=workflow)

    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = conflict_row
    db.execute = AsyncMock(return_value=result_mock)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    service = WorkflowOrphanService(db=db)
    return service, db


class TestReplaceWorkflowASTSemantics:
    """replace_workflow now validates via file content (AST), not a sibling DB row."""

    @pytest.mark.asyncio
    async def test_happy_path_repoints_and_clears_orphan_flag(self):
        """Successfully repoints an orphaned workflow at a valid decorated function."""
        wf = _make_workflow(is_orphaned=True, type="workflow")
        service, db = _make_service(workflow=wf, conflict_row=None)

        with patch.object(service, "_read_file", return_value=_WORKFLOW_CODE):
            result = await service.replace_workflow(
                workflow_id=wf.id,
                source_path="workflows/new.py",
                function_name="my_func",
            )

        assert result.path == "workflows/new.py"
        assert result.function_name == "my_func"
        assert result.is_orphaned is False
        db.commit.assert_awaited_once()


class TestRemapWorkflowReferences:
    """Tests for moving references from one workflow row to another."""

    @pytest.mark.asyncio
    async def test_remaps_references_to_active_target(self):
        old_workflow = Workflow(
            id=uuid4(),
            name="Old Tool",
            function_name="old_tool",
            type="tool",
            path="workflows/old_tool.py",
            is_active=False,
            is_orphaned=True,
        )
        target_workflow = Workflow(
            id=uuid4(),
            name="Active Tool",
            function_name="active_tool",
            type="tool",
            path="workflows/active_tool.py",
            is_active=True,
            is_orphaned=False,
        )
        db = AsyncMock()
        db.get = AsyncMock(side_effect=[old_workflow, target_workflow])
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(rowcount=2),
                MagicMock(rowcount=1),
                MagicMock(rowcount=3),
            ]
        )
        db.commit = AsyncMock()

        service = WorkflowOrphanService(db)
        with patch.object(
            service, "_remap_agent_tool_references", AsyncMock(return_value=4)
        ) as remap_agents:
            result = await service.remap_workflow_references(
                source_workflow_id=old_workflow.id,
                target_workflow_id=target_workflow.id,
            )

        assert result.source_workflow_id == str(old_workflow.id)
        assert result.target_workflow_id == str(target_workflow.id)
        assert result.updated == {
            "forms": 3,
            "form_fields": 3,
            "agents": 4,
            "apps": 0,
        }
        assert old_workflow.is_active is False
        assert old_workflow.is_orphaned is True
        assert db.execute.await_count == 3
        db.commit.assert_awaited_once()
        remap_agents.assert_awaited_once_with(old_workflow.id, target_workflow.id)

    @pytest.mark.asyncio
    async def test_rejects_when_workflow_not_found(self):
        """Raises ValueError when no workflow row exists for the given ID."""
        service, _ = _make_service(workflow=None)

        with pytest.raises(ValueError, match="not found"):
            await service.replace_workflow(
                workflow_id=uuid4(),
                source_path="workflows/new.py",
                function_name="my_func",
            )

    @pytest.mark.asyncio
    async def test_rejects_when_workflow_not_orphaned(self):
        """Raises ValueError when the target workflow is active (not orphaned)."""
        wf = _make_workflow(is_orphaned=False)
        service, _ = _make_service(workflow=wf)

        with pytest.raises(ValueError, match="not orphaned"):
            await service.replace_workflow(
                workflow_id=wf.id,
                source_path="workflows/new.py",
                function_name="my_func",
            )

    @pytest.mark.asyncio
    async def test_rejects_when_file_not_found(self):
        """Raises ValueError when the target file cannot be read."""
        wf = _make_workflow(is_orphaned=True)
        service, _ = _make_service(workflow=wf, conflict_row=None)

        with patch.object(service, "_read_file", return_value=None):
            with pytest.raises(ValueError, match="not found"):
                await service.replace_workflow(
                    workflow_id=wf.id,
                    source_path="workflows/missing.py",
                    function_name="my_func",
                )

    @pytest.mark.asyncio
    async def test_rejects_when_function_missing_from_file(self):
        """Raises ValueError when file exists but lacks the named decorated function."""
        wf = _make_workflow(is_orphaned=True)
        service, _ = _make_service(workflow=wf, conflict_row=None)

        with patch.object(service, "_read_file", return_value=_NO_DECORATOR_CODE):
            with pytest.raises(ValueError, match="not found in"):
                await service.replace_workflow(
                    workflow_id=wf.id,
                    source_path="workflows/new.py",
                    function_name="my_func",
                )

    @pytest.mark.asyncio
    async def test_rejects_uniqueness_collision(self):
        """Raises ValueError when (path, function_name) is already claimed by an active row."""
        wf = _make_workflow(is_orphaned=True)
        other_wf = _make_workflow(is_orphaned=False)  # already active at that location
        service, _ = _make_service(workflow=wf, conflict_row=other_wf)

        with patch.object(service, "_read_file", return_value=_WORKFLOW_CODE):
            with pytest.raises(ValueError, match="already registered"):
                await service.replace_workflow(
                    workflow_id=wf.id,
                    source_path="workflows/new.py",
                    function_name="my_func",
                )

    @pytest.mark.asyncio
    async def test_rejects_decorator_type_change_by_default(self):
        """Raises ValueError when new decorator type differs from original (default-deny)."""
        wf = _make_workflow(is_orphaned=True, type="workflow")
        service, _ = _make_service(workflow=wf, conflict_row=None)

        # new file has @data_provider, but wf.type == "workflow"
        with patch.object(service, "_read_file", return_value=_DATA_PROVIDER_CODE):
            with pytest.raises(ValueError, match="type change"):
                await service.replace_workflow(
                    workflow_id=wf.id,
                    source_path="workflows/new.py",
                    function_name="my_func",
                )

    @pytest.mark.asyncio
    async def test_allows_decorator_type_change_when_flag_set(self):
        """Allows type change when allow_type_change=True is passed."""
        wf = _make_workflow(is_orphaned=True, type="workflow")
        service, _ = _make_service(workflow=wf, conflict_row=None)

        with patch.object(service, "_read_file", return_value=_DATA_PROVIDER_CODE):
            result = await service.replace_workflow(
                workflow_id=wf.id,
                source_path="workflows/new.py",
                function_name="my_func",
                allow_type_change=True,
            )

        assert result.path == "workflows/new.py"
        assert result.is_orphaned is False

    @pytest.mark.asyncio
    async def test_accepts_tool_decorator(self):
        """@tool-decorated functions are accepted (same decorator family)."""
        wf = _make_workflow(is_orphaned=True, type="tool")
        service, _ = _make_service(workflow=wf, conflict_row=None)

        with patch.object(service, "_read_file", return_value=_TOOL_CODE):
            result = await service.replace_workflow(
                workflow_id=wf.id,
                source_path="workflows/new.py",
                function_name="my_func",
            )

        assert result.is_orphaned is False


class TestWorkflowOrphanPureHelpers:
    """Pure AST/signature helper coverage for WorkflowOrphanService."""

    def test_file_contains_workflow_accepts_supported_decorator_shapes(self):
        service = WorkflowOrphanService(db=None)
        code = """
@workflow
def plain():
    pass

@bifrost.tool()
def dotted():
    pass

@data_provider()
async def async_provider():
    pass
"""

        assert service.file_contains_workflow(code, "plain")
        assert service.file_contains_workflow(code, "dotted")
        assert service.file_contains_workflow(code, "async_provider")

    def test_file_contains_workflow_rejects_missing_bad_syntax_or_undecorated(self):
        service = WorkflowOrphanService(db=None)

        assert not service.file_contains_workflow("def plain():\n    pass\n", "plain")
        assert not service.file_contains_workflow(
            "@workflow\ndef other():\n    pass\n", "plain"
        )
        assert not service.file_contains_workflow("def broken(:\n", "plain")

    def test_detect_type_returns_decorator_family(self):
        service = WorkflowOrphanService(db=None)
        code = """
@tool()
def repair_ticket():
    pass
"""

        assert service._detect_type(code, "repair_ticket") == "tool"
        assert service._detect_type(code, "missing") is None
        assert service._detect_type("def broken(:\n", "repair_ticket") is None

    def test_parse_function_signature_skips_context_and_formats_annotations(self):
        service = WorkflowOrphanService(db=None)
        code = """
from typing import Optional

def run(self, ticket_id: str, context: ExecutionContext, priority: int = 1) -> str | None:
    return ticket_id
"""

        signature = service._parse_function_signature(code, "run")

        assert signature == FunctionSignature(
            name="run",
            parameters=[
                ("ticket_id", "str", False),
                ("priority", "int", True),
            ],
            return_type="str | None",
        )
        assert service._signature_to_string(signature) == (
            "(ticket_id: str, priority: int = ...) -> str | None"
        )

    def test_parse_function_signature_handles_missing_or_invalid_code(self):
        service = WorkflowOrphanService(db=None)

        assert service._parse_function_signature("def broken(:\n", "run") is None
        assert service._parse_function_signature("def other():\n    pass\n", "run") is None

    def test_signature_compatibility_exact_compatible_and_incompatible(self):
        service = WorkflowOrphanService(db=None)
        original = FunctionSignature(
            name="run",
            parameters=[("ticket_id", "str", False), ("priority", "int", True)],
            return_type="str",
        )

        assert service._check_signature_compatibility(original, original) == "exact"
        assert service._check_signature_compatibility(
            original,
            FunctionSignature(
                name="replacement",
                parameters=[("ticket_id", "str", False)],
                return_type="str | None",
            ),
        ) == "compatible"
        assert service._check_signature_compatibility(
            original,
            FunctionSignature(
                name="bad",
                parameters=[("customer_id", "str", False)],
                return_type="str",
            ),
        ) == "incompatible"
        assert service._check_signature_compatibility(
            original,
            FunctionSignature(
                name="bad",
                parameters=[
                    ("ticket_id", "str", False),
                    ("extra", "str", False),
                ],
                return_type="str",
            ),
        ) == "incompatible"

    def test_types_compatible_handles_optional_unions(self):
        service = WorkflowOrphanService(db=None)

        assert service._types_compatible("str", "str")
        assert service._types_compatible("str", "str | None")
        assert service._types_compatible("Optional[str]", "str")
        assert not service._types_compatible("int", "str")

    def test_props_contain_workflow_recurses_nested_dicts_and_lists(self):
        service = WorkflowOrphanService(db=None)

        assert service._props_contain_workflow(
            {"children": [{"props": {"dataProviderId": "wf-1"}}]},
            "wf-1",
        )
        assert service._props_contain_workflow({"workflowId": "wf-1"}, "wf-1")
        assert not service._props_contain_workflow({"workflowId": "wf-2"}, "wf-1")
        assert not service._props_contain_workflow(None, "wf-1")
