# Technology Stack

**Analysis Date:** 2026-01-30

## Languages

**Primary:**
- Python 3.11 - FastAPI backend, workflow execution engine, background jobs
- TypeScript 5.9+ - React frontend (Vite), API client types
- JavaScript/TypeScript - Node dependencies, tooling

**Secondary:**
- SQL - PostgreSQL database, schema migrations (Alembic)
- YAML - Configuration files, workflow/form definitions

## Runtime

**Environment:**
- Python 3.11 (FastAPI backend, execution engine)
- Node 20.0.0+ (React client)
- Docker & Docker Compose (all services containerized)

**Package Manager:**
- pip + requirements.txt (Python)
- npm (Node.js)
- Lockfile: `package-lock.json` (npm), Python requirements pinned

## Frameworks

**Core Backend:**
- FastAPI 0.11+ - HTTP API framework, OpenAPI documentation
- SQLAlchemy 2.0+ (with asyncio) - ORM, async database operations
- Pydantic v2 - Request/response validation, data models
- Uvicorn - ASGI server (hot reload via watchdog in development)

**Background & Async:**
- APScheduler - Scheduled jobs (cron-like execution)
- aio-pika - Async RabbitMQ client
- aiofiles - Async file operations
- asyncpg - Async PostgreSQL driver
- httpx, aiohttp - Async HTTP clients

**Core Frontend:**
- React 19.2.1 - UI framework
- Vite 7.2.7 - Build tool, dev server with HMR
- TypeScript 5.9.3 - Type safety
- React Router 7.9.3 - Client-side routing

**UI & Styling:**
- shadcn/ui - Radix UI component library (buttons, dialogs, forms, tables, etc.)
- TailwindCSS 4.0 - Utility-first CSS
- Tailwind Merge - Class name merging utilities
- Framer Motion 12.23 - Animation library
- Lucide React 0.556 - Icon library
- Monaco Editor 0.55.1 - Code editor widget

**Forms & Data:**
- React Hook Form 7.65 - Form state management
- Zod 4.1.12 - Runtime schema validation
- @hookform/resolvers - Validation adapter for React Hook Form

**API & Queries:**
- TanStack React Query 5.90.2 (@tanstack/react-query) - Server state, caching, sync
- openapi-fetch 0.15 - Type-safe OpenAPI client
- openapi-react-query - React Query adapter for OpenAPI client
- openapi-typescript 7.9.1 - Generate TypeScript types from OpenAPI spec

**Graph & Visualization:**
- @xyflow/react 12.10 - Workflow/DAG visualization
- Recharts 3.5.1 - Data visualization charts
- Dagre 0.8.5 - Graph layout algorithm
- react-markdown 10.1 - Markdown rendering
- react-syntax-highlighter 16.1 - Code syntax highlighting

**State Management:**
- Zustand 5.0.8 - Global state (lightweight alternative to Redux)
- next-themes 0.4.6 - Dark mode theme management

**Developer Tools:**
- ESLint 9.13 - Linting (with React Hooks, React Refresh plugins)
- Prettier 3.6.2 - Code formatting
- TypeScript Compiler - Type checking
- Playwright 1.56 - E2E testing framework

**Testing:**
- pytest - Python test framework
- pytest-asyncio - Async test support
- pytest-cov - Coverage reporting
- websockets - WebSocket E2E tests
- unittest.mock - Mocking (built-in Python)

**Development:**
- debugpy - Remote debugging for VS Code
- ruff - Python linter & formatter
- watchdog - File watching for hot reload

## Key Dependencies

**Critical:**
- `fastapi` - HTTP API framework, OpenAPI generation
- `sqlalchemy[asyncio]` - Database ORM, async support
- `pydantic` - Request/response validation (source of truth for API models)
- `asyncpg` - High-performance async PostgreSQL driver
- `aio-pika` - Message queue integration (RabbitMQ)
- `redis` - Caching, session storage, worker heartbeats
- `pyotp` - TOTP/HOTP MFA implementation
- `webauthn>=2.0.0` - WebAuthn/Passkeys support
- `pyjwt[crypto]` - JWT token signing/verification
- `pwdlib[bcrypt]` - Secure password hashing

**LLM & AI:**
- `openai` - OpenAI API client (GPT models)
- `anthropic` - Anthropic Claude API client
- `mcp>=1.26.0` - Model Context Protocol SDK (structuredContent support)
- `fastmcp>=2.0,<3` - FastMCP server implementation (MCP tools/resources)

**Code Transformation:**
- `libcst` - Concrete Syntax Tree for Python AST manipulation (preserves formatting)

**Object Storage:**
- `aiobotocore` - Async AWS S3 client
- `types-aiobotocore[s3]` - Type stubs for S3

**Git Integration:**
- `dulwich` - Python git library
- `PyGithub` - GitHub REST API client

**Data Processing:**
- `PyYAML` - YAML parsing
- `croniter` - CRON expression parsing
- `jmespath` - JMESPath queries for data extraction
- `python-dateutil` - Date/time utilities

**Vector Store:**
- `pgvector` - PostgreSQL vector extension for semantic search, RAG

**Database:**
- `alembic` - Schema migrations
- `psycopg2-binary` - Sync PostgreSQL driver (for CLI/migrations)

**Logging & Monitoring:**
- `python-json-logger` - JSON structured logging
- `structlog` - Structured logging framework
- `psutil` - Process monitoring (worker heartbeats)

**Security:**
- `cryptography` - Encryption utilities (Fernet for secrets)
- `python-jose[cryptography]` - JWT alternative implementation

**Configuration:**
- `pydantic-settings` - Environment variable loading
- `python-dotenv` - .env file support
- `email-validator` - Email validation (Pydantic plugin)

## Configuration

**Environment:**
All configuration uses environment variables with `BIFROST_` prefix:
- `BIFROST_ENVIRONMENT` - development/testing/production
- `BIFROST_SECRET_KEY` - JWT/encryption key (minimum 32 chars)
- `BIFROST_DATABASE_URL` - Async PostgreSQL URL
- `BIFROST_RABBITMQ_URL` - RabbitMQ connection
- `BIFROST_REDIS_URL` - Redis connection
- `BIFROST_S3_*` - S3/MinIO configuration
- `BIFROST_*_TOKEN_EXPIRE_*` - Token expiration settings
- `BIFROST_MFA_*` - MFA configuration
- `BIFROST_WEBAUTHN_*` - WebAuthn/Passkeys configuration
- `ANTHROPIC_API_KEY` - Anthropic API key (also accepts `BIFROST_ANTHROPIC_API_KEY`)

**Configuration File:**
- `api/src/config.py` - Centralized Settings class (Pydantic-based)
- `client/tsconfig.json` - TypeScript compilation config with path aliases (`@/*`)
- `client/.eslintrc.js` - ESLint rules (React, TypeScript)
- `.env.example` - Environment template

**Build:**
- `Dockerfile` - Production API container
- `Dockerfile.dev` - Development API container with debugpy
- `docker-compose.yml` - Production orchestration
- `docker-compose.dev.yml` - Development orchestration (hot reload)
- `docker-compose.test.yml` - Testing orchestration
- `vite.config.ts` - Vite build configuration
- `tsconfig*.json` - TypeScript configurations (app, node, references)

## Platform Requirements

**Development:**
- Docker & Docker Compose (local development uses containers)
- Python 3.11
- Node 20.0.0+
- PostgreSQL (via Docker)
- RabbitMQ (via Docker)
- Redis (via Docker)
- MinIO (via Docker for local S3)
- VS Code (for debugpy support)

**Production:**
- Docker & Docker Compose or Kubernetes
- PostgreSQL database
- RabbitMQ message broker
- Redis cache
- S3-compatible storage (AWS S3, MinIO, or equivalent)
- Public URL for webhooks (optional)
- HTTPS/TLS for secure communication

**Runtime Dependencies:**
- Uvicorn ASGI server
- Hot reload via watchdog (development only)
- Worker pool manager for workflow execution

---

*Stack analysis: 2026-01-30*
