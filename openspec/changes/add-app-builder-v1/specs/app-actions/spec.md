# Capability: App Actions

Workflow execution, variable management, and action handling.

## ADDED Requirements

### Requirement: Workflow Execution

The system SHALL provide workflow execution from app actions with loading states, results, and error handling.

#### Scenario: Execute workflow from button
- **WHEN** a button with onClick type="workflow" is clicked
- **THEN** the specified workflow is executed with provided input data

#### Scenario: Execute workflow from table action
- **WHEN** a table row action executes a workflow
- **THEN** the workflow receives row data as input

#### Scenario: Loading state during execution
- **WHEN** a workflow is executing
- **THEN** the triggering element shows a loading state

#### Scenario: Workflow success handling
- **WHEN** a workflow completes successfully
- **THEN** the success handler (navigate, set-variable, refresh-table) is executed

#### Scenario: Workflow error handling
- **WHEN** a workflow fails with an error
- **THEN** an error toast notification is displayed with details

#### Scenario: Workflow with input expressions
- **WHEN** workflow input contains expressions like {{ params.id }}
- **THEN** expressions are evaluated before execution

#### Scenario: Concurrent workflow execution
- **WHEN** multiple workflows are triggered simultaneously
- **THEN** each executes independently with its own loading state

### Requirement: Variable Store

The system SHALL provide a reactive variable store for page-level state management.

#### Scenario: Initialize variables on page load
- **WHEN** a page loads
- **THEN** variables are initialized to their default values if specified

#### Scenario: Set variable from action
- **WHEN** an action with type="set-variable" executes
- **THEN** the specified variable is updated with the value

#### Scenario: Set variable from workflow result
- **WHEN** a workflow completes with onComplete type="set-variable"
- **THEN** the workflow output is stored in the variable

#### Scenario: Variables trigger re-render
- **WHEN** a variable value changes
- **THEN** components referencing that variable re-render

#### Scenario: Variable scoped to page
- **WHEN** navigating away from a page
- **THEN** page variables are cleared (not persisted)

#### Scenario: Variable available in expressions
- **WHEN** an expression references {{ variables.name }}
- **THEN** the current variable value is resolved

### Requirement: Table Refresh

The system SHALL provide table refresh functionality for updating displayed data after actions.

#### Scenario: Refresh table by alias
- **WHEN** refreshTable("surveys") is called
- **THEN** the table component with alias "surveys" refetches its data

#### Scenario: Refresh table from onComplete
- **WHEN** a workflow completes with onComplete type="refresh-table"
- **THEN** the specified table is automatically refreshed

#### Scenario: Refresh maintains pagination
- **WHEN** a table is refreshed
- **THEN** the current page and sort order are preserved

#### Scenario: Refresh clears selection
- **WHEN** a table is refreshed
- **THEN** any selected rows are deselected

### Requirement: Toast Notifications

The system SHALL display toast notifications for action feedback.

#### Scenario: Success toast on workflow completion
- **WHEN** a workflow completes successfully
- **THEN** a success toast is displayed (auto-dismiss after 5 seconds)

#### Scenario: Error toast on failure
- **WHEN** an action fails with an error
- **THEN** an error toast is displayed with the error message

#### Scenario: Custom success message
- **WHEN** an action specifies a successMessage
- **THEN** that message is displayed in the success toast

#### Scenario: Toast dismissible
- **WHEN** a toast is displayed
- **THEN** the user can manually dismiss it

### Requirement: Confirmation Dialogs

The system SHALL display confirmation dialogs for destructive or important actions.

#### Scenario: Show confirmation before action
- **WHEN** an action specifies confirm with title and message
- **THEN** a confirmation dialog appears before executing

#### Scenario: Confirmation with dynamic message
- **WHEN** confirm.message contains expressions like {{ row.data.name }}
- **THEN** expressions are evaluated in the message

#### Scenario: Confirm executes action
- **WHEN** user confirms in the dialog
- **THEN** the action proceeds

#### Scenario: Cancel prevents action
- **WHEN** user cancels in the dialog
- **THEN** the action is not executed

#### Scenario: Custom confirm/cancel labels
- **WHEN** confirm specifies confirmLabel and cancelLabel
- **THEN** the dialog buttons use those labels

### Requirement: Delete Action

The system SHALL provide a built-in delete action for table rows.

#### Scenario: Delete row from table
- **WHEN** a table row action with type="delete" is triggered
- **THEN** the document is deleted from the table's data source

#### Scenario: Delete with confirmation
- **WHEN** a delete action is triggered
- **THEN** a confirmation dialog appears by default

#### Scenario: Delete refreshes table
- **WHEN** a document is successfully deleted
- **THEN** the table automatically refreshes

#### Scenario: Delete error handling
- **WHEN** a delete operation fails
- **THEN** an error toast is displayed and the row remains
