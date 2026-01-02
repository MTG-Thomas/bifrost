# Design: App Builder v1

## Context

Bifrost currently provides forms and workflows for MSP automation. This change extends the platform to a low-code app builder, enabling users to build complete CRUD applications without writing code. The architecture builds on existing infrastructure to minimize duplication while providing maximum flexibility.

## Goals

- Enable building CRUD applications without code
- Maintain backward compatibility with existing forms and workflows
- Support multi-tenant global apps with organization-scoped data
- Provide versioning and draft/publish workflow for safe iteration
- Make data operations available to workflows via SDK

## Non-Goals

- Offline/PWA support (future consideration)
- Real-time collaboration on app editing (future)
- App templates or marketplace (post-MVP)
- Custom component development by users (future)

## Design Principles

1. **Extend, don't replace** - Build on existing form builder, components, and workflows
2. **JSON-portable** - App definitions stored as JSON for dual-write and versioning
3. **SDK-first** - Data operations available to workflows via `tables` SDK module
4. **Scope consistency** - Follow existing `organization_id: UUID | None` pattern
5. **Unified components** - Form fields and app components share the same system
6. **Multi-tenant capable** - Global apps with org-scoped data

## Decisions

### Decision 1: Unified Component System

**Choice:** Forms and apps share the same component system.

**Rationale:**
- A Form becomes a named collection of field components with a submit workflow
- Reduces duplication between form builder and app builder
- Enables form embedding in apps with consistent behavior
- Simplifies maintenance and feature parity

**Alternatives Considered:**
- Separate component systems (more flexibility, but significant duplication)
- Component inheritance hierarchy (complex, fragile across versions)

### Decision 2: JSON-Based App Definitions

**Choice:** Store app definitions as JSONB in PostgreSQL.

**Rationale:**
- Portable format for import/export
- Easy versioning (store previous definitions as JSON array)
- Enables dual-write (draft + live) without schema complexity
- GIN indexing for efficient queries if needed

**Alternatives Considered:**
- GraphQL schema (more type-safe, but adds complexity)
- Custom DSL (proprietary, harder to version and debug)
- Normalized relational model (schema explosion, complex migrations)

### Decision 3: Three-Level Permissions

**Choice:** App-level, page-level, and component-level (via visibility expressions).

**Rationale:**
- App-level: Coarse access control (who can open the app)
- Page-level: Route-based restrictions (admin pages)
- Component-level: Fine-grained UI control via `visible` expressions
- No database schema changes needed for new permission rules

**Alternatives Considered:**
- RLS policies only (too coarse-grained for UI elements)
- ACL table per resource (excessive complexity)
- Single-level permissions (insufficient granularity)

### Decision 4: Draft/Live Versioning

**Choice:** Two JSONB columns (draft_definition, live_definition) with version history array.

**Rationale:**
- Clear separation between editor state and published state
- Instant rollback to previous versions
- No complex branching model needed
- History limited to last 10 versions (configurable)

**Alternatives Considered:**
- Git-based versioning (overkill for app definitions)
- Separate versions table (more complex queries)
- Single definition with feature flags (confusing for users)

### Decision 5: JSONB-Only Data Storage

**Choice:** Use JSONB for document data, no partition/sort keys.

**Rationale:**
- Sufficient for automation platform use cases (not replacing DynamoDB)
- Simpler mental model than NoSQL key design
- GIN indexing provides adequate query performance
- Schema hints optional for validation/UI purposes

**Alternatives Considered:**
- Add partition_key/sort_key columns (unnecessary complexity)
- Use separate storage backend (operational overhead)

### Decision 6: SDK-First Data Access

**Choice:** All table operations available via `tables` SDK module.

**Rationale:**
- Workflows can programmatically manage data
- Consistent API for both HTTP and SDK access
- Enables automation scenarios (import data, batch updates)
- Repository pattern keeps business logic separate from routes

### Decision 7: Expression Syntax

**Choice:** `{{ variable.path }}` syntax with filter support.

**Rationale:**
- Familiar syntax (similar to Jinja2, Liquid, Handlebars)
- Easy to parse and evaluate at runtime
- Supports nested property access and filters
- Clear visual distinction from static content

**Expression Examples:**
```
{{ user.role }}                    # Simple property
{{ params.id }}                    # Route parameters
{{ survey.data.customer_name }}    # Nested access
{{ tables.expenses | count }}      # With filter
{{ user.role == 'admin' }}         # Boolean expression
```

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| JSON schema versioning complexity | High | Start with version 1, define migration path before v2 |
| Expression evaluator performance | Medium | Profile with realistic data, cache compiled expressions |
| Component palette grows unbounded | Medium | Define clear component categories, promote patterns to core |
| Editor state management complexity | Medium | Use Zustand with clear separation of concerns |
| Mobile responsiveness issues | Medium | Design mobile-first, test on real devices |

## Migration Path

1. **Phase 1-2** (Backend): Implement data storage and app container - backward compatible
2. **Phase 3-8** (Runtime): Build layout, components, routing, actions - internal development
3. **Phase 9** (Editor): Visual builder - staff-only initially
4. **Phase 10** (Embedding): Public release with documentation

## Open Questions

1. **Component schema versioning**: Should we version component definitions separately from app definitions?
2. **Real-time updates**: Start with polling, evaluate WebSocket need based on usage patterns
3. **Expression sandboxing**: How to prevent infinite loops or expensive computations in expressions?
