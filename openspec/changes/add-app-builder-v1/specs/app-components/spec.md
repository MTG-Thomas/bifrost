# Capability: App Components

Display and interactive components for building app interfaces.

## ADDED Requirements

### Requirement: Table Component

The system SHALL provide a Table component for displaying data with columns, actions, pagination, sorting, and filtering.

#### Scenario: Table renders columns from data source
- **WHEN** a Table component specifies dataSource and columns
- **THEN** data is fetched and rendered in a table with specified columns

#### Scenario: Table column types
- **WHEN** columns specify type as text, number, date, or badge
- **THEN** cell values are formatted according to the column type

#### Scenario: Table badge column colors
- **WHEN** a badge column specifies badgeColors mapping
- **THEN** cell values are rendered with the corresponding color

#### Scenario: Table pagination
- **WHEN** Table specifies paginated=true and pageSize
- **THEN** data is paginated with page controls

#### Scenario: Table sorting
- **WHEN** Table specifies sortable=true on columns
- **THEN** clicking column headers sorts the data

#### Scenario: Table filtering
- **WHEN** Table specifies filterable=true
- **THEN** filter controls are available for narrowing data

#### Scenario: Table searching
- **WHEN** Table specifies searchable=true
- **THEN** a search input filters data across text columns

#### Scenario: Table row selection
- **WHEN** Table specifies selectable=true
- **THEN** rows can be selected with checkboxes

#### Scenario: Table row actions
- **WHEN** Table specifies rowActions array
- **THEN** action buttons appear for each row

#### Scenario: Table bulk actions
- **WHEN** Table specifies bulkActions and rows are selected
- **THEN** bulk action buttons appear for operating on selected rows

#### Scenario: Table header actions
- **WHEN** Table specifies headerActions (e.g., "Add New" button)
- **THEN** action buttons appear in the table header

#### Scenario: Table row click navigation
- **WHEN** Table specifies onRowClick with type="navigate"
- **THEN** clicking a row navigates to the specified page with row data

#### Scenario: Table empty state
- **WHEN** Table has no data and specifies emptyMessage
- **THEN** the empty message and optional action are displayed

### Requirement: Button Component

The system SHALL provide a Button component with variants, sizes, icons, and action handlers.

#### Scenario: Button variants
- **WHEN** Button specifies variant (default, primary, secondary, destructive, outline, ghost)
- **THEN** the button is styled accordingly

#### Scenario: Button sizes
- **WHEN** Button specifies size (sm, md, lg)
- **THEN** the button is sized accordingly

#### Scenario: Button with icon
- **WHEN** Button specifies an icon
- **THEN** the icon is displayed alongside the label

#### Scenario: Button onClick workflow
- **WHEN** Button specifies onClick with type="workflow"
- **THEN** clicking executes the specified workflow with input data

#### Scenario: Button onClick navigate
- **WHEN** Button specifies onClick with type="navigate"
- **THEN** clicking navigates to the specified page

#### Scenario: Button onClick modal
- **WHEN** Button specifies onClick with type="modal"
- **THEN** clicking opens a modal with the specified form

#### Scenario: Button onClick set-variable
- **WHEN** Button specifies onClick with type="set-variable"
- **THEN** clicking sets the specified page variable

#### Scenario: Button loading state
- **WHEN** a workflow is executing after button click
- **THEN** the button shows loading spinner with optional loadingText

#### Scenario: Button disabled state
- **WHEN** Button specifies disabled expression evaluating to true
- **THEN** the button is disabled and unclickable

#### Scenario: Button confirmation dialog
- **WHEN** Button specifies confirm with title and message
- **THEN** a confirmation dialog appears before executing the action

### Requirement: Card Component

The system SHALL provide Card and StatCard components for organizing content.

#### Scenario: Card with title and children
- **WHEN** Card specifies title and children layout
- **THEN** a card is rendered with header and content area

#### Scenario: Card with header actions
- **WHEN** Card specifies headerActions
- **THEN** action buttons appear in the card header

#### Scenario: Card styling options
- **WHEN** Card specifies padding and shadow props
- **THEN** the card is styled accordingly

#### Scenario: Stat card with value
- **WHEN** StatCard specifies title and value expression
- **THEN** the stat is displayed prominently with the evaluated value

#### Scenario: Stat card with trend
- **WHEN** StatCard specifies trend with value and direction
- **THEN** a trend indicator shows the change direction

#### Scenario: Stat card clickable
- **WHEN** StatCard specifies onClick handler
- **THEN** the card is clickable and executes the action

### Requirement: Display Components

The system SHALL provide display components for text, headings, images, and visual elements.

#### Scenario: Heading component
- **WHEN** Heading specifies text and level (1-6)
- **THEN** the appropriate heading element is rendered

#### Scenario: Text component
- **WHEN** Text specifies label and value
- **THEN** a label-value pair is displayed

#### Scenario: Divider component
- **WHEN** Divider is placed in layout
- **THEN** a horizontal line separates content

#### Scenario: Spacer component
- **WHEN** Spacer specifies height
- **THEN** vertical space is added to the layout

#### Scenario: Image component
- **WHEN** Image specifies src and optional dimensions
- **THEN** the image is rendered with specified sizing

#### Scenario: Badge component
- **WHEN** Badge specifies value and color
- **THEN** a colored badge displays the value

#### Scenario: Progress component
- **WHEN** Progress specifies value (0-100)
- **THEN** a progress bar shows the percentage

### Requirement: File Viewer Component

The system SHALL provide a FileViewer component for displaying files inline, in modals, or as download links.

#### Scenario: Inline file viewer for images
- **WHEN** FileViewer specifies mode="inline" for an image file
- **THEN** the image is displayed inline with optional maxHeight

#### Scenario: Inline file viewer for PDFs
- **WHEN** FileViewer specifies mode="inline" for a PDF file
- **THEN** a PDF viewer is embedded in the page

#### Scenario: Modal file viewer
- **WHEN** FileViewer specifies mode="modal"
- **THEN** clicking opens the file in a modal overlay

#### Scenario: Download link mode
- **WHEN** FileViewer specifies mode="download-link"
- **THEN** a download link is rendered for the file

#### Scenario: File viewer from expression
- **WHEN** FileViewer specifies filePath as expression
- **THEN** the file reference is resolved from context

### Requirement: Interactive Components

The system SHALL provide Tabs, Modal, and DropdownMenu components for interactive interfaces.

#### Scenario: Tabs component
- **WHEN** Tabs specifies items with labels and content
- **THEN** tabbed interface allows switching between content panels

#### Scenario: Modal component
- **WHEN** Modal is triggered by an action
- **THEN** a modal overlay displays the specified content

#### Scenario: Modal with form
- **WHEN** Modal contains a FormEmbed or FormGroup
- **THEN** form submission can close the modal

#### Scenario: Dropdown menu component
- **WHEN** DropdownMenu specifies trigger and items
- **THEN** clicking trigger shows menu with action items
