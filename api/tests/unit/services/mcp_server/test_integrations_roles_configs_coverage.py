from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import configs, integrations, roles


def _context(**overrides):
    ctx = SimpleNamespace(
        is_platform_admin=False,
        org_id=str(uuid4()),
        user_id=str(uuid4()),
        user_email="operator@example.test",
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def _content_text(result) -> str:
    content = result.content
    if isinstance(content, list):
        return "\n".join(getattr(item, "text", str(item)) for item in content)
    return str(content)


def _fake_rest_client(http=None):
    @asynccontextmanager
    async def fake_client(_context):
        yield http or AsyncMock()

    return fake_client


def _resolver(return_by_kind=None, *, error_by_kind=None):
    return_by_kind = return_by_kind or {}
    error_by_kind = error_by_kind or {}

    class Resolver:
        def __init__(self, _http):
            pass

        async def resolve(self, kind, ref):
            if kind in error_by_kind:
                raise error_by_kind[kind]
            return return_by_kind.get(kind, f"{kind}-{ref}-uuid")

    return Resolver


def _fake_tool_db(db):
    @asynccontextmanager
    async def fake_get_tool_db(_context):
        yield db

    return fake_get_tool_db


@pytest.mark.asyncio
async def test_list_roles_shapes_list_response_and_http_errors():
    ctx = _context()
    call_rest = AsyncMock(return_value=(200, [{"name": "Admin"}, {"name": "Ops"}]))

    with patch.object(roles, "call_rest", call_rest):
        result = await roles.list_roles(ctx)

    call_rest.assert_awaited_once_with(ctx, "GET", "/api/roles")
    assert result.structured_content == {
        "roles": [{"name": "Admin"}, {"name": "Ops"}],
        "count": 2,
    }
    assert "Found 2 role" in _content_text(result)

    with patch.object(roles, "call_rest", AsyncMock(return_value=(503, "down"))):
        failed = await roles.list_roles(ctx)

    assert "list_roles failed: HTTP 503" in failed.structured_content["error"]
    assert failed.structured_content["body"] == "down"


@pytest.mark.asyncio
async def test_role_detail_update_and_delete_cover_validation_resolution_and_rest():
    missing = await roles.get_role(_context(), "")
    assert missing.structured_content["error"] == "role_ref is required"

    with (
        patch.object(roles, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver(error_by_kind={"role": RuntimeError("no access")})),
    ):
        unresolved = await roles.update_role(_context(), "restricted")

    assert "could not resolve role 'restricted'" in unresolved.structured_content["error"]
    assert unresolved.structured_content["detail"] == "no access"

    async def assemble(_context, fields, *, is_update):
        assert is_update is True
        assert fields == {"name": "Operators", "description": None, "permissions": {"jobs": ["read"]}}
        return {"name": "Operators", "permissions": {"jobs": ["read"]}}

    ctx = _context()
    call_rest = AsyncMock(return_value=(200, {"id": "role-uuid", "name": "Operators"}))
    with (
        patch.object(roles, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"role": "role-uuid"})),
        patch.object(roles, "_assemble_role_body", assemble),
        patch.object(roles, "call_rest", call_rest),
    ):
        updated = await roles.update_role(ctx, "operators", name="Operators", permissions={"jobs": ["read"]})

    call_rest.assert_awaited_once_with(
        ctx,
        "PATCH",
        "/api/roles/role-uuid",
        json_body={"name": "Operators", "permissions": {"jobs": ["read"]}},
    )
    assert updated.structured_content["name"] == "Operators"

    delete_call = AsyncMock(return_value=(204, ""))
    with (
        patch.object(roles, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"role": "role-uuid"})),
        patch.object(roles, "call_rest", delete_call),
    ):
        deleted = await roles.delete_role(ctx, "operators")

    assert deleted.structured_content == {"deleted": "role-uuid"}
    delete_call.assert_awaited_once_with(ctx, "DELETE", "/api/roles/role-uuid")


@pytest.mark.asyncio
async def test_create_role_reports_assembly_and_http_errors_then_success():
    missing = await roles.create_role(_context(), "")
    assert missing.structured_content["error"] == "name is required"

    async def bad_assemble(*_args, **_kwargs):
        raise ValueError("bad permission")

    with patch.object(roles, "_assemble_role_body", bad_assemble):
        invalid = await roles.create_role(_context(), "Ops", permissions={"bad": True})

    assert "invalid input: bad permission" in invalid.structured_content["error"]
    assert invalid.structured_content["detail"] == "bad permission"

    async def assemble(_context, fields, *, is_update):
        assert is_update is False
        return {key: value for key, value in fields.items() if value is not None}

    with (
        patch.object(roles, "_assemble_role_body", assemble),
        patch.object(roles, "call_rest", AsyncMock(return_value=(409, {"detail": "duplicate"}))),
    ):
        failed = await roles.create_role(_context(), "Ops")

    assert "create_role failed: HTTP 409" in failed.structured_content["error"]
    assert failed.structured_content["body"] == {"detail": "duplicate"}

    call_rest = AsyncMock(return_value=(201, {"id": "role-1", "name": "Ops"}))
    with (
        patch.object(roles, "_assemble_role_body", assemble),
        patch.object(roles, "call_rest", call_rest),
    ):
        result = await roles.create_role(_context(), "Ops", description="Operations")

    assert result.structured_content["id"] == "role-1"
    assert "Created role: Ops" in _content_text(result)


@pytest.mark.asyncio
async def test_configs_create_update_delete_resolve_refs_and_shape_bodies():
    missing = await configs.create_config(_context(), "", "value")
    assert missing.structured_content["error"] == "key is required"

    ctx = _context()
    create_call = AsyncMock(return_value=(201, {"key": "api.token", "type": "secret"}))
    with (
        patch.object(configs, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"org": "org-uuid"})),
        patch.object(configs, "call_rest", create_call),
    ):
        created = await configs.create_config(
            ctx,
            "api.token",
            "secret-value",
            config_type="secret",
            description="API token",
            organization_id="Midtown",
        )

    create_call.assert_awaited_once_with(
        ctx,
        "POST",
        "/api/config",
        json_body={
            "key": "api.token",
            "value": "secret-value",
            "type": "secret",
            "description": "API token",
            "organization_id": "org-uuid",
        },
    )
    assert created.structured_content["key"] == "api.token"

    update_call = AsyncMock(return_value=(200, "ok"))
    with (
        patch.object(configs, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"config": "config-uuid"})),
        patch.object(configs, "call_rest", update_call),
    ):
        updated = await configs.update_config(ctx, "api.token", description="rotated")

    update_call.assert_awaited_once_with(
        ctx,
        "PUT",
        "/api/config/config-uuid",
        json_body={"description": "rotated"},
    )
    assert updated.structured_content == {"body": "ok"}

    delete_call = AsyncMock(return_value=(500, {"detail": "protected"}))
    with (
        patch.object(configs, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"config": "config-uuid"})),
        patch.object(configs, "call_rest", delete_call),
    ):
        failed = await configs.delete_config(ctx, "api.token")

    assert "delete_config failed: HTTP 500" in failed.structured_content["error"]
    assert failed.structured_content["body"] == {"detail": "protected"}


@pytest.mark.asyncio
async def test_config_get_handles_resolution_list_lookup_and_inaccessible_row():
    missing = await configs.get_config(_context(), "")
    assert missing.structured_content["error"] == "config_ref is required"

    ctx = _context()
    with (
        patch.object(configs, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver(error_by_kind={"config": RuntimeError("hidden")})),
    ):
        unresolved = await configs.get_config(ctx, "secret.key")

    assert "could not resolve config 'secret.key'" in unresolved.structured_content["error"]
    assert unresolved.structured_content["detail"] == "hidden"

    with (
        patch.object(configs, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"config": "config-uuid"})),
        patch.object(
            configs,
            "call_rest",
            AsyncMock(return_value=(200, [{"id": "config-uuid", "key": "secret.key"}])),
        ),
    ):
        result = await configs.get_config(ctx, "secret.key")

    assert result.structured_content == {"id": "config-uuid", "key": "secret.key"}

    with (
        patch.object(configs, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"config": "config-uuid"})),
        patch.object(configs, "call_rest", AsyncMock(return_value=(200, []))),
    ):
        inaccessible = await configs.get_config(ctx, "secret.key")

    assert "resolved to config-uuid" in inaccessible.structured_content["error"]


@pytest.mark.asyncio
async def test_integrations_wrappers_validate_resolve_and_forward_rest_calls():
    assert (
        await integrations.create_integration(_context(), "")
    ).structured_content["error"] == "name is required"

    ctx = _context()

    async def assemble(_context, fields, *, model_name):
        assert model_name == "IntegrationCreate"
        assert fields["name"] == "Halo"
        return {"name": "Halo", "config_schema": fields["config_schema"]}

    create_call = AsyncMock(return_value=(201, {"id": "integration-uuid", "name": "Halo"}))
    with (
        patch.object(integrations, "_assemble_integration_body", assemble),
        patch.object(integrations, "call_rest", create_call),
    ):
        created = await integrations.create_integration(ctx, "Halo", config_schema=[{"key": "url"}])

    create_call.assert_awaited_once_with(
        ctx,
        "POST",
        "/api/integrations",
        json_body={"name": "Halo", "config_schema": [{"key": "url"}]},
    )
    assert created.structured_content["name"] == "Halo"

    with (
        patch.object(integrations, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver(error_by_kind={"integration": RuntimeError("ambiguous")})),
    ):
        unresolved = await integrations.get_integration(ctx, "Halo")

    assert "could not resolve integration 'Halo'" in unresolved.structured_content["error"]
    assert unresolved.structured_content["detail"] == "ambiguous"

    get_call = AsyncMock(return_value=(200, "raw"))
    with (
        patch.object(integrations, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"integration": "integration-uuid"})),
        patch.object(integrations, "call_rest", get_call),
    ):
        fetched = await integrations.get_integration(ctx, "Halo")

    assert fetched.structured_content == {"body": "raw"}
    assert "Integration: integration-uuid" in _content_text(fetched)


@pytest.mark.asyncio
async def test_integration_mapping_validation_uuid_and_http_error_paths():
    ctx = _context()
    assert (
        await integrations.add_integration_mapping(ctx, "", "org", "entity")
    ).structured_content["error"] == "integration_ref is required"
    assert (
        await integrations.add_integration_mapping(ctx, "Halo", "", "entity")
    ).structured_content["error"] == "organization is required"
    assert (
        await integrations.update_integration_mapping(ctx, "Halo", "not-a-uuid")
    ).structured_content["error"] == "mapping_id must be a UUID, got 'not-a-uuid'"

    async def bad_assemble(*_args, **_kwargs):
        raise ValueError("org not found")

    with (
        patch.object(integrations, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"integration": "integration-uuid"})),
        patch.object(integrations, "_assemble_integration_body", bad_assemble),
    ):
        invalid = await integrations.add_integration_mapping(ctx, "Halo", "Missing", "123")

    assert "invalid input: org not found" in invalid.structured_content["error"]
    assert invalid.structured_content["detail"] == "org not found"

    mapping_id = str(uuid4())
    update_call = AsyncMock(return_value=(404, {"detail": "missing"}))
    with (
        patch.object(integrations, "rest_client", _fake_rest_client()),
        patch("bifrost.refs.RefResolver", _resolver({"integration": "integration-uuid"})),
        patch.object(integrations, "_assemble_integration_body", AsyncMock(return_value={"entity_name": "Customer"})),
        patch.object(integrations, "call_rest", update_call),
    ):
        failed = await integrations.update_integration_mapping(
            ctx,
            "Halo",
            mapping_id,
            entity_name="Customer",
        )

    assert "update_integration_mapping failed: HTTP 404" in failed.structured_content["error"]
    assert failed.structured_content["body"] == {"detail": "missing"}


@pytest.mark.asyncio
async def test_list_integrations_uses_org_scope_and_shapes_database_rows():
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [
        SimpleNamespace(name="Halo", has_oauth_config=True, entity_id_name="Company"),
        SimpleNamespace(name="NinjaOne", has_oauth_config=False, entity_id_name=None),
    ]
    db.execute.return_value = result

    with patch.object(integrations, "get_tool_db", _fake_tool_db(db)):
        listed = await integrations.list_integrations(_context(org_id="org-uuid"))

    assert listed.structured_content == {
        "integrations": [
            {"name": "Halo", "has_oauth": True, "entity_id_name": "Company"},
            {"name": "NinjaOne", "has_oauth": False, "entity_id_name": None},
        ],
        "count": 2,
    }
    assert "Found 2 integration" in _content_text(listed)

    class BrokenDb:
        async def execute(self, *_args, **_kwargs):
            raise RuntimeError("db down")

    with patch.object(integrations, "get_tool_db", _fake_tool_db(BrokenDb())):
        failed = await integrations.list_integrations(_context())

    assert "Error listing integrations" in failed.structured_content["error"]
