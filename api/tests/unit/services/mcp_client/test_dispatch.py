from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.mcp_client import dispatch
from src.services.mcp_client.auth_resolution import ResolutionPath
from src.services.mcp_client.errors import NeedsReauthError, ToolDispatchError


class TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text

    def model_dump(self) -> dict[str, str]:
        return {"type": "text", "text": self.text}


class _FakeClientContext:
    def __init__(self, client) -> None:
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return None


def test_looks_like_auth_error_checks_exception_chain() -> None:
    root = RuntimeError("vendor said invalid_token")
    wrapped = RuntimeError("protocol failure")
    wrapped.__cause__ = root

    assert dispatch._looks_like_auth_error(wrapped)
    assert dispatch._looks_like_auth_error(RuntimeError("HTTP 403 Forbidden"))
    assert not dispatch._looks_like_auth_error(RuntimeError("timeout"))


def test_normalize_call_tool_result_preserves_structured_content() -> None:
    result = SimpleNamespace(
        content=[TextBlock("hello"), object()],
        structured_content={"answer": 42},
        is_error=False,
    )

    assert dispatch._normalize_call_tool_result(result) == {
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "unknown", "value": str(result.content[1])},
        ],
        "structured_content": {"answer": 42},
        "is_error": False,
    }


def test_enforce_result_size_cap_passes_small_payload_through() -> None:
    envelope = {
        "content": [{"type": "text", "text": "small"}],
        "structured_content": {"ok": True},
        "is_error": False,
        "_resolution_path": "user_token",
    }

    assert dispatch._enforce_result_size_cap(envelope, "lookup", uuid4()) is envelope


def test_enforce_result_size_cap_replaces_oversized_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(dispatch, "_MAX_TOOL_RESULT_BYTES", 20)
    connection_id = uuid4()

    result = dispatch._enforce_result_size_cap(
        {
            "content": [{"type": "text", "text": "x" * 100}],
            "structured_content": {"rows": ["x" * 100]},
            "is_error": False,
            "_resolution_path": "service_fallback_chat",
        },
        "list_tickets",
        connection_id,
    )

    assert result["is_error"] is True
    assert result["_resolution_path"] == "service_fallback_chat"
    assert result["structured_content"]["_bifrost_truncated"] is True
    assert result["structured_content"]["tool_name"] == "list_tickets"
    assert result["structured_content"]["cap_bytes"] == 20
    assert result["content"][0]["type"] == "text"
    assert "returned" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_call_remote_preserves_raw_call_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(id=uuid4())
    raw_result = SimpleNamespace(
        content=[TextBlock("remote")],
        structured_content={"ok": True},
        is_error=True,
    )

    class FakeClient:
        async def call_tool_mcp(self, name, arguments):
            assert name == "lookup"
            assert arguments == {"id": 7}
            return raw_result

    monkeypatch.setattr(
        dispatch.mcp_client_session,
        "open_client",
        lambda connection_arg, token: _FakeClientContext(FakeClient()),
    )

    result = await dispatch._call_remote(
        connection,
        "access-token",
        "lookup",
        {"id": 7},
    )

    assert result is raw_result


@pytest.mark.asyncio
async def test_invoke_rejects_missing_or_disabled_catalog_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(id=uuid4())

    async def missing_tool(connection_id, tool_name, db):
        return None

    monkeypatch.setattr(dispatch, "_load_tool", missing_tool)

    with pytest.raises(ToolDispatchError, match="not found in catalog"):
        await dispatch.invoke(connection, "missing", {}, uuid4(), SimpleNamespace())

    async def disabled_tool(connection_id, tool_name, db):
        return SimpleNamespace(enabled=False, disabled_reason="admin disabled")

    monkeypatch.setattr(dispatch, "_load_tool", disabled_tool)

    with pytest.raises(ToolDispatchError, match="admin disabled"):
        await dispatch.invoke(connection, "disabled", {}, uuid4(), SimpleNamespace())


@pytest.mark.asyncio
async def test_invoke_resolves_token_calls_remote_and_adds_resolution_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(id=uuid4())
    caller_user_id = uuid4()
    db = SimpleNamespace()

    async def enabled_tool(connection_id, tool_name, db_arg):
        return SimpleNamespace(enabled=True)

    async def resolve_token(connection_arg, user_id_arg, db_arg):
        assert connection_arg is connection
        assert user_id_arg == caller_user_id
        assert db_arg is db
        return "access-token", ResolutionPath.SERVICE_FALLBACK_CHAT

    async def call_remote(connection_arg, token, tool_name, arguments):
        assert token == "access-token"
        assert tool_name == "lookup"
        assert arguments == {"id": 123}
        return SimpleNamespace(
            content=[TextBlock("ok")],
            structured_content={"ok": True},
            is_error=False,
        )

    monkeypatch.setattr(dispatch, "_load_tool", enabled_tool)
    monkeypatch.setattr(dispatch, "resolve_token", resolve_token)
    monkeypatch.setattr(dispatch, "_call_remote", call_remote)

    result = await dispatch.invoke(connection, "lookup", {"id": 123}, caller_user_id, db)

    assert result == {
        "content": [{"type": "text", "text": "ok"}],
        "structured_content": {"ok": True},
        "is_error": False,
        "_resolution_path": "service_fallback_chat",
    }


@pytest.mark.asyncio
async def test_invoke_wraps_non_auth_remote_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = SimpleNamespace(id=uuid4())

    async def enabled_tool(connection_id, tool_name, db):
        return SimpleNamespace(enabled=True)

    async def resolve_token(connection_arg, user_id_arg, db_arg):
        return "token", ResolutionPath.USER_TOKEN

    async def call_remote(connection_arg, token, tool_name, arguments):
        raise RuntimeError("network down")

    monkeypatch.setattr(dispatch, "_load_tool", enabled_tool)
    monkeypatch.setattr(dispatch, "resolve_token", resolve_token)
    monkeypatch.setattr(dispatch, "_call_remote", call_remote)

    with pytest.raises(ToolDispatchError, match="network down"):
        await dispatch.invoke(connection, "lookup", {}, uuid4(), SimpleNamespace())


@pytest.mark.asyncio
async def test_invoke_auth_retry_user_token_raises_needs_reauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(id=uuid4())
    db = SimpleNamespace(refresh_calls=0)

    async def refresh(obj):
        assert obj is connection
        db.refresh_calls += 1

    db.refresh = refresh

    async def enabled_tool(connection_id, tool_name, db_arg):
        return SimpleNamespace(enabled=True)

    async def resolve_token(connection_arg, user_id_arg, db_arg):
        return "token", ResolutionPath.USER_TOKEN

    async def call_remote(connection_arg, token, tool_name, arguments):
        raise RuntimeError("401 unauthorized")

    monkeypatch.setattr(dispatch, "_load_tool", enabled_tool)
    monkeypatch.setattr(dispatch, "resolve_token", resolve_token)
    monkeypatch.setattr(dispatch, "_call_remote", call_remote)

    with pytest.raises(NeedsReauthError) as exc_info:
        await dispatch.invoke(connection, "lookup", {}, uuid4(), db)

    assert exc_info.value.reauth_url == f"/me/connections/{connection.id}/connect"
    assert exc_info.value.connection_id == connection.id
    assert exc_info.value.tool_name == "lookup"
    assert db.refresh_calls == 1


@pytest.mark.asyncio
async def test_invoke_auth_retry_service_path_wraps_retry_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = SimpleNamespace(id=uuid4())
    db = SimpleNamespace()

    async def refresh(obj):
        return None

    db.refresh = refresh

    async def enabled_tool(connection_id, tool_name, db_arg):
        return SimpleNamespace(enabled=True)

    async def resolve_token(connection_arg, user_id_arg, db_arg):
        return "token", ResolutionPath.SERVICE_FALLBACK_AUTONOMOUS

    async def call_remote(connection_arg, token, tool_name, arguments):
        raise RuntimeError("403 forbidden")

    monkeypatch.setattr(dispatch, "_load_tool", enabled_tool)
    monkeypatch.setattr(dispatch, "resolve_token", resolve_token)
    monkeypatch.setattr(dispatch, "_call_remote", call_remote)

    with pytest.raises(ToolDispatchError, match="failed after retry"):
        await dispatch.invoke(connection, "lookup", {}, None, db)
