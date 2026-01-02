# Capability: App Editor

Visual app builder with drag-drop, component palette, and property editing.

## ADDED Requirements

### Requirement: Editor Shell

The system SHALL provide an editor shell with three-panel layout for building apps visually.

#### Scenario: Editor layout structure
- **WHEN** the app editor is opened
- **THEN** it displays left panel (pages), center panel (canvas), right panel (properties)

#### Scenario: Page tree in left panel
- **WHEN** viewing the left panel
- **THEN** a tree of all pages in the app is displayed

#### Scenario: Canvas in center panel
- **WHEN** viewing the center panel
- **THEN** the selected page layout is rendered for editing

#### Scenario: Properties in right panel
- **WHEN** a component is selected
- **THEN** the right panel shows editable properties for that component

#### Scenario: Toolbar with actions
- **WHEN** viewing the editor
- **THEN** a toolbar displays Save Draft, Preview, and Publish actions

### Requirement: Page Management

The system SHALL provide page management operations within the editor.

#### Scenario: Create new page
- **WHEN** user clicks "Add Page" in the page tree
- **THEN** a new page is created with default route and empty layout

#### Scenario: Rename page
- **WHEN** user renames a page
- **THEN** the page title and route are updated

#### Scenario: Delete page
- **WHEN** user deletes a page
- **THEN** the page is removed from the app definition

#### Scenario: Reorder pages
- **WHEN** user drags a page in the tree
- **THEN** the page order in the definition is updated

#### Scenario: Select page to edit
- **WHEN** user clicks a page in the tree
- **THEN** that page's layout is shown in the canvas

### Requirement: Drag and Drop Canvas

The system SHALL provide a drag-and-drop canvas for visual layout editing.

#### Scenario: Drag component from palette
- **WHEN** user drags a component from the palette to canvas
- **THEN** the component is added to the layout at the drop position

#### Scenario: Drag to reorder components
- **WHEN** user drags an existing component within the canvas
- **THEN** the component is moved to the new position

#### Scenario: Drop indicators
- **WHEN** user drags over valid drop targets
- **THEN** visual indicators show where the component will be placed

#### Scenario: Drag into containers
- **WHEN** user drags a component into a row/column/grid container
- **THEN** the component is added as a child of that container

#### Scenario: Select component on click
- **WHEN** user clicks a component on the canvas
- **THEN** it becomes selected and properties panel updates

#### Scenario: Delete component with keyboard
- **WHEN** a component is selected and user presses Delete/Backspace
- **THEN** the component is removed from the layout

### Requirement: Component Palette

The system SHALL provide a component palette with categorized components.

#### Scenario: Layout components category
- **WHEN** viewing the palette
- **THEN** layout components (row, column, grid) are available

#### Scenario: Form components category
- **WHEN** viewing the palette
- **THEN** form field components are available

#### Scenario: Display components category
- **WHEN** viewing the palette
- **THEN** display components (heading, text, card, table) are available

#### Scenario: Interactive components category
- **WHEN** viewing the palette
- **THEN** interactive components (button, tabs, modal) are available

#### Scenario: Search components
- **WHEN** user types in palette search
- **THEN** components are filtered by name

#### Scenario: Drag from palette
- **WHEN** user drags a component from palette
- **THEN** a new instance is created on drop

### Requirement: Property Editor

The system SHALL provide a dynamic property editor based on selected component type.

#### Scenario: Show properties for selected component
- **WHEN** a component is selected
- **THEN** editable properties for that component type are displayed

#### Scenario: Edit text properties
- **WHEN** a property is a text field
- **THEN** a text input allows editing

#### Scenario: Edit select properties
- **WHEN** a property is an enum/options field
- **THEN** a dropdown allows selection

#### Scenario: Edit boolean properties
- **WHEN** a property is a boolean
- **THEN** a toggle/checkbox allows editing

#### Scenario: Edit complex properties
- **WHEN** a property is complex (columns, actions, dataSource)
- **THEN** a specialized editor or modal allows configuration

#### Scenario: Property changes update canvas
- **WHEN** a property value is changed
- **THEN** the canvas immediately reflects the change

#### Scenario: Validation errors shown
- **WHEN** a property value is invalid
- **THEN** a validation error is displayed

### Requirement: Navigation Editor

The system SHALL provide navigation configuration for navbar and sidebar.

#### Scenario: Edit navbar items
- **WHEN** user edits navigation settings
- **THEN** navbar items (title, logo, nav items) can be configured

#### Scenario: Edit sidebar sections
- **WHEN** user edits sidebar settings
- **THEN** sections and items can be added, edited, reordered, removed

#### Scenario: Configure nav item properties
- **WHEN** editing a nav item
- **THEN** label, icon, page/url can be configured

#### Scenario: Preview navigation changes
- **WHEN** navigation is edited
- **THEN** the app shell preview updates accordingly

### Requirement: Preview Mode

The system SHALL provide a preview mode for testing the app without publishing.

#### Scenario: Toggle preview mode
- **WHEN** user clicks Preview
- **THEN** the editor switches to preview showing the rendered app

#### Scenario: Preview uses draft definition
- **WHEN** in preview mode
- **THEN** the draft definition is used (not live)

#### Scenario: Exit preview mode
- **WHEN** user exits preview
- **THEN** they return to the editor view

#### Scenario: Preview in new window
- **WHEN** user chooses to preview in new window
- **THEN** the app opens in a new browser tab

### Requirement: Draft and Publish

The system SHALL support draft saving and publishing within the editor.

#### Scenario: Auto-save drafts
- **WHEN** changes are made in the editor
- **THEN** drafts are automatically saved periodically

#### Scenario: Manual save draft
- **WHEN** user clicks Save Draft
- **THEN** the current state is saved as draft

#### Scenario: Publish confirmation
- **WHEN** user clicks Publish
- **THEN** a confirmation dialog shows changes since last publish

#### Scenario: Publish updates live version
- **WHEN** user confirms publish
- **THEN** the draft becomes live and version is incremented

#### Scenario: Show unsaved changes indicator
- **WHEN** there are unsaved changes
- **THEN** an indicator shows the draft has modifications

### Requirement: Undo and Redo

The system SHALL provide undo/redo functionality in the editor.

#### Scenario: Undo last change
- **WHEN** user presses Ctrl/Cmd+Z or clicks Undo
- **THEN** the last edit is reverted

#### Scenario: Redo undone change
- **WHEN** user presses Ctrl/Cmd+Shift+Z or clicks Redo
- **THEN** the undone edit is re-applied

#### Scenario: Undo stack tracks component changes
- **WHEN** components are added, moved, deleted, or properties changed
- **THEN** each action is added to the undo stack

#### Scenario: Undo stack clears on page switch
- **WHEN** user switches to a different page
- **THEN** the undo stack is cleared for the new context
