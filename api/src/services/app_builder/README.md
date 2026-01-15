# App Builder

Low-code application builder with drag-and-drop component composition, draft/live versioning, and dynamic expression binding.

## Architecture Overview

```
                          +-----------------------+
                          |    applications       |
                          |  - name, slug, icon   |
                          |  - organization_id    |
                          |  - active_version_id  |
                          |  - draft_version_id   |
                          +-----------------------+
                                     |
                    +----------------+----------------+
                    |                                 |
                    v                                 v
          +------------------+              +------------------+
          |   app_versions   |              |   app_versions   |
          |   (draft)        |              |   (published)    |
          +------------------+              +------------------+
                    |                                 |
                    v                                 v
          +------------------+              +------------------+
          |    app_pages     |              |    app_pages     |
          |  - page_id       |              |  - page_id       |
          |  - title, path   |              |  - title, path   |
          |  - data_sources  |              |  - data_sources  |
          |  - version_id    |              |  - version_id    |
          +------------------+              +------------------+
                    |                                 |
                    v                                 v
          +------------------+              +------------------+
          |  app_components  |              |  app_components  |
          |  - component_id  |              |  - component_id  |
          |  - type, props   |              |  - type, props   |
          |  - parent_id     |              |  - parent_id     |
          |  - component_order|             |  - component_order|
          +------------------+              +------------------+
```

## Database Schema

### Applications
Organization-scoped metadata container:
- `organization_id`: NULL for global apps, UUID for org-scoped
- `active_version_id`: Currently published (live) version
- `draft_version_id`: Work-in-progress version
- `navigation`, `permissions`: App-level JSONB config

### AppVersion
Point-in-time snapshot:
- Links to pages via `version_id` foreign key
- Publishing creates a new version from draft
- Rollback changes `active_version_id` to any previous version

### AppPage
Page definition within a version:
- `page_id`: Stable identifier (e.g., "dashboard")
- `path`: Route path (e.g., "/", "/clients/:id")
- `data_sources`: JSONB array of data source configs
- `launch_workflow_id`: Workflow to execute on page load

### AppComponent
Flat storage with tree structure via `parent_id`:
- `component_id`: Stable identifier (e.g., "btn_submit")
- `type`: Component type ("button", "data-table", "row", etc.)
- `props`: JSONB with type-specific properties
- `parent_id`: UUID reference to parent component (tree structure)
- `component_order`: Sibling ordering within parent

## Versioning Model

```
Draft Version (editable)
    |
    |-- [Publish] --> Creates new AppVersion
    |                 Copies all pages/components
    |                 Sets as active_version_id
    |
Active Version (immutable, live)
    |
    |-- [Rollback] --> Sets active_version_id to previous version
```

- **Draft**: Always editable, one per app
- **Published versions**: Immutable snapshots, many per app
- **Rollback**: Changes `active_version_id` without modifying draft

## Component System

### Type Discrimination

Components use a discriminated union on the `type` field:

```python
AppComponent = Annotated[
    Union[
        HeadingComponent,
        TextComponent,
        ButtonComponent,
        DataTableComponent,
        # ... 20+ component types
    ],
    Field(discriminator="type"),
]
```

Each component type has typed props validated by Pydantic.

### Component Categories

**Display**
- `heading`: H1-H6 text with level prop
- `text`: Paragraph with optional label
- `html`: Raw HTML/JSX content
- `image`: Image with src, alt, sizing
- `badge`: Status badge with variant
- `progress`: Progress bar with value

**Layout**
- `row`, `column`, `grid`: Layout containers (children stored as separate rows)
- `card`: Card with title, description, children
- `divider`: Horizontal/vertical divider
- `spacer`: Configurable whitespace
- `tabs`: Tabbed content with items

**Data**
- `data-table`: Table with columns, actions, row click handlers
- `file-viewer`: Display files inline, modal, or download

**Form Inputs**
- `text-input`: Text with validation (email, url, pattern)
- `number-input`: Number with min/max/step
- `select`: Dropdown with static or dynamic options
- `checkbox`: Boolean toggle
- `form-embed`: Embed a form by ID
- `form-group`: Group fields with shared label

**Actions**
- `button`: Navigate, trigger workflow, open modal, submit form
- `modal`: Dialog with content layout and footer actions

### Common Component Fields

Every component supports:
- `visible`: Expression for conditional rendering
- `width`: "auto", "full", "1/2", "1/3", "1/4", "2/3", "3/4"
- `loading_workflows`: Workflow IDs that trigger skeleton state
- `grid_span`: Column span for grid layouts
- `repeat_for`: Iterate over array (items, item_key, as)

## Expression Syntax

Dynamic values use `{{ expression }}` syntax:

```
{{ variable }}                    Page variable
{{ workflow.dataSourceId }}       Workflow result by data source ID
{{ row.fieldName }}               Table row context (in row actions)
{{ field.inputId }}               Form field value (in submit handlers)
{{ data.sourceId.path }}          Data source value
```

## Data Sources

Pages define data sources in `data_sources` array:

| Type | Description |
|------|-------------|
| `api` | REST endpoint with `endpoint` prop |
| `static` | Hardcoded `data` value |
| `computed` | Expression-based `expression` prop |
| `data-provider` | External data provider by `data_provider_id` |
| `workflow` | Execute workflow by `workflow_id` on page load |

```python
DataSourceConfig(
    id="clients",
    type="workflow",
    workflow_id="...",
    input_params={"org_id": "{{ organization.id }}"},
)
```

## Key Services

### AppBuilderService

Tree operations and versioning:
- `flatten_layout_tree()`: Convert nested JSON to component rows
- `build_layout_tree()`: Reconstruct tree from flat rows
- `create_page_with_layout()`: Create page with flattened components
- `publish_with_versioning()`: Create new version from draft
- `rollback_to_version()`: Change active version
- `export_application()` / `import_application()`: JSON portability

### AppComponentsService

Component-level CRUD:
- `create_component()`: Add component with auto-ordering
- `update_component()`: Update props/fields
- `move_component()`: Reparent with order rebalancing
- `delete_component()`: Remove with cascade
- `batch_update_props()`: Bulk property updates

## API Endpoints

### Applications
- `POST /api/applications` - Create app
- `GET /api/applications` - List apps
- `GET /api/applications/{slug}` - Get metadata
- `PATCH /api/applications/{slug}` - Update metadata
- `DELETE /api/applications/{slug}` - Delete app

### Draft/Publish
- `GET /api/applications/{app_id}/draft` - Get draft definition
- `PUT /api/applications/{app_id}/draft` - Save draft definition
- `POST /api/applications/{app_id}/publish` - Publish draft to live
- `POST /api/applications/{app_id}/rollback` - Rollback to previous version

### Pages (separate router)
- `GET /api/applications/{app_id}/pages` - List pages
- `POST /api/applications/{app_id}/pages` - Create page
- `GET /api/applications/{app_id}/pages/{page_id}` - Get page with layout
- `PATCH /api/applications/{app_id}/pages/{page_id}` - Update page
- `DELETE /api/applications/{app_id}/pages/{page_id}` - Delete page

### Components (separate router)
- `GET /api/applications/{app_id}/pages/{page_id}/components` - List components
- `POST /api/applications/{app_id}/pages/{page_id}/components` - Create component
- `PATCH /api/.../components/{component_id}` - Update component
- `POST /api/.../components/{component_id}/move` - Move component
- `DELETE /api/.../components/{component_id}` - Delete component

## Key Files

| File | Responsibility |
|------|----------------|
| `models/orm/applications.py` | ORM models (Application, AppVersion, AppPage, AppComponent) |
| `models/contracts/app_components.py` | Pydantic component types and props |
| `services/app_builder_service.py` | Tree flattening, reconstruction, versioning |
| `services/app_components_service.py` | Component CRUD, move, batch operations |
| `routers/applications.py` | App-level endpoints and repository |
| `routers/app_pages.py` | Page CRUD endpoints |
| `routers/app_components.py` | Component CRUD endpoints |
