# Capability: Data Tables

Persistent table storage for application data with flexible JSONB documents.

## ADDED Requirements

### Requirement: Table Management

The system SHALL provide table management operations allowing users to create, list, retrieve, update, and delete tables within their organization scope.

#### Scenario: Create table with name and optional schema
- **WHEN** a user creates a table with name "customers" and optional schema hints
- **THEN** the table is created with a unique ID, the user's organization scope, and timestamps

#### Scenario: Create global table (platform scope)
- **WHEN** a platform administrator creates a table with no organization context
- **THEN** the table is created with `organization_id = NULL` indicating global scope

#### Scenario: List tables in organization
- **WHEN** a user lists tables for their organization
- **THEN** all tables scoped to that organization are returned, plus any global tables

#### Scenario: Get table by name
- **WHEN** a user retrieves a table by name within their organization
- **THEN** the table metadata is returned including schema, timestamps, and ID

#### Scenario: Delete table cascades to documents
- **WHEN** a user deletes a table
- **THEN** the table and all associated documents are permanently removed

#### Scenario: Table names unique within organization
- **WHEN** a user attempts to create a table with a name that already exists in their organization
- **THEN** the operation fails with a conflict error

### Requirement: Document Storage

The system SHALL provide document storage operations allowing users to create, read, update, and delete documents within tables using flexible JSONB data.

#### Scenario: Insert document with arbitrary JSON data
- **WHEN** a user inserts a document into a table with JSON data
- **THEN** the document is stored with a unique ID, table reference, and timestamps

#### Scenario: Get document by ID
- **WHEN** a user retrieves a document by its ID from a table
- **THEN** the complete document data is returned with metadata

#### Scenario: Update document with partial data
- **WHEN** a user updates a document with partial JSON data
- **THEN** the specified fields are merged with existing data, preserving unchanged fields

#### Scenario: Delete document by ID
- **WHEN** a user deletes a document by its ID
- **THEN** the document is removed and subsequent queries do not return it

#### Scenario: Document inherits table organization scope
- **WHEN** a document is inserted into a table
- **THEN** the document inherits the table's organization scope for access control

### Requirement: Document Querying

The system SHALL provide query operations allowing users to filter, sort, and paginate documents using JSONB field comparisons.

#### Scenario: Query with equality filter
- **WHEN** a user queries documents where `status = "active"`
- **THEN** only documents with matching field values are returned

#### Scenario: Query with comparison operators
- **WHEN** a user queries documents where `created_at > "2024-01-01"` using gt/gte/lt/lte operators
- **THEN** documents matching the comparison are returned

#### Scenario: Query with pattern matching
- **WHEN** a user queries documents where `name` contains "acme" using like/ilike/contains/starts_with/ends_with
- **THEN** documents with matching patterns are returned (case-sensitive or insensitive as specified)

#### Scenario: Query with IN operator
- **WHEN** a user queries documents where `status` is in ["pending", "approved"]
- **THEN** documents matching any of the specified values are returned

#### Scenario: Query with NULL check
- **WHEN** a user queries documents where `deleted_at` is null
- **THEN** documents with null or missing values for that field are returned

#### Scenario: Query with pagination
- **WHEN** a user queries documents with limit=10 and offset=20
- **THEN** 10 documents starting from position 20 are returned with total count

#### Scenario: Query with ordering
- **WHEN** a user queries documents ordered by `created_at` descending
- **THEN** documents are returned sorted by the specified field and direction

#### Scenario: Count documents matching filter
- **WHEN** a user requests a count of documents matching a filter
- **THEN** the total count is returned without document data

### Requirement: SDK Tables Module

The system SHALL provide a `tables` SDK module allowing workflows to perform all table and document operations programmatically.

#### Scenario: Workflow creates table via SDK
- **WHEN** a workflow calls `tables.create_table("customers", schema)`
- **THEN** a table is created in the workflow's organization scope

#### Scenario: Workflow inserts document via SDK
- **WHEN** a workflow calls `tables.insert("customers", {"name": "Acme Corp"})`
- **THEN** the document is inserted and returned with its ID

#### Scenario: Workflow queries documents via SDK
- **WHEN** a workflow calls `tables.query("customers", where={"status": "active"})`
- **THEN** matching documents are returned as a list with pagination info

#### Scenario: Workflow updates document via SDK
- **WHEN** a workflow calls `tables.update("customers", doc_id, {"status": "inactive"})`
- **THEN** the document is updated with merged data

#### Scenario: Workflow deletes document via SDK
- **WHEN** a workflow calls `tables.delete_document("customers", doc_id)`
- **THEN** the document is removed and the operation returns success

#### Scenario: SDK operations respect organization scope
- **WHEN** a workflow executes table operations
- **THEN** all operations are automatically scoped to the workflow's organization context
