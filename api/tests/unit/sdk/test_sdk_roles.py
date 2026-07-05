"""
Unit tests for Bifrost Roles SDK module.

Tests platform mode (inside workflows) operations.
Uses mocked dependencies for fast, isolated testing.
"""

import pytest
import importlib
from unittest.mock import MagicMock
from uuid import uuid4

roles_module = importlib.import_module("bifrost.roles")
knowledge_module = importlib.import_module("bifrost.knowledge")
roles = roles_module.roles
knowledge = knowledge_module.knowledge



@pytest.fixture
def test_org_id():
    """Return a test organization ID."""
    return str(uuid4())


@pytest.fixture
def test_context(test_org_id):
    """Create execution context for platform mode testing."""
    from src.sdk.context import ExecutionContext, Organization

    org = Organization(id=test_org_id, name="Test Org", is_active=True)
    return ExecutionContext(
        user_id="test-user",
        email="test@example.com",
        name="Test User",
        scope=test_org_id,
        organization=org,
        is_platform_admin=False,
        is_function_key=False,
        execution_id="test-exec-123",
    )


@pytest.fixture
def admin_context(test_org_id):
    """Create platform admin execution context."""
    from src.sdk.context import ExecutionContext, Organization

    org = Organization(id=test_org_id, name="Test Org", is_active=True)
    return ExecutionContext(
        user_id="admin-user",
        email="admin@example.com",
        name="Admin User",
        scope=test_org_id,
        organization=org,
        is_platform_admin=True,
        is_function_key=False,
        execution_id="admin-exec-456",
    )


@pytest.fixture
def mock_role():
    """Create a mock role database object."""
    mock = MagicMock()
    mock.id = uuid4()
    mock.organization_id = uuid4()
    mock.name = "test_role"
    mock.display_name = "Test Role"
    mock.description = "A test role"
    mock.permissions = ["read:workflows", "execute:workflows"]
    mock.is_system_role = False
    mock.created_at = "2025-01-01T00:00:00"
    mock.updated_at = "2025-01-01T00:00:00"
    return mock


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    @property
    def is_success(self):
        return 200 <= self.status_code < 300

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def get(self, path, **kwargs):
        self.calls.append(("GET", path, kwargs))
        return self.responses.pop(0)

    async def post(self, path, **kwargs):
        self.calls.append(("POST", path, kwargs))
        return self.responses.pop(0)

    async def patch(self, path, **kwargs):
        self.calls.append(("PATCH", path, kwargs))
        return self.responses.pop(0)

    async def delete(self, path, **kwargs):
        self.calls.append(("DELETE", path, kwargs))
        return self.responses.pop(0)


def _role_payload(**overrides):
    payload = {
        "id": "role-1",
        "name": "Technician",
        "description": "Can work tickets",
        "permissions": {},
        "is_active": True,
        "is_system": False,
        "created_at": None,
        "updated_at": None,
    }
    payload.update(overrides)
    return payload


def _knowledge_payload(**overrides):
    payload = {
        "id": "doc-1",
        "namespace": "docs",
        "key": "intro",
        "content": "Hello",
        "metadata": {"source": "unit"},
        "score": 0.9,
        "organization_id": None,
        "created_at": None,
        "updated_at": None,
    }
    payload.update(overrides)
    return payload


def _namespace_payload(**overrides):
    payload = {
        "namespace": "docs",
        "document_count": 2,
        "scopes": {"total": 2},
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_roles_sdk_crud_and_assignment_http_paths(monkeypatch):
    client = _Client(
        [
            _Response(_role_payload(id="role-new")),
            _Response([_role_payload(id="role-1"), _role_payload(id="role-2")]),
            _Response(_role_payload(description="Updated")),
            _Response({}),
            _Response({"user_ids": ["u1", "u2"]}),
            _Response({"form_ids": ["f1"]}),
            _Response({}),
            _Response({}),
        ]
    )
    monkeypatch.setattr(roles_module, "get_client", lambda: client)

    created = await roles.create("Technician", "Can work tickets")
    listed = await roles.list()
    updated = await roles.update("role-1", description="Updated")
    await roles.delete("role-1")
    users = await roles.list_users("role-1")
    forms = await roles.list_forms("role-1")
    await roles.assign_users("role-1", ["u1"])
    await roles.assign_forms("role-1", ["f1"])

    assert created.id == "role-new"
    assert len(listed) == 2
    assert updated.description == "Updated"
    assert users == ["u1", "u2"]
    assert forms == ["f1"]
    assert client.calls == [
        ("POST", "/api/roles", {"json": {"name": "Technician", "description": "Can work tickets", "is_active": True}}),
        ("GET", "/api/roles", {}),
        ("PATCH", "/api/roles/role-1", {"json": {"description": "Updated"}}),
        ("DELETE", "/api/roles/role-1", {}),
        ("GET", "/api/roles/role-1/users", {}),
        ("GET", "/api/roles/role-1/forms", {}),
        ("POST", "/api/roles/role-1/users", {"json": {"user_ids": ["u1"]}}),
        ("POST", "/api/roles/role-1/forms", {"json": {"form_ids": ["f1"]}}),
    ]


@pytest.mark.asyncio
async def test_roles_sdk_raises_value_error_on_not_found(monkeypatch):
    for method, args in [
        (roles.get, ("missing",)),
        (roles.update, ("missing",)),
        (roles.delete, ("missing",)),
        (roles.list_users, ("missing",)),
        (roles.list_forms, ("missing",)),
        (roles.assign_users, ("missing", ["u1"])),
        (roles.assign_forms, ("missing", ["f1"])),
    ]:
        client = _Client([_Response({}, status_code=404)])
        monkeypatch.setattr(roles_module, "get_client", lambda: client)
        with pytest.raises(ValueError, match="Role not found"):
            await method(*args)


@pytest.mark.asyncio
async def test_knowledge_sdk_store_search_delete_and_list_payloads(monkeypatch):
    client = _Client(
        [
            _Response({"id": "doc-1"}),
            _Response({"ids": ["doc-1", "doc-2"]}),
            _Response([_knowledge_payload(id="doc-1")]),
            _Response({"deleted": True}),
            _Response({"deleted_count": 3}),
            _Response([_namespace_payload()]),
        ]
    )
    monkeypatch.setattr(knowledge_module, "get_client", lambda: client)
    monkeypatch.setattr(knowledge_module, "resolve_scope", lambda scope: f"scope:{scope}" if scope else None)

    doc_id = await knowledge.store("Hello", namespace="docs", key="intro", metadata={"source": "unit"}, scope="org")
    ids = await knowledge.store_many([{"content": "A"}], namespace="docs", scope="org", timeout=10)
    results = await knowledge.search(
        "hello",
        namespace="docs",
        limit=2,
        min_score=0.5,
        metadata_filter={"source": "unit"},
        scope="org",
        fallback=False,
    )
    deleted = await knowledge.delete("intro", namespace="docs", scope="org")
    deleted_count = await knowledge.delete_namespace("docs", scope="org")
    namespaces = await knowledge.list_namespaces(scope="org", include_global=False)

    assert doc_id == "doc-1"
    assert ids == ["doc-1", "doc-2"]
    assert results[0].id == "doc-1"
    assert deleted is True
    assert deleted_count == 3
    assert namespaces[0].namespace == "docs"
    assert client.calls[0] == (
        "POST",
        "/api/sdk/knowledge/store",
        {"json": {"content": "Hello", "namespace": "docs", "key": "intro", "metadata": {"source": "unit"}, "scope": "scope:org"}},
    )
    assert client.calls[1][2]["timeout"] == 10
    assert client.calls[2][2]["json"]["namespace"] == ["docs"]
    assert client.calls[2][2]["json"]["fallback"] is False
    assert client.calls[4] == (
        "DELETE",
        "/api/sdk/knowledge/namespace/docs",
        {"params": {"scope": "scope:org"}},
    )
    assert client.calls[5][2]["params"] == {"include_global": False, "scope": "scope:org"}


@pytest.mark.asyncio
async def test_knowledge_get_returns_none_on_404_and_model_on_success(monkeypatch):
    client = _Client([
        _Response({}, status_code=404),
        _Response(_knowledge_payload(id="doc-2", key="second")),
    ])
    monkeypatch.setattr(knowledge_module, "get_client", lambda: client)
    monkeypatch.setattr(knowledge_module, "resolve_scope", lambda scope: scope)

    missing = await knowledge.get("missing", namespace="docs", scope="global")
    found = await knowledge.get("second", namespace="docs")

    assert missing is None
    assert found is not None
    assert found.key == "second"
    assert client.calls[0] == (
        "GET",
        "/api/sdk/knowledge/get",
        {"params": {"key": "missing", "namespace": "docs", "scope": "global"}},
    )
    assert client.calls[1] == (
        "GET",
        "/api/sdk/knowledge/get",
        {"params": {"key": "second", "namespace": "docs"}},
    )
