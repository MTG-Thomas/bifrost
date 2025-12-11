"""
Unit tests for workflow validation service.

Tests the validate_workflow_file function which validates workflow files
for syntax errors, decorator issues, and Pydantic validation.
"""

import pytest

from src.services.workflow_validation import (
    validate_workflow_file,
    _convert_workflow_metadata_to_model,
    _extract_relative_path,
)
from src.services.execution.module_loader import WorkflowMetadata, WorkflowParameter


class TestExtractRelativePath:
    """Test _extract_relative_path helper function"""

    def test_extract_from_home_path(self):
        """Test extraction from /home/ path"""
        result = _extract_relative_path("/home/user/workflows/my_workflow.py")
        assert result == "/workspace/user/workflows/my_workflow.py"

    def test_extract_from_platform_path(self):
        """Test extraction from /platform/ path"""
        result = _extract_relative_path("/platform/workflows/my_workflow.py")
        assert result == "/workspace/workflows/my_workflow.py"

    def test_extract_from_workspace_path(self):
        """Test extraction from path already containing /workspace/"""
        result = _extract_relative_path("/some/path/workspace/workflows/my_workflow.py")
        assert result == "/workspace/workflows/my_workflow.py"

    def test_none_input(self):
        """Test None input returns None"""
        result = _extract_relative_path(None)
        assert result is None

    def test_empty_string(self):
        """Test empty string returns None"""
        result = _extract_relative_path("")
        assert result is None


class TestConvertWorkflowMetadataToModel:
    """Test _convert_workflow_metadata_to_model conversion function"""

    def test_basic_conversion(self):
        """Test basic metadata conversion without parameters"""
        metadata = WorkflowMetadata(
            id="test-uuid",
            name="test_workflow",
            description="Test workflow description",
            category="Testing",
            tags=["test"],
            execution_mode="sync",
            timeout_seconds=300,
            source_file_path="/home/test/workflows/test.py"
        )

        result = _convert_workflow_metadata_to_model(metadata)

        assert result.id == "test-uuid"
        assert result.name == "test_workflow"
        assert result.description == "Test workflow description"
        assert result.category == "Testing"
        assert result.tags == ["test"]
        assert result.execution_mode == "sync"
        assert result.timeout_seconds == 300

    def test_conversion_with_parameters(self):
        """Test conversion includes workflow parameters correctly"""
        metadata = WorkflowMetadata(
            id="test-uuid",
            name="test_workflow",
            description="Test",
            parameters=[
                WorkflowParameter(
                    name="name",
                    type="string",
                    required=True,
                    label="Name",
                    default_value=None
                ),
                WorkflowParameter(
                    name="count",
                    type="int",
                    required=False,
                    label="Count",
                    default_value=1
                ),
            ]
        )

        result = _convert_workflow_metadata_to_model(metadata)

        assert len(result.parameters) == 2

        name_param = result.parameters[0]
        assert name_param.name == "name"
        assert name_param.type == "string"
        assert name_param.required is True
        assert name_param.label == "Name"

        count_param = result.parameters[1]
        assert count_param.name == "count"
        assert count_param.type == "int"
        assert count_param.required is False
        assert count_param.default_value == 1

    def test_conversion_handles_missing_optional_fields(self):
        """Test conversion handles parameters with only required fields.

        This test verifies the fix for the AttributeError when workflow parameters
        don't have form-specific fields like data_provider, help_text, validation.
        """
        # WorkflowParameter dataclass only has: name, type, label, required, default_value
        # It does NOT have: data_provider, help_text, validation, options
        # The conversion function should handle this gracefully
        metadata = WorkflowMetadata(
            id="test-uuid",
            name="test_workflow",
            description="Test",
            parameters=[
                WorkflowParameter(
                    name="simple_param",
                    type="string",
                    required=True,
                )
            ]
        )

        # This should NOT raise AttributeError
        result = _convert_workflow_metadata_to_model(metadata)

        assert len(result.parameters) == 1
        assert result.parameters[0].name == "simple_param"


class TestValidateWorkflowFile:
    """Test validate_workflow_file function"""

    @pytest.fixture
    def temp_workspace(self, tmp_path):
        """Create a workspace directory at the hardcoded path"""
        from pathlib import Path
        workspace = Path("/tmp/bifrost/workspace")
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    @pytest.mark.asyncio
    async def test_valid_workflow_passes_validation(self, temp_workspace):
        """Test that a valid workflow file passes validation"""
        workflow_content = '''
"""Test workflow"""

from bifrost import workflow

@workflow(
    category="testing",
    tags=["test"],
)
async def test_valid_workflow(name: str) -> dict:
    """A simple test workflow."""
    return {"greeting": f"Hello, {name}!"}
'''
        # Create the workflow file
        workflow_file = temp_workspace / "test_workflow.py"
        workflow_file.write_text(workflow_content)

        result = await validate_workflow_file("test_workflow.py")

        assert result.valid is True
        assert result.metadata is not None
        assert result.metadata.name == "test_valid_workflow"
        # Should have warnings about category and tags being default, but not errors
        errors = [i for i in result.issues if i.severity == "error"]
        assert len(errors) == 0

    @pytest.mark.asyncio
    async def test_syntax_error_fails_validation(self, temp_workspace):
        """Test that syntax errors are caught"""
        workflow_content = '''
"""Invalid syntax"""

def test_workflow(
    # Missing closing paren
'''
        workflow_file = temp_workspace / "invalid_syntax.py"
        workflow_file.write_text(workflow_content)

        result = await validate_workflow_file("invalid_syntax.py")

        assert result.valid is False
        assert any("Syntax error" in i.message for i in result.issues)

    @pytest.mark.asyncio
    async def test_missing_decorator_fails_validation(self, temp_workspace):
        """Test that missing @workflow decorator is caught"""
        workflow_content = '''
"""No decorator"""

async def test_workflow(name: str) -> dict:
    """A test workflow without decorator."""
    return {"name": name}
'''
        workflow_file = temp_workspace / "no_decorator.py"
        workflow_file.write_text(workflow_content)

        result = await validate_workflow_file("no_decorator.py")

        assert result.valid is False
        assert any("No @workflow decorator found" in i.message for i in result.issues)

    @pytest.mark.asyncio
    async def test_invalid_workflow_name_fails_validation(self, temp_workspace):
        """Test that invalid workflow names (not snake_case) are caught"""
        workflow_content = '''
"""Invalid name"""

from bifrost import workflow

@workflow(
    name="InvalidCamelCase",
    category="testing",
)
async def invalid_workflow(name: str) -> dict:
    """Test workflow with invalid name."""
    return {"name": name}
'''
        workflow_file = temp_workspace / "invalid_name.py"
        workflow_file.write_text(workflow_content)

        result = await validate_workflow_file("invalid_name.py")

        assert result.valid is False
        assert any("Invalid workflow name" in i.message for i in result.issues)

    @pytest.mark.asyncio
    async def test_validation_with_content_parameter(self, temp_workspace):
        """Test validation using content parameter instead of reading from disk"""
        workflow_content = '''
"""Test workflow"""

from bifrost import workflow

@workflow(
    category="testing",
    tags=["test"],
)
async def content_test_workflow(value: int = 1) -> dict:
    """A workflow validated via content parameter."""
    return {"doubled": value * 2}
'''
        # Pass content directly, path is just for display
        result = await validate_workflow_file(
            "fake_path.py",
            content=workflow_content
        )

        assert result.valid is True
        assert result.metadata is not None
        assert result.metadata.name == "content_test_workflow"

    @pytest.mark.asyncio
    async def test_file_not_found_fails_validation(self, temp_workspace):
        """Test that non-existent file fails validation"""
        result = await validate_workflow_file("nonexistent_workflow.py")

        assert result.valid is False
        assert any("File not found" in i.message for i in result.issues)

    @pytest.mark.asyncio
    async def test_invalid_execution_mode_fails_validation(self, temp_workspace):
        """Test that invalid execution mode is caught"""
        workflow_content = '''
"""Invalid execution mode"""

from bifrost import workflow

@workflow(
    execution_mode="invalid_mode",
    category="testing",
)
async def test_workflow(name: str) -> dict:
    """Test workflow."""
    return {"name": name}
'''
        result = await validate_workflow_file(
            "test.py",
            content=workflow_content
        )

        assert result.valid is False
        assert any("Invalid execution mode" in i.message for i in result.issues)

    @pytest.mark.asyncio
    async def test_missing_description_warning(self, temp_workspace):
        """Test that missing description generates an error"""
        workflow_content = '''
"""Module docstring"""

from bifrost import workflow

@workflow(
    category="testing",
)
async def no_description_workflow(name: str):
    # No docstring on function
    return {"name": name}
'''
        result = await validate_workflow_file(
            "test.py",
            content=workflow_content
        )

        # The decorator extracts description from docstring first line
        # If no docstring, description will be empty which is an error
        assert result.valid is False
        assert any("description is required" in i.message.lower() for i in result.issues)
