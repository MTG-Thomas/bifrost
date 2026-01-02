# Capability: Applications

Application container with JSON definitions, draft/live versioning, and publish workflow.

## ADDED Requirements

### Requirement: Application Management

The system SHALL provide application management operations allowing users to create, list, retrieve, update, and delete applications within their organization scope.

#### Scenario: Create application with name and slug
- **WHEN** a user creates an application with name "Customer Portal" and slug "customer-portal"
- **THEN** the application is created with unique ID, empty draft definition, and version 0

#### Scenario: Application slugs unique within organization
- **WHEN** a user attempts to create an application with a slug that already exists in their organization
- **THEN** the operation fails with a conflict error

#### Scenario: Slug validation enforces URL-friendly format
- **WHEN** a user creates an application with slug containing invalid characters
- **THEN** the operation fails with a validation error indicating the required format (lowercase, hyphens only)

#### Scenario: List applications in organization
- **WHEN** a user lists applications for their organization
- **THEN** all applications scoped to that organization are returned with metadata

#### Scenario: Get application by slug
- **WHEN** a user retrieves an application by slug within their organization
- **THEN** the application metadata is returned including version info and timestamps

#### Scenario: Update application metadata
- **WHEN** a user updates an application's name, description, or icon
- **THEN** the metadata is updated without affecting the definition or versions

#### Scenario: Delete application
- **WHEN** a user deletes an application
- **THEN** the application and all version history are permanently removed

### Requirement: Application Definitions

The system SHALL store application definitions as JSON with support for draft and live versions enabling safe iteration.

#### Scenario: Get draft definition for editing
- **WHEN** a user requests the draft definition of an application
- **THEN** the current draft JSONB definition is returned for editing

#### Scenario: Save draft definition
- **WHEN** a user saves a draft definition with updated JSON
- **THEN** the draft definition is stored and draft version is incremented

#### Scenario: Get live definition for runtime
- **WHEN** a user or runtime requests the live definition of an application
- **THEN** the published JSONB definition is returned (or empty if never published)

#### Scenario: Draft and live definitions independent
- **WHEN** a user edits the draft definition
- **THEN** the live definition remains unchanged until explicitly published

### Requirement: Version Management

The system SHALL provide version management with publish and rollback operations, tracking the last 10 versions for recovery.

#### Scenario: Publish draft to live
- **WHEN** a user publishes an application
- **THEN** the draft definition becomes the new live definition, live version increments, and published_at is updated

#### Scenario: Version history tracks previous definitions
- **WHEN** a user publishes an application
- **THEN** the previous live definition is added to version history with timestamp and version number

#### Scenario: Version history limited to 10 entries
- **WHEN** version history exceeds 10 entries
- **THEN** the oldest entry is removed to maintain the limit

#### Scenario: View version history
- **WHEN** a user requests version history for an application
- **THEN** the last 10 versions are returned with definitions, timestamps, and version numbers

#### Scenario: Rollback to previous version
- **WHEN** a user rolls back to a specific version number
- **THEN** the selected version's definition becomes the new live definition with incremented version

#### Scenario: Rollback fails for non-existent version
- **WHEN** a user attempts to rollback to a version not in history
- **THEN** the operation fails with a not found error

### Requirement: Application Definition Schema

The system SHALL validate application definitions against a JSON schema ensuring required fields and valid structure.

#### Scenario: Definition includes version field
- **WHEN** an application definition is stored
- **THEN** it contains a `version` field indicating the schema version for future migrations

#### Scenario: Definition includes settings with default page
- **WHEN** an application definition is stored
- **THEN** it contains `settings.defaultPage` specifying the initial route

#### Scenario: Definition includes navigation configuration
- **WHEN** an application definition includes navigation
- **THEN** it contains navbar and/or sidebar configurations with items

#### Scenario: Definition includes pages array
- **WHEN** an application definition is stored
- **THEN** it contains a `pages` array with route, title, and layout for each page

#### Scenario: Definition includes entity references
- **WHEN** an application definition references tables, workflows, or forms
- **THEN** it contains arrays mapping entity IDs to aliases for use in the app
