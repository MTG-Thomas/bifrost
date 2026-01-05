## ADDED Requirements

### Requirement: Entity Type Routing
The system SHALL route file operations based on workspace_files.entity_type.

#### Scenario: Read workflow entity
- **WHEN** a read request is made for a path
- **AND** workspace_files shows `entity_type='workflow'`
- **THEN** content is fetched from `workflows.code` by `entity_id`

#### Scenario: Read form entity
- **WHEN** a read request is made for a path
- **AND** workspace_files shows `entity_type='form'`
- **THEN** content is fetched from `forms.definition` by `entity_id`
- **AND** serialized as JSON

#### Scenario: Read app entity
- **WHEN** a read request is made for a path
- **AND** workspace_files shows `entity_type='app'`
- **THEN** content is fetched from `applications.definition` by `entity_id`
- **AND** serialized as JSON

#### Scenario: Read agent entity
- **WHEN** a read request is made for a path
- **AND** workspace_files shows `entity_type='agent'`
- **THEN** content is fetched from `agents.definition` by `entity_id`
- **AND** serialized as JSON

#### Scenario: Read regular file
- **WHEN** a read request is made for a path
- **AND** workspace_files shows `entity_type=NULL`
- **THEN** content is fetched from S3

### Requirement: Backfill on Workspace Sync
The system SHALL backfill missing code columns from existing files on workspace sync startup.

#### Scenario: Workflow missing code column
- **WHEN** workspace sync starts
- **AND** a workflow record has `code=NULL`
- **AND** the file exists in S3
- **THEN** the file content is read from S3
- **AND** stored in `workflows.code`
- **AND** `code_hash` is computed and stored

#### Scenario: All workflows have code
- **WHEN** workspace sync starts
- **AND** all workflow records have populated `code` columns
- **THEN** no backfill is performed
- **AND** sync continues normally

### Requirement: Content Hash Computation
The system SHALL compute and store content hashes for all files.

#### Scenario: Hash stored on workflow save
- **WHEN** a workflow is saved via editor
- **THEN** a SHA256 hash of the content is computed
- **AND** stored in `workflows.code_hash`

#### Scenario: Hash stored on regular file save
- **WHEN** a regular file is saved via editor
- **THEN** a SHA256 hash of the content is computed
- **AND** stored in `workspace_files.content_hash`

#### Scenario: Hash comparison for change detection
- **WHEN** a file save request is made
- **AND** the computed hash matches the stored hash
- **THEN** no write is performed (file unchanged)

### Requirement: Redundant Workflow References
Forms SHALL store redundant workflow identification for portability.

#### Scenario: Form saves workflow metadata
- **WHEN** a form is linked to a workflow
- **THEN** the form stores `workflow_id` (UUID)
- **AND** the form stores `workflow_path` (file path)
- **AND** the form stores `workflow_function_name` (Python function name)

#### Scenario: Git import resolves by path
- **WHEN** a form file is imported via git sync
- **AND** `workflow_id` does not match any local workflow
- **THEN** the system looks up by `(workflow_path, workflow_function_name)`
- **AND** if found, updates `workflow_id` to the local workflow's ID

#### Scenario: Reference resolution on form load
- **WHEN** a form is loaded
- **AND** `workflow_id` does not match any workflow
- **THEN** the system looks up by `(workflow_path, workflow_function_name)`
- **AND** if found, updates `workflow_id` to the matching workflow's ID
- **AND** if not found, returns error with helpful message

### Requirement: Git Sync Serialization
The system SHALL serialize database entities to files only for git operations.

#### Scenario: Git push serializes workflows
- **WHEN** a git push operation is initiated
- **THEN** each workflow is serialized to a `.py` file at its path
- **AND** the file contains the code from `workflows.code`
- **AND** no `id` parameter is included in decorators

#### Scenario: Git push serializes forms
- **WHEN** a git push operation is initiated
- **THEN** each form is serialized to a `.form.json` file at its path
- **AND** the file contains the JSON from `forms.definition`
- **AND** includes `workflow_path` and `workflow_function_name` for portability

#### Scenario: Git pull parses workflows
- **WHEN** a git pull operation completes
- **AND** a `.py` file with workflow decorators exists
- **THEN** the file is parsed and upserted to `workflows` table
- **AND** workspace_files entry is created with `entity_type='workflow'`

#### Scenario: Git pull parses forms
- **WHEN** a git pull operation completes
- **AND** a `.form.json` file exists
- **THEN** the file is parsed and upserted to `forms` table
- **AND** workspace_files entry is created with `entity_type='form'`
- **AND** `workflow_id` is resolved from `workflow_path` and `workflow_function_name`
