# Codebase Structure

**Analysis Date:** 2026-01-30

## Directory Layout

```
bifrost/
├── api/                         # Python FastAPI backend
│   ├── src/
│   │   ├── main.py              # FastAPI app entry point, lifespan hooks, global exception handlers
│   │   ├── config.py            # Settings (env vars, defaults)
│   │   ├── routers/             # HTTP endpoint handlers (40+ routers, one per feature)
│   │   ├── models/
│   │   │   ├── orm/             # SQLAlchemy ORM models (database schema)
│   │   │   └── contracts/       # Pydantic request/response models (API DTOs)
│   │   ├── repositories/        # Data access layer (OrgScopedRepository pattern)
│   │   ├── services/            # Business logic (execution, GitHub sync, LLM, MCP, etc.)
│   │   ├── core/                # Infrastructure (auth, database, cache, pubsub, security)
│   │   ├── jobs/
│   │   │   ├── consumers/       # RabbitMQ message processors (workflow execution, package install)
│   │   │   ├── schedulers/      # APScheduler background tasks (cron, cleanup, OAuth refresh)
│   │   │   └── rabbitmq.py      # RabbitMQ connection and base consumer
│   │   ├── worker/              # Background worker service (main.py entry point)
│   │   └── scheduler/           # Background scheduler service (main.py entry point)
│   ├── alembic/                 # Database migrations (Alembic)
│   ├── tests/                   # Unit and integration tests
│   │   ├── unit/                # Isolated tests (mocked dependencies)
│   │   ├── integration/         # E2E tests (real infrastructure via Docker)
│   │   └── fixtures/            # Test data factories, auth helpers
│   └── requirements.txt         # Python dependencies
│
├── client/                      # TypeScript React frontend
│   ├── src/
│   │   ├── main.tsx             # React app entry point
│   │   ├── App.tsx              # Root component, routing setup
│   │   ├── pages/               # Page components (workflows, forms, apps, etc.)
│   │   ├── components/          # Reusable components
│   │   │   ├── ui/              # shadcn/ui primitives (Button, Dialog, etc.)
│   │   │   ├── [feature]/       # Feature-specific components (agents, forms, editor, etc.)
│   │   │   └── layout/          # Layout components (header, sidebar, navigation)
│   │   ├── hooks/               # Custom React hooks (useAuth, useQueries, etc.)
│   │   ├── services/            # API client services (HTTP wrappers)
│   │   ├── stores/              # Zustand state stores (editor, chat, notifications, etc.)
│   │   ├── lib/                 # Utilities and type definitions
│   │   │   ├── v1.d.ts          # Auto-generated API types (from OpenAPI schema)
│   │   │   ├── api-client.ts    # HTTP client base (Auth header injection, error handling)
│   │   │   ├── app-code-*.ts    # App code compilation, runtime, routing
│   │   │   └── utils.ts         # Helper functions
│   │   └── index.css            # TailwindCSS styles
│   ├── package.json             # Frontend dependencies
│   └── tsconfig.json            # TypeScript config
│
├── docker-compose.yml           # Production deployment config
├── docker-compose.dev.yml       # Development config (hot reload)
├── docker-compose.test.yml      # Test infrastructure (PostgreSQL, RabbitMQ, Redis)
├── CLAUDE.md                    # Project-specific rules and workflows
├── .env.example                 # Environment variables template
└── README.md                    # Project documentation
```

## Directory Purposes

**api/src/routers/:**
- Purpose: HTTP endpoint handlers, request validation, auth checks
- Contains: 40+ router files (one per feature domain)
- Key files:
  - `auth.py`: Login, token refresh, MFA setup, passkeys
  - `workflows.py`: CRUD operations on workflows
  - `executions.py`: Workflow execution endpoints, result retrieval
  - `cli.py`: SDK endpoints called during workflow execution
  - `forms.py`, `applications.py`, `agents.py`: CRUD + execution
  - `integrations.py`, `oauth_connections.py`: External service integrations
- Pattern: Each router has `/api/{feature}` prefix, imports from services/repositories

**api/src/services/:**
- Purpose: Business logic, orchestration, external integrations
- Key subdirectories:
  - `execution/` - ProcessPoolManager, worker processes, execution context
  - `file_storage/` - File operations, indexing
  - `llm/` - LLM provider clients (OpenAI, Anthropic, etc.)
  - `mcp_server/` - Model Context Protocol implementation
  - `webhooks/` - Webhook processing and routing
  - `events/` - Event publishing and handling
- Key files:
  - `agent_executor.py` - Agent orchestration
  - `github_sync.py` - GitHub repository sync (93KB - complex)
  - `email_service.py` - Email sending
  - `decorator_property_service.py` - Workflow code decoration

**api/src/repositories/:**
- Purpose: Type-safe data access with org scoping and RBAC
- Key files:
  - `org_scoped.py` - Base class implementing org cascade + role checking
  - `base.py` - Base repository for non-org-scoped entities
  - `README.md` - Documentation of OrgScopedRepository pattern
  - Individual repos: `executions.py`, `workflows.py`, `forms.py`, `agents.py`, etc.

**api/src/models/orm/:**
- Purpose: SQLAlchemy ORM models defining database schema
- Contains: 28 model files
- Key models:
  - `users.py`, `organizations.py` - User and org management
  - `workflows.py`, `forms.py`, `applications.py` - Core entities
  - `executions.py` - Workflow execution records
  - `agents.py` - LLM agents
  - `integrations.py` - External integrations
  - `events.py`, `metrics.py` - Logging and metrics

**api/src/models/contracts/:**
- Purpose: Pydantic models for API request/response validation
- Contains: 37 model files (mirror ORM structure)
- Pattern: Each resource has `Create*`, `Update*`, `Response`, `List*` variants
- Auto-generated types in `client/src/lib/v1.d.ts` from OpenAPI schema

**api/src/core/:**
- Purpose: Cross-cutting infrastructure
- Key files:
  - `database.py` - PostgreSQL async connection, session factory
  - `auth.py` - JWT validation, CurrentUser dependency
  - `security.py` - Password hashing, token creation, CSRF
  - `cache/` - Redis client initialization, cache keys
  - `redis_client.py` - Resilient Redis client
  - `pubsub.py` - Redis pub/sub for WebSocket updates
  - `locks.py` - Distributed locks (Redis-based)
  - `rate_limit.py` - Rate limiting per endpoint

**api/src/jobs/:**
- Purpose: Asynchronous background work
- `consumers/` - RabbitMQ message processors
  - `workflow_execution.py` - Dequeue and execute workflows
  - `package_install.py` - Handle pip package installation
- `schedulers/` - APScheduler background tasks
  - `cron_scheduler.py` - Trigger workflows on cron schedule
  - `event_cleanup.py` - Delete old execution records
  - `oauth_token_refresh.py` - Refresh OAuth tokens
- `rabbitmq.py` - Base consumer class, connection pooling

**client/src/pages/:**
- Purpose: Page-level components (routable)
- Key pages:
  - `workflows/` - Workflow editor, list, execution
  - `forms/` - Form builder, form responses
  - `applications/` - App builder, app designer
  - `executions/` - Execution history, logs, real-time monitoring
  - `settings/` - Organization and user settings

**client/src/hooks/:**
- Purpose: Custom React hooks (business logic)
- Pattern: `use*` naming convention
- Examples: `useAuth`, `useWorkflows`, `useForms`, `useExecutions`

**client/src/stores/:**
- Purpose: Zustand state management
- Files: `*.store.ts` suffix
- Key stores:
  - `editorStore.ts` - Workflow/form editor state
  - `chatStore.ts` - Chat/LLM interaction state
  - `executionStreamStore.ts` - Real-time execution updates
  - `notificationStore.ts` - Toast notifications

## Key File Locations

**Entry Points:**
- API: `api/src/main.py` - FastAPI app instance and lifespan
- Worker: `api/src/worker/main.py` - Background job consumer
- Scheduler: `api/src/scheduler/main.py` - Background scheduler
- Frontend: `client/src/main.tsx` - React app root

**Configuration:**
- `api/src/config.py` - Settings (environment variables, defaults)
- `api/alembic/alembic.ini` - Alembic migration config
- `client/tsconfig.json` - TypeScript config
- `.env` - Environment variables (not committed)
- `.env.example` - Environment template

**Core Logic:**
- Authentication: `api/src/routers/auth.py`, `api/src/core/auth.py`
- Execution: `api/src/services/execution/`, `api/src/jobs/consumers/workflow_execution.py`
- Workflow validation: `api/src/services/workflow_validation.py`
- Organization access: `api/src/repositories/org_scoped.py`

**Testing:**
- Unit tests: `api/tests/unit/` (mock dependencies)
- Integration tests: `api/tests/integration/` (real infrastructure)
- Test fixtures: `api/tests/fixtures/` (test data, auth helpers)
- Run via: `./test.sh` (manages Docker dependencies)

## Naming Conventions

**Files:**
- `*_router.py` → FastAPI routers (e.g., `auth_router.py`)
- `*_repository.py` → Data access classes (e.g., `execution_repository.py`)
- `*_service.py` → Business logic services (e.g., `email_service.py`)
- `*.store.ts` → Zustand stores (e.g., `editorStore.ts`)
- `*.test.ts` → Unit tests (e.g., `Button.test.ts`)
- `use*.ts` → Custom hooks (e.g., `useAuth.ts`)

**Directories:**
- `routers/` → HTTP endpoint handlers
- `services/` → Business logic
- `repositories/` → Data access
- `models/` → Data models (ORM + Pydantic)
- `core/` → Infrastructure utilities
- `jobs/` → Background job workers
- `components/` → React components
- `pages/` → Page-level routes
- `hooks/` → Custom React hooks
- `stores/` → State management
- `lib/` → Utility functions and types

**Database Naming:**
- Table names: snake_case plural (e.g., `workflows`, `form_roles`)
- Column names: snake_case (e.g., `created_at`, `is_active`)
- Foreign keys: `{entity}_id` (e.g., `workflow_id`, `organization_id`)

**API Naming:**
- Endpoints: `/api/{resource}` for collections, `/api/{resource}/{id}` for items
- Request models: `{Resource}Create`, `{Resource}Update`
- Response models: `{Resource}Response`, `{Resource}List`
- Query params: camelCase (e.g., `?pageSize=10&sortBy=createdAt`)

## Where to Add New Code

**New REST Endpoint:**
1. Create request/response models in `api/src/models/contracts/{feature}.py`
2. Create data access layer in `api/src/repositories/{feature}.py` (if data access needed)
3. Create business logic in `api/src/services/{feature}_service.py` (if complex)
4. Create router in `api/src/routers/{feature}.py` or add to existing router
5. Register router in `api/src/routers/__init__.py`
6. Import and include in `api/src/main.py` line 22-73

**New ORM Model:**
1. Create model in `api/src/models/orm/{feature}.py` (inherits from `Base`)
2. Create migration: `cd api && alembic revision -m "add {feature} table"`
3. Edit migration file to create table
4. Restart API: `docker compose restart api` (migration runs on startup)

**New Service/Business Logic:**
1. Create file in `api/src/services/{feature}_service.py` or feature subdir
2. Import in router and call before returning response
3. Keep routers thin (just validation + response formatting)

**New Frontend Component:**
1. For reusable components: `client/src/components/{feature}/MyComponent.tsx`
2. For page-specific: `client/src/pages/{page}/components/MyComponent.tsx`
3. Create tests: `client/src/components/{feature}/MyComponent.test.tsx`
4. Add styles with TailwindCSS (in className attribute)

**New Frontend Page:**
1. Create folder in `client/src/pages/{feature}/`
2. Create `Page.tsx` (main page component)
3. Create `components/` subdirectory if page-specific components needed
4. Register route in `client/src/App.tsx` (React Router setup)

**New State Store:**
1. Create `client/src/stores/{feature}.store.ts`
2. Export hook: `export const use{Feature} = () => use{Feature}Store()`
3. Components import hook, not store directly
4. Use `subscribeWithSelector` middleware for granular subscriptions

## Special Directories

**api/alembic/:**
- Purpose: Database schema migrations
- Generated: Yes (auto-created by Alembic)
- Committed: Yes
- Files: `versions/*.py` (one migration per change)
- Run: `docker compose restart api` applies pending migrations on startup

**api/tests/:**
- Purpose: Test suites
- Generated: No
- Committed: Yes
- Run: `./test.sh` (from repo root, manages Docker infrastructure)
- Organization: `unit/` (mocked), `integration/` (real DB), `fixtures/` (helpers)

**client/src/lib/v1.d.ts:**
- Purpose: Auto-generated TypeScript types from OpenAPI schema
- Generated: Yes (by `npm run generate:types` in client/)
- Committed: Yes
- Never edit manually - regenerate after API changes
- Requires: API running at `http://localhost:3000/api/openapi.json`

**api/shared/:**
- Purpose: Shared modules across API/worker/scheduler
- Generated: No
- Committed: Yes
- Currently empty (business logic in api/src/services/)
- Note: For future expansion of shared utilities

**config/:**
- Purpose: Application configuration files
- Contains: ENV-specific config, feature flags
- Committed: Yes

---

*Structure analysis: 2026-01-30*
