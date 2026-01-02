# Change: Add App Builder v1

## Why

Extend Bifrost from a forms/workflow engine to a low-code app builder, enabling customers to build CRUD applications with data storage, multi-page navigation, and workflow-driven automation without writing code.

**Target Use Case:** A customer wants to build a small CRM to track customers, contacts, sales leads, and site surveys. They need to fill out forms onsite, upload pictures, run AI analysis on submissions to generate proposals, view site surveys in a list, and send proposals with one click.

## What Changes

- **Data Foundation**: Add Tables and Documents for persistent application data storage with JSONB flexibility
- **Application Container**: Add versioned application definitions with draft/publish workflow
- **Layout System**: Add recursive layout containers (row/column/grid) with component composition
- **Forms Integration**: Unify form fields and app components; support embedded forms and inline form groups
- **Display Components**: Add Table, Card, StatCard, FileViewer, and other display components
- **Interactive Components**: Add Button, Tabs, Modal, DropdownMenu with action handlers
- **Navigation & Routing**: Add App Shell (navbar/sidebar), React Router integration, page context
- **Permissions**: Add three-level permissions (app, page, component visibility expressions)
- **Action System**: Add workflow execution with loading states, variable store, expression evaluation
- **App Editor**: Add visual page builder with drag-drop, component palette, property panel
- **Embedding**: Add iframe/SDK embedding with token auth for multi-tenant global apps

## Impact

### Affected Specs (New)
- `data-tables` - Table storage and document CRUD
- `applications` - Application container and versioning
- `app-layout` - Layout system and rendering
- `app-forms` - Form integration (hybrid model)
- `app-components` - Display and interactive components
- `app-routing` - Navigation and page routing
- `app-permissions` - Three-level permission system
- `app-actions` - Workflow execution and variables
- `app-editor` - Visual editor UI
- `app-embedding` - Embedding and multi-tenant support

### Affected Code

**Backend (Python/FastAPI):**
- `api/src/models/orm/tables.py` - Table and Document ORM models
- `api/src/models/orm/applications.py` - Application ORM model
- `api/src/models/contracts/tables.py` - Table/Document Pydantic models
- `api/src/models/contracts/applications.py` - Application Pydantic models
- `api/src/routers/tables.py` - Table/Document API endpoints
- `api/src/routers/applications.py` - Application API endpoints
- `api/bifrost/tables.py` - SDK tables module for workflows
- `api/src/routers/cli.py` - CLI endpoints for SDK

**Frontend (TypeScript/React):**
- `client/src/pages/apps/` - App runtime renderer
- `client/src/pages/apps/editor/` - Visual app editor
- `client/src/components/app-builder/` - App builder components
- `client/src/lib/v1.d.ts` - Auto-generated TypeScript types

### Database Changes

```sql
CREATE TABLE tables (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    organization_id UUID REFERENCES organizations(id),
    application_id UUID REFERENCES applications(id),
    schema JSONB,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    created_by VARCHAR(255),
    UNIQUE(organization_id, name)
);

CREATE TABLE documents (
    id UUID PRIMARY KEY,
    table_id UUID NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    data JSONB NOT NULL,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    created_by VARCHAR(255),
    updated_by VARCHAR(255)
);

CREATE TABLE applications (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL,
    organization_id UUID REFERENCES organizations(id),
    live_definition JSONB,
    draft_definition JSONB,
    live_version INT DEFAULT 0,
    draft_version INT DEFAULT 1,
    version_history JSONB DEFAULT '[]',
    published_at TIMESTAMP,
    description TEXT,
    icon VARCHAR(50),
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    created_by VARCHAR(255),
    UNIQUE(organization_id, slug)
);
```

### Migration Files
- `api/alembic/versions/20260101_000000_add_tables_and_documents.py`
- `api/alembic/versions/20260102_000000_add_applications.py`
