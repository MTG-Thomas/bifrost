"""Test MCP app tools validate navigation through Pydantic models."""

from src.models.contracts.app_components import NavigationConfig


class TestNavigationValidation:
    """Test that NavigationConfig properly validates navigation data."""

    def test_valid_navigation_with_sidebar(self):
        """NavigationConfig accepts valid sidebar field."""
        nav = NavigationConfig.model_validate({
            "sidebar": [
                {"id": "home", "label": "Home", "path": "/", "icon": "Home"},
                {"id": "settings", "label": "Settings", "path": "/settings"},
            ],
            "show_sidebar": True,
        })
        assert nav.sidebar is not None
        assert len(nav.sidebar) == 2
        assert nav.sidebar[0].id == "home"
        assert nav.show_sidebar is True

    def test_navigation_ignores_unknown_fields(self):
        """NavigationConfig ignores unknown fields like 'items'.

        Note: Pydantic's default is extra="ignore", so unknown fields are dropped.
        The navigation will be stored with sidebar=None, which is semantically
        different from what the caller intended, but doesn't break validation.
        """
        nav = NavigationConfig.model_validate({
            "items": [{"id": "test", "label": "Test"}]
        })
        # items is ignored, sidebar remains None
        assert nav.sidebar is None

    def test_navigation_model_dump_excludes_none(self):
        """model_dump(exclude_none=True) produces clean output."""
        nav = NavigationConfig.model_validate({
            "sidebar": [{"id": "home", "label": "Home"}],
            "show_sidebar": True,
        })
        dumped = nav.model_dump(exclude_none=True)

        # Should have sidebar and show_sidebar
        assert "sidebar" in dumped
        assert "show_sidebar" in dumped

    def test_navitem_validation_catches_bad_nested_data(self):
        """NavItem validation catches invalid data inside sidebar items."""
        # This will fail because NavItem requires 'id' and 'label'
        from pydantic import ValidationError
        import pytest

        with pytest.raises(ValidationError):
            NavigationConfig.model_validate({
                "sidebar": [{"invalid_field": "value"}]  # Missing required fields
            })
