import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

os.environ.setdefault("BIFROST_SECRET_KEY", "test-secret-key-for-main-app-unit-tests")

from src import main


pytestmark = pytest.mark.unit


class _AsyncContext:
    def __init__(self, value=None, enter_error=None):
        self.value = value
        self.enter_error = enter_error
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        if self.enter_error:
            raise self.enter_error
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        self.exited = True
        return False


@pytest.fixture(autouse=True)
def reset_mcp_cache():
    main._mcp_asgi_app = None
    yield
    main._mcp_asgi_app = None


def test_get_mcp_asgi_app_caches_created_app(monkeypatch):
    created = object()
    factory = Mock(return_value=created)
    monkeypatch.setattr("src.routers.mcp.get_mcp_asgi_app", factory)

    first = main._get_mcp_asgi_app()
    second = main._get_mcp_asgi_app()

    assert first is created
    assert second is created
    factory.assert_called_once_with()


def test_get_mcp_asgi_app_leaves_cache_empty_after_factory_failure(
    monkeypatch, caplog
):
    def fail_create():
        raise RuntimeError("mcp unavailable")

    monkeypatch.setattr("src.routers.mcp.get_mcp_asgi_app", fail_create)

    with caplog.at_level("WARNING", logger="src.main"):
        result = main._get_mcp_asgi_app()

    assert result is None
    assert main._mcp_asgi_app is None
    assert "Could not create MCP ASGI app: mcp unavailable" in caplog.text


@pytest.mark.asyncio
async def test_register_dynamic_workflow_endpoints_uses_db_context(monkeypatch):
    app = object()
    db = object()
    context = _AsyncContext(db)
    register = AsyncMock(return_value=3)
    monkeypatch.setattr("src.core.database.get_db_context", lambda: context)
    monkeypatch.setattr(
        "src.services.openapi_endpoints.register_workflow_endpoints",
        register,
    )

    await main.register_dynamic_workflow_endpoints(app)

    assert context.entered is True
    assert context.exited is True
    register.assert_awaited_once_with(app, db)


@pytest.mark.asyncio
async def test_register_dynamic_workflow_endpoints_logs_and_continues_on_failure(
    monkeypatch, caplog
):
    monkeypatch.setattr(
        "src.core.database.get_db_context",
        lambda: _AsyncContext(enter_error=RuntimeError("db offline")),
    )

    with caplog.at_level("WARNING", logger="src.main"):
        await main.register_dynamic_workflow_endpoints(object())

    assert "Failed to register workflow endpoints: db offline" in caplog.text


@pytest.mark.asyncio
async def test_create_default_user_returns_before_opening_db_without_credentials(
    monkeypatch,
):
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(default_user_email="", default_user_password=""),
    )

    def fail_db_context():
        raise AssertionError("database should not be opened")

    monkeypatch.setattr("src.core.database.get_db_context", fail_db_context)

    await main.create_default_user()


@pytest.mark.asyncio
async def test_create_default_user_skips_existing_user(monkeypatch, caplog):
    existing = SimpleNamespace(email="admin@example.test")
    user_repo = SimpleNamespace(get_by_email=AsyncMock(return_value=existing))
    context = _AsyncContext(SimpleNamespace())
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(
            default_user_email="admin@example.test",
            default_user_password="secret",
        ),
    )
    monkeypatch.setattr("src.core.database.get_db_context", lambda: context)
    monkeypatch.setattr("src.repositories.users.UserRepository", lambda db: user_repo)

    with caplog.at_level("INFO", logger="src.main"):
        await main.create_default_user()

    user_repo.get_by_email.assert_awaited_once_with("admin@example.test")
    assert "Default user already exists: admin@example.test" in caplog.text


@pytest.mark.asyncio
async def test_create_default_user_provisions_and_disables_mfa(monkeypatch):
    user = SimpleNamespace(email="admin@example.test", id="user-1")
    user_repo = SimpleNamespace(get_by_email=AsyncMock(return_value=None))
    db = SimpleNamespace(commit=AsyncMock())
    provision = AsyncMock(return_value=SimpleNamespace(user=user))
    monkeypatch.setattr(
        main,
        "get_settings",
        lambda: SimpleNamespace(
            default_user_email="admin@example.test",
            default_user_password="secret",
        ),
    )
    monkeypatch.setattr("src.core.database.get_db_context", lambda: _AsyncContext(db))
    monkeypatch.setattr(
        "src.repositories.users.UserRepository",
        lambda session: user_repo,
    )
    monkeypatch.setattr(
        "src.core.security.get_password_hash",
        lambda password: f"hashed:{password}",
    )
    monkeypatch.setattr(
        "src.services.user_provisioning.ensure_user_provisioned",
        provision,
    )

    await main.create_default_user()

    provision.assert_awaited_once_with(
        db=db,
        email="admin@example.test",
        name="Dev Admin",
    )
    assert user.hashed_password == "hashed:secret"
    assert user.mfa_enabled is False
    db.commit.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_value_error_exception_handler_returns_validation_error():
    handler = main.app.exception_handlers[ValueError]
    request = SimpleNamespace(method="GET", url=SimpleNamespace(path="/demo"))

    response = await handler(request, ValueError("bad input"))

    assert response.status_code == 422
    assert b'"error":"validation_error"' in response.body
    assert b'"message":"bad input"' in response.body


@pytest.mark.asyncio
async def test_generic_exception_handler_hides_internal_detail(caplog):
    handler = main.app.exception_handlers[Exception]
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/demo"))

    with caplog.at_level("ERROR", logger="src.main"):
        response = await handler(request, RuntimeError("secret internals"))

    assert response.status_code == 500
    assert b'"error":"internal_error"' in response.body
    assert b"secret internals" not in response.body
    assert "Unhandled exception on POST /demo: secret internals" in caplog.text
