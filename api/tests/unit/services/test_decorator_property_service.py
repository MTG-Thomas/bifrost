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


class TestStripIds:

    def test_strip_id_from_decorator_with_multiple_args(self):
        source = '@workflow(id="abc-123", name="test")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.strip_ids(source)

        assert result.modified is True
        assert "abc-123" not in result.new_content
        assert 'name="test"' in result.new_content
        assert "@workflow(" in result.new_content

    def test_strip_id_only_arg_converts_to_bare(self):
        source = '@workflow(id="abc-123")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.strip_ids(source)

        assert result.modified is True
        assert "abc-123" not in result.new_content
        assert "@workflow\n" in result.new_content

    def test_strip_no_id_present(self):
        source = '@workflow(name="test")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.strip_ids(source)

        assert result.modified is False
        assert result.new_content == source

    def test_strip_bare_decorator_unchanged(self):
        source = '@workflow\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.strip_ids(source)

        assert result.modified is False
        assert result.new_content == source

    def test_strip_ids_from_multiple_functions(self):
        source = (
            '@workflow(id="aaa", name="A")\n'
            'def func_a():\n    pass\n\n'
            '@data_provider(id="bbb", name="B")\n'
            'def func_b():\n    pass\n'
        )
        service = get_service()
        result = service.strip_ids(source)

        assert result.modified is True
        assert "aaa" not in result.new_content
        assert "bbb" not in result.new_content
        assert 'name="A"' in result.new_content
        assert 'name="B"' in result.new_content
        assert len(result.changes) == 2

    def test_strip_id_preserves_other_args_order(self):
        source = '@workflow(name="X", id="abc", category="Y")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.strip_ids(source)

        assert result.modified is True
        decorators = service.read_decorators(result.new_content)
        assert decorators[0].properties == {"name": "X", "category": "Y"}

    def test_strip_id_changes_list_describes_removal(self):
        source = '@workflow(id="abc-123", name="test")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.strip_ids(source)

        assert len(result.changes) == 1
        assert "Removed id" in result.changes[0]
        assert "my_func" in result.changes[0]

    def test_strip_id_handles_parse_error(self):
        source = '@workflow(\n    broken syntax!!!\n)\ndef f():\n    pass\n'
        service = get_service()
        result = service.strip_ids(source)

        assert result.modified is False
        assert result.new_content == source
        assert "Parse error" in result.changes[0]

    def test_strip_ignores_unsupported_decorators(self):
        source = '@custom(id="abc")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.strip_ids(source)

        assert result.modified is False
        assert 'id="abc"' in result.new_content

    def test_strip_tool_decorator(self):
        source = '@tool(id="tool-id", name="My Tool")\ndef my_tool():\n    pass\n'
        service = get_service()
        result = service.strip_ids(source)

        assert result.modified is True
        assert "tool-id" not in result.new_content
        assert 'name="My Tool"' in result.new_content


class TestInjectSpecificIds:

    def test_inject_into_bare_decorator(self):
        source = '@workflow\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.inject_specific_ids(source, {"my_func": "injected-id"})

        assert result.modified is True
        assert "injected-id" in result.new_content
        decorators = service.read_decorators(result.new_content)
        assert decorators[0].properties["id"] == "injected-id"

    def test_inject_into_decorator_with_args(self):
        source = '@workflow(name="test")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.inject_specific_ids(source, {"my_func": "injected-id"})

        assert result.modified is True
        decorators = service.read_decorators(result.new_content)
        assert decorators[0].properties["id"] == "injected-id"
        assert decorators[0].properties["name"] == "test"

    def test_inject_skips_functions_not_in_map(self):
        source = (
            '@workflow(name="A")\ndef func_a():\n    pass\n\n'
            '@workflow(name="B")\ndef func_b():\n    pass\n'
        )
        service = get_service()
        result = service.inject_specific_ids(source, {"func_a": "id-for-a"})

        assert result.modified is True
        decorators = service.read_decorators(result.new_content)
        func_a = next(d for d in decorators if d.function_name == "func_a")
        func_b = next(d for d in decorators if d.function_name == "func_b")
        assert func_a.properties["id"] == "id-for-a"
        assert "id" not in func_b.properties

    def test_inject_skips_if_id_already_present(self):
        source = '@workflow(id="existing", name="test")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.inject_specific_ids(source, {"my_func": "new-id"})

        assert result.modified is False
        assert "existing" in result.new_content
        assert "new-id" not in result.new_content

    def test_inject_empty_map_no_changes(self):
        source = '@workflow(name="test")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.inject_specific_ids(source, {})

        assert result.modified is False
        assert result.new_content == source

    def test_inject_changes_list_describes_injection(self):
        source = '@workflow\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.inject_specific_ids(source, {"my_func": "the-id"})

        assert len(result.changes) == 1
        assert "Injected" in result.changes[0]
        assert "the-id" in result.changes[0]
        assert "my_func" in result.changes[0]

    def test_inject_multiple_functions(self):
        source = (
            '@workflow\ndef func_a():\n    pass\n\n'
            '@data_provider\ndef func_b():\n    pass\n'
        )
        service = get_service()
        result = service.inject_specific_ids(
            source, {"func_a": "id-a", "func_b": "id-b"}
        )

        assert result.modified is True
        assert len(result.changes) == 2
        decorators = service.read_decorators(result.new_content)
        func_a = next(d for d in decorators if d.function_name == "func_a")
        func_b = next(d for d in decorators if d.function_name == "func_b")
        assert func_a.properties["id"] == "id-a"
        assert func_b.properties["id"] == "id-b"

    def test_inject_handles_parse_error(self):
        source = '@workflow(\n    broken!!!\n)\ndef f():\n    pass\n'
        service = get_service()
        result = service.inject_specific_ids(source, {"f": "some-id"})

        assert result.modified is False
        assert "Parse error" in result.changes[0]

    def test_inject_ignores_unsupported_decorators(self):
        source = '@custom\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.inject_specific_ids(source, {"my_func": "some-id"})

        assert result.modified is False


class TestWritePropertyResult:

    def test_write_returns_changes_list(self):
        source = '@workflow(name="X")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.write_properties(source, "my_func", {"category": "Y"})

        assert result.modified is True
        assert len(result.changes) == 1
        assert "Added" in result.changes[0]
        assert "category" in result.changes[0]

    def test_update_returns_updated_change(self):
        source = '@workflow(name="Old")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.write_properties(source, "my_func", {"name": "New"})

        assert result.modified is True
        assert len(result.changes) == 1
        assert "Updated" in result.changes[0]

    def test_no_matching_function_returns_unmodified(self):
        source = '@workflow(name="X")\ndef my_func():\n    pass\n'
        service = get_service()
        result = service.write_properties(source, "other_func", {"name": "Y"})

        assert result.modified is False
        assert result.new_content == source
        assert result.changes == []


class TestFormattingPreservation:

    def test_strip_ids_preserves_comments(self):
        source = (
            '# This is a workflow file\n'
            '@workflow(id="abc", name="test")  # my decorator\n'
            'def my_func():\n'
            '    # body comment\n'
            '    pass\n'
        )
        service = get_service()
        result = service.strip_ids(source)

        assert "# This is a workflow file" in result.new_content
        assert "# body comment" in result.new_content

    def test_write_preserves_body_indentation(self):
        source = (
            '@workflow(name="test")\n'
            'def my_func():\n'
            '    x = 1\n'
            '    if x:\n'
            '        y = 2\n'
        )
        service = get_service()
        result = service.write_properties(source, "my_func", {"category": "Admin"})

        assert "    x = 1\n" in result.new_content
        assert "        y = 2\n" in result.new_content

    def test_inject_preserves_blank_lines_between_functions(self):
        source = (
            '@workflow\n'
            'def func_a():\n'
            '    pass\n'
            '\n'
            '\n'
            '@workflow\n'
            'def func_b():\n'
            '    pass\n'
        )
        service = get_service()
        result = service.inject_specific_ids(source, {"func_a": "id-a"})

        assert "\n\n\n" in result.new_content

    def test_roundtrip_inject_then_strip(self):
        original = '@workflow(name="test")\ndef my_func():\n    pass\n'
        service = get_service()

        injected = service.inject_specific_ids(original, {"my_func": "roundtrip-id"})
        assert injected.modified is True
        assert "roundtrip-id" in injected.new_content

        stripped = service.strip_ids(injected.new_content)
        assert stripped.modified is True
        assert "roundtrip-id" not in stripped.new_content
        assert 'name="test"' in stripped.new_content

    def test_strip_then_inject_roundtrip(self):
        source = '@workflow(id="old-id", name="test")\ndef my_func():\n    pass\n'
        service = get_service()

        stripped = service.strip_ids(source)
        assert "old-id" not in stripped.new_content

        injected = service.inject_specific_ids(
            stripped.new_content, {"my_func": "new-id"}
        )
        assert injected.modified is True
        decorators = service.read_decorators(injected.new_content)
        assert decorators[0].properties["id"] == "new-id"
        assert decorators[0].properties["name"] == "test"
