# External Integrations

**Analysis Date:** 2026-01-30

## APIs & External Services

**LLM Providers:**
- OpenAI - GPT chat completions and embeddings
  - SDK/Client: `openai` (Python)
  - Config: `api/src/services/llm/openai_client.py`, `api/src/services/embeddings/openai_client.py`
  - Auth: API key via `BIFROST_OPENAI_API_KEY` (stored encrypted in system_configs table)
  - Usage: Agent execution, chat, code generation, semantic search embeddings

- Anthropic Claude - Claude chat completions and tool use
  - SDK/Client: `anthropic` (Python)
  - Config: `api/src/services/llm/anthropic_client.py`
  - Auth: API key via `BIFROST_ANTHROPIC_API_KEY` or `ANTHROPIC_API_KEY`
  - Usage: Agent execution, chat, code analysis

**Model Context Protocol (MCP):**
- FastMCP - MCP server for exposing Bifrost tools/resources to Claude
  - SDK: `fastmcp>=2.0,<3` (Python)
  - Server: `api/src/services/mcp_server/` (FastMCP implementation)
  - Routes: `api/src/routers/mcp.py`
  - Auth: OAuth flow for external client authentication
  - Usage: Exposes Bifrost workflows/forms as MCP tools, allows external LLM clients to invoke

**GitHub Integration:**
- GitHub REST API - Repository sync, file operations, webhook management
  - SDK/Client: `PyGithub` (Python), git operations via GitHub REST API
  - Service: `api/src/services/github_api.py` (API client wrapper)
  - Config: `api/src/services/github_config.py`
  - Auth: GitHub OAuth tokens (stored in `oauth_tokens` table)
  - Sync: `api/src/services/github_sync.py` (bidirectional repo sync)
  - Webhooks: `api/src/jobs/schedulers/webhook_renewal.py` (automatic renewal)
  - Usage: Pull/sync code from repos, trigger workflows on GitHub events

## Data Storage

**Databases:**
- PostgreSQL 14+
  - Connection: `BIFROST_DATABASE_URL` (async via asyncpg)
  - Sync URL: `BIFROST_DATABASE_URL_SYNC` (for Alembic migrations, CLI)
  - Client: SQLAlchemy 2.0+ with asyncio
  - ORM: SQLAlchemy declarative models in `api/src/models/orm/`
  - Migrations: Alembic in `api/alembic/`
  - Extensions: pgvector (for vector embeddings/semantic search)
  - Pool: PgBouncer for connection pooling

**File Storage:**
- S3-Compatible Object Storage
  - Provider: AWS S3, MinIO, or equivalent
  - Config: Environment variables:
    - `BIFROST_S3_BUCKET` - Bucket name (required)
    - `BIFROST_S3_ENDPOINT_URL` - Endpoint (None for AWS, `http://minio:9000` for local)
    - `BIFROST_S3_ACCESS_KEY` - AWS/MinIO access key
    - `BIFROST_S3_SECRET_KEY` - AWS/MinIO secret key
    - `BIFROST_S3_REGION` - AWS region (default: us-east-1)
  - Client: `aiobotocore` (async boto3)
  - Service: `api/src/services/file_storage/s3_client.py`
  - Usage: Workspace files, workflow artifacts, uploaded documents

**Caching:**
- Redis
  - Connection: `BIFROST_REDIS_URL`
  - Client: `redis` (v5.0+ with async support)
  - Usage: Session storage, cache layer, worker heartbeats, distributed locks
  - Features: Async support, pub/sub for pubsub manager, set-based worker tracking

## Authentication & Identity

**Auth Provider:**
- Custom implementation with multiple auth methods:

  **Password Authentication:**
  - Hashing: bcrypt via `pwdlib[bcrypt]`
  - Service: `api/src/routers/auth.py`
  - MFA enforcement: TOTP + trusted devices

  **OAuth 2.0 / SSO:**
  - Configurable OAuth providers (GitHub, Google, Microsoft, custom)
  - Service: `api/src/routers/oauth_sso.py`
  - Config: `api/src/services/oauth_config_service.py`
  - Storage: `api/src/models/orm/oauth_connections.py`
  - Token storage: Encrypted in database (Fernet encryption)
  - Webhook renewal: `api/src/jobs/schedulers/oauth_token_refresh.py`

  **MFA (Multi-Factor Authentication):**
  - TOTP: `pyotp` library (Google Authenticator, Authy compatible)
  - Trusted Devices: 30-day device registration
  - Recovery Codes: 10 codes per user
  - Settings: `BIFROST_MFA_*` environment variables
  - Service: `api/src/routers/mfa.py`

  **WebAuthn/Passkeys:**
  - Biometric/passwordless login
  - Library: `webauthn>=2.0.0`
  - Config: `BIFROST_WEBAUTHN_RP_ID`, `BIFROST_WEBAUTHN_ORIGIN`
  - Service: `api/src/routers/passkeys.py`
  - Browser support: Face ID, Touch ID, Windows Hello, security keys

**Token Management:**
- JWT Tokens (HS256)
  - Signing: `pyjwt[crypto]`
  - Access Token: 30 minutes (configurable)
  - Refresh Token: 7 days (configurable)
  - Issuer: `bifrost-api`
  - Audience: Validated per request
  - Secret: `BIFROST_SECRET_KEY` (minimum 32 chars, required)

## Monitoring & Observability

**Error Tracking:**
- None built-in (application-level exception handling via FastAPI)
- Structured logging available via `structlog`

**Logs:**
- Structured logging: `structlog` + `python-json-logger`
- Console output in development
- JSON format for production (suitable for log aggregation)
- Components logged:
  - API requests/responses (via exception handlers)
  - Workflow execution trace
  - LLM interactions
  - Database operations
  - Message queue events
  - Worker heartbeats

**Process Monitoring:**
- Worker heartbeats: Published to Redis every `BIFROST_WORKER_HEARTBEAT_INTERVAL_SECONDS` (default: 10s)
- Worker health: Registered in Redis with TTL of `BIFROST_WORKER_REGISTRATION_TTL_SECONDS` (default: 30s)
- Memory monitoring: `psutil` for process metrics
- Service: `api/src/services/execution/memory_monitor.py`

## CI/CD & Deployment

**Hosting:**
- Docker containers (development and production)
- Kubernetes ready (k8s manifests in `k8s/`)
- Reverse proxy required: nginx (for production)

**CI Pipeline:**
- GitHub Actions (`.github/workflows/`)
- Automated testing, type checking, linting

**Containerization:**
- API: `Dockerfile` (production), `Dockerfile.dev` (development with debugpy)
- Scheduler: Background job processor
- Worker: Workflow execution processes (pool-based)
- Client: Node.js-based Vite build
- Infrastructure: PostgreSQL, RabbitMQ, Redis, MinIO (via docker-compose)

## Environment Configuration

**Required env vars (at minimum for production):**
- `BIFROST_SECRET_KEY` - JWT/encryption key (32+ chars, auto-generated by setup.sh)
- `POSTGRES_PASSWORD` - PostgreSQL password
- `RABBITMQ_PASSWORD` - RabbitMQ password
- `BIFROST_ENVIRONMENT` - Set to "production"
- `BIFROST_DEBUG` - Set to "false"

**S3 Configuration (for production):**
- `BIFROST_S3_BUCKET` - Storage bucket name
- `BIFROST_S3_ACCESS_KEY` - Access key
- `BIFROST_S3_SECRET_KEY` - Secret key
- `BIFROST_S3_ENDPOINT_URL` - Endpoint (AWS S3: omit, MinIO: specify URL)
- `BIFROST_S3_REGION` - Region (default: us-east-1)

**LLM Configuration (for AI features):**
- `BIFROST_OPENAI_API_KEY` - OpenAI API key (if using OpenAI)
- `ANTHROPIC_API_KEY` - Anthropic API key (if using Claude)
- Store via `POST /api/llm-config/` endpoint after first deployment

**MCP Configuration:**
- `BIFROST_MCP_BASE_URL` - Public URL for MCP OAuth endpoints (e.g., ngrok for external access)
- Configured in system_configs table via `POST /api/mcp-config/`

**Secrets location:**
- Development: `.env` file (generated by `setup.sh`)
- Production: Environment variables (managed by hosting platform)
- Database: Encrypted secrets stored in `system_configs` and `oauth_tokens` tables (Fernet encryption)

## Webhooks & Callbacks

**Incoming Webhooks:**
- GitHub Webhooks
  - Endpoint: `POST /api/github/webhooks`
  - Events: Push, pull request, release (configurable per repo)
  - Payload verification: GitHub signature validation
  - Usage: Trigger workflows on GitHub repository events
  - Renewal: Automatic via scheduler (`api/src/jobs/schedulers/webhook_renewal.py`)

- Generic Webhooks
  - Endpoint: `POST /api/webhooks/{webhook_id}` (dynamic)
  - Protocol: `api/src/services/webhooks/protocol.py`
  - Registry: `api/src/services/webhooks/registry.py`
  - Adapters: Generic adapter for custom webhooks
  - Usage: External systems trigger Bifrost workflows

**Outgoing Webhooks:**
- Event notification webhooks
  - Trigger: Workflow execution events (start, complete, error)
  - Configuration: Per-organization webhook subscriptions
  - Retry policy: Configurable retries on failure
  - Service: `api/src/services/notification_service.py`
  - Storage: `api/src/models/orm/webhooks.py`

**Real-time Updates:**
- WebSocket connections for live workflow execution
  - Endpoint: `WS /ws` (authentication required)
  - Events: Execution state changes, log streaming
  - Manager: `api/src/core/pubsub.py` (pub/sub message hub)
  - Router: `api/src/routers/websocket.py`

## Code Analysis & AST Transformation

**Git Operations:**
- Library: `dulwich` (pure Python git implementation)
- Usage: Clone, commit, push operations for workflow versioning

**AST Manipulation:**
- Library: `libcst` (Concrete Syntax Tree)
- Usage: Python code transformation with formatting preservation
- Examples: Inject imports, rewrite function calls, inject authentication

---

*Integration audit: 2026-01-30*
