# Authorization Check Consolidation

## Status: Planning

## Problem

Access control is inconsistently implemented across forms, apps, and agents:

| Entity | List Endpoint | Get/Execute Endpoint | Location |
|--------|--------------|---------------------|----------|
| **Forms** | SQL subquery filtering | `_check_form_access()` inline | `forms.py` |
| **Agents** | Python loop filtering | `_check_agent_access()` | `chat.py` |
| **Apps** | No access filtering | No access check | `applications.py` |

**Immediate Bug:** Platform admins cannot open/preview apps because `get_by_slug()` uses org filtering that doesn't account for admin access.

**Unused Code:** `AuthorizationService` exists at `api/src/services/authorization.py` but is not used by any router.

## Plain-English Access Rules

### Forms
- **Platform Admin**: Can access any form
- **Org User**: Can access forms in their org OR global, where:
  - `authenticated` → any authenticated user in scope
  - `role_based` → user must have a role assigned via `form_roles`

### Apps
- **Platform Admin**: Can access any app (list, get, edit, preview)
- **Org User**: Can access apps in their org OR global, where:
  - `authenticated` → any authenticated user in scope
  - `role_based` → user must have a role assigned via `app_roles`
- **Note**: Org users cannot preview drafts (only published/live versions)

### Agents
- **Platform Admin**: Can see all agents, use any agent in chat
- **Org User**: Can see/use agents in scope where:
  - `authenticated` → any authenticated user in scope
  - `role_based` → user must have a role assigned via `agent_roles`
- **Note**: Users don't GET agents directly - they execute via chat. List is for chat UI.

## Unified Access Model

All three entities share:
1. `organization_id` - NULL for global, UUID for org-scoped
2. `access_level` - "authenticated" or "role_based"
3. `*_roles` junction table - links entity to roles

```
can_access(entity, user):
  1. Platform admin? → YES
  2. Entity org != user org AND entity not global? → NO
  3. access_level == "authenticated"? → YES
  4. access_level == "role_based"? → user has matching role
  5. Otherwise → NO
```

## Implementation Tasks

### Phase 1: Extend AuthorizationService (non-breaking)

- [ ] **1.1** Add imports for Application, Agent, AppRole, AgentRole to `authorization.py`
- [ ] **1.2** Add `_check_role_access(entity_id, entity_type)` helper method
- [ ] **1.3** Add `can_access_entity(entity, entity_type)` unified method
- [ ] **1.4** Add convenience wrappers: `can_access_form()`, `can_access_app()`, `can_access_agent()`
- [ ] **1.5** Add unit tests for new AuthorizationService methods

### Phase 2: Fix Applications Router (fixes the bug)

- [ ] **2.1** Update `get_application_or_404()` to use `AuthorizationService.can_access_app()`
- [ ] **2.2** Update `get_application_by_id_or_404()` similarly
- [ ] **2.3** Remove org filtering from app fetch (access check handles it)
- [ ] **2.4** Verify E2E tests pass: `./test.sh --e2e tests/e2e/api/test_applications.py`

### Phase 3: Migrate Forms Router (cleanup)

- [ ] **3.1** Replace `_check_form_access()` calls with `AuthorizationService.can_access_form()`
- [ ] **3.2** Delete the inline `_check_form_access()` function
- [ ] **3.3** Verify forms tests pass

### Phase 4: Migrate Chat/Agents (cleanup)

- [ ] **4.1** Replace `_check_agent_access()` in chat.py with `AuthorizationService.can_access_agent()`
- [ ] **4.2** Delete the inline `_check_agent_access()` function
- [ ] **4.3** Update agents.py list endpoint to use AuthorizationService for filtering (optional - current Python filtering works)
- [ ] **4.4** Verify chat/agent tests pass

### Phase 5: Cleanup

- [ ] **5.1** Remove any now-unused imports in routers
- [ ] **5.2** Run full test suite: `./test.sh`
- [ ] **5.3** Run type checks: `cd api && pyright`
- [ ] **5.4** Run linting: `cd api && ruff check .`

## Files to Modify

| File | Changes |
|------|---------|
| `api/src/services/authorization.py` | Add app/agent access methods |
| `api/src/routers/applications.py` | Use AuthorizationService |
| `api/src/routers/forms.py` | Migrate to AuthorizationService |
| `api/src/routers/chat.py` | Migrate to AuthorizationService |
| `api/src/routers/agents.py` | Optional: use AuthorizationService for list filtering |

## Code Changes

### AuthorizationService additions

```python
from src.models.orm.applications import Application
from src.models.orm.app_roles import AppRole
from src.models.orm.agents import Agent
from src.models.orm.agent_roles import AgentRole

class AuthorizationService:
    # ... existing code ...

    async def can_access_entity(
        self,
        entity: Form | Application | Agent,
        entity_type: Literal["form", "app", "agent"],
    ) -> bool:
        """Unified access check for forms, apps, and agents."""
        # Platform admins can access anything
        if self.context.is_platform_admin:
            return True

        # Check org scoping
        entity_org = getattr(entity, 'organization_id', None)
        if entity_org is not None and entity_org != self.context.org_id:
            return False

        # Check access level
        access_level = getattr(entity, 'access_level', 'authenticated') or 'authenticated'

        # Handle enum vs string
        if hasattr(access_level, 'value'):
            access_level = access_level.value

        if access_level == "authenticated":
            return True

        if access_level == "role_based":
            return await self._check_role_access(entity.id, entity_type)

        return False

    async def _check_role_access(
        self,
        entity_id: UUID,
        entity_type: Literal["form", "app", "agent"],
    ) -> bool:
        """Check if user has a role granting access to this entity."""
        user_roles = await self.get_user_role_ids()
        if not user_roles:
            return False

        # Map entity type to role table and column
        from src.models.orm.app_roles import AppRole
        from src.models.orm.agent_roles import AgentRole
        from src.models.orm.forms import FormRole

        config = {
            "form": (FormRole, FormRole.form_id),
            "app": (AppRole, AppRole.app_id),
            "agent": (AgentRole, AgentRole.agent_id),
        }
        role_table, id_column = config[entity_type]

        query = select(role_table.role_id).where(id_column == entity_id)
        result = await self.db.execute(query)
        entity_roles = [str(r) for r in result.scalars().all()]

        return any(role in entity_roles for role in user_roles)

    # Convenience wrappers
    async def can_access_form(self, form: Form) -> bool:
        return await self.can_access_entity(form, "form")

    async def can_access_app(self, app: Application) -> bool:
        return await self.can_access_entity(app, "app")

    async def can_access_agent(self, agent: Agent) -> bool:
        return await self.can_access_entity(agent, "agent")
```

### applications.py update

```python
from src.services.authorization import AuthorizationService

async def get_application_or_404(
    ctx: Context,
    slug: str,
    scope: str | None = None,
) -> Application:
    """Get application by slug with access control."""
    # Fetch without org filter - access check handles scoping
    query = select(Application).where(Application.slug == slug)
    result = await ctx.db.execute(query)
    app = result.scalar_one_or_none()

    if not app:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{slug}' not found",
        )

    auth = AuthorizationService(ctx.db, ctx)
    if not await auth.can_access_app(app):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied to this application",
        )

    return app
```

## Existing Tests

- `api/tests/e2e/api/test_applications.py` - Has `TestApplicationAccess` class
- No unit tests for `AuthorizationService` currently
- Forms and agents have various E2E tests

The changes should be largely transparent as:
1. We're fixing access to work correctly (platform admins can now access apps)
2. Existing tests check for 200 or 403 responses flexibly
3. The logic is being consolidated, not changed

## Verification

```bash
# Run all tests
./test.sh

# Run E2E tests specifically
./test.sh --e2e

# Run application-specific E2E tests
./test.sh --e2e tests/e2e/api/test_applications.py

# Type check
cd api && pyright

# Lint
cd api && ruff check .
```

## Manual Verification

1. Log in as platform admin
2. Create an app under an organization
3. Verify: list shows the app ✓
4. Verify: can open/preview the app (was broken, now fixed)
5. Verify: can edit the app

6. Log in as org user
7. Verify: can see `authenticated` apps in their org + global
8. Verify: can see `role_based` apps only if they have the role
9. Verify: cannot see apps from other orgs
