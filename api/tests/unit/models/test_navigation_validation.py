"""Test that navigation models reject unknown fields."""

import pytest
from pydantic import ValidationError

from src.models.contracts.app_components import NavItem, NavigationConfig


def test_navitem_rejects_unknown_fields():
    """NavItem should reject unknown fields like 'items'."""
    with pytest.raises(ValidationError) as exc_info:
        NavItem(id="test", label="Test", unknown_field="bad")
    assert "extra_forbidden" in str(exc_info.value)


def test_navigation_config_rejects_items_field():
    """NavigationConfig should reject 'items' field (must use 'sidebar')."""
    with pytest.raises(ValidationError) as exc_info:
        NavigationConfig(items=[{"id": "test", "label": "Test"}])
    assert "extra_forbidden" in str(exc_info.value)


def test_navigation_config_accepts_sidebar():
    """NavigationConfig should accept proper 'sidebar' field."""
    nav = NavigationConfig(
        sidebar=[NavItem(id="home", label="Home", path="/", icon="Home")]
    )
    assert len(nav.sidebar) == 1
    assert nav.sidebar[0].id == "home"
