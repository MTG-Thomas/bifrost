"""
Integration tests for Bifrost SDK from workflows

Tests that user workflows can import and use the bifrost SDK.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Import bifrost context functions directly
# This ensures we use the same ContextVar instance that storage module uses
from bifrost._context import set_execution_context, clear_execution_context, get_execution_context


# The test is mounted at ``/app/tests`` in the Dockerized test runner, while
# host-side paths include the repository's ``api/tests`` prefix.  In both
# environments the SDK package root is three parents above this file.
SDK_ROOT = Path(__file__).resolve().parents[3]
TEST_ENVS = {**os.environ, "PYTHONPATH": str(SDK_ROOT)}


def _run_import_probe(script: str) -> dict[str, object]:
    code = "import json\n" + textwrap.dedent(script)

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(SDK_ROOT),
        env=TEST_ENVS,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())



@pytest.fixture
def test_context():
    """Create a test execution context"""
    from src.sdk.context import ExecutionContext, Organization

    org = Organization(id="test-org", name="Test Org", is_active=True)
    return ExecutionContext(
        user_id="test-user",
        email="test@example.com",
        name="Test User",
        scope="test-org",
        organization=org,
        is_platform_admin=False,
        is_function_key=False,
        execution_id="test-exec-123"
    )


@pytest.fixture
def admin_context():
    """Create an admin execution context"""
    from src.sdk.context import ExecutionContext, Organization

    org = Organization(id="test-org", name="Test Org", is_active=True)
    return ExecutionContext(
        user_id="admin-user",
        email="admin@example.com",
        name="Admin User",
        scope="test-org",
        organization=org,
        is_platform_admin=True,
        is_function_key=False,
        execution_id="test-exec-456"
    )


class TestSDKImportsFromWorkflow:
    """Test that SDK can be imported from workflow code"""

    def test_import_bifrost_organizations(self):
        """Test importing organizations module"""
        from bifrost import organizations

        # Verify module has expected methods
        assert hasattr(organizations, 'create')
        assert hasattr(organizations, 'get')
        assert hasattr(organizations, 'list')
        assert hasattr(organizations, 'update')

    def test_import_bifrost_workflows(self):
        """Test importing workflows module"""
        from bifrost import workflows

        assert hasattr(workflows, 'list')
        assert hasattr(workflows, 'get')

    def test_import_bifrost_files(self):
        """Test importing files module"""
        from bifrost import files

        assert hasattr(files, 'read')
        assert hasattr(files, 'write')
        assert hasattr(files, 'list')
        assert hasattr(files, 'delete')
        assert hasattr(files, 'exists')

    def test_import_bifrost_forms(self):
        """Test importing forms module"""
        from bifrost import forms

        assert hasattr(forms, 'list')
        assert hasattr(forms, 'get')

    def test_import_order_import_module_then_package_subprocess(self):
        """Import `bifrost.executions` then `from bifrost import executions` in isolation."""
        result = _run_import_probe(
            """
            import bifrost.executions
            from bifrost import executions

            import bifrost
            print(json.dumps({
                "type_name": type(executions).__name__,
                "is_class": isinstance(executions, type),
                "has_list": hasattr(executions, "list"),
                "package_attr_is_same": executions is bifrost.executions,
                "package_attr_type": type(bifrost.executions).__name__,
            }))
            """
        )

        assert result["is_class"] is True
        assert result["type_name"] == "type"
        assert result["has_list"] is True
        assert result["package_attr_is_same"] is True
        assert result["package_attr_type"] == "type"

    def test_import_order_import_submodule_then_package_subprocess(self):
        """Import `from bifrost.executions import executions` then `from bifrost import executions`."""
        result = _run_import_probe(
            """
            from bifrost.executions import executions as submodule_executions
            from bifrost import executions

            import bifrost
            print(json.dumps({
                "is_same_object": submodule_executions is executions,
                "package_attr_is_same": executions is bifrost.executions,
                "package_attr_type": type(bifrost.executions).__name__,
                "type_name": type(executions).__name__,
                "has_list": hasattr(executions, "list"),
            }))
            """
        )

        assert result["is_same_object"] is True
        assert result["package_attr_is_same"] is True
        assert result["package_attr_type"] == "type"
        assert result["type_name"] == "type"
        assert result["has_list"] is True

    def test_import_order_import_package_then_module_subprocess(self):
        """Import `from bifrost import executions` then `from bifrost.executions import executions`."""
        result = _run_import_probe(
            """
            from bifrost import executions as package_executions
            from bifrost.executions import executions as module_executions

            print(json.dumps({
                "is_same_object": package_executions is module_executions,
                "type_name": type(module_executions).__name__,
                "has_list": hasattr(module_executions, "list"),
                "has_get_current_logs": hasattr(module_executions, "get_current_logs"),
            }))
            """
        )

        assert result["is_same_object"] is True
        assert result["type_name"] == "type"
        assert result["has_list"] is True
        assert result["has_get_current_logs"] is True

    def test_import_executions_class_alias_identity_subprocess(self):
        """Assert canonical class and alias are identical in isolated interpreter."""
        result = _run_import_probe(
            """
            from bifrost.executions import Executions, executions

            print(json.dumps({
                "alias_identity": Executions is executions,
                "is_class": isinstance(executions, type),
                "has_list": hasattr(executions, "list"),
                "has_get": hasattr(executions, "get"),
                "class_name": Executions.__name__,
            }))
            """
        )

        assert result["alias_identity"] is True
        assert result["is_class"] is True
        assert result["class_name"] == "Executions"
        assert result["has_list"] is True
        assert result["has_get"] is True

    def test_import_bifrost_roles(self):
        """Test importing roles module"""
        from bifrost import roles

        assert hasattr(roles, 'create')
        assert hasattr(roles, 'get')
        assert hasattr(roles, 'list')
        assert hasattr(roles, 'update')
        assert hasattr(roles, 'delete')
        assert hasattr(roles, 'assign_users')
        assert hasattr(roles, 'assign_forms')


class TestSDKUsageFromWorkflow:
    """Test SDK usage patterns from workflow code"""

    @pytest.mark.asyncio
    async def test_workflow_can_use_sdk_context(self, test_context):
        """Test that workflow can access SDK with context"""

        # Set context (simulates what workflow engine does)
        set_execution_context(test_context)

        try:
            # In a real scenario with database, this would work
            # For now, we verify the context is accessible
            context = get_execution_context()
            assert context.org_id == "test-org"
            assert context.user_id == "test-user"
        finally:
            clear_execution_context()

    async def test_sdk_without_context_raises_error(self):
        """Test that SDK raises clear error when used without context"""
        from bifrost import organizations
        from bifrost.client import _clear_client

        # Ensure no context is set and no client injected
        clear_execution_context()
        _clear_client()

        # Attempting to use SDK should raise RuntimeError about not being logged in
        with pytest.raises(RuntimeError, match="Not logged in"):
            await organizations.list()


class TestImportRestrictions:
    """Test that import restrictions work correctly"""

    def test_home_code_cannot_import_src_directly(self):
        """Test that code in /home cannot import from src.*"""
        # This would need to be tested with actual files in /home
        # For now, we verify the restrictor is configured correctly

        from src.services.execution.import_restrictor import get_active_restrictors

        restrictors = get_active_restrictors()

        if restrictors:
            restrictor = restrictors[0]
            # Verify blocked prefixes include 'src.'
            assert 'src.' in restrictor.BLOCKED_PREFIXES

            # Verify bifrost is in allowed exports
            assert 'bifrost' in restrictor.ALLOWED_EXPORTS

    def test_bifrost_modules_are_whitelisted(self):
        """Test that bifrost SDK modules are whitelisted for import"""
        from src.services.execution.import_restrictor import WorkspaceImportRestrictor

        # Check that bifrost modules are in whitelist
        assert 'bifrost' in WorkspaceImportRestrictor.ALLOWED_EXPORTS
        assert 'bifrost.organizations' in WorkspaceImportRestrictor.ALLOWED_EXPORTS
        assert 'bifrost.workflows' in WorkspaceImportRestrictor.ALLOWED_EXPORTS
        assert 'bifrost.files' in WorkspaceImportRestrictor.ALLOWED_EXPORTS
        assert 'bifrost.forms' in WorkspaceImportRestrictor.ALLOWED_EXPORTS
        assert 'bifrost.executions' in WorkspaceImportRestrictor.ALLOWED_EXPORTS
        assert 'bifrost.roles' in WorkspaceImportRestrictor.ALLOWED_EXPORTS


class TestEndToEndSDKUsage:
    """End-to-end tests of SDK usage patterns"""

    @pytest.mark.asyncio
    async def test_complete_workflow_sdk_pattern(self, test_context):
        """
        Test complete pattern: context setup, SDK usage, context teardown.

        This simulates what happens in a real workflow execution.
        """
        from bifrost import organizations

        # 1. Workflow engine sets context
        set_execution_context(test_context)

        try:
            # 2. Workflow uses SDK
            # (Would normally interact with database)

            # Verify context is accessible using get_execution_context
            ctx = get_execution_context()
            assert ctx.org_id == "test-org"

            # 3. SDK operations would happen here
            # organizations.list()
            # files.write("output.txt", b"data")

        finally:
            # 4. Workflow engine clears context
            clear_execution_context()

        # 5. After context cleared, SDK should raise error
        with pytest.raises(RuntimeError):
            await organizations.list()
