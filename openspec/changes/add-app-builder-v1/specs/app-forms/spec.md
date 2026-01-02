# Capability: App Forms

Form integration with unified components, embedding, and inline form groups.

## ADDED Requirements

### Requirement: Unified Field Components

The system SHALL provide field components that work identically in standalone forms, embedded forms, and inline form groups within apps.

#### Scenario: Text input component
- **WHEN** a text-input component is rendered with name, label, placeholder, and validation props
- **THEN** it captures text input and validates according to pattern, minLength, maxLength

#### Scenario: Number input component
- **WHEN** a number-input component is rendered with min, max, step props
- **THEN** it captures numeric input within the specified constraints

#### Scenario: Textarea component
- **WHEN** a textarea component is rendered with rows and maxLength props
- **THEN** it captures multi-line text input with the specified dimensions

#### Scenario: Select component
- **WHEN** a select component is rendered with options array
- **THEN** it displays a dropdown with the specified options for single selection

#### Scenario: Multi-select component
- **WHEN** a multi-select component is rendered with options and maxItems
- **THEN** it allows selecting multiple options up to the specified limit

#### Scenario: Checkbox component
- **WHEN** a checkbox component is rendered
- **THEN** it captures a boolean true/false value

#### Scenario: Radio group component
- **WHEN** a radio-group component is rendered with options
- **THEN** it displays mutually exclusive options for single selection

#### Scenario: Date picker component
- **WHEN** a date-picker component is rendered with minDate and maxDate
- **THEN** it captures a date value within the allowed range

#### Scenario: File upload component
- **WHEN** a file-upload component is rendered with accept, maxSize, and multiple props
- **THEN** it allows uploading files matching the constraints

#### Scenario: Rich text component
- **WHEN** a rich-text component is rendered
- **THEN** it provides a WYSIWYG editor for formatted text input

### Requirement: Form Embedding

The system SHALL provide a FormEmbed component allowing apps to reference and display existing forms with inline progress.

#### Scenario: Embed form by ID
- **WHEN** a FormEmbed component references an existing form ID
- **THEN** the form fields are rendered within the app page

#### Scenario: Embed form with inline mode
- **WHEN** FormEmbed specifies mode="inline"
- **THEN** the form renders directly within the page layout

#### Scenario: Embed form with modal mode
- **WHEN** FormEmbed specifies mode="modal"
- **THEN** the form opens in a modal dialog when triggered

#### Scenario: Embed form with prefill
- **WHEN** FormEmbed specifies prefill values using expressions
- **THEN** form fields are pre-populated with the resolved values

#### Scenario: Embed form shows inline progress
- **WHEN** FormEmbed specifies showProgress=true and user submits
- **THEN** workflow execution progress displays inline instead of redirecting

#### Scenario: Embed form onComplete navigation
- **WHEN** FormEmbed specifies onComplete with type="navigate"
- **THEN** the app navigates to the specified page after workflow completion

#### Scenario: Embed form onComplete variable set
- **WHEN** FormEmbed specifies onComplete with type="set-variable"
- **THEN** the workflow result is stored in the specified page variable

#### Scenario: Embed form onComplete table refresh
- **WHEN** FormEmbed specifies onComplete with type="refresh-table"
- **THEN** the specified table component refreshes its data

### Requirement: Form Groups

The system SHALL provide a FormGroup component for inline field collections that submit to workflows via buttons.

#### Scenario: Form group collects named fields
- **WHEN** a FormGroup contains field components
- **THEN** field values are collected under the group's name identifier

#### Scenario: Button submits form group
- **WHEN** a Button specifies submitForm matching a FormGroup name
- **THEN** clicking the button collects field values from that group

#### Scenario: Form group validates before submit
- **WHEN** a user clicks submit on a form group with invalid fields
- **THEN** validation errors are displayed and submission is prevented

#### Scenario: Form group passes values to workflow
- **WHEN** a form group is submitted with onClick type="workflow"
- **THEN** collected field values are passed as workflow input

#### Scenario: Form group supports nested layout
- **WHEN** a FormGroup contains layout containers with fields
- **THEN** all nested fields are collected regardless of layout depth

### Requirement: Inline Progress Display

The system SHALL display workflow execution progress inline when forms are submitted with showProgress enabled.

#### Scenario: Progress bar during execution
- **WHEN** a form is submitted with showProgress and workflow is running
- **THEN** a progress bar displays with percentage completion

#### Scenario: Step indicators during execution
- **WHEN** workflow emits step events during execution
- **THEN** step indicators show completed, current, and pending steps

#### Scenario: Success message on completion
- **WHEN** workflow completes successfully
- **THEN** a success message is displayed with optional output data

#### Scenario: Error message on failure
- **WHEN** workflow fails with an error
- **THEN** an error message is displayed with details

#### Scenario: Cancel button during execution
- **WHEN** workflow is running with cancellable=true
- **THEN** a cancel button is available to abort the workflow
