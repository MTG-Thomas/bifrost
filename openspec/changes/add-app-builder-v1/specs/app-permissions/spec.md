# Capability: App Permissions

Three-level permission system for apps, pages, and components.

## ADDED Requirements

### Requirement: App-Level Permissions

The system SHALL provide app-level access control determining who can access an application.

#### Scenario: Public app access
- **WHEN** an application specifies permissions.access.public=true
- **THEN** any authenticated user in the organization can access the app

#### Scenario: Role-restricted app access
- **WHEN** an application specifies permissions.access.roles=["admin", "manager"]
- **THEN** only users with those roles can access the app

#### Scenario: User-specific app access
- **WHEN** an application specifies permissions.access.users=[user_id_1, user_id_2]
- **THEN** only those specific users can access the app

#### Scenario: Combined role and user access
- **WHEN** an application specifies both roles and users in access
- **THEN** users matching either criteria can access the app

#### Scenario: Access denied response
- **WHEN** a user without permission attempts to access an app
- **THEN** they receive an access denied error or redirect

### Requirement: Page-Level Permissions

The system SHALL provide page-level permissions for restricting access to specific pages within an app.

#### Scenario: Page role restriction
- **WHEN** a page specifies permissions.pages["/settings"].roles=["admin"]
- **THEN** only admins can access the settings page

#### Scenario: Page user restriction
- **WHEN** a page specifies permissions.pages["/admin"].users=[admin_id]
- **THEN** only that specific user can access the admin page

#### Scenario: Hidden page in navigation
- **WHEN** a page specifies permissions.pages["/debug"].hidden=true
- **THEN** the page is hidden from navigation but accessible by URL

#### Scenario: Page inherits app permissions
- **WHEN** a page has no explicit permissions
- **THEN** it inherits the app-level access permissions

#### Scenario: Page restricts beyond app level
- **WHEN** a page specifies stricter permissions than the app
- **THEN** the page permission is enforced (cannot grant more than app allows)

#### Scenario: Permission denied redirect
- **WHEN** a user navigates to a page they cannot access
- **THEN** they are redirected to a permission denied page or previous page

### Requirement: Component-Level Visibility

The system SHALL evaluate visibility expressions on components for fine-grained UI control.

#### Scenario: Role-based component visibility
- **WHEN** a component specifies visible="{{ user.role == 'admin' }}"
- **THEN** the component only renders for admin users

#### Scenario: Multi-role component visibility
- **WHEN** a component specifies visible="{{ user.roles | includes: 'manager' }}"
- **THEN** the component renders for users with the manager role

#### Scenario: Owner-based component visibility
- **WHEN** a component specifies visible="{{ user.id == variables.record.created_by }}"
- **THEN** the component only renders for the record creator

#### Scenario: Organization-based visibility
- **WHEN** a component specifies visible="{{ organization.id == 'specific-org-id' }}"
- **THEN** the component only renders for users in that organization

#### Scenario: Variable-based visibility
- **WHEN** a component specifies visible="{{ variables.selectedItem != null }}"
- **THEN** the component only renders when the variable has a value

#### Scenario: Hidden component takes no space
- **WHEN** a component's visibility expression evaluates to false
- **THEN** the component is not rendered and takes no layout space

### Requirement: Navigation Filtering

The system SHALL filter navigation items based on user permissions.

#### Scenario: Hide inaccessible pages from nav
- **WHEN** a user cannot access a page due to permissions
- **THEN** the nav item for that page is hidden

#### Scenario: Show accessible pages in nav
- **WHEN** a user can access a page
- **THEN** the nav item for that page is visible

#### Scenario: Hidden pages not in nav
- **WHEN** a page is marked as hidden in permissions
- **THEN** it never appears in navigation regardless of access

#### Scenario: Navigation updates on permission change
- **WHEN** user permissions change during session
- **THEN** navigation reflects the updated permissions

### Requirement: Permission Evaluation Context

The system SHALL provide a consistent context for evaluating permission expressions.

#### Scenario: User context available
- **WHEN** evaluating permission expressions
- **THEN** user.id, user.email, user.name, user.role, user.roles are available

#### Scenario: Organization context available
- **WHEN** evaluating permission expressions
- **THEN** organization.id and organization.name are available

#### Scenario: Route params available
- **WHEN** evaluating permission expressions on a page
- **THEN** params from the current route are available

#### Scenario: Page variables available
- **WHEN** evaluating permission expressions
- **THEN** current page variables are available

#### Scenario: Expression evaluation is secure
- **WHEN** evaluating permission expressions
- **THEN** expressions cannot access sensitive data or execute arbitrary code
