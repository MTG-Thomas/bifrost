"""
Unit tests for package install consumer helper methods.

Tests the requirements.txt manipulation logic:
1. _append_package_to_requirements - adds/updates packages in requirements content
2. _get_current_requirements - retrieves cached requirements
"""

import pytest
from unittest.mock import AsyncMock, patch

from src.jobs.consumers.package_install import PackageInstallConsumer


class TestAppendPackageToRequirements:
    """Tests for _append_package_to_requirements static method."""

    @pytest.fixture
    def consumer(self) -> PackageInstallConsumer:
        return PackageInstallConsumer()

    def test_append_to_empty_requirements(self, consumer: PackageInstallConsumer):
        """Test appending to empty requirements content."""
        result = consumer._append_package_to_requirements("", "flask", "2.3.0")
        assert result == "flask==2.3.0\n"

    def test_append_to_whitespace_only(self, consumer: PackageInstallConsumer):
        """Test appending to whitespace-only content."""
        result = consumer._append_package_to_requirements("   \n\n  ", "flask", "2.3.0")
        assert result == "flask==2.3.0\n"

    def test_append_to_existing_requirements(self, consumer: PackageInstallConsumer):
        """Test appending to existing requirements."""
        current = "requests==2.31.0\n"
        result = consumer._append_package_to_requirements(current, "flask", "2.3.0")
        assert "requests==2.31.0" in result
        assert "flask==2.3.0" in result
        # Verify ordering - original should come first
        lines = result.strip().split("\n")
        assert lines[0] == "requests==2.31.0"
        assert lines[1] == "flask==2.3.0"

    def test_append_multiple_packages(self, consumer: PackageInstallConsumer):
        """Test appending multiple packages sequentially."""
        result = consumer._append_package_to_requirements("", "flask", "2.3.0")
        result = consumer._append_package_to_requirements(result, "requests", "2.31.0")
        result = consumer._append_package_to_requirements(result, "pydantic", "2.0.0")
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert "flask==2.3.0" in result
        assert "requests==2.31.0" in result
        assert "pydantic==2.0.0" in result

    def test_update_existing_package_version(self, consumer: PackageInstallConsumer):
        """Test updating an existing package to a new version."""
        current = "flask==2.0.0\nrequests==2.31.0\n"
        result = consumer._append_package_to_requirements(current, "flask", "2.3.0")
        # New version should replace old
        assert "flask==2.3.0" in result
        assert "flask==2.0.0" not in result
        # Other packages should remain unchanged
        assert "requests==2.31.0" in result

    def test_update_package_case_insensitive(self, consumer: PackageInstallConsumer):
        """Test that package name matching is case-insensitive."""
        current = "Flask==2.0.0\n"
        result = consumer._append_package_to_requirements(current, "flask", "2.3.0")
        assert "flask==2.3.0" in result
        assert "Flask==2.0.0" not in result

    def test_update_package_preserves_other_entries(self, consumer: PackageInstallConsumer):
        """Test that updating a package preserves other entries."""
        current = "flask==2.0.0\nrequests==2.31.0\npydantic==2.0.0\n"
        result = consumer._append_package_to_requirements(current, "requests", "2.32.0")
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert "flask==2.0.0" in result
        assert "requests==2.32.0" in result
        assert "pydantic==2.0.0" in result

    def test_append_package_without_version(self, consumer: PackageInstallConsumer):
        """Test appending package without version specifier."""
        result = consumer._append_package_to_requirements("", "flask", None)
        assert result == "flask\n"

    def test_update_versioned_to_unversioned(self, consumer: PackageInstallConsumer):
        """Test updating a versioned package to unversioned."""
        current = "flask==2.0.0\n"
        result = consumer._append_package_to_requirements(current, "flask", None)
        assert result == "flask\n"

    def test_handles_different_version_specifiers(self, consumer: PackageInstallConsumer):
        """Test handling of packages with different version specifiers."""
        # Test with >= specifier
        current = "flask>=2.0.0\n"
        result = consumer._append_package_to_requirements(current, "flask", "2.3.0")
        assert "flask==2.3.0" in result
        assert "flask>=2.0.0" not in result

        # Test with <= specifier
        current = "flask<=3.0.0\n"
        result = consumer._append_package_to_requirements(current, "flask", "2.3.0")
        assert "flask==2.3.0" in result

        # Test with ~= specifier
        current = "flask~=2.0.0\n"
        result = consumer._append_package_to_requirements(current, "flask", "2.3.0")
        assert "flask==2.3.0" in result

    def test_filters_empty_lines(self, consumer: PackageInstallConsumer):
        """Test that empty lines are filtered out."""
        current = "flask==2.0.0\n\n\nrequests==2.31.0\n\n"
        result = consumer._append_package_to_requirements(current, "pydantic", "2.0.0")
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert "" not in lines

    def test_ensures_trailing_newline(self, consumer: PackageInstallConsumer):
        """Test that output always has trailing newline."""
        result = consumer._append_package_to_requirements("", "flask", "2.3.0")
        assert result.endswith("\n")

        result = consumer._append_package_to_requirements("flask==2.0.0", "requests", "2.31.0")
        assert result.endswith("\n")

    def test_handles_package_with_extras(self, consumer: PackageInstallConsumer):
        """Test handling packages with extras in current requirements."""
        # Note: This tests parsing, not extras preservation
        # Package name before [] is used for matching
        current = "requests[security]==2.31.0\n"
        # Adding plain requests should NOT match requests[security]
        # because "requests[security]".split("==")[0] = "requests[security]"
        # and "requests[security]" != "requests"
        result = consumer._append_package_to_requirements(current, "requests", "2.32.0")
        # Both should be present since they're different specs
        assert "requests[security]==2.31.0" in result
        assert "requests==2.32.0" in result


class TestGetCurrentRequirements:
    """Tests for _get_current_requirements async method."""

    @pytest.fixture
    def consumer(self) -> PackageInstallConsumer:
        return PackageInstallConsumer()

    @pytest.mark.asyncio
    async def test_returns_content_when_cached(self, consumer: PackageInstallConsumer):
        """Test that cached requirements content is returned."""
        cached_data = {"content": "flask==2.3.0\nrequests==2.31.0\n", "hash": "abc123"}

        with patch(
            "src.jobs.consumers.package_install.get_requirements",
            new_callable=AsyncMock,
            return_value=cached_data,
        ):
            result = await consumer._get_current_requirements()
            assert result == "flask==2.3.0\nrequests==2.31.0\n"

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_not_cached(
        self, consumer: PackageInstallConsumer
    ):
        """Test that empty string is returned when no cache exists."""
        with patch(
            "src.jobs.consumers.package_install.get_requirements",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await consumer._get_current_requirements()
            assert result == ""

    @pytest.mark.asyncio
    async def test_returns_empty_string_for_empty_content(
        self, consumer: PackageInstallConsumer
    ):
        """Test handling of cached data with empty content."""
        cached_data = {"content": "", "hash": "empty"}

        with patch(
            "src.jobs.consumers.package_install.get_requirements",
            new_callable=AsyncMock,
            return_value=cached_data,
        ):
            result = await consumer._get_current_requirements()
            # Empty string content should still be returned
            assert result == ""
