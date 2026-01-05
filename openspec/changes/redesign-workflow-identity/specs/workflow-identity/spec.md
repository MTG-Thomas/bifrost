## ADDED Requirements

### Requirement: Database Code Storage
The system SHALL store workflow Python code in the `workflows.code` database column.

#### Scenario: Workflow code persisted on editor save
- **WHEN** a Python file with `@workflow` decorator is saved via editor
- **THEN** the complete file content is stored in `workflows.code`
- **AND** a SHA256 hash is stored in `workflows.code_hash`
- **AND** no file is written to S3

#### Scenario: Code persists after file deletion
- **WHEN** a workflow is deleted via editor
- **THEN** the workflow record is soft-deleted in database
- **AND** the `workflows.code` column retains the code for recovery
- **AND** forms referencing the workflow continue to function

### Requirement: Database-Based Execution
The system SHALL execute workflows from the `workflows.code` column using exec() with namespace injection.

#### Scenario: Workflow execution from database
- **WHEN** a workflow is triggered for execution
- **THEN** the system loads code from `workflows.code`
- **AND** compiles using `compile(code, filename=implicit_path, mode='exec')`
- **AND** executes using `exec()` with injected namespace
- **AND** namespace includes `__file__`, `__package__`, `__name__`, `__builtins__`

#### Scenario: Import resolution from regular files
- **WHEN** workflow code contains `from modules.helpers import x`
- **AND** `modules/helpers.py` exists as a regular file in S3
- **THEN** the import succeeds because sys.path includes the synced workspace
- **AND** the imported module executes normally

#### Scenario: Stack traces show meaningful paths
- **WHEN** a workflow raises an exception
- **THEN** the stack trace shows the implicit file path (e.g., `workflows/process.py:42`)
- **AND** developers can understand where the error occurred

### Requirement: No IDs in User Files
The system SHALL NOT inject or require UUIDs in workflow decorator parameters.

#### Scenario: New workflow without ID parameter
- **WHEN** a new Python file is saved with `@workflow(name="My Workflow")`
- **AND** no `id` parameter is present
- **THEN** the system generates a UUID internally
- **AND** the file content stored in `workflows.code` does NOT include an ID

#### Scenario: Existing ID parameters ignored
- **WHEN** a file contains `@workflow(id="some-uuid", name="...")`
- **THEN** the `id` parameter is ignored during parsing
- **AND** identity is determined by database record, not file content

### Requirement: Unified File System View
The system SHALL provide a unified view of platform entities and regular files through workspace_files.

#### Scenario: Workflow appears in file tree
- **WHEN** a workflow exists in the database
- **THEN** a workspace_files entry exists with matching path
- **AND** `entity_type` is set to 'workflow'
- **AND** `entity_id` references the workflow record

#### Scenario: Editor reads workflow content
- **WHEN** a file read request is made for a path with `entity_type='workflow'`
- **THEN** the system fetches content from `workflows.code`
- **AND** returns it as if reading a regular file

#### Scenario: Editor reads regular file content
- **WHEN** a file read request is made for a path with `entity_type=NULL`
- **THEN** the system fetches content from S3
- **AND** returns it as a regular file

### Requirement: Content Type Detection on Write
The system SHALL detect content type and route writes to the correct backend.

#### Scenario: Python file with workflow decorator
- **WHEN** a `.py` file is saved via editor
- **AND** it contains `@workflow`, `@data_provider`, or `@tool` decorator
- **THEN** the content is upserted to the `workflows` table
- **AND** workspace_files is updated with `entity_type='workflow'`

#### Scenario: Regular Python file
- **WHEN** a `.py` file is saved via editor
- **AND** it contains no recognized decorators
- **THEN** the content is written to S3
- **AND** workspace_files is updated with `entity_type=NULL`

#### Scenario: File transitions from regular to workflow
- **WHEN** a regular Python file is edited to add a `@workflow` decorator
- **THEN** the file is deleted from S3
- **AND** a workflow record is created in the database
- **AND** workspace_files is updated with `entity_type='workflow'`

#### Scenario: File transitions from workflow to regular
- **WHEN** a workflow file is edited to remove all decorators
- **THEN** the workflow record is soft-deleted
- **AND** the content is written to S3
- **AND** workspace_files is updated with `entity_type=NULL`
