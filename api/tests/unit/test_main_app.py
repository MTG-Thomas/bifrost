import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError, OperationalError

os.environ.setdefault("BIFROST_SECRET_KEY", "test-secret-key-for-main-app-unit-tests")

from src import main
from src.core.request_context import get_request_session_id, get_request_user
from src.services.audit_context import current_actor


pytestmark = pytest.mark.unit


class _WidgetModel(BaseModel):
    count: int


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


class _SessionFactory:
    def __init__(self, contexts):
        self.contexts = list(contexts)

    def __call__(self):
        return self.contexts.pop(0)


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
async def test_app_lifespan_seeds_policy_rules_and_shuts_down(
    monkeypatch,
    caplog,
):
    settings = SimpleNamespace(
        default_user_email="admin@example.test",
        default_user_password="secret",
        environment="unit",
    )
    policy_db = SimpleNamespace(commit=AsyncMock())
    session_factory = _SessionFactory([_AsyncContext(policy_db)])
    policy_service = SimpleNamespace(seed_builtin_admin_bypass=AsyncMock())

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "configure_opentelemetry", Mock())
    monkeypatch.setattr(main, "init_db", AsyncMock())
    monkeypatch.setattr(
        "src.core.entity_change_hook.register_entity_change_hooks",
        Mock(),
    )
    monkeypatch.setattr(main, "register_dynamic_workflow_endpoints", AsyncMock())
    monkeypatch.setattr(main, "create_default_user", AsyncMock())
    monkeypatch.setattr(main, "get_session_factory", lambda: session_factory)
    monkeypatch.setattr(
        "src.services.policy_rule_service.PolicyRuleService",
        lambda db: policy_service,
    )
    monkeypatch.setattr(main.pubsub_manager, "close", AsyncMock())
    monkeypatch.setattr(main, "close_health_check_clients", AsyncMock())
    monkeypatch.setattr(main, "close_db", AsyncMock())

    with caplog.at_level("INFO", logger="src.main"):
        async with main.app_lifespan(SimpleNamespace()):
            pass

    main.configure_opentelemetry.assert_called_once_with("bifrost-api")
    main.init_db.assert_awaited_once_with()
    main.register_dynamic_workflow_endpoints.assert_awaited_once()
    main.create_default_user.assert_awaited_once_with()
    policy_service.seed_builtin_admin_bypass.assert_awaited_once_with()
    policy_db.commit.assert_awaited_once_with()
    main.pubsub_manager.close.assert_awaited_once_with()
    main.close_health_check_clients.assert_awaited_once_with()
    main.close_db.assert_awaited_once_with()
    assert "Built-in policy rules seeded" in caplog.text
    assert "Bifrost API shutdown complete" in caplog.text


@pytest.mark.asyncio
async def test_app_lifespan_logs_optional_startup_failures(monkeypatch, caplog):
    settings = SimpleNamespace(
        default_user_email="",
        default_user_password="",
        environment="unit",
    )
    def failing_session_factory():
        raise RuntimeError("session factory offline")

    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "configure_opentelemetry", Mock())
    monkeypatch.setattr(main, "init_db", AsyncMock())
    monkeypatch.setattr(
        "src.core.entity_change_hook.register_entity_change_hooks",
        Mock(),
    )
    monkeypatch.setattr(main, "register_dynamic_workflow_endpoints", AsyncMock())
    monkeypatch.setattr(main, "get_session_factory", failing_session_factory)
    monkeypatch.setattr(main.pubsub_manager, "close", AsyncMock())
    monkeypatch.setattr(main, "close_health_check_clients", AsyncMock())
    monkeypatch.setattr(main, "close_db", AsyncMock())

    with caplog.at_level("WARNING", logger="src.main"):
        async with main.app_lifespan(SimpleNamespace()):
            pass

    assert "Built-in policy rule seeding failed: session factory offline" in caplog.text
    main.close_db.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_combined_lifespan_enters_mcp_lifespan_when_available(monkeypatch):
    entered = {"app": False}

    @asynccontextmanager
    async def fake_app_lifespan(app):
        entered["app"] = True
        yield

    mcp_context = _AsyncContext()
    mcp_app = SimpleNamespace(lifespan=Mock(return_value=mcp_context))
    monkeypatch.setattr(main, "app_lifespan", fake_app_lifespan)
    monkeypatch.setattr(main, "_get_mcp_asgi_app", lambda: mcp_app)

    async with main.lifespan(SimpleNamespace()):
        assert mcp_context.entered is True

    assert entered["app"] is True
    assert mcp_context.exited is True


@pytest.mark.asyncio
async def test_combined_lifespan_yields_without_mcp_lifespan(monkeypatch):
    yielded = False

    @asynccontextmanager
    async def fake_app_lifespan(app):
        yield

    monkeypatch.setattr(main, "app_lifespan", fake_app_lifespan)
    monkeypatch.setattr(main, "_get_mcp_asgi_app", SimpleNamespace)

    async with main.lifespan(SimpleNamespace()):
        yielded = True

    assert yielded is True


def _add_context_probe_route(captured: dict[str, Any]) -> str:
    path = f"/__unit/context/{uuid4().hex}"

    async def probe():
        user = get_request_user()
        actor = current_actor()
        captured.update(
            {
                "request_user": user,
                "session_id": get_request_session_id(),
                "actor": actor,
            }
        )
        return {"ok": True}

    main.app.add_api_route(path, probe, methods=["GET"])
    main.app.router.routes.insert(0, main.app.router.routes.pop())
    return path


def test_request_context_middleware_sets_user_session_and_audit_actor(
    monkeypatch,
):
    user_id = "12345678-1234-5678-1234-567812345678"
    org_id = "87654321-4321-6789-4321-678987654321"
    monkeypatch.setattr(
        "src.core.security.decode_token",
        lambda token, expected_type: {
            "sub": user_id,
            "org_id": org_id,
            "email": "admin@example.test",
            "name": "Admin User",
        },
    )
    captured: dict[str, Any] = {}
    path = _add_context_probe_route(captured)

    response = TestClient(main.app).get(
        path,
        headers={
            "Authorization": "Bearer access-token",
            "X-Bifrost-Watch-Session": "watch-1",
            "X-Forwarded-For": "203.0.113.10, 10.0.0.1",
            "User-Agent": "unit-agent",
        },
    )

    assert response.status_code == 200
    assert captured["request_user"].user_id == user_id
    assert captured["request_user"].user_name == "Admin User"
    assert captured["session_id"] == "watch-1"
    actor = captured["actor"]
    assert actor.user_id == UUID(user_id)
    assert actor.organization_id == UUID(org_id)
    assert actor.email == "admin@example.test"
    assert actor.name == "Admin User"
    assert actor.ip_address == "203.0.113.10"
    assert actor.user_agent == "unit-agent"
    assert actor.source == "http"
    assert get_request_user() is None
    assert get_request_session_id() is None
    assert current_actor() is None


def test_request_context_middleware_accepts_cookie_token_with_bad_uuid_claims(
    monkeypatch,
):
    monkeypatch.setattr(
        "src.core.security.decode_token",
        lambda token, expected_type: {
            "sub": "not-a-uuid",
            "org_id": "not-an-org-uuid",
            "email": "reader@example.test",
        },
    )
    captured: dict[str, Any] = {}
    path = _add_context_probe_route(captured)

    client = TestClient(main.app)
    client.cookies.set("access_token", "cookie-token")
    response = client.get(path, headers={"User-Agent": "cookie-agent"})

    assert response.status_code == 200
    assert captured["request_user"].user_id == "not-a-uuid"
    assert captured["request_user"].user_name == "reader@example.test"
    actor = captured["actor"]
    assert actor.user_id is None
    assert actor.organization_id is None
    assert actor.email == "reader@example.test"
    assert actor.name is None
    assert actor.user_agent == "cookie-agent"


def test_request_context_middleware_clears_context_after_decode_failure(
    monkeypatch,
):
    def fail_decode(token, expected_type):
        raise RuntimeError("token service unavailable")

    monkeypatch.setattr("src.core.security.decode_token", fail_decode)
    captured: dict[str, Any] = {}
    path = _add_context_probe_route(captured)

    response = TestClient(main.app).get(
        path,
        headers={
            "Authorization": "Bearer broken-token",
            "X-Bifrost-Watch-Session": "watch-failed",
        },
    )

    assert response.status_code == 200
    assert captured["request_user"] is None
    assert captured["session_id"] == "watch-failed"
    assert captured["actor"].user_id is None
    assert get_request_user() is None
    assert get_request_session_id() is None
    assert current_actor() is None


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
            debug=False,
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
    handler = cast(
        Callable[[Any, Exception], Awaitable[Any]],
        main.app.exception_handlers[ValueError],
    )
    request = SimpleNamespace(method="GET", url=SimpleNamespace(path="/demo"))

    response = await handler(request, ValueError("bad input"))

    assert response.status_code == 422
    assert b'"error":"validation_error"' in response.body
    assert b'"message":"bad input"' in response.body


@pytest.mark.asyncio
async def test_request_validation_exception_handler_summarizes_fields():
    handler = cast(
        Callable[[Any, Exception], Awaitable[Any]],
        main.app.exception_handlers[RequestValidationError],
    )
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/widgets"))
    exc = RequestValidationError(
        [
            {
                "loc": ("body", "name"),
                "msg": "Field required",
                "type": "missing",
            },
            {
                "loc": ("query", "limit"),
                "msg": "Input should be greater than 0",
                "type": "greater_than",
            },
        ]
    )

    response = await handler(request, exc)

    assert response.status_code == 422
    assert b'"error":"validation_error"' in response.body
    assert b"name: Field required" in response.body
    assert b"query.limit: Input should be greater than 0" in response.body


@pytest.mark.asyncio
async def test_pydantic_validation_exception_handler_returns_field_details():
    handler = cast(
        Callable[[Any, Exception], Awaitable[Any]],
        main.app.exception_handlers[main.PydanticValidationError],
    )
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/widgets"))
    with pytest.raises(main.PydanticValidationError) as raised:
        _WidgetModel(count="many")

    response = await handler(request, raised.value)

    assert response.status_code == 422
    assert b'"message":"Validation failed"' in response.body
    assert b'"count"' in response.body
    assert b"valid integer" in response.body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("detail", "expected_message"),
    [
        ("UNIQUE constraint failed: widgets.name", "Resource already exists"),
        ("FOREIGN KEY constraint failed", "Referenced resource not found"),
        ("check constraint failed", "Database constraint violation"),
    ],
)
async def test_integrity_exception_handler_classifies_constraint_errors(
    detail,
    expected_message,
    caplog,
):
    handler = cast(
        Callable[[Any, Exception], Awaitable[Any]],
        main.app.exception_handlers[IntegrityError],
    )
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/widgets"))

    with caplog.at_level("WARNING", logger="src.main"):
        response = await handler(
            request,
            IntegrityError("insert widgets", {}, Exception(detail)),
        )

    assert response.status_code == 409
    assert b'"error":"conflict"' in response.body
    assert expected_message.encode() in response.body
    assert "IntegrityError" in caplog.text


@pytest.mark.asyncio
async def test_no_result_exception_handler_returns_not_found():
    handler = cast(
        Callable[[Any, Exception], Awaitable[Any]],
        main.app.exception_handlers[main.NoResultFound],
    )
    request = SimpleNamespace(method="GET", url=SimpleNamespace(path="/widgets/1"))

    response = await handler(request, main.NoResultFound("missing"))

    assert response.status_code == 404
    assert b'"error":"not_found"' in response.body
    assert b"Resource not found" in response.body


@pytest.mark.asyncio
async def test_timeout_exception_handler_returns_gateway_timeout(caplog):
    handler = cast(
        Callable[[Any, Exception], Awaitable[Any]],
        main.app.exception_handlers[TimeoutError],
    )
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/slow"))

    with caplog.at_level("WARNING", logger="src.main"):
        response = await handler(request, TimeoutError("too slow"))

    assert response.status_code == 504
    assert b'"error":"timeout"' in response.body
    assert "Timeout error on POST /slow" in caplog.text


@pytest.mark.asyncio
async def test_operational_exception_handler_hides_database_details(caplog):
    handler = cast(
        Callable[[Any, Exception], Awaitable[Any]],
        main.app.exception_handlers[OperationalError],
    )
    request = SimpleNamespace(method="GET", url=SimpleNamespace(path="/widgets"))

    with caplog.at_level("ERROR", logger="src.main"):
        response = await handler(
            request,
            OperationalError("select widgets", {}, Exception("connection refused")),
        )

    assert response.status_code == 503
    assert b'"error":"service_unavailable"' in response.body
    assert b"connection refused" not in response.body
    assert "Database operational error" in caplog.text


@pytest.mark.asyncio
async def test_generic_exception_handler_hides_internal_detail(caplog):
    handler = cast(
        Callable[[Any, Exception], Awaitable[Any]],
        main.app.exception_handlers[Exception],
    )
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/demo"))

    with caplog.at_level("ERROR", logger="src.main"):
        response = await handler(request, RuntimeError("secret internals"))

    assert response.status_code == 500
    assert b'"error":"internal_error"' in response.body
    assert b"secret internals" not in response.body
    assert "Unhandled exception on POST /demo: secret internals" in caplog.text
