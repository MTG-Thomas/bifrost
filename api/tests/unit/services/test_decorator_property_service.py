"""Unit tests for DecoratorPropertyService."""

import sys

import pytest


@pytest.fixture(autouse=True)
def reset_libcst_and_service():
    """
    Force a fresh import of libcst and DecoratorPropertyService before each test.

    This is necessary because libcst's native parser can get into a corrupted
    state when other tests in the suite trigger parsing errors. The parser
    maintains internal state that can persist incorrectly across calls.
    """
    # Remove all libcst modules from cache
    libcst_modules = [k for k in list(sys.modules.keys()) if k.startswith('libcst')]
    for mod in libcst_modules:
        del sys.modules[mod]

    # Also remove the decorator property service to force reimport with fresh libcst
    service_key = 'src.services.decorator_property_service'
    if service_key in sys.modules:
        del sys.modules[service_key]

    yield


def get_service():
    """Import and return a fresh DecoratorPropertyService instance."""
    # Import fresh each time to get fresh libcst
    from src.services.decorator_property_service import DecoratorPropertyService
    return DecoratorPropertyService()


class TestDecoratorPropertyReader:
    """Test reading decorator properties."""

    def test_read_bare_workflow_decorator(self):
        """Test reading @workflow without parentheses."""
        content = '''
@workflow
async def my_workflow():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        assert len(decorators) == 1
        assert decorators[0].function_name == "my_workflow"
        assert decorators[0].decorator_type == "workflow"
        assert decorators[0].properties == {}
        assert decorators[0].has_parentheses is False

    def test_read_workflow_with_id(self):
        """Test reading @workflow(id="...")."""
        content = '''
@workflow(id="abc-123")
async def my_workflow():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        assert len(decorators) == 1
        assert decorators[0].properties == {"id": "abc-123"}
        assert decorators[0].has_parentheses is True

    def test_read_workflow_with_multiple_properties(self):
        """Test reading @workflow with multiple properties."""
        content = '''
@workflow(id="abc-123", name="My Workflow", category="Admin", tags=["admin", "m365"])
async def my_workflow():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        assert len(decorators) == 1
        assert decorators[0].properties == {
            "id": "abc-123",
            "name": "My Workflow",
            "category": "Admin",
            "tags": ["admin", "m365"],
        }

    def test_read_data_provider_decorator(self):
        """Test reading @data_provider decorator."""
        content = '''
@data_provider(name="Get Users", category="m365")
async def get_users():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        assert len(decorators) == 1
        assert decorators[0].decorator_type == "data_provider"
        assert decorators[0].function_name == "get_users"
        assert decorators[0].properties == {
            "name": "Get Users",
            "category": "m365",
        }

    def test_read_tool_decorator(self):
        """Test reading @tool decorator."""
        content = '''
@tool(name="Search Users")
async def search_users(query: str):
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        assert len(decorators) == 1
        assert decorators[0].decorator_type == "tool"
        assert decorators[0].properties == {"name": "Search Users"}

    def test_read_multiple_decorators_in_file(self):
        """Test reading multiple decorators from one file."""
        content = '''
@workflow
async def workflow_a():
    pass

@workflow(name="Workflow B", category="Admin")
async def workflow_b():
    pass

@data_provider
async def provider_a():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        assert len(decorators) == 3

        # workflow_a
        assert decorators[0].function_name == "workflow_a"
        assert decorators[0].decorator_type == "workflow"
        assert decorators[0].has_parentheses is False

        # workflow_b
        assert decorators[1].function_name == "workflow_b"
        assert decorators[1].decorator_type == "workflow"
        assert decorators[1].properties == {"name": "Workflow B", "category": "Admin"}

        # provider_a
        assert decorators[2].function_name == "provider_a"
        assert decorators[2].decorator_type == "data_provider"

    def test_read_properties_for_specific_function(self):
        """Test reading properties for a specific function."""
        content = '''
@workflow(id="aaa", name="Workflow A")
async def workflow_a():
    pass

@workflow(id="bbb", name="Workflow B")
async def workflow_b():
    pass
'''
        service = get_service()

        props_a = service.read_properties(content, "workflow_a", "workflow")
        assert props_a == {"id": "aaa", "name": "Workflow A"}

        props_b = service.read_properties(content, "workflow_b", "workflow")
        assert props_b == {"id": "bbb", "name": "Workflow B"}

        props_none = service.read_properties(content, "nonexistent", "workflow")
        assert props_none is None

    def test_read_boolean_properties(self):
        """Test reading boolean properties."""
        content = '''
@workflow(endpoint_enabled=True, public_endpoint=False)
async def my_workflow():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        assert decorators[0].properties == {
            "endpoint_enabled": True,
            "public_endpoint": False,
        }

    def test_read_integer_properties(self):
        """Test reading integer properties."""
        content = '''
@workflow(timeout_seconds=3600)
async def my_workflow():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        assert decorators[0].properties == {"timeout_seconds": 3600}

    def test_ignores_non_supported_decorators(self):
        """Test that non-workflow/data_provider decorators are ignored."""
        content = '''
@some_other_decorator
@workflow(id="abc")
@another_decorator(foo="bar")
async def my_workflow():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        # Should only find the @workflow decorator
        assert len(decorators) == 1
        assert decorators[0].decorator_type == "workflow"

    def test_handles_syntax_error_gracefully(self):
        """Test that syntax errors don't crash the reader."""
        content = '''
@workflow(
    invalid syntax here!!!
)
async def my_workflow():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        # Should return empty list, not crash
        assert decorators == []


class TestDecoratorPropertyTransformer:
    """Test transforming/modifying decorators."""

    def test_write_specific_property(self):
        """Test writing a specific property to a decorator."""
        content = '''@workflow(name="Original")
async def my_workflow():
    pass
'''
        service = get_service()
        result = service.write_properties(
            content,
            "my_workflow",
            {"category": "Updated Category"},
        )

        assert result.modified is True
        # LibCST adds spaces around = when adding new properties
        assert "category" in result.new_content and "Updated Category" in result.new_content
        assert "Original" in result.new_content

    def test_update_existing_property(self):
        """Test updating an existing property value."""
        content = '''@workflow(name="Original", category="Old Category")
async def my_workflow():
    pass
'''
        service = get_service()
        result = service.write_properties(
            content,
            "my_workflow",
            {"category": "New Category"},
        )

        assert result.modified is True
        assert 'category="New Category"' in result.new_content
        assert "Old Category" not in result.new_content

    def test_write_property_to_bare_decorator(self):
        """Test writing a property converts bare decorator to call."""
        content = '''@workflow
async def my_workflow():
    pass
'''
        service = get_service()
        result = service.write_properties(
            content,
            "my_workflow",
            {"id": "custom-id"},
        )

        assert result.modified is True
        # LibCST adds spaces around = for new arguments
        assert "@workflow(" in result.new_content
        assert "custom-id" in result.new_content

    def test_target_specific_function(self):
        """Test that property writes target the correct function."""
        content = '''@workflow(name="A")
async def workflow_a():
    pass

@workflow(name="B")
async def workflow_b():
    pass
'''
        service = get_service()
        result = service.write_properties(
            content,
            "workflow_a",
            {"category": "Updated"},
        )

        assert result.modified is True
        # Only workflow_a should be modified
        decorators = service.read_decorators(result.new_content)

        # Find workflow_a
        workflow_a = next(d for d in decorators if d.function_name == "workflow_a")
        assert workflow_a.properties.get("category") == "Updated"

        # workflow_b should be unchanged
        workflow_b = next(d for d in decorators if d.function_name == "workflow_b")
        assert "category" not in workflow_b.properties

    def test_write_list_property(self):
        """Test writing a list property."""
        content = '''@workflow
async def my_workflow():
    pass
'''
        service = get_service()
        result = service.write_properties(
            content,
            "my_workflow",
            {"tags": ["admin", "m365", "important"]},
        )

        assert result.modified is True
        # LibCST adds spaces around = for new arguments
        assert "tags" in result.new_content
        assert "admin" in result.new_content

    def test_write_boolean_property(self):
        """Test writing a boolean property."""
        content = '''@workflow
async def my_workflow():
    pass
'''
        service = get_service()
        result = service.write_properties(
            content,
            "my_workflow",
            {"endpoint_enabled": True},
        )

        assert result.modified is True
        # LibCST adds spaces around = for new arguments
        assert "endpoint_enabled" in result.new_content
        assert "True" in result.new_content

    def test_handles_parse_error_in_write(self):
        """Test that parse errors don't crash write operations."""
        content = '''@workflow(
    broken syntax
)
async def my_workflow():
    pass
'''
        service = get_service()
        result = service.write_properties(
            content,
            "my_workflow",
            {"id": "test"},
        )

        # Should not crash, should return unmodified
        assert result.modified is False
        assert "Parse error" in result.changes[0]


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_file(self):
        """Test handling of empty file."""
        service = get_service()
        decorators = service.read_decorators("")
        assert decorators == []

    def test_file_with_no_decorators(self):
        """Test file with no workflow/data_provider decorators."""
        content = '''
def regular_function():
    pass

class MyClass:
    def method(self):
        pass
'''
        service = get_service()
        decorators = service.read_decorators(content)
        assert decorators == []

    def test_nested_function_with_decorator(self):
        """Test decorator on nested function is detected."""
        content = '''
def outer():
    @workflow
    async def inner():
        pass
'''
        service = get_service()
        decorators = service.read_decorators(content)
        assert len(decorators) == 1
        assert decorators[0].function_name == "inner"

    def test_class_method_with_decorator(self):
        """Test decorator on class method is detected."""
        content = '''
class MyWorkflows:
    @workflow
    async def my_method(self):
        pass
'''
        service = get_service()
        decorators = service.read_decorators(content)
        assert len(decorators) == 1
        assert decorators[0].function_name == "my_method"

    def test_module_prefixed_decorator(self):
        """Test decorator with module prefix like bifrost.workflow."""
        content = '''
import bifrost

@bifrost.workflow
async def my_workflow():
    pass
'''
        service = get_service()
        decorators = service.read_decorators(content)

        # Should recognize bifrost.workflow as workflow
        assert len(decorators) == 1
        assert decorators[0].decorator_type == "workflow"
