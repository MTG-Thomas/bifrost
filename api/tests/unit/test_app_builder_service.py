# api/tests/unit/test_app_builder_service.py
"""Unit tests for simplified app builder service tree functions."""
from uuid import UUID, uuid4


def test_flatten_components_simple():
    """Flatten a simple component list with column containing heading."""
    from src.models.contracts.app_components import ColumnComponent, HeadingComponent
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        ColumnComponent(
            id="col1",
            children=[
                HeadingComponent(id="h1", text="Hello"),
            ],
        ),
    ]

    rows = flatten_components(components, page_id)

    # Should have 2 rows: column and heading
    assert len(rows) == 2

    col_row = next(r for r in rows if r["component_id"] == "col1")
    heading_row = next(r for r in rows if r["component_id"] == "h1")

    # Column assertions
    assert col_row["type"] == "column"
    assert col_row["parent_id"] is None
    assert col_row["page_id"] == page_id
    assert col_row["component_order"] == 0

    # Heading assertions
    assert heading_row["type"] == "heading"
    assert heading_row["parent_id"] == col_row["id"]
    assert heading_row["page_id"] == page_id
    assert heading_row["component_order"] == 0
    # Props should contain text
    assert heading_row["props"]["text"] == "Hello"


def test_flatten_components_nested():
    """Flatten nested containers: row > card > text."""
    from src.models.contracts.app_components import (
        CardComponent,
        RowComponent,
        TextComponent,
    )
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        RowComponent(
            id="row1",
            children=[
                CardComponent(
                    id="card1",
                    title="Card",
                    children=[
                        TextComponent(id="t1", text="Content"),
                    ],
                ),
            ],
        ),
    ]

    rows = flatten_components(components, page_id)

    # Should have 3 rows: row, card, text
    assert len(rows) == 3

    row_row = next(r for r in rows if r["component_id"] == "row1")
    card_row = next(r for r in rows if r["component_id"] == "card1")
    text_row = next(r for r in rows if r["component_id"] == "t1")

    # Check parent chain: row -> card -> text
    assert row_row["parent_id"] is None
    assert card_row["parent_id"] == row_row["id"]
    assert text_row["parent_id"] == card_row["id"]

    # Check card props include title
    assert card_row["props"]["title"] == "Card"


def test_flatten_components_preserves_order():
    """Flatten preserves component order within siblings."""
    from src.models.contracts.app_components import ColumnComponent, HeadingComponent
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        ColumnComponent(
            id="col1",
            children=[
                HeadingComponent(id="h1", text="First"),
                HeadingComponent(id="h2", text="Second"),
                HeadingComponent(id="h3", text="Third"),
            ],
        ),
    ]

    rows = flatten_components(components, page_id)

    h1_row = next(r for r in rows if r["component_id"] == "h1")
    h2_row = next(r for r in rows if r["component_id"] == "h2")
    h3_row = next(r for r in rows if r["component_id"] == "h3")

    assert h1_row["component_order"] == 0
    assert h2_row["component_order"] == 1
    assert h3_row["component_order"] == 2


def test_flatten_components_extracts_base_fields():
    """Flatten extracts ComponentBase fields like visible, width, loading_workflows."""
    from src.models.contracts.app_components import HeadingComponent, RowComponent
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        RowComponent(
            id="row1",
            visible="{{ user.isAdmin }}",
            width="full",
            loading_workflows=["wf-1", "wf-2"],
            children=[
                HeadingComponent(id="h1", text="Title"),
            ],
        ),
    ]

    rows = flatten_components(components, page_id)
    row_row = next(r for r in rows if r["component_id"] == "row1")

    assert row_row["visible"] == "{{ user.isAdmin }}"
    assert row_row["width"] == "full"
    assert row_row["loading_workflows"] == ["wf-1", "wf-2"]


def test_flatten_components_tabs_with_tab_items():
    """Flatten tabs component with tab-item children."""
    from src.models.contracts.app_components import (
        TabItemComponent,
        TabsComponent,
        TextComponent,
    )
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        TabsComponent(
            id="tabs1",
            default_tab="tab1",
            children=[
                TabItemComponent(
                    id="tab1",
                    label="First Tab",
                    value="first",
                    children=[
                        TextComponent(id="t1", text="Tab 1 content"),
                    ],
                ),
                TabItemComponent(
                    id="tab2",
                    label="Second Tab",
                    value="second",
                    children=[
                        TextComponent(id="t2", text="Tab 2 content"),
                    ],
                ),
            ],
        ),
    ]

    rows = flatten_components(components, page_id)

    # Should have 5 rows: tabs, tab1, t1, tab2, t2
    assert len(rows) == 5

    tabs_row = next(r for r in rows if r["component_id"] == "tabs1")
    tab1_row = next(r for r in rows if r["component_id"] == "tab1")
    tab2_row = next(r for r in rows if r["component_id"] == "tab2")
    t1_row = next(r for r in rows if r["component_id"] == "t1")
    t2_row = next(r for r in rows if r["component_id"] == "t2")

    # Check parent relationships
    assert tabs_row["parent_id"] is None
    assert tab1_row["parent_id"] == tabs_row["id"]
    assert tab2_row["parent_id"] == tabs_row["id"]
    assert t1_row["parent_id"] == tab1_row["id"]
    assert t2_row["parent_id"] == tab2_row["id"]

    # Check tab-item props
    assert tab1_row["props"]["label"] == "First Tab"
    assert tab1_row["props"]["value"] == "first"


def test_flatten_components_grid_with_span():
    """Flatten grid component with children having grid_span."""
    from src.models.contracts.app_components import (
        CardComponent,
        GridComponent,
    )
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        GridComponent(
            id="grid1",
            columns=3,
            gap="md",
            children=[
                CardComponent(id="card1", title="Card 1", grid_span=2),
                CardComponent(id="card2", title="Card 2", grid_span=1),
            ],
        ),
    ]

    rows = flatten_components(components, page_id)

    grid_row = next(r for r in rows if r["component_id"] == "grid1")
    card1_row = next(r for r in rows if r["component_id"] == "card1")
    card2_row = next(r for r in rows if r["component_id"] == "card2")

    # Check grid props
    assert grid_row["props"]["columns"] == 3
    assert grid_row["props"]["gap"] == "md"

    # Check grid_span is in props
    assert card1_row["props"]["grid_span"] == 2
    assert card2_row["props"]["grid_span"] == 1


def test_flatten_components_form_group():
    """Flatten form-group component with form field children."""
    from src.models.contracts.app_components import (
        FormGroupComponent,
        TextInputComponent,
    )
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        FormGroupComponent(
            id="fg1",
            label="User Info",
            direction="row",
            gap=16,
            children=[
                TextInputComponent(
                    id="input1",
                    field_id="first_name",
                    label="First Name",
                ),
                TextInputComponent(
                    id="input2",
                    field_id="last_name",
                    label="Last Name",
                ),
            ],
        ),
    ]

    rows = flatten_components(components, page_id)

    assert len(rows) == 3

    fg_row = next(r for r in rows if r["component_id"] == "fg1")
    input1_row = next(r for r in rows if r["component_id"] == "input1")
    input2_row = next(r for r in rows if r["component_id"] == "input2")

    # Check parent relationships
    assert fg_row["parent_id"] is None
    assert input1_row["parent_id"] == fg_row["id"]
    assert input2_row["parent_id"] == fg_row["id"]

    # Check form-group props
    assert fg_row["props"]["label"] == "User Info"
    assert fg_row["props"]["direction"] == "row"
    assert fg_row["props"]["gap"] == 16


def test_flatten_components_modal():
    """Flatten modal component with children."""
    from src.models.contracts.app_components import (
        ModalComponent,
        TextComponent,
    )
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        ModalComponent(
            id="modal1",
            title="Confirm Action",
            description="Are you sure?",
            size="lg",
            children=[
                TextComponent(id="t1", text="Modal body content"),
            ],
        ),
    ]

    rows = flatten_components(components, page_id)

    assert len(rows) == 2

    modal_row = next(r for r in rows if r["component_id"] == "modal1")
    text_row = next(r for r in rows if r["component_id"] == "t1")

    assert modal_row["type"] == "modal"
    assert modal_row["props"]["title"] == "Confirm Action"
    assert modal_row["props"]["description"] == "Are you sure?"
    assert modal_row["props"]["size"] == "lg"

    assert text_row["parent_id"] == modal_row["id"]


def test_flatten_components_empty_children():
    """Flatten handles empty children list correctly."""
    from src.models.contracts.app_components import RowComponent
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        RowComponent(id="row1", children=[]),
    ]

    rows = flatten_components(components, page_id)

    assert len(rows) == 1
    assert rows[0]["component_id"] == "row1"
    assert rows[0]["type"] == "row"


def test_flatten_components_leaf_component_no_children():
    """Flatten handles leaf components that don't have children field."""
    from src.models.contracts.app_components import ButtonComponent
    from src.services.app_builder_service import flatten_components

    page_id = uuid4()
    components = [
        ButtonComponent(
            id="btn1",
            label="Click Me",
            action_type="workflow",
            workflow_id="wf-123",
            variant="default",
        ),
    ]

    rows = flatten_components(components, page_id)

    assert len(rows) == 1

    btn_row = rows[0]
    assert btn_row["component_id"] == "btn1"
    assert btn_row["type"] == "button"
    assert btn_row["props"]["label"] == "Click Me"
    assert btn_row["props"]["action_type"] == "workflow"
    assert btn_row["props"]["workflow_id"] == "wf-123"


# =============================================================================
# Tests for build_unified_component_tree
# =============================================================================


class FakeComponent:
    """Fake ORM component for testing build_unified_component_tree."""

    def __init__(
        self,
        id: UUID,
        component_id: str,
        type: str,
        props: dict,
        component_order: int,
        parent_id: UUID | None = None,
        visible: str | None = None,
        width: str | None = None,
        loading_workflows: list[str] | None = None,
    ):
        self.id = id
        self.component_id = component_id
        self.type = type
        self.props = props
        self.component_order = component_order
        self.parent_id = parent_id
        self.visible = visible
        self.width = width
        self.loading_workflows = loading_workflows


def test_build_unified_component_tree_simple():
    """Build tree from flat rows with fake ORM objects."""
    from src.models.contracts.app_components import ColumnComponent, HeadingComponent
    from src.services.app_builder_service import build_unified_component_tree

    # Create fake ORM rows
    col_uuid = uuid4()
    h1_uuid = uuid4()

    fake_rows = [
        FakeComponent(
            id=col_uuid,
            component_id="col1",
            type="column",
            props={},
            component_order=0,
            parent_id=None,
        ),
        FakeComponent(
            id=h1_uuid,
            component_id="h1",
            type="heading",
            props={"text": "Hello"},
            component_order=0,
            parent_id=col_uuid,
        ),
    ]

    # Build tree
    result = build_unified_component_tree(fake_rows)

    # Should have 1 root component (the column)
    assert len(result) == 1

    col = result[0]
    assert isinstance(col, ColumnComponent)
    assert col.id == "col1"
    assert col.type == "column"

    # Column should have 1 child (the heading)
    assert len(col.children) == 1

    heading = col.children[0]
    assert isinstance(heading, HeadingComponent)
    assert heading.id == "h1"
    assert heading.type == "heading"
    assert heading.text == "Hello"


def test_build_unified_component_tree_nested():
    """Build tree from nested rows: row > card > text."""
    from src.models.contracts.app_components import (
        CardComponent,
        RowComponent,
        TextComponent,
    )
    from src.services.app_builder_service import build_unified_component_tree

    row_uuid = uuid4()
    card_uuid = uuid4()
    text_uuid = uuid4()

    fake_rows = [
        FakeComponent(
            id=row_uuid,
            component_id="row1",
            type="row",
            props={},
            component_order=0,
            parent_id=None,
        ),
        FakeComponent(
            id=card_uuid,
            component_id="card1",
            type="card",
            props={"title": "Card Title"},
            component_order=0,
            parent_id=row_uuid,
        ),
        FakeComponent(
            id=text_uuid,
            component_id="t1",
            type="text",
            props={"text": "Content"},
            component_order=0,
            parent_id=card_uuid,
        ),
    ]

    result = build_unified_component_tree(fake_rows)

    # Row at root
    assert len(result) == 1
    row = result[0]
    assert isinstance(row, RowComponent)
    assert row.id == "row1"

    # Card is child of row
    assert len(row.children) == 1
    card = row.children[0]
    assert isinstance(card, CardComponent)
    assert card.id == "card1"
    assert card.title == "Card Title"

    # Text is child of card
    assert len(card.children) == 1
    text = card.children[0]
    assert isinstance(text, TextComponent)
    assert text.id == "t1"
    assert text.text == "Content"


def test_build_unified_component_tree_preserves_order():
    """Build tree preserves component order within siblings."""
    from src.models.contracts.app_components import ColumnComponent, HeadingComponent
    from src.services.app_builder_service import build_unified_component_tree

    col_uuid = uuid4()
    h1_uuid = uuid4()
    h2_uuid = uuid4()
    h3_uuid = uuid4()

    fake_rows = [
        FakeComponent(
            id=col_uuid,
            component_id="col1",
            type="column",
            props={},
            component_order=0,
            parent_id=None,
        ),
        # Deliberately out of order to test sorting
        FakeComponent(
            id=h3_uuid,
            component_id="h3",
            type="heading",
            props={"text": "Third"},
            component_order=2,
            parent_id=col_uuid,
        ),
        FakeComponent(
            id=h1_uuid,
            component_id="h1",
            type="heading",
            props={"text": "First"},
            component_order=0,
            parent_id=col_uuid,
        ),
        FakeComponent(
            id=h2_uuid,
            component_id="h2",
            type="heading",
            props={"text": "Second"},
            component_order=1,
            parent_id=col_uuid,
        ),
    ]

    result = build_unified_component_tree(fake_rows)

    col = result[0]
    assert len(col.children) == 3

    # Children should be in order by component_order
    assert col.children[0].id == "h1"
    assert col.children[0].text == "First"
    assert col.children[1].id == "h2"
    assert col.children[1].text == "Second"
    assert col.children[2].id == "h3"
    assert col.children[2].text == "Third"


def test_build_unified_component_tree_preserves_base_fields():
    """Build tree preserves base fields like visible, width, loading_workflows."""
    from src.models.contracts.app_components import HeadingComponent
    from src.services.app_builder_service import build_unified_component_tree

    h1_uuid = uuid4()

    fake_rows = [
        FakeComponent(
            id=h1_uuid,
            component_id="h1",
            type="heading",
            props={"text": "Title"},
            component_order=0,
            parent_id=None,
            visible="{{ user.isAdmin }}",
            width="full",
            loading_workflows=["wf-1", "wf-2"],
        ),
    ]

    result = build_unified_component_tree(fake_rows)

    heading = result[0]
    assert isinstance(heading, HeadingComponent)
    assert heading.visible == "{{ user.isAdmin }}"
    assert heading.width == "full"
    assert heading.loading_workflows == ["wf-1", "wf-2"]


def test_build_unified_component_tree_empty():
    """Build tree handles empty input."""
    from src.services.app_builder_service import build_unified_component_tree

    result = build_unified_component_tree([])
    assert result == []


def test_build_and_flatten_roundtrip():
    """Flatten then build should produce equivalent structure."""
    from src.models.contracts.app_components import (
        CardComponent,
        ColumnComponent,
        HeadingComponent,
        TextComponent,
    )
    from src.services.app_builder_service import (
        build_unified_component_tree,
        flatten_components,
    )

    page_id = uuid4()

    # Original structure
    original = [
        ColumnComponent(
            id="col1",
            children=[
                HeadingComponent(id="h1", text="Welcome"),
                CardComponent(
                    id="card1",
                    title="Info Card",
                    children=[
                        TextComponent(id="t1", text="Card content"),
                    ],
                ),
            ],
        ),
    ]

    # Flatten to rows
    rows = flatten_components(original, page_id)

    # Convert to fake ORM objects (simulating what would come from database)
    fake_rows = [
        FakeComponent(
            id=row["id"],
            component_id=row["component_id"],
            type=row["type"],
            props=row["props"],
            component_order=row["component_order"],
            parent_id=row["parent_id"],
            visible=row["visible"],
            width=row["width"],
            loading_workflows=row["loading_workflows"],
        )
        for row in rows
    ]

    # Rebuild tree
    rebuilt = build_unified_component_tree(fake_rows)

    # Verify structure matches
    assert len(rebuilt) == 1
    col = rebuilt[0]
    assert isinstance(col, ColumnComponent)
    assert col.id == "col1"
    assert len(col.children) == 2

    # First child is heading
    h1 = col.children[0]
    assert isinstance(h1, HeadingComponent)
    assert h1.id == "h1"
    assert h1.text == "Welcome"

    # Second child is card
    card = col.children[1]
    assert isinstance(card, CardComponent)
    assert card.id == "card1"
    assert card.title == "Info Card"
    assert len(card.children) == 1

    # Card has text child
    t1 = card.children[0]
    assert isinstance(t1, TextComponent)
    assert t1.id == "t1"
    assert t1.text == "Card content"


def test_build_and_flatten_roundtrip_with_tabs():
    """Roundtrip with tabs and tab-items."""
    from src.models.contracts.app_components import (
        TabItemComponent,
        TabsComponent,
        TextComponent,
    )
    from src.services.app_builder_service import (
        build_unified_component_tree,
        flatten_components,
    )

    page_id = uuid4()

    # Original structure with tabs
    original = [
        TabsComponent(
            id="tabs1",
            default_tab="tab1",
            children=[
                TabItemComponent(
                    id="tab1",
                    label="First Tab",
                    value="first",
                    children=[
                        TextComponent(id="t1", text="Tab 1 content"),
                    ],
                ),
                TabItemComponent(
                    id="tab2",
                    label="Second Tab",
                    value="second",
                    children=[
                        TextComponent(id="t2", text="Tab 2 content"),
                    ],
                ),
            ],
        ),
    ]

    # Flatten to rows
    rows = flatten_components(original, page_id)

    # Convert to fake ORM objects
    fake_rows = [
        FakeComponent(
            id=row["id"],
            component_id=row["component_id"],
            type=row["type"],
            props=row["props"],
            component_order=row["component_order"],
            parent_id=row["parent_id"],
            visible=row["visible"],
            width=row["width"],
            loading_workflows=row["loading_workflows"],
        )
        for row in rows
    ]

    # Rebuild tree
    rebuilt = build_unified_component_tree(fake_rows)

    # Verify structure
    assert len(rebuilt) == 1
    tabs = rebuilt[0]
    assert isinstance(tabs, TabsComponent)
    assert tabs.id == "tabs1"
    assert tabs.default_tab == "tab1"
    assert len(tabs.children) == 2

    # Verify tab items
    tab1 = tabs.children[0]
    assert isinstance(tab1, TabItemComponent)
    assert tab1.id == "tab1"
    assert tab1.label == "First Tab"
    assert tab1.value == "first"
    assert len(tab1.children) == 1
    assert tab1.children[0].text == "Tab 1 content"

    tab2 = tabs.children[1]
    assert isinstance(tab2, TabItemComponent)
    assert tab2.id == "tab2"
    assert tab2.label == "Second Tab"
    assert tab2.value == "second"
    assert len(tab2.children) == 1
    assert tab2.children[0].text == "Tab 2 content"
