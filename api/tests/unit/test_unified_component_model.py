# api/tests/unit/test_unified_component_model.py
"""Unit tests for unified AppComponent model."""
import pytest
from pydantic import ValidationError


def test_row_component_accepts_children():
    """Row component should accept children list."""
    from src.models.contracts.app_components import HeadingComponent, HeadingProps, RowComponent

    # Use existing HeadingComponent structure with props wrapper
    heading = HeadingComponent(id="h1", props=HeadingProps(text="Hello"))
    row = RowComponent(id="row1", children=[heading], gap="md")

    assert row.type == "row"
    assert len(row.children) == 1
    assert row.children[0].id == "h1"


def test_button_component_rejects_children():
    """Button component should reject children field."""
    from src.models.contracts.app_components import ButtonComponent, ButtonProps

    with pytest.raises(ValidationError) as exc_info:
        # ButtonComponent uses props wrapper and should have extra="forbid" on the model
        ButtonComponent(
            id="btn1",
            props=ButtonProps(label="Click", action_type="custom"),
            children=[],  # type: ignore[call-arg]
        )

    assert "children" in str(exc_info.value).lower() or "extra" in str(exc_info.value).lower()


def test_discriminated_union_routes_by_type():
    """AppComponent union should route to correct model by type."""
    from pydantic import TypeAdapter
    from src.models.contracts.app_components import AppComponent

    adapter = TypeAdapter(AppComponent)

    # Row should parse to RowComponent
    row_data = {"id": "r1", "type": "row", "children": [], "gap": "md"}
    row = adapter.validate_python(row_data)
    assert row.__class__.__name__ == "RowComponent"

    # Button should parse to ButtonComponent (with props wrapper)
    btn_data = {
        "id": "b1",
        "type": "button",
        "props": {"label": "Click", "action_type": "custom"},
    }
    btn = adapter.validate_python(btn_data)
    assert btn.__class__.__name__ == "ButtonComponent"
