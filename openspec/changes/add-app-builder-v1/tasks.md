# Tasks: Add App Builder v1

## 1. Phase 1: Data Foundation (Tables & Documents)

**Status: COMPLETE**

- [x] 1.1 Create alembic migration for `tables` and `documents` tables
- [x] 1.2 Implement ORM models in `api/src/models/orm/tables.py`
- [x] 1.3 Implement contract models in `api/src/models/contracts/tables.py`
- [x] 1.4 Create router endpoints in `api/src/routers/tables.py`
- [x] 1.5 Implement SDK module `api/bifrost/tables.py`
- [x] 1.6 Add CLI endpoints in `api/src/routers/cli.py` for SDK access
- [x] 1.7 Write integration tests `api/tests/integration/api/test_tables.py`
- [x] 1.8 Write unit tests `api/tests/unit/sdk/test_sdk_tables.py`

## 2. Phase 2: Application Container

**Status: COMPLETE**

- [x] 2.1 Create alembic migration for `applications` table
- [x] 2.2 Implement ORM model in `api/src/models/orm/applications.py`
- [x] 2.3 Implement contract models in `api/src/models/contracts/applications.py`
- [x] 2.4 Create router with CRUD, draft/publish, rollback in `api/src/routers/applications.py`
- [x] 2.5 Implement version history tracking (last 10 versions)
- [x] 2.6 Write integration tests for application endpoints

## 3. Phase 3: Layout System

**Status: COMPLETE** (tests pending)

- [x] 3.1 Define TypeScript types for layout containers (row/column/grid) - `client/src/lib/app-builder-types.ts`
- [x] 3.2 Define TypeScript types for component base (props, dataSource, visible) - `client/src/lib/app-builder-types.ts`
- [x] 3.3 Implement recursive layout renderer component - `client/src/components/app-builder/LayoutRenderer.tsx`
- [x] 3.4 Implement component registry with type-safe props - `client/src/components/app-builder/ComponentRegistry.ts`
- [x] 3.5 Implement expression parser for `{{ }}` syntax - `client/src/lib/expression-parser.ts`
- [x] 3.6 Implement expression evaluator with context resolution - `client/src/lib/expression-parser.ts`
- [ ] 3.7 Write unit tests for layout renderer
- [ ] 3.8 Write unit tests for expression evaluator

## 4. Phase 4: Forms Integration

**Status: COMPLETE** (tests pending)

- [x] 4.1 Define FormEmbed and FormGroup types - `app-builder-types.ts`
- [x] 4.2 Implement FormEmbed component for referencing existing forms - `FormEmbedComponent.tsx`
- [x] 4.3 Implement FormGroup component for inline field collections - `FormGroupComponent.tsx`
- [x] 4.4 Implement inline progress display component - `ExecutionInlineDisplay.tsx`
- [x] 4.5 Register FormEmbed and FormGroup in ComponentRegistry - `components/index.ts`
- [x] 4.6 Add FormEmbed and FormGroup to ComponentPalette - `ComponentPalette.tsx`
- [ ] 4.7 Write integration tests for form embedding
- [x] 4.8 Add onExecutionStart callback and preventNavigation prop to FormRenderer
- [x] 4.9 Create ExecutionInlineDisplay component for compact execution viewing
- [x] 4.10 Update FormEmbedComponent with form→executing→complete state machine
- [x] 4.11 Wire up onSubmit actions after execution completes
- [x] 4.12 Inject execution result into expression context

## 5. Phase 5: Display & Interactive Components

**Status: COMPLETE** (tests pending)

- [x] 5.1 Implement Table component with columns, pagination, sorting - `DataTableComponent.tsx`
- [x] 5.2 Implement Table row actions and bulk actions - `DataTableComponent.tsx`
- [x] 5.3 Implement Button component with action handlers - `ButtonComponent.tsx`
- [x] 5.4 Implement Card and StatCard components - `CardComponent.tsx`, `StatCardComponent.tsx`
- [x] 5.5 Implement FileViewer component (inline/modal/download) - `FileViewerComponent.tsx`
- [x] 5.6 Implement Heading, Text, Divider, Spacer components - `HeadingComponent.tsx`, `TextComponent.tsx`, `DividerComponent.tsx`, `SpacerComponent.tsx`
- [x] 5.7 Implement Image component - `ImageComponent.tsx`
- [x] 5.8 Implement Tabs component - `TabsComponent.tsx`
- [x] 5.9 Implement Modal component for inline forms - `ModalComponent.tsx`
- [x] 5.10 Implement Badge and Progress components - `BadgeComponent.tsx`, `ProgressComponent.tsx`
- [ ] 5.11 Write unit tests for each component
- [x] 5.12 Add row context support (`{{ row.* }}`) to DataTable actions
- [x] 5.13 Add `disabled` expression support to Button component
- [x] 5.14 Add `disabled` expression support to DataTable row/header actions

## 6. Phase 6: Navigation & Routing

**Status: COMPLETE** (tests pending)

- [x] 6.1 Implement App Shell component (navbar, sidebar, main content) - `client/src/components/app-builder/AppShell.tsx`
- [x] 6.2 Implement NavItem and SidebarSection components - integrated in `AppShell.tsx`
- [x] 6.3 Integrate React Router for client-side routing - `client/src/App.tsx` routes
- [x] 6.4 Implement route generation from page definitions - `ApplicationRunner.tsx`
- [x] 6.5 Implement PageContext provider with params, query, user, org - `client/src/contexts/AppContext.tsx`
- [x] 6.6 Implement navigate, setVariable, executeWorkflow, refreshTable functions
- [ ] 6.7 Write integration tests for routing

## 7. Phase 7: Permissions System

**Status: COMPLETE** (tests pending)

- [x] 7.1 Define permission schema in application definition - `app-builder-types.ts` (PermissionConfig, PagePermission)
- [x] 7.2 Implement app-level access check middleware - `app-builder-permissions.ts` (hasAppAccess, getAppPermissionLevel)
- [x] 7.3 Implement page-level permission guard component - `PermissionGuard.tsx`
- [x] 7.4 Integrate component visibility expression evaluation - Already in LayoutRenderer via `visible` expression
- [x] 7.5 Implement permission denied page/redirect - `PermissionGuard.tsx` (AccessDenied, LoginRequired components)
- [x] 7.6 Implement navigation filtering (hide inaccessible pages) - `AppShell.tsx` (hasPageAccess check)
- [ ] 7.7 Write unit tests for permission evaluation

## 8. Phase 8: Action System

**Status: COMPLETE** (tests pending)

- [x] 8.1 Implement useAppBuilderActions hook with loading/error states - `client/src/hooks/useAppBuilderActions.ts`
- [x] 8.2 Implement variable store with Zustand - `client/src/stores/app-builder.store.ts`
- [x] 8.3 Implement table refresh mechanism - in app-builder.store.ts
- [x] 8.4 Implement toast notifications for action results - integrated with sonner
- [x] 8.5 Implement confirmation dialog for destructive actions
- [ ] 8.6 Write integration tests for workflow execution
- [x] 8.7 Integrate WorkflowParametersForm for workflow input modal
- [x] 8.8 Add workflow metadata lookup to determine if modal needed
- [x] 8.9 Inject workflow output into context as `{{ workflow.result.* }}` - `ApplicationRunner.tsx`, `AppContext.tsx`
- [x] 8.10 Support onComplete actions (navigate, set-variable, refresh-table) - `ApplicationRunner.tsx`, `ButtonComponent.tsx`

## 9. Phase 9: App Editor

**Status: COMPLETE** (undo/redo and tests pending)

- [x] 9.1 Implement editor shell with three-panel layout - `client/src/components/app-builder/editor/EditorShell.tsx`
- [x] 9.2 Implement page tree navigator with CRUD - `client/src/components/app-builder/editor/PageTree.tsx`
- [x] 9.3 Implement drag-and-drop canvas - `client/src/components/app-builder/editor/EditorCanvas.tsx`
- [x] 9.4 Implement component palette - `client/src/components/app-builder/editor/ComponentPalette.tsx`
- [x] 9.5 Implement property editor panel (dynamic by component type) - `client/src/components/app-builder/editor/PropertyEditor.tsx`
- [x] 9.6 Implement navigation editor (navbar/sidebar items) - `NavigationEditor.tsx`, navigation config in AppShell.tsx
- [x] 9.7 Implement preview mode toggle - Preview tab in editor
- [x] 9.8 Implement auto-save for drafts - Save Draft button
- [x] 9.9 Implement publish confirmation flow - Publish dialog
- [ ] 9.10 Implement undo/redo functionality
- [ ] 9.11 Write integration tests for editor workflows
- [x] 9.12 Create VariablePreview panel component - `client/src/components/app-builder/editor/VariablePreview.tsx`
- [x] 9.13 Add variable preview toggle to editor toolbar - Variable icon in EditorShell.tsx
- [x] 9.14 Add variable insertion helper (click to insert `{{ path }}`) - CopyButton in VariablePreview.tsx
- [x] 9.15 Add row context hints to property editor for table actions - Implemented via WorkflowParameterEditor with `isRowAction` prop

## 10. Phase 10: Embedding & Access

**Status: MOSTLY COMPLETE** (token auth deferred to future work)

- [x] 10.1 Create standalone app renderer route `/apps/:slug` - `ApplicationRunner.tsx`, routes in `App.tsx`
- [x] 10.2 Create embed route `/embed/:slug` - `ApplicationEmbed` component, route in `App.tsx`
- [ ] 10.3 Implement token-based authentication for embeds (deferred - requires backend API endpoint)
- [x] 10.4 Implement theme customization for embeds - URL params (`primaryColor`, `backgroundColor`, `textColor`, `logo`)
- [x] 10.5 Verify multi-tenant global app data scoping - Handled by existing org-scoped queries in API
- [ ] 10.6 Write end-to-end tests for embedded apps
- [x] 10.7 Write user documentation for app builder - Completed in bifrost-docs repository

## 11. Validation & Quality

- [ ] All unit tests passing
- [ ] All integration tests passing
- [x] Backend type checking passing (`pyright`) - 9 errors unrelated to App Builder (MCP/fastmcp imports)
- [ ] Backend linting passing (`ruff check`) - Has pre-existing issues unrelated to App Builder
- [x] Frontend type checking passing (`npm run tsc`)
- [x] Frontend linting passing (`npm run lint`)
- [ ] TypeScript types regenerated (`npm run generate:types`)

## 12. Phase 12: Form Input Components

**Status: COMPLETE**

- [x] 12.1 Define form input types in app-builder-types.ts
- [x] 12.2 Implement TextInputComponent with label, placeholder, validation
- [x] 12.3 Implement SelectComponent with static/data-driven options
- [x] 12.4 Implement CheckboxComponent
- [x] 12.5 Implement NumberInputComponent with min/max
- [x] 12.6 Implement field value tracking in AppContext (`{{ field.* }}`)
- [x] 12.7 Add "submit" action type to Button for form submission
- [x] 12.8 Register all input components in ComponentRegistry
- [x] 12.9 Add input components to ComponentPalette

## 13. Phase 13: Page Lifecycle & Data

**Status: COMPLETE**

- [x] 13.1 Add launchWorkflowId to PageDefinition type - `app-builder-types.ts`
- [x] 13.2 Implement launch workflow execution on page mount - `usePageData.ts`, `AppRenderer.tsx`
- [x] 13.3 Inject launch workflow results into context as `{{ workflow.* }}` - `usePageData.ts`, `AppRenderer.tsx`
- [x] 13.4 Add workflow selector to page properties in editor - `PropertyEditor.tsx` PagePropertiesSection
- [x] 13.5 Implement data source configuration in page definition - Enhanced DataSource type with data-provider/workflow types
- [x] 13.6 Implement data provider loading with input params - `usePageData.ts`
- [x] 13.7 Connect DataTable to data source by name - Already supported via `context.data?.[props.dataSource]`
- [x] 13.8 Connect Select options to data source - Already supported via `optionsSource` and `context.data`

## 14. Bugs to Fix

- [x] 14.1 BUG: Padding and gap properties not applying to layouts in preview - Fixed with inline styles in LayoutRenderer.tsx
- [x] 14.2 BUG: Debug and fix expression evaluation for layout properties - Switched to inline styles (Tailwind JIT can't compile dynamic values)

## 16. Property Editor UX Overhaul

**Status: COMPLETE**

- [x] 16.1 Create WorkflowPicker component - Dropdown that fetches available workflows
- [x] 16.2 Create KeyValueEditor component - Visual key-value pairs with add/remove
- [x] 16.3 Create ActionBuilder component - Visual action configuration
- [x] 16.4 Create ColumnBuilder component - Visual DataTable column editor
- [x] 16.5 Create OptionBuilder component - Visual Select options editor
- [x] 16.6 Create TableActionBuilder component - Visual row/header action editor
- [x] 16.7 Integrate WorkflowPicker into Button workflow action
- [x] 16.8 Integrate KeyValueEditor for action parameters
- [x] 16.9 Integrate ColumnBuilder for DataTable columns
- [x] 16.10 Integrate TableActionBuilder for DataTable row/header actions
- [x] 16.11 Integrate OptionBuilder for Select options
- [x] 16.12 Add visual Row Click behavior editor with type selector
- [x] 16.13 Create WorkflowParameterEditor - Shows workflow parameters from metadata with expression support
- [x] 16.14 Integrate WorkflowParameterEditor for Button workflow/submit actions
- [x] 16.15 Integrate WorkflowParameterEditor for TableActionBuilder workflow actions

## 15. Documentation

**Status: COMPLETE** (in bifrost-docs repository)

- [x] 15.1 Update README with App Builder overview - `bifrost-docs/src/content/docs/core-concepts/app-builder.mdx`
- [x] 15.2 Document SDK `tables` module - Already in `bifrost-docs/src/content/docs/sdk-reference/sdk/tables-module.mdx`
- [x] 15.3 Document application definition schema - `bifrost-docs/src/content/docs/sdk-reference/app-builder/schema.mdx`
- [x] 15.4 Document component library with examples - `bifrost-docs/src/content/docs/sdk-reference/app-builder/components.mdx`
- [x] 15.5 Create App Builder user guide - `bifrost-docs/src/content/docs/sdk-reference/app-builder/` (expressions.mdx, actions.mdx)
