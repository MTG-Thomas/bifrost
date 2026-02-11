"""
Integration tests for virtual import system.

Tests the complete flow of:
1. Worker loading modules from Redis cache
2. Virtual import hook integration
"""

import sys

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture(autouse=True)
def reset_redis_client():
    """Reset the Redis client singleton between tests to avoid event loop issues."""
    import src.core.redis_client as redis_module

    # Reset before test
    redis_module._redis_client = None
    yield
    # Reset after test
    redis_module._redis_client = None


@pytest.mark.integration
class TestVirtualImportIntegration:
    """Integration tests for virtual import from Redis cache."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Clean up after each test."""
        yield
        # Remove virtual import hooks
        sys.meta_path = [
            finder
            for finder in sys.meta_path
            if not finder.__class__.__name__ == "VirtualModuleFinder"
        ]
        # Remove test modules from sys.modules
        to_remove = [k for k in sys.modules if k.startswith("integration_test_")]
        for k in to_remove:
            del sys.modules[k]
        # Reset global finder
        import src.services.execution.virtual_import as module

        module._finder = None

    @pytest.mark.asyncio
    async def test_virtual_import_loads_from_redis(self, db_session: AsyncSession):
        """Test importing a module via virtual import hook from Redis cache."""
        from src.core.module_cache import clear_module_cache, set_module
        from src.services.execution.virtual_import import (
            install_virtual_import_hook,
            remove_virtual_import_hook,
        )

        # Set up module in Redis cache
        await clear_module_cache()
        await set_module(
            path="integration_test_virtual.py",
            content='VIRTUAL_IMPORT_VALUE = "loaded_from_redis"\ndef test_func(): return 42',
            content_hash="test123",
        )

        # Install virtual import hook
        install_virtual_import_hook()

        try:
            # Import the module - should be loaded from Redis
            import integration_test_virtual  # type: ignore[import-not-found]

            assert integration_test_virtual.VIRTUAL_IMPORT_VALUE == "loaded_from_redis"
            assert integration_test_virtual.test_func() == 42
            # Virtual modules use relative paths, not absolute filesystem paths
            assert integration_test_virtual.__file__ == "integration_test_virtual.py"

        finally:
            remove_virtual_import_hook()
            await clear_module_cache()

    @pytest.mark.asyncio
    async def test_virtual_import_package_with_submodule(self, db_session: AsyncSession):
        """Test importing a package with submodules from Redis cache."""
        from src.core.module_cache import clear_module_cache, set_module
        from src.services.execution.virtual_import import (
            install_virtual_import_hook,
            invalidate_module_index,
            remove_virtual_import_hook,
        )

        # Set up package in Redis cache
        await clear_module_cache()
        await set_module(
            path="integration_test_pkg/__init__.py",
            content='PKG_NAME = "test_package"',
            content_hash="pkg123",
        )
        await set_module(
            path="integration_test_pkg/helpers.py",
            content='HELPER_VALUE = "from_helpers"',
            content_hash="helper123",
        )

        # Install virtual import hook
        install_virtual_import_hook()
        invalidate_module_index()  # Force refresh of index

        try:
            # Import package and submodule
            import integration_test_pkg  # type: ignore[import-not-found]
            from integration_test_pkg import helpers  # type: ignore[import-not-found]

            assert integration_test_pkg.PKG_NAME == "test_package"
            assert helpers.HELPER_VALUE == "from_helpers"

        finally:
            remove_virtual_import_hook()
            await clear_module_cache()


@pytest.mark.integration
class TestWorkerVirtualImportHook:
    """Tests for worker.py virtual import hook installation."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Clean up after each test."""
        yield
        # Remove virtual import hooks
        sys.meta_path = [
            finder
            for finder in sys.meta_path
            if not finder.__class__.__name__ == "VirtualModuleFinder"
        ]
        # Reset global finder
        import src.services.execution.virtual_import as module

        module._finder = None

    def test_install_virtual_import_hook(self):
        """Test that install_virtual_import_hook creates and registers a finder."""
        from src.services.execution.virtual_import import (
            get_virtual_finder,
            install_virtual_import_hook,
        )

        # Install the hook
        finder = install_virtual_import_hook()

        # Verify the hook was created
        assert finder is not None, "install_virtual_import_hook should return a finder"

        # Verify it can be retrieved
        retrieved = get_virtual_finder()
        assert retrieved is finder, "get_virtual_finder should return the installed finder"

    def test_virtual_finder_in_meta_path_after_install(self):
        """Test that VirtualModuleFinder is in sys.meta_path after installation."""
        from src.services.execution.virtual_import import install_virtual_import_hook

        # Install the hook
        install_virtual_import_hook()

        # Check it's in meta_path
        has_virtual_finder = any(
            finder.__class__.__name__ == "VirtualModuleFinder" for finder in sys.meta_path
        )
        assert has_virtual_finder, "VirtualModuleFinder should be in sys.meta_path"


@pytest.mark.integration
class TestEndToEndModuleLoading:
    """End-to-end tests for module loading from DB through Redis to import."""

    @pytest.fixture(autouse=True)
    def cleanup(self):
        """Clean up after each test."""
        yield
        # Remove virtual import hooks
        sys.meta_path = [
            finder
            for finder in sys.meta_path
            if not finder.__class__.__name__ == "VirtualModuleFinder"
        ]
        # Remove test modules
        to_remove = [k for k in sys.modules if k.startswith("e2e_test_")]
        for k in to_remove:
            del sys.modules[k]
        # Reset global finder
        import src.services.execution.virtual_import as module

        module._finder = None

    @pytest.mark.asyncio
    async def test_full_flow_cache_to_import(self, db_session: AsyncSession):
        """Test complete flow: set_module -> Redis -> virtual import."""
        from src.core.module_cache import clear_module_cache, set_module
        from src.services.execution.virtual_import import (
            install_virtual_import_hook,
            invalidate_module_index,
            remove_virtual_import_hook,
        )

        content = """
WORKFLOW_NAME = "e2e_test"

def run_workflow(params):
    return {"status": "success", "name": WORKFLOW_NAME, "params": params}
"""

        try:
            # Step 1: Set module directly in Redis cache
            await clear_module_cache()
            await set_module(
                path="e2e_test_workflow.py",
                content=content,
                content_hash="e2e123",
            )

            # Step 2: Install virtual import hook
            install_virtual_import_hook()
            invalidate_module_index()

            # Step 3: Import and use the module
            import e2e_test_workflow  # type: ignore[import-not-found]

            assert e2e_test_workflow.WORKFLOW_NAME == "e2e_test"

            result = e2e_test_workflow.run_workflow({"key": "value"})
            assert result["status"] == "success"
            assert result["name"] == "e2e_test"
            assert result["params"] == {"key": "value"}

        finally:
            remove_virtual_import_hook()
            await clear_module_cache()
