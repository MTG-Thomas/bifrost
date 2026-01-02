# Capability: App Routing

Navigation system with app shell, routing, and page context.

## ADDED Requirements

### Requirement: App Shell

The system SHALL provide an App Shell component with configurable navbar, sidebar, and main content area.

#### Scenario: App shell with navbar
- **WHEN** an application definition includes navigation.navbar
- **THEN** a top navigation bar renders with title, logo, and nav items

#### Scenario: App shell with sidebar
- **WHEN** an application definition includes navigation.sidebar
- **THEN** a side navigation renders with sections and items

#### Scenario: Sidebar collapsible
- **WHEN** sidebar specifies collapsible=true
- **THEN** the sidebar can be collapsed/expanded by the user

#### Scenario: Sidebar default collapsed
- **WHEN** sidebar specifies defaultCollapsed=true
- **THEN** the sidebar starts in collapsed state

#### Scenario: Nav item links to page
- **WHEN** a nav item specifies page property
- **THEN** clicking navigates to the internal page route

#### Scenario: Nav item links to external URL
- **WHEN** a nav item specifies url property
- **THEN** clicking opens the external URL

#### Scenario: Nav item with icon
- **WHEN** a nav item specifies icon property
- **THEN** the icon is displayed alongside the label

#### Scenario: User menu in navbar
- **WHEN** navbar specifies userMenu=true
- **THEN** a user dropdown menu appears with profile and logout options

#### Scenario: Main content area renders current page
- **WHEN** the app shell is active
- **THEN** the main content area renders the matched page layout

### Requirement: Page Routing

The system SHALL integrate React Router for client-side routing based on page definitions.

#### Scenario: Routes generated from pages
- **WHEN** an application definition includes pages array
- **THEN** routes are generated for each page's route property

#### Scenario: Static routes match exactly
- **WHEN** a page defines route="/surveys"
- **THEN** navigation to /surveys renders that page

#### Scenario: Dynamic routes with parameters
- **WHEN** a page defines route="/surveys/:id"
- **THEN** navigation to /surveys/123 renders that page with params.id="123"

#### Scenario: Default page on app load
- **WHEN** a user opens an app without specific route
- **THEN** the app navigates to settings.defaultPage

#### Scenario: Unknown route handling
- **WHEN** a user navigates to an undefined route
- **THEN** a 404 or redirect to default page occurs

#### Scenario: Browser history integration
- **WHEN** a user navigates within the app
- **THEN** browser back/forward buttons work correctly

### Requirement: Page Context

The system SHALL provide a PageContext with route params, query params, user info, organization info, and page variables.

#### Scenario: Context includes route params
- **WHEN** a page is rendered at route /surveys/:id with /surveys/123
- **THEN** context.params.id equals "123"

#### Scenario: Context includes query params
- **WHEN** a page is loaded with URL ?status=pending&page=2
- **THEN** context.query.status equals "pending" and context.query.page equals "2"

#### Scenario: Context includes current user
- **WHEN** an authenticated user views a page
- **THEN** context.user includes id, email, name, and role

#### Scenario: Context includes organization
- **WHEN** a user is viewing within an organization
- **THEN** context.organization includes id and name

#### Scenario: Context includes page variables
- **WHEN** variables are set via actions or workflows
- **THEN** context.variables contains the current values

### Requirement: Navigation Functions

The system SHALL provide navigation functions for programmatic page transitions and state management.

#### Scenario: Navigate to page
- **WHEN** navigate("/surveys/123") is called
- **THEN** the app navigates to the specified page

#### Scenario: Navigate with params
- **WHEN** navigate("/surveys/:id", { id: "123" }) is called
- **THEN** the route is constructed and navigated to

#### Scenario: Set page variable
- **WHEN** setVariable("selectedItem", value) is called
- **THEN** the page variable is updated and components re-render

#### Scenario: Execute workflow from context
- **WHEN** executeWorkflow("generate-proposal", input) is called
- **THEN** the workflow executes with the provided input

#### Scenario: Refresh table data
- **WHEN** refreshTable("surveys") is called
- **THEN** the table component with that alias refetches its data

### Requirement: Page Guards

The system SHALL support page-level guards for conditional access and redirects.

#### Scenario: Guard condition check
- **WHEN** a page specifies guard.condition expression
- **THEN** the expression is evaluated before rendering

#### Scenario: Guard redirect on failure
- **WHEN** guard condition evaluates to false and redirect is specified
- **THEN** the user is redirected to the specified page

#### Scenario: Guard allows access on success
- **WHEN** guard condition evaluates to true
- **THEN** the page renders normally
