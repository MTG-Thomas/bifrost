# Architecture

**Analysis Date:** 2026-01-30

## Pattern Overview

**Overall:** Distributed microservices with clear separation between HTTP API, async workers, and background jobs. FastAPI backend with async/await for I/O-bound operations and multiprocessing for compute-intensive workflow execution.

**Key Characteristics:**
- Async-first Python backend (FastAPI + SQLAlchemy + asyncpg)
- Process pool isolation for untrusted code execution (user workflows)
- RabbitMQ for async message queuing (workflow execution, package installation)
- Redis for caching, sessions, pub/sub, and execution result buffers
- PostgreSQL for persistent data with Alembic migrations
- Organization-scoped data access with role-based access control

## Layers

**HTTP API Layer:**
- Purpose: Handle synchronous HTTP requests, auth, validation
- Location: `api/src/routers/` (40+ routers, one per feature domain)
- Contains: Endpoint handlers, request/response validation
- Depends on: Repositories, Services, Database
- Used by: Frontend client, CLI, external integrations

**Business Logic Layer (Services):**
- Purpose: Core business logic, orchestration, external integrations
- Location: `api/src/services/` (execution engine, GitHub sync, LLM, MCP, etc.)
- Contains: Complex workflows, integrations, algorithms
- Depends on: Repositories, Core utilities, External APIs
- Used by: Routers, Workers, Schedulers

**Data Access Layer (Repositories):**
- Purpose: Type-safe database access with org scoping and RBAC
- Location: `api/src/repositories/` (OrgScopedRepository pattern)
- Contains: SQLAlchemy queries, org cascade scoping, role checks
- Depends on: ORM models, Database session
- Used by: Services, Routers

**Data Model Layer:**
- Purpose: Schema definition and API contracts
- Location:
  - `api/src/models/orm/` - SQLAlchemy ORM models (PostgreSQL schema)
  - `api/src/models/contracts/` - Pydantic models (request/response DTOs)
- Contains: Database schema, validation rules, type information
- Depends on: SQLAlchemy, Pydantic
- Used by: Repositories, Routers, Services

**Core Infrastructure:**
- Purpose: Cross-cutting concerns, utilities, configuration
- Location: `api/src/core/` (auth, database, cache, security, pubsub, etc.)
- Contains: Database connections, Redis client, authentication, CSRF, metrics, rate limiting
- Depends on: External libraries (SQLAlchemy, Redis, cryptography)
- Used by: All layers

**Background Job Processing:**
- Purpose: Execute async work outside HTTP request cycle
- Locations:
  - `api/src/jobs/consumers/` - RabbitMQ message processors
  - `api/src/jobs/schedulers/` - APScheduler background tasks
  - `api/src/worker/main.py` - Worker service entry point
  - `api/src/scheduler/main.py` - Scheduler service entry point
- Contains: Workflow execution, package installation, cron scheduling
- Depends on: Services, Repositories, Database, RabbitMQ, Redis
- Used by: API (queues work), Scheduler (schedules work)

**Execution Engine (Process Isolation):**
- Purpose: Safely execute untrusted user workflow code in isolated processes
- Location: `api/src/services/execution/`
- Contains: ProcessPoolManager (reusable worker pool), simple_worker (single-use worker), SDK bindings
- Pattern: Worker processes spawned from pool, stdin/stdout communication
- Isolation: Each execution context validated, sandboxed filesystem, timeout enforcement

## Data Flow

**User Workflow Execution (Async):**

1. Frontend/API calls `POST /api/executions` with workflow_id, inputs, schedule_at
2. Router validates auth, creates execution record in DB, stores context in Redis
3. Router publishes RabbitMQ message (workflow-executions queue)
4. Router returns execution_id immediately (async)
5. WorkflowExecutionConsumer dequeues message, reads context from Redis
6. Consumer calls ProcessPoolManager.execute()
7. ProcessPoolManager routes to worker process (or creates new if pool below min)
8. Worker process imports user code, executes with SDK bindings
9. SDK bindings call back to API via HTTP (system user auth)
10. Worker returns result, ProcessPoolManager calls on_result callback
11. Consumer writes result to DB, flushes logs, publishes update via Redis pub/sub
12. Frontend receives update via WebSocket (pub/sub), displays result

**Cron Workflow Trigger:**

1. Scheduler service (APScheduler) wakes at scheduled time
2. Scheduler queries DB for workflows with matching cron expression
3. Scheduler publishes execution message to RabbitMQ
4. Same flow as User Workflow Execution above

**Organization-Scoped Data Access:**

1. API endpoint receives request with auth context (org_id, user_id, is_superuser)
2. Router creates OrgScopedRepository with context
3. Repository applies organization cascade:
   - For ID lookups: Find entity, verify scope + role access
   - For name/key lookups: Try org-specific first, fall back to global
4. Repository checks role-based access (if entity has role table)
5. Returns entity or raises AccessDeniedError
6. Router constructs response and returns to client

**State Management:**
- **Execution state**: Stored in PostgreSQL (creation time, status, result, error)
- **Pending execution context**: Redis (fast lookups during message processing)
- **Session cache**: Redis with TTL (user auth state)
- **Execution logs**: PostgreSQL + flushed to filesystem
- **Real-time updates**: Redis pub/sub → WebSocket push to frontend
- **Metrics**: PostgreSQL (ai_usage table) for billing/analytics

## Key Abstractions

**OrgScopedRepository:**
- Purpose: Unified org-scoped data access with RBAC
- Examples: `ApplicationRepository`, `FormRepository`, `WorkflowRepository`, `TableRepository`
- Pattern: Subclass with `model` and optional `role_table` attributes
- Features: Cascade scoping (org-specific → global), role validation, superuser bypass for scope
- Files: `api/src/repositories/org_scoped.py`, `api/src/repositories/README.md`

**Execution Context (SDK):**
- Purpose: Environment passed to user workflow code
- Examples: `tables.query()`, `files.read()`, `http.post()`, `forms.get()`
- Pattern: SDK methods translate to API calls during execution
- Security: Execution starts with validated scope (org_id), SDK validates all data access within that scope
- Files: `api/src/services/execution/`, `api/src/routers/cli.py`

**ProcessPoolManager:**
- Purpose: Reusable pool of worker processes for workflow execution
- Pattern: Spawn N processes on startup, distribute work with job queues
- Isolation: Each process has stdin/stdout for JSON communication
- Lifecycle: Heartbeat publishing, timeout enforcement (SIGTERM → SIGKILL), crash recovery
- Files: `api/src/services/execution/process_pool.py`

**Auth/Security:**
- JWT tokens (access + refresh with rotation)
- Passkeys support (WebAuthn)
- OAuth 2.0 SSO (Google, GitHub, custom)
- CSRF protection (cookie-based auth)
- MFA support (TOTP)
- Files: `api/src/routers/auth.py`, `api/src/core/auth.py`, `api/src/core/security.py`

**Workflow/App Code Compilation:**
- Purpose: Transform user-written Python/JavaScript to executable code
- Examples: Form/App validation, Workflow logic
- Pattern: Parse code, validate security (sandbox), transpile/compile
- Files: `api/src/services/execution/`, `client/src/lib/app-code-*`

## Entry Points

**API Server:**
- Location: `api/src/main.py` → `create_app()` function
- Triggers: `uvicorn src.main:app` or container startup
- Responsibilities:
  - Initialize database and Redis connections
  - Register all routers (40+ endpoints)
  - Mount FastMCP ASGI app for MCP protocol
  - Set up global exception handlers
  - Setup lifespan hooks (startup/shutdown)

**Worker Service:**
- Location: `api/src/worker/main.py` → `Worker` class
- Triggers: Container startup (`docker-compose up worker`)
- Responsibilities:
  - Initialize RabbitMQ consumers (WorkflowExecutionConsumer, PackageInstallConsumer)
  - Start ProcessPoolManager for workflow execution
  - Consume and process messages from queues
  - Update DB and pub/sub with results

**Scheduler Service:**
- Location: `api/src/scheduler/main.py` → `Scheduler` class
- Triggers: Container startup (`docker-compose up scheduler`)
- Responsibilities:
  - Initialize APScheduler
  - Register cron triggers for scheduled workflows
  - Execute periodic cleanup tasks (stuck executions, OAuth refresh)
  - Listen for on-demand requests via Redis pub/sub

**Frontend App:**
- Location: `client/src/main.tsx` → `App.tsx`
- Triggers: Browser navigation to `http://localhost:3000`
- Responsibilities:
  - Authentication (login, passkey, OAuth)
  - Render pages (workflows, forms, apps, executions, etc.)
  - Real-time updates via WebSocket
  - Editor components for code/forms

## Error Handling

**Strategy:** Layered exception handlers with user-friendly messages

**Patterns:**

Global exception handlers in FastAPI app (`api/src/main.py`):
- `PydanticValidationError` → 422 with field errors
- `IntegrityError` (unique, FK) → 409 conflict
- `NoResultFound` → 404 not found
- `ValueError` → 422 validation error
- `TimeoutError` → 504 gateway timeout
- `OperationalError` (DB connection) → 503 service unavailable
- `Exception` (catch-all) → 500 internal error with safe message

Repository layer (`api/src/repositories/org_scoped.py`):
- `AccessDeniedError` raised when user lacks permission
- `NotFoundError` raised when entity not in user scope

Execution layer (`api/src/services/execution/`):
- Timeout errors logged, execution marked failed with message
- Process crashes detected, logged, process recycled
- Workflow exceptions caught, stored in execution result, visible to user

## Cross-Cutting Concerns

**Logging:**
- Framework: Python logging module
- Config: `api/src/main.py` line 76-87 (basicConfig)
- Pattern: Loggers per module, execution engine at DEBUG level for troubleshooting
- Key loggers: `src.services.execution`, `bifrost` (system agents)

**Validation:**
- Pydantic models in `api/src/models/contracts/` for all inputs
- ORM models enforce schema constraints
- Workflow code validation before execution (AST analysis for security)

**Authentication:**
- Dependency: `CurrentActiveUser` from `api/src/core/auth.py`
- Tokens: JWT (access + refresh), stored in httpOnly cookies or Authorization header
- MFA: Optional TOTP challenge before token creation
- Passkeys: WebAuthn verification

**Metrics & Observability:**
- Execution metrics: AI usage (tokens, cost) tracked in DB
- Request metrics: CPU, memory, latency tracked via Prometheus-style exports
- Logs: Execution logs flushed to PostgreSQL + filesystem
- Pub/Sub: Real-time execution updates pushed to WebSocket clients

**Caching:**
- Data provider cache: `api/src/core/cache/data_provider_cache.py` (Redis + in-memory)
- Module cache: `api/src/core/module_cache.py` (user code modules)
- Config cache: `api/src/core/config_resolver.py` (organization settings)

**Rate Limiting:**
- Auth endpoints: `auth_limiter` (prevent brute force)
- MFA endpoints: `mfa_limiter` (prevent OTP brute force)
- Global: Redis-backed rate limiting per user/IP
- Files: `api/src/core/rate_limit.py`

---

*Architecture analysis: 2026-01-30*
