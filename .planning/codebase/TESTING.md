# Testing Patterns

**Analysis Date:** 2026-01-30

## Test Framework

**Runner:**
- pytest with pytest-asyncio plugin
- Config in `api/pyproject.toml`:
  ```toml
  [tool.pytest.ini_options]
  addopts = "-ra -q --cov=. --cov-report=term-missing"
  testpaths = ["tests"]
  asyncio_mode = "auto"
  ```

**Assertion Library:**
- pytest built-in assertions and fixtures

**Run Commands:**
```bash
./test.sh                          # Run all tests with Docker dependencies
./test.sh tests/unit/              # Run unit tests only
./test.sh tests/integration/       # Run integration tests
./test.sh tests/integration/platform/test_sdk.py  # Run specific file
./test.sh -v                       # Verbose output
./test.sh --coverage               # With coverage report
./test.sh --e2e                    # E2E tests (workers, scheduler)
./test.sh --client                 # Playwright E2E tests
./test.sh --client-dev             # Fast Playwright iteration
```

**Important:** Always use `./test.sh` to run tests - it manages Docker containers (PostgreSQL, RabbitMQ, Redis) that tests depend on. Running pytest directly will fail for integration tests.

## Test File Organization

**Location:**
- Unit tests: `api/tests/unit/`
- Integration tests: `api/tests/integration/`
- E2E tests: `api/tests/e2e/` and `client/` for Playwright
- Fixtures: `api/tests/fixtures/`

**Naming:**
- Test modules: `test_{feature}.py`
- Test classes: `Test{Feature}` (optional, can use flat functions)
- Test functions: `test_{scenario}_{expected_result}()`

**Examples:**
- `api/tests/unit/cache/test_data_provider_cache.py`
- `api/tests/integration/platform/test_deactivation_protection.py`
- `api/tests/fixtures/auth.py` - Shared authentication helpers

**Structure:**
```
api/tests/
├── conftest.py                 # Global pytest configuration
├── fixtures/
│   ├── auth.py                # JWT token generation
│   └── large_module_generator.py
├── unit/
│   ├── cache/
│   │   └── test_data_provider_cache.py
│   ├── core/
│   │   └── test_locks.py
│   └── services/
│       └── test_notification_service.py
├── integration/
│   ├── conftest.py            # Integration-specific setup
│   ├── platform/
│   │   └── test_sdk.py
│   └── mcp/
│       └── test_mcp_scoped_lookups.py
└── e2e/
    └── fixtures/
        ├── setup.py
        └── github_setup.py
```

## Test Structure

**Suite Organization:**
```python
"""Unit tests for data provider Redis cache."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.core.cache.data_provider_cache import (
    TTL_DATA_PROVIDER,
    acquire_compute_lock,
    cache_result,
    compute_param_hash,
    data_provider_cache_key,
)


class TestKeyGeneration:
    """Tests for cache key generation functions."""

    def test_data_provider_cache_key_with_org(self):
        """Key includes org scope when org_id provided."""
        key = data_provider_cache_key("org-123", "get_users", "abc123")
        assert key == "bifrost:org:org-123:dp:get_users:abc123"

    def test_data_provider_cache_key_global(self):
        """Key uses global scope when org_id is None."""
        key = data_provider_cache_key(None, "get_users", "abc123")
        assert key == "bifrost:global:dp:get_users:abc123"


class TestComputeParamHash:
    """Tests for parameter hashing."""

    def test_empty_params_returns_empty(self):
        """Empty or None params return 'empty' hash."""
        assert compute_param_hash(None) == "empty"
        assert compute_param_hash({}) == "empty"

    def test_deterministic_hash(self):
        """Same params produce same hash."""
        params = {"user_id": "123", "limit": 10}
        hash1 = compute_param_hash(params)
        hash2 = compute_param_hash(params)
        assert hash1 == hash2
```

**Patterns:**
- **Test Classes:** Organize related tests logically (optional but recommended for clarity)
- **Test Methods:** Single assertion per test preferred, or related assertions for same scenario
- **Docstrings:** Each test has a one-line docstring explaining what is tested
- **No setup/teardown needed:** pytest fixtures handle DB transaction rollback

## Mocking

**Framework:** `unittest.mock` (built-in Python)

**Patterns:**

**Mock Redis (unit tests):**
```python
@pytest.fixture
def mock_redis():
    """Mock Redis connection for unit tests."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.delete = AsyncMock(return_value=True)
    mock.expire = AsyncMock(return_value=True)
    return mock
```

**Mock in test:**
```python
@pytest.mark.asyncio
async def test_cache_hit_returns_entry():
    """Returns cached entry when found and not expired."""
    expires_at = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
    cached_data = {
        "data": {"users": [1, 2, 3]},
        "expires_at": expires_at
    }

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))

    with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
        mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await get_cached_result("org-123", "get_users", {"id": 1})
        assert result is not None
        assert result["data"] == {"users": [1, 2, 3]}
```

**Mock RabbitMQ:**
```python
@pytest.fixture
def mock_rabbitmq():
    """Mock RabbitMQ connection for unit tests."""
    mock = AsyncMock()
    mock.publish = AsyncMock(return_value=None)
    mock.consume = AsyncMock(return_value=None)
    return mock
```

**What to Mock:**
- External services (APIs, OAuth providers, SendGrid)
- Repository/database access (in unit tests)
- Message queue operations
- File system operations (unless testing file operations specifically)
- Time/date functions (for deterministic tests)

**What NOT to Mock:**
- The service/component being tested
- Internal methods of the component (test public API only)
- Business logic you're testing
- In integration tests: Don't mock the database - use the real test database

## Fixtures and Test Data

**Test Data Fixtures:**
```python
@pytest.fixture
def sample_user_data() -> dict[str, Any]:
    """Sample user data for testing."""
    return {
        "email": "test@example.com",
        "password": "SecurePassword123!",
        "name": "Test User",
    }

@pytest.fixture
def sample_org_data() -> dict[str, Any]:
    """Sample organization data for testing."""
    return {
        "name": "Test Organization",
        "domain": "example.com",
    }
```

**Database Fixtures:**
```python
@pytest_asyncio.fixture
async def db_session(async_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """
    Provide a database session for each test.

    Each test gets its own session that is rolled back after the test,
    ensuring test isolation.
    """
    async with async_session_factory() as session:
        yield session
        await session.rollback()

@pytest_asyncio.fixture
async def clean_db(db_session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """
    Provide a clean database for tests that need it.

    Truncates all tables before the test runs.
    Use sparingly as this is slower than transaction rollback.
    """
    # Get all table names
    result = await db_session.execute(
        text("""
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            AND tablename != 'alembic_version'
        """)
    )
    tables = [row[0] for row in result.fetchall()]

    if tables:
        # Disable foreign key checks, truncate, re-enable
        await db_session.execute(text("SET session_replication_role = 'replica'"))
        for table in tables:
            await db_session.execute(text(f'TRUNCATE TABLE "{table}" CASCADE'))
        await db_session.execute(text("SET session_replication_role = 'origin'"))
        await db_session.commit()

    yield db_session
```

**Authentication Fixtures:**
Located in `api/tests/fixtures/auth.py` - provides JWT token generation for testing authenticated endpoints.

```python
def create_test_jwt(
    user_id: str | None = None,
    email: str = "test@example.com",
    name: str = "Test User",
    is_superuser: bool = False,
    organization_id: str | None = None,
) -> str:
    """Create test JWT token for authentication."""
    if user_id is None:
        user_id = DEFAULT_ADMIN_ID if is_superuser else DEFAULT_USER_ID

    if not is_superuser and organization_id is None:
        organization_id = "00000000-0000-4000-8000-000000000100"

    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "email": email,
        "name": name,
        "is_superuser": is_superuser,
        "org_id": None if (is_superuser and organization_id is None) else organization_id,
        "roles": ["authenticated", "PlatformAdmin"] if is_superuser else ["authenticated", "OrgUser"],
        "exp": now + timedelta(hours=2),
        "iat": now,
        "iss": TEST_JWT_ISSUER,
        "aud": TEST_JWT_AUDIENCE,
        "type": "access",
    }
    return jwt.encode(payload, TEST_SECRET_KEY, algorithm=TEST_ALGORITHM)

def create_superuser_jwt(
    user_id: str = "test-platform-admin-123",
    email: str = "admin@platform.com",
    name: str = "Platform Admin"
) -> str:
    """Create test JWT token for superuser/platform admin."""
    return create_test_jwt(
        user_id=user_id,
        email=email,
        name=name,
        is_superuser=True,
    )
```

**Location:** `api/tests/fixtures/`

## Test Types

### Unit Tests

**Scope:** Test isolated business logic without external dependencies

**Location:** `api/tests/unit/`

**Characteristics:**
- Mock all external dependencies (database, APIs, message queue)
- Fast execution (milliseconds)
- Use `unittest.mock` for mocking
- Focus on function/method correctness

**Example:**
```python
class TestGetCachedResult:
    """Tests for cache retrieval."""

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        """Returns None when key not in cache."""
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)

        with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_cached_result("org-123", "get_users", {"id": 1})
            assert result is None

    @pytest.mark.asyncio
    async def test_expired_cache_returns_none(self):
        """Returns None and deletes expired entries."""
        expires_at = (datetime.utcnow() - timedelta(minutes=5)).isoformat()
        cached_data = {
            "data": {"users": [1, 2, 3]},
            "expires_at": expires_at
        }

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=json.dumps(cached_data))
        mock_redis.delete = AsyncMock()

        with patch("src.core.cache.data_provider_cache.get_redis") as mock_get_redis:
            mock_get_redis.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
            mock_get_redis.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await get_cached_result("org-123", "get_users", {"id": 1})
            assert result is None
            mock_redis.delete.assert_called_once()
```

### Integration Tests

**Scope:** Test components working together with real infrastructure

**Location:** `api/tests/integration/`

**Characteristics:**
- Use real Docker services: PostgreSQL, RabbitMQ, Redis
- Test complete workflows end-to-end
- Via actual HTTP requests to API (not direct function calls)
- Only mock external services we don't control

**Setup:**
```python
# api/tests/integration/conftest.py
@pytest_asyncio.fixture
async def integration_db_session(db_session: AsyncSession):
    """
    Provide a database session for integration tests.

    Wraps the base db_session fixture with integration-specific setup.
    """
    yield db_session

@pytest.fixture(scope="module")
def api_base_url():
    """
    Base URL for API integration tests.

    Default: http://localhost:18000 (docker-compose.test.yml offset port)
    Can be overridden with TEST_API_URL environment variable.
    """
    return os.getenv("TEST_API_URL", "http://localhost:18000")
```

**Example:**
```python
# api/tests/integration/platform/test_deactivation_protection.py
@pytest.mark.integration
async def test_deactivation_prevents_api_access(
    integration_db_session,
    api_base_url,
):
    """Deactivated users cannot access API endpoints."""
    # Create user via database
    user = User(email="test@example.com", is_active=True)
    integration_db_session.add(user)
    await integration_db_session.commit()

    # Create JWT token
    token = create_test_jwt(user_id=str(user.id), email=user.email)

    # Make successful request
    headers = {"Authorization": f"Bearer {token}"}
    response = await httpx.AsyncClient().get(
        f"{api_base_url}/api/me",
        headers=headers
    )
    assert response.status_code == 200

    # Deactivate user
    user.is_active = False
    await integration_db_session.commit()

    # Now request should fail
    response = await httpx.AsyncClient().get(
        f"{api_base_url}/api/me",
        headers=headers
    )
    assert response.status_code == 401
```

### E2E Tests

**Scope:** Full system tests including workers and scheduler

**Location:** `api/tests/e2e/`

**Characteristics:**
- Tests complete workflows with background jobs
- Uses real API, workers, scheduler
- Slower but catches integration issues
- Ran with `./test.sh --e2e`

**Playwright E2E Tests:**

**Location:** `client/` (Playwright configuration in `playwright.config.ts`)

**Run:**
```bash
./test.sh --client              # Full Playwright E2E tests with API
./test.sh --client-dev          # Fast Playwright iteration (keeps stack running)
npm run test:e2e                # From client directory
npm run test:e2e:ui             # Interactive UI mode
npm run test:e2e:debug          # Debug mode with inspector
```

## Coverage

**Requirements:** No explicit minimum enforced, but aim for high coverage on business logic

**View Coverage:**
```bash
./test.sh --coverage            # Generates coverage report
# Coverage report appears in terminal output (term-missing format)
```

**pytest coverage config (pyproject.toml):**
```toml
[tool.coverage.run]
source = ["."]
omit = ["tests/*", "**/__init__.py"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "if __name__ == .__main__.:"
]
```

## Common Patterns

### Async Testing

**Mark with decorator:**
```python
@pytest.mark.asyncio
async def test_async_operation():
    """Test async function."""
    result = await async_function()
    assert result is not None
```

**No need for event loop fixture:** pytest-asyncio handles this automatically with `asyncio_mode = "auto"` in config.

### Error Testing

**Pattern 1: Test exception is raised**
```python
@pytest.mark.asyncio
async def test_invalid_token_raises_error():
    """Invalid token raises HTTPException."""
    with pytest.raises(HTTPException) as exc_info:
        await verify_token(invalid_token)

    assert exc_info.value.status_code == 401
    assert "Invalid token" in str(exc_info.value.detail)
```

**Pattern 2: Test error response from API**
```python
@pytest.mark.integration
async def test_login_with_invalid_password(api_base_url):
    """Invalid password returns 401."""
    response = await httpx.AsyncClient().post(
        f"{api_base_url}/api/auth/login",
        data={"username": "test@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["detail"]
```

### Database Assertions

**Test data persisted to database:**
```python
@pytest.mark.integration
async def test_user_creation_persists(integration_db_session):
    """Created user is persisted to database."""
    user = User(email="test@example.com", name="Test User")
    integration_db_session.add(user)
    await integration_db_session.commit()

    # Query to verify
    result = await integration_db_session.execute(
        select(User).where(User.email == "test@example.com")
    )
    persisted_user = result.scalar()
    assert persisted_user is not None
    assert persisted_user.name == "Test User"
```

### Parametrized Tests

**Test multiple scenarios:**
```python
@pytest.mark.parametrize("org_id,expected", [
    ("org-123", "bifrost:org:org-123:dp:get_users:abc123"),
    (None, "bifrost:global:dp:get_users:abc123"),
    ("GLOBAL", "bifrost:global:dp:get_users:abc123"),
])
def test_data_provider_cache_key(org_id, expected):
    """Cache key changes based on org_id."""
    key = data_provider_cache_key(org_id, "get_users", "abc123")
    assert key == expected
```

## Test Configuration

**Global configuration:** `api/tests/conftest.py`

**Key environment variables:**
```python
# Database (async)
TEST_DATABASE_URL = os.getenv(
    "BIFROST_DATABASE_URL",
    "postgresql+asyncpg://bifrost:bifrost_test@pgbouncer:5432/bifrost_test",
)

# Message queue
TEST_RABBITMQ_URL = os.getenv(
    "BIFROST_RABBITMQ_URL",
    "amqp://bifrost:bifrost_test@rabbitmq:5672/",
)

# Cache
TEST_REDIS_URL = os.getenv(
    "BIFROST_REDIS_URL",
    "redis://redis:6379/0",
)

# API endpoint
TEST_API_URL = os.getenv("TEST_API_URL", "http://api:8000")
```

**Pytest markers:**
```python
config.addinivalue_line("markers", "unit: Unit tests (fast, mocked dependencies)")
config.addinivalue_line("markers", "integration: Integration tests (real database, message queue)")
config.addinivalue_line("markers", "e2e: End-to-end tests (full API with all services)")
config.addinivalue_line("markers", "slow: Tests that take >1 second")
```

**Use markers:**
```python
@pytest.mark.unit
def test_simple_logic():
    ...

@pytest.mark.integration
async def test_with_database():
    ...

@pytest.mark.e2e
async def test_full_workflow():
    ...

@pytest.mark.slow
def test_takes_time():
    ...
```

## Completion Checklist

Before marking Python test work complete:

- [ ] Unit tests written for new business logic
- [ ] Integration tests written for new endpoints/workflows
- [ ] `./test.sh` passes with 100% success rate
- [ ] External dependencies properly mocked in unit tests
- [ ] Integration tests use real infrastructure (Docker databases)
- [ ] Tests have clear, descriptive names and docstrings
- [ ] Error cases tested (negative tests included)
- [ ] No hardcoded test data (use fixtures)
- [ ] Async tests properly marked with `@pytest.mark.asyncio`

## Best Practices

**✅ Do:**
- Test one thing per test (or related assertions for same scenario)
- Use descriptive test names that explain what is being tested
- Mock external dependencies in unit tests
- Use fixtures to share test data and setup
- Keep tests fast (unit tests run in milliseconds)
- Test both happy path and error cases
- Document non-obvious test logic with comments

**❌ Don't:**
- Mock the component being tested
- Create interdependent tests (each test must be independent)
- Use sleep/time.sleep in tests (use mocking instead)
- Test implementation details (test behavior)
- Copy test code (extract to fixtures or helper functions)
- Leave print statements in tests (use pytest -s if needed)
- Run only one test type (run full suite with `./test.sh`)

---

*Testing analysis: 2026-01-30*
