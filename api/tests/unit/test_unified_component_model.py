# api/tests/unit/test_unified_component_model.py
"""Unit tests for unified AppComponent model."""
import pytest
from pydantic import ValidationError


def test_row_component_accepts_children():
    """Row component should accept children list."""
    from src.models.contracts.app_components import HeadingComponent, RowComponent

    # Use HeadingComponent with flat props (no props wrapper)
    heading = HeadingComponent(id="h1", text="Hello")
    row = RowComponent(id="row1", children=[heading], gap="md")

    assert row.type == "row"
    assert len(row.children) == 1
    assert row.children[0].id == "h1"


def test_button_component_rejects_children():
    """Button component should reject children field."""
    from src.models.contracts.app_components import ButtonComponent

    with pytest.raises(ValidationError) as exc_info:
        # ButtonComponent should have extra="forbid" from ComponentBase
        ButtonComponent(
            id="btn1",
            label="Click",
            action_type="custom",
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

    # Button should parse to ButtonComponent (with flat props)
    btn_data = {
        "id": "b1",
        "type": "button",
        "label": "Click",
        "action_type": "custom",
    }
    btn = adapter.validate_python(btn_data)
    assert btn.__class__.__name__ == "ButtonComponent"


# ============================================================================
# Task 1.2: Content Container Components Tests
# ============================================================================


def test_card_component_with_children():
    """Card should have children at top level, not in props."""
    from src.models.contracts.app_components import CardComponent, HeadingComponent

    # Use HeadingComponent as child (flat props structure)
    heading = HeadingComponent(id="h1", text="Hello")
    card = CardComponent(
        id="card1",
        title="My Card",
        children=[heading],
    )

    assert card.type == "card"
    assert card.title == "My Card"
    assert len(card.children) == 1


def test_card_component_with_collapsible():
    """Card should support collapsible configuration."""
    from src.models.contracts.app_components import CardComponent

    card = CardComponent(
        id="card1",
        title="Collapsible Card",
        collapsible=True,
        default_collapsed=True,
    )

    assert card.collapsible is True
    assert card.default_collapsed is True


def test_modal_component_with_children():
    """Modal should have children at top level."""
    from src.models.contracts.app_components import HeadingComponent, ModalComponent

    heading = HeadingComponent(id="h1", text="Modal content")
    modal = ModalComponent(
        id="modal1",
        title="My Modal",
        children=[heading],
    )

    assert modal.type == "modal"
    assert len(modal.children) == 1


def test_modal_component_with_footer_actions():
    """Modal should support footer actions."""
    from src.models.contracts.app_components import ModalComponent, ModalFooterAction

    modal = ModalComponent(
        id="modal1",
        title="Confirm",
        footer_actions=[
            ModalFooterAction(label="Cancel", action_type="custom"),
            ModalFooterAction(label="Submit", action_type="submit", variant="default"),
        ],
    )

    assert len(modal.footer_actions) == 2
    assert modal.footer_actions[0].label == "Cancel"


def test_tabs_with_tab_items():
    """Tabs should contain TabItemComponent children."""
    from src.models.contracts.app_components import (
        HeadingComponent,
        TabItemComponent,
        TabsComponent,
    )

    tab1 = TabItemComponent(
        id="tab1",
        label="First",
        value="first",
        children=[HeadingComponent(id="h1", text="Tab 1 content")],
    )
    tabs = TabsComponent(id="tabs1", children=[tab1])

    assert tabs.type == "tabs"
    assert len(tabs.children) == 1
    assert tabs.children[0].type == "tab-item"


def test_tab_item_value_defaults_to_none():
    """TabItem value should default to None (frontend can default to label)."""
    from src.models.contracts.app_components import TabItemComponent

    tab = TabItemComponent(id="tab1", label="First")

    assert tab.label == "First"
    assert tab.value is None


def test_form_group_with_children():
    """FormGroup should have children at top level."""
    from src.models.contracts.app_components import (
        FormGroupComponent,
        TextInputComponent,
    )

    text_input = TextInputComponent(id="input1", field_id="name", label="Name")
    form_group = FormGroupComponent(
        id="group1",
        label="User Info",
        direction="row",
        children=[text_input],
    )

    assert form_group.type == "form-group"
    assert form_group.label == "User Info"
    assert len(form_group.children) == 1


def test_container_components_inherit_from_component_base():
    """Content container components should inherit from ComponentBase (have extra=forbid)."""
    from src.models.contracts.app_components import (
        CardComponent,
        FormGroupComponent,
        ModalComponent,
        TabItemComponent,
        TabsComponent,
    )

    # All should reject unknown fields due to extra="forbid" from ComponentBase
    with pytest.raises(ValidationError):
        CardComponent(id="c1", unknown_field="x")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        ModalComponent(id="m1", title="Test", unknown_field="x")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        TabsComponent(id="t1", unknown_field="x")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        TabItemComponent(id="ti1", label="Tab", unknown_field="x")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        FormGroupComponent(id="fg1", unknown_field="x")  # type: ignore[call-arg]


def test_discriminated_union_routes_container_types():
    """AppComponent union should route container types correctly."""
    from pydantic import TypeAdapter

    from src.models.contracts.app_components import AppComponent

    adapter = TypeAdapter(AppComponent)

    # Card should parse to CardComponent
    card_data = {"id": "c1", "type": "card", "title": "Test", "children": []}
    card = adapter.validate_python(card_data)
    assert card.__class__.__name__ == "CardComponent"

    # Modal should parse to ModalComponent
    modal_data = {"id": "m1", "type": "modal", "title": "Test", "children": []}
    modal = adapter.validate_python(modal_data)
    assert modal.__class__.__name__ == "ModalComponent"

    # Tabs should parse to TabsComponent
    tabs_data = {"id": "t1", "type": "tabs", "children": []}
    tabs = adapter.validate_python(tabs_data)
    assert tabs.__class__.__name__ == "TabsComponent"

    # TabItem should parse to TabItemComponent
    tab_item_data = {"id": "ti1", "type": "tab-item", "label": "Tab 1", "children": []}
    tab_item = adapter.validate_python(tab_item_data)
    assert tab_item.__class__.__name__ == "TabItemComponent"

    # FormGroup should parse to FormGroupComponent
    form_group_data = {"id": "fg1", "type": "form-group", "children": []}
    form_group = adapter.validate_python(form_group_data)
    assert form_group.__class__.__name__ == "FormGroupComponent"


# ============================================================================
# Task 1.3: Leaf Components Flat Props Tests
# ============================================================================


def test_heading_props_at_top_level():
    """Heading should have text/level at top level, not in props."""
    from src.models.contracts.app_components import HeadingComponent

    heading = HeadingComponent(id="h1", text="Hello World", level=2)

    assert heading.type == "heading"
    assert heading.text == "Hello World"
    assert heading.level == 2
    # Should NOT have a props field
    assert not hasattr(heading, "props")


def test_text_props_at_top_level():
    """Text should have text/label at top level."""
    from src.models.contracts.app_components import TextComponent

    text = TextComponent(id="t1", text="Body text", label="My Label")

    assert text.type == "text"
    assert text.text == "Body text"
    assert text.label == "My Label"
    assert not hasattr(text, "props")


def test_html_props_at_top_level():
    """Html should have content at top level."""
    from src.models.contracts.app_components import HtmlComponent

    html = HtmlComponent(id="html1", content="<div>Hello</div>")

    assert html.type == "html"
    assert html.content == "<div>Hello</div>"
    assert not hasattr(html, "props")


def test_divider_props_at_top_level():
    """Divider should have orientation at top level."""
    from src.models.contracts.app_components import DividerComponent

    divider = DividerComponent(id="div1", orientation="horizontal")

    assert divider.type == "divider"
    assert divider.orientation == "horizontal"
    assert not hasattr(divider, "props")


def test_spacer_props_at_top_level():
    """Spacer should have size at top level."""
    from src.models.contracts.app_components import SpacerComponent

    spacer = SpacerComponent(id="sp1", size=24)

    assert spacer.type == "spacer"
    assert spacer.size == 24
    assert not hasattr(spacer, "props")


def test_button_props_at_top_level():
    """Button should have label/variant at top level."""
    from src.models.contracts.app_components import ButtonComponent

    btn = ButtonComponent(
        id="btn1",
        label="Submit",
        action_type="workflow",
        workflow_id="wf-123",
        variant="default",
    )

    assert btn.type == "button"
    assert btn.label == "Submit"
    assert btn.action_type == "workflow"
    assert btn.workflow_id == "wf-123"
    assert btn.variant == "default"
    assert not hasattr(btn, "props")


def test_image_props_at_top_level():
    """Image should have src/alt at top level."""
    from src.models.contracts.app_components import ImageComponent

    img = ImageComponent(id="img1", src="/image.png", alt="An image", max_width=400)

    assert img.type == "image"
    assert img.src == "/image.png"
    assert img.alt == "An image"
    assert img.max_width == 400
    assert not hasattr(img, "props")


def test_badge_props_at_top_level():
    """Badge should have text/variant at top level."""
    from src.models.contracts.app_components import BadgeComponent

    badge = BadgeComponent(id="badge1", text="New", variant="secondary")

    assert badge.type == "badge"
    assert badge.text == "New"
    assert badge.variant == "secondary"
    assert not hasattr(badge, "props")


def test_progress_props_at_top_level():
    """Progress should have value/show_label at top level."""
    from src.models.contracts.app_components import ProgressComponent

    progress = ProgressComponent(id="prog1", value=75, show_label=True)

    assert progress.type == "progress"
    assert progress.value == 75
    assert progress.show_label is True
    assert not hasattr(progress, "props")


def test_stat_card_props_at_top_level():
    """StatCard should have title/value at top level."""
    from src.models.contracts.app_components import StatCardComponent, StatCardTrend

    stat = StatCardComponent(
        id="stat1",
        title="Revenue",
        value="{{ data.revenue }}",
        icon="DollarSign",
        trend=StatCardTrend(value="+15%", direction="up"),
    )

    assert stat.type == "stat-card"
    assert stat.title == "Revenue"
    assert stat.value == "{{ data.revenue }}"
    assert stat.icon == "DollarSign"
    assert stat.trend is not None
    assert not hasattr(stat, "props")


def test_data_table_props_at_top_level():
    """DataTable should have columns/data_source at top level."""
    from src.models.contracts.app_components import DataTableComponent, TableColumn

    table = DataTableComponent(
        id="table1",
        data_source="users_data",
        columns=[TableColumn(key="name", header="Name")],
        paginated=True,
    )

    assert table.type == "data-table"
    assert table.data_source == "users_data"
    assert len(table.columns) == 1
    assert table.paginated is True
    assert not hasattr(table, "props")


def test_file_viewer_props_at_top_level():
    """FileViewer should have src/file_name at top level."""
    from src.models.contracts.app_components import FileViewerComponent

    viewer = FileViewerComponent(
        id="viewer1",
        src="/doc.pdf",
        file_name="document.pdf",
        display_mode="inline",
    )

    assert viewer.type == "file-viewer"
    assert viewer.src == "/doc.pdf"
    assert viewer.file_name == "document.pdf"
    assert viewer.display_mode == "inline"
    assert not hasattr(viewer, "props")


def test_text_input_props_at_top_level():
    """TextInput should have field_id/label at top level."""
    from src.models.contracts.app_components import TextInputComponent

    input_comp = TextInputComponent(
        id="input1",
        field_id="email",
        label="Email",
        input_type="email",
        required=True,
    )

    assert input_comp.type == "text-input"
    assert input_comp.field_id == "email"
    assert input_comp.label == "Email"
    assert input_comp.input_type == "email"
    assert input_comp.required is True
    assert not hasattr(input_comp, "props")


def test_number_input_props_at_top_level():
    """NumberInput should have field_id/min/max at top level."""
    from src.models.contracts.app_components import NumberInputComponent

    input_comp = NumberInputComponent(
        id="num1",
        field_id="age",
        label="Age",
        min=0,
        max=120,
    )

    assert input_comp.type == "number-input"
    assert input_comp.field_id == "age"
    assert input_comp.min == 0
    assert input_comp.max == 120
    assert not hasattr(input_comp, "props")


def test_select_props_at_top_level():
    """Select should have field_id/options at top level."""
    from src.models.contracts.app_components import SelectComponent, SelectOption

    select = SelectComponent(
        id="select1",
        field_id="country",
        label="Country",
        options=[SelectOption(value="us", label="United States")],
    )

    assert select.type == "select"
    assert select.field_id == "country"
    assert select.label == "Country"
    assert len(select.options) == 1
    assert not hasattr(select, "props")


def test_checkbox_props_at_top_level():
    """Checkbox should have field_id/label at top level."""
    from src.models.contracts.app_components import CheckboxComponent

    checkbox = CheckboxComponent(
        id="cb1",
        field_id="agree",
        label="I agree",
        default_checked=True,
    )

    assert checkbox.type == "checkbox"
    assert checkbox.field_id == "agree"
    assert checkbox.label == "I agree"
    assert checkbox.default_checked is True
    assert not hasattr(checkbox, "props")


def test_form_embed_props_at_top_level():
    """FormEmbed should have form_id at top level."""
    from src.models.contracts.app_components import FormEmbedComponent

    embed = FormEmbedComponent(
        id="embed1",
        form_id="form-123",
        show_title=True,
    )

    assert embed.type == "form-embed"
    assert embed.form_id == "form-123"
    assert embed.show_title is True
    assert not hasattr(embed, "props")


def test_leaf_components_inherit_from_component_base():
    """Leaf components should inherit from ComponentBase (have extra=forbid)."""
    from src.models.contracts.app_components import (
        ButtonComponent,
        DataTableComponent,
        HeadingComponent,
        TableColumn,
        TextComponent,
    )

    # All should reject unknown fields due to extra="forbid" from ComponentBase
    with pytest.raises(ValidationError):
        HeadingComponent(id="h1", text="Hi", unknown_field="x")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        TextComponent(id="t1", text="Hi", unknown_field="x")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        ButtonComponent(
            id="b1", label="Click", action_type="custom", unknown_field="x"
        )  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        DataTableComponent(
            id="dt1",
            data_source="data",
            columns=[TableColumn(key="k", header="H")],
            unknown_field="x",
        )  # type: ignore[call-arg]


def test_discriminated_union_routes_leaf_types():
    """AppComponent union should route leaf types correctly with flat props."""
    from pydantic import TypeAdapter

    from src.models.contracts.app_components import AppComponent

    adapter = TypeAdapter(AppComponent)

    # Heading with flat props
    heading_data = {"id": "h1", "type": "heading", "text": "Hello", "level": 2}
    heading = adapter.validate_python(heading_data)
    assert heading.__class__.__name__ == "HeadingComponent"
    assert heading.text == "Hello"  # type: ignore

    # Button with flat props
    btn_data = {
        "id": "b1",
        "type": "button",
        "label": "Click",
        "action_type": "navigate",
    }
    btn = adapter.validate_python(btn_data)
    assert btn.__class__.__name__ == "ButtonComponent"
    assert btn.label == "Click"  # type: ignore

    # DataTable with flat props
    table_data = {
        "id": "t1",
        "type": "data-table",
        "data_source": "data",
        "columns": [{"key": "name", "header": "Name"}],
    }
    table = adapter.validate_python(table_data)
    assert table.__class__.__name__ == "DataTableComponent"
    assert table.data_source == "data"  # type: ignore


# ============================================================================
# Task 1.4: AppComponent Union and PageDefinition Tests
# ============================================================================


def test_app_component_union_includes_layouts():
    """AppComponent union should include row, column, grid."""
    from pydantic import TypeAdapter

    from src.models.contracts.app_components import AppComponent

    adapter = TypeAdapter(AppComponent)

    # All these should parse successfully
    row = adapter.validate_python({"id": "r1", "type": "row", "children": []})
    col = adapter.validate_python({"id": "c1", "type": "column", "children": []})
    grid = adapter.validate_python({"id": "g1", "type": "grid", "children": []})

    assert row.type == "row"
    assert col.type == "column"
    assert grid.type == "grid"


def test_page_definition_has_children():
    """PageDefinition should have children instead of layout."""
    from src.models.contracts.app_components import ColumnComponent, PageDefinition

    page = PageDefinition(
        id="page1",
        title="Home",
        path="/",
        children=[ColumnComponent(id="col1", children=[])],
    )

    assert len(page.children) == 1
    assert page.children[0].type == "column"
