# Capability: App Embedding

Embedding support for apps with token auth and multi-tenant global apps.

## ADDED Requirements

### Requirement: Standalone App Route

The system SHALL provide a standalone route for rendering published apps.

#### Scenario: Access app by slug
- **WHEN** a user navigates to /apps/{slug}
- **THEN** the published app renders with its live definition

#### Scenario: App not found handling
- **WHEN** a user navigates to /apps/{invalid-slug}
- **THEN** a 404 not found page is displayed

#### Scenario: Unpublished app handling
- **WHEN** a user navigates to an app with no live definition
- **THEN** an appropriate message indicates the app is not yet published

#### Scenario: App respects user permissions
- **WHEN** a user without access navigates to an app
- **THEN** access is denied based on app permissions

#### Scenario: App routes work with browser navigation
- **WHEN** a user navigates within an app and uses browser back/forward
- **THEN** navigation works correctly

### Requirement: Embed Route

The system SHALL provide an embed route for iframe integration.

#### Scenario: Embed route renders app
- **WHEN** /embed/{slug} is loaded in an iframe
- **THEN** the app renders without browser chrome

#### Scenario: Embed route minimal UI
- **WHEN** app is rendered via embed route
- **THEN** no platform navigation or headers are shown

#### Scenario: Embed route with token auth
- **WHEN** /embed/{slug}?token={jwt} is accessed
- **THEN** the token is validated and user context is established

#### Scenario: Embed route supports query params
- **WHEN** /embed/{slug}?page=/surveys&param=value is accessed
- **THEN** the app opens to the specified page with params

### Requirement: Token Authentication

The system SHALL provide token-based authentication for embedded apps.

#### Scenario: Generate embed token
- **WHEN** an embed token is requested for a user and app
- **THEN** a JWT token is generated with user context and expiration

#### Scenario: Token includes user context
- **WHEN** a token is generated
- **THEN** it contains user ID, email, role, and organization

#### Scenario: Token validation
- **WHEN** an embed request includes a token
- **THEN** the token signature and expiration are validated

#### Scenario: Invalid token rejection
- **WHEN** an invalid or expired token is provided
- **THEN** access is denied with appropriate error

#### Scenario: Token scope to specific app
- **WHEN** a token is generated for a specific app
- **THEN** it cannot be used to access other apps

### Requirement: Theme Customization

The system SHALL support theme customization for embedded apps.

#### Scenario: Custom primary color
- **WHEN** an embed specifies theme.primaryColor
- **THEN** the app uses that color for primary UI elements

#### Scenario: Custom logo
- **WHEN** an embed specifies theme.logo
- **THEN** the app displays that logo in navigation

#### Scenario: Theme passed via URL params
- **WHEN** /embed/{slug}?primaryColor=%23007bff is accessed
- **THEN** the theme is applied from URL parameters

#### Scenario: Theme passed via JavaScript SDK
- **WHEN** Bifrost.mount() is called with theme options
- **THEN** the theme is applied to the embedded app

### Requirement: Multi-Tenant Global Apps

The system SHALL support global apps that serve multiple organizations with scoped data.

#### Scenario: Global app definition
- **WHEN** an app has organization_id=NULL
- **THEN** it is a global app accessible by multiple organizations

#### Scenario: Global app data scoping
- **WHEN** a user from Org A accesses a global app
- **THEN** table queries are automatically scoped to Org A's data

#### Scenario: Global app different org data
- **WHEN** a user from Org B accesses the same global app
- **THEN** table queries return Org B's data (not Org A's)

#### Scenario: Global app shared definition
- **WHEN** a global app is updated
- **THEN** all organizations see the same app definition

#### Scenario: Org-specific app overrides global
- **WHEN** an organization creates their own app with the same slug
- **THEN** their users see the org-specific app instead of global

### Requirement: JavaScript SDK Embedding

The system SHALL provide a JavaScript SDK for programmatic embedding.

#### Scenario: Mount app to DOM element
- **WHEN** Bifrost.mount("#container", { app: "crm", token: "..." }) is called
- **THEN** the app is rendered into the specified DOM element

#### Scenario: SDK passes configuration
- **WHEN** mount is called with options
- **THEN** the app receives theme, initial page, and other configuration

#### Scenario: SDK handles authentication
- **WHEN** mount is called with a token
- **THEN** the SDK sets up authentication for API calls

#### Scenario: SDK unmount cleanup
- **WHEN** Bifrost.unmount("#container") is called
- **THEN** the app is cleanly removed and resources released

#### Scenario: SDK event communication
- **WHEN** the embedded app triggers events
- **THEN** the parent page can listen for and handle them
