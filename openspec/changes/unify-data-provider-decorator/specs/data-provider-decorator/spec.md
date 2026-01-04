# Consolidated Executables Specification

## ADDED Requirements

### Requirement: Workflow table type discriminator field

The `workflows` table MUST have a `type` field to distinguish between workflow, tool, and data_provider types.

#### Scenario: Type field values

Given the `workflows` table
Then it has a `type` column with values: `workflow`, `tool`, `data_provider`
And the default value is `workflow`
And there is an index on the `type` column

#### Scenario: is_tool column replaced

Given workflows with `is_tool=true`
When the migration runs
Then they have `type='tool'`
And the `is_tool` column is dropped

---

### Requirement: Data providers stored in workflows table

All data providers MUST be stored in the `workflows` table with `type='data_provider'`.

#### Scenario: Data provider discovery

Given a Python file with `@data_provider` decorator
When the file is discovered
Then a row is created in `workflows` with `type='data_provider'`
And `cache_ttl_seconds` is populated from decorator

#### Scenario: Data provider upsert

Given an existing data provider in `workflows`
When the file is re-discovered
Then the existing row is updated (not duplicated)
And the unique constraint on `(file_path, function_name)` is used

---

### Requirement: ExecutableMetadata base class for all decorator types

The system MUST provide an `ExecutableMetadata` base class that `WorkflowMetadata` and `DataProviderMetadata` extend.

#### Scenario: WorkflowMetadata extends ExecutableMetadata

Given `ExecutableMetadata` defines fields: `id`, `name`, `description`, `category`, `tags`, `timeout_seconds`, `parameters`, `source_file_path`, `type`
When creating a `WorkflowMetadata` instance
Then it inherits all base fields
And adds workflow-specific fields: `execution_mode`, `schedule`, `endpoint_enabled`, etc.
And `type` is set to `workflow` or `tool`

#### Scenario: DataProviderMetadata extends ExecutableMetadata

Given `ExecutableMetadata` defines common fields
When creating a `DataProviderMetadata` instance
Then it inherits all base fields
And adds data-provider-specific field: `cache_ttl_seconds`
And `type` is set to `data_provider`
And `timeout_seconds` defaults to 300 (vs 1800 for workflows)

---

### Requirement: FormField FK points to workflows table

The `form_fields.data_provider_id` foreign key MUST reference the `workflows` table.

#### Scenario: FK constraint update

Given `form_fields.data_provider_id` currently references `data_providers.id`
When the migration runs
Then the FK is updated to reference `workflows.id`
And `ondelete='SET NULL'` is preserved

#### Scenario: Form field invokes data provider

Given a form field with `data_provider_id` referencing a workflow with `type='data_provider'`
When the form renders
Then the data provider is invoked correctly
And options are returned

---

### Requirement: Data providers executable via workflows execute endpoint

The `/api/workflows/execute` endpoint MUST handle `type='data_provider'` workflows. Access is superuser-only.

#### Scenario: Execute data provider via workflow endpoint

Given workflow with `type='data_provider'` and ID "abc-123"
And user is a platform admin (superuser)
When `POST /api/workflows/execute` with `workflow_id: "abc-123"`
Then the data provider is executed
And response includes `result_type = "data_provider"`
And result contains list of options

#### Scenario: Execute workflow via same endpoint

Given workflow with `type='workflow'` and ID "xyz-789"
When `POST /api/workflows/execute` with `workflow_id: "xyz-789"`
Then the workflow is executed normally
And response includes `result_type = "workflow"`

---

### Requirement: Data provider API filters by type

The `/api/data-providers` endpoints MUST query the `workflows` table with `type='data_provider'` filter.

#### Scenario: List data providers

Given multiple workflows with different types
When `GET /api/data-providers`
Then only workflows with `type='data_provider'` are returned

#### Scenario: Invoke data provider

Given workflow with `type='data_provider'` and ID "abc-123"
When `POST /api/data-providers/abc-123/invoke`
Then the data provider is executed
And options are returned

#### Scenario: Invoke non-data-provider fails

Given workflow with `type='workflow'` and ID "xyz-789"
When `POST /api/data-providers/xyz-789/invoke`
Then request fails with 404 error
And message indicates data provider not found

---

### Requirement: MCP execute_workflow handles all types

The MCP `execute_workflow` tool MUST handle all executable types based on the workflow's `type` field.

#### Scenario: Execute data provider via MCP

Given workflow with `type='data_provider'`
When calling `execute_workflow(workflow_id="abc-123")`
Then data provider is executed
And response includes `type: "data_provider"`
And response includes `options` list

#### Scenario: Execute workflow via MCP

Given workflow with `type='workflow'`
When calling `execute_workflow(workflow_id="xyz-789")`
Then workflow is executed
And response includes `type: "workflow"`
And response includes `result`

---

### Requirement: Workflows UI displays all types with filter toggles

The Workflows page MUST display all executable types with filter toggles.

#### Scenario: Filter toggles available

Given user navigates to Workflows page
Then filter toggles are visible: [All] [Workflows] [Tools] [Data Providers]
And "All" is selected by default

#### Scenario: Filter by type

Given filter "Data Providers" is selected
Then only workflows with `type='data_provider'` are displayed

#### Scenario: Data provider badge displayed

Given a workflow with `type='data_provider'` is displayed
Then it shows a "Data Provider" badge
And the badge has distinct styling (teal color, Database icon)

---

## MODIFIED Requirements

### Requirement: Workflow decorators set type field

The `@workflow`, `@tool`, and `@data_provider` decorators MUST set the appropriate `type` field.

#### Scenario: workflow decorator sets type

Given `@workflow` decorator
When applied to a function
Then metadata has `type='workflow'`

#### Scenario: tool decorator sets type

Given `@tool` decorator
When applied to a function
Then metadata has `type='tool'`

#### Scenario: data_provider decorator sets type

Given `@data_provider` decorator
When applied to a function
Then metadata has `type='data_provider'`

---

### Requirement: Data providers table dropped

The `data_providers` table MUST be dropped after migration.

#### Scenario: Table dropped

Given all data is migrated to `workflows` table
And all FK constraints are updated
When the drop migration runs
Then `data_providers` table is removed

---

## REMOVED Requirements

### Requirement: Separate data_providers table

The separate `data_providers` table is REMOVED and consolidated into `workflows`.

### Requirement: is_tool boolean field

The `is_tool` boolean field is REMOVED and replaced by `type` discriminator.
