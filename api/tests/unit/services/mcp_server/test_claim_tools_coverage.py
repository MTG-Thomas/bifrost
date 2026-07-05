from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.services.mcp_server.tools import claims


def _context() -> SimpleNamespace:
    return SimpleNamespace(user_email="user@example.test")


@pytest.mark.asyncio
async def test_list_claims_passes_scope_and_formats_count():
    calls = []

    async def call_rest(context, method, path, **kwargs):
        calls.append((context, method, path, kwargs))
        return 200, {"claims": [{"name": "ticket_ids"}, {"name": "assets"}]}

    with patch.object(claims, "call_rest", call_rest):
        result = await claims.list_claims(_context(), scope="global")

    assert result.structured_content["count"] == 2
    assert result.structured_content["claims"][0]["name"] == "ticket_ids"
    assert calls[0][1:] == (
        "GET",
        "/api/claims",
        {"params": {"scope": "global"}},
    )


@pytest.mark.asyncio
async def test_list_claims_reports_http_errors_and_non_dict_body():
    async def failed_call_rest(*_args, **_kwargs):
        return 503, {"detail": "unavailable"}

    with patch.object(claims, "call_rest", failed_call_rest):
        failed = await claims.list_claims(_context())

    assert "list_claims failed: HTTP 503" in failed.structured_content["error"]
    assert failed.structured_content["body"] == {"detail": "unavailable"}

    async def list_body_call_rest(*_args, **_kwargs):
        return 200, ["not", "a", "dict"]

    with patch.object(claims, "call_rest", list_body_call_rest):
        result = await claims.list_claims(_context())

    assert result.structured_content == {"claims": [], "count": 0}


@pytest.mark.asyncio
async def test_get_claim_requires_name_and_wraps_rest_response():
    missing = await claims.get_claim(_context(), "")
    assert missing.structured_content["error"] == claims.NAME_REQUIRED

    async def call_rest(_context, method, path, **kwargs):
        assert method == "GET"
        assert path == "/api/claims/customer_ids"
        assert kwargs == {"params": {"scope": "org"}}
        return 200, {"name": "customer_ids", "type": "list"}

    with patch.object(claims, "call_rest", call_rest):
        result = await claims.get_claim(_context(), "customer_ids", scope="org")

    assert result.structured_content["name"] == "customer_ids"
    assert "Custom claim: customer_ids" in result.content[0].text

    async def missing_call_rest(*_args, **_kwargs):
        return 404, {"detail": "missing"}

    with patch.object(claims, "call_rest", missing_call_rest):
        failed = await claims.get_claim(_context(), "missing")

    assert "get_claim failed: HTTP 404" in failed.structured_content["error"]


@pytest.mark.asyncio
async def test_create_claim_validates_body_and_calls_rest():
    missing_name = await claims.create_claim(_context(), "", {"table": "tickets"})
    missing_query = await claims.create_claim(_context(), "tickets", {})

    assert missing_name.structured_content["error"] == claims.NAME_REQUIRED
    assert missing_query.structured_content["error"] == "query is required"

    assembled = {"name": "tickets", "query": {"table": "tickets"}}

    async def assemble(context, fields, *, is_update):
        assert context.user_email == "user@example.test"
        assert fields["name"] == "tickets"
        assert fields["type"] == "list"
        assert is_update is False
        return assembled

    async def call_rest(_context, method, path, **kwargs):
        assert method == "POST"
        assert path == "/api/claims"
        assert kwargs == {
            "json_body": assembled,
            "params": {"scope": "global"},
        }
        return 201, {"name": "tickets", "id": "claim-1"}

    with (
        patch.object(claims, "_assemble_claim_body", assemble),
        patch.object(claims, "call_rest", call_rest),
    ):
        result = await claims.create_claim(
            _context(),
            "tickets",
            {"table": "tickets"},
            scope="global",
        )

    assert result.structured_content["name"] == "tickets"
    assert "Created custom claim" in result.content[0].text


@pytest.mark.asyncio
async def test_create_claim_reports_assembly_and_http_errors():
    async def bad_assemble(*_args, **_kwargs):
        raise ValueError("bad filter")

    with patch.object(claims, "_assemble_claim_body", bad_assemble):
        invalid = await claims.create_claim(_context(), "tickets", {"bad": True})

    assert "invalid input: bad filter" in invalid.structured_content["error"]
    assert invalid.structured_content["detail"] == "bad filter"

    async def assemble(*_args, **_kwargs):
        return {"name": "tickets"}

    async def failed_call_rest(*_args, **_kwargs):
        return 409, {"detail": "duplicate"}

    with (
        patch.object(claims, "_assemble_claim_body", assemble),
        patch.object(claims, "call_rest", failed_call_rest),
    ):
        failed = await claims.create_claim(_context(), "tickets", {"table": "tickets"})

    assert "create_claim failed: HTTP 409" in failed.structured_content["error"]
    assert failed.structured_content["body"] == {"detail": "duplicate"}


@pytest.mark.asyncio
async def test_update_claim_validates_and_patches_rest():
    missing = await claims.update_claim(_context(), "")
    assert missing.structured_content["error"] == claims.NAME_REQUIRED

    async def assemble(_context, fields, *, is_update):
        assert fields == {"description": "Updated", "type": None, "query": None}
        assert is_update is True
        return {"description": "Updated"}

    async def call_rest(_context, method, path, **kwargs):
        assert method == "PATCH"
        assert path == "/api/claims/tickets"
        assert kwargs == {
            "json_body": {"description": "Updated"},
            "params": {"scope": "org"},
        }
        return 200, {"name": "tickets", "description": "Updated"}

    with (
        patch.object(claims, "_assemble_claim_body", assemble),
        patch.object(claims, "call_rest", call_rest),
    ):
        result = await claims.update_claim(
            _context(),
            "tickets",
            description="Updated",
            scope="org",
        )

    assert result.structured_content["description"] == "Updated"
    assert "Updated custom claim: tickets" in result.content[0].text


@pytest.mark.asyncio
async def test_update_claim_reports_assembly_and_http_errors():
    async def bad_assemble(*_args, **_kwargs):
        raise RuntimeError("cannot resolve table")

    with patch.object(claims, "_assemble_claim_body", bad_assemble):
        invalid = await claims.update_claim(_context(), "tickets", query={"bad": True})

    assert "invalid input: cannot resolve table" in invalid.structured_content["error"]
    assert invalid.structured_content["detail"] == "cannot resolve table"

    async def assemble(*_args, **_kwargs):
        return {"type": "list"}

    async def failed_call_rest(*_args, **_kwargs):
        return 400, {"detail": "bad"}

    with (
        patch.object(claims, "_assemble_claim_body", assemble),
        patch.object(claims, "call_rest", failed_call_rest),
    ):
        failed = await claims.update_claim(_context(), "tickets", type="list")

    assert "update_claim failed: HTTP 400" in failed.structured_content["error"]
    assert failed.structured_content["body"] == {"detail": "bad"}


@pytest.mark.asyncio
async def test_delete_claim_requires_name_and_accepts_success_statuses():
    missing = await claims.delete_claim(_context(), "")
    assert missing.structured_content["error"] == claims.NAME_REQUIRED

    calls = []

    async def call_rest(_context, method, path, **kwargs):
        calls.append((method, path, kwargs))
        return 204, ""

    with patch.object(claims, "call_rest", call_rest):
        result = await claims.delete_claim(_context(), "tickets", scope="global")

    assert result.structured_content == {"deleted": "tickets"}
    assert calls == [
        ("DELETE", "/api/claims/tickets", {"params": {"scope": "global"}})
    ]

    async def failed_call_rest(*_args, **_kwargs):
        return 500, {"detail": "boom"}

    with patch.object(claims, "call_rest", failed_call_rest):
        failed = await claims.delete_claim(_context(), "tickets")

    assert "delete_claim failed: HTTP 500" in failed.structured_content["error"]
