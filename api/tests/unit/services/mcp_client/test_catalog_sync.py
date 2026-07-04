from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.services.mcp_client import catalog_sync
from src.services.mcp_client.errors import MisconfigError, ToolDispatchError


class _SchemaModel:
    def model_dump(self) -> dict[str, object]:
        return {"type": "object", "properties": {"id": {"type": "string"}}}


class _FakeScalars:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[object] | None = None, scalar: object | None = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def scalar_one_or_none(self) -> object | None:
        return self._scalar

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeDb:
    def __init__(self, results: list[_FakeResult]) -> None:
        self._results = list(results)
        self.added: list[object] = []
        self.commits = 0

    async def execute(self, statement) -> _FakeResult:
        assert statement is not None
        return self._results.pop(0)

    def add(self, row: object) -> None:
        self.added.append(row)

    async def commit(self) -> None:
        self.commits += 1


class _FakeSession:
    def __init__(self, tools: list[object] | None = None, error: Exception | None = None) -> None:
        self._tools = tools or []
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def list_tools(self):
        if self._error is not None:
            raise self._error
        return SimpleNamespace(tools=self._tools)


def _connection() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), service_oauth_token=SimpleNamespace(id=uuid4()))


@pytest.mark.asyncio
async def test_resolve_service_token_rejects_missing_service_token() -> None:
    connection = SimpleNamespace(id=uuid4(), service_oauth_token=None)

    with pytest.raises(MisconfigError, match="none configured"):
        await catalog_sync._resolve_service_token_for_sync(connection, SimpleNamespace())


@pytest.mark.asyncio
async def test_resolve_service_token_rejects_missing_provider() -> None:
    token = SimpleNamespace(id=uuid4(), provider_id=uuid4())
    connection = SimpleNamespace(id=uuid4(), service_oauth_token=token)
    db = _FakeDb([_FakeResult(scalar=None)])

    with pytest.raises(MisconfigError, match="has no provider row"):
        await catalog_sync._resolve_service_token_for_sync(connection, db)


@pytest.mark.asyncio
async def test_resolve_service_token_refreshes_stale_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = SimpleNamespace(id=uuid4(), provider_id=uuid4())
    provider = SimpleNamespace(id=token.provider_id)
    connection = SimpleNamespace(id=uuid4(), service_oauth_token=token)
    db = _FakeDb([_FakeResult(scalar=provider)])

    monkeypatch.setattr(catalog_sync, "_is_token_fresh", lambda token_arg: False)

    async def refresh(token_arg, provider_arg, db_arg):
        assert token_arg is token
        assert provider_arg is provider
        assert db_arg is db
        return True

    monkeypatch.setattr(catalog_sync, "_refresh_token_in_place", refresh)
    monkeypatch.setattr(catalog_sync, "_decode_access_token", lambda token_arg: "decoded")

    assert await catalog_sync._resolve_service_token_for_sync(connection, db) == "decoded"


@pytest.mark.asyncio
async def test_resolve_service_token_raises_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = SimpleNamespace(id=uuid4(), provider_id=uuid4())
    connection = SimpleNamespace(id=uuid4(), service_oauth_token=token)
    db = _FakeDb([_FakeResult(scalar=SimpleNamespace(id=token.provider_id))])

    monkeypatch.setattr(catalog_sync, "_is_token_fresh", lambda token_arg: False)

    async def refresh(token_arg, provider_arg, db_arg):
        return False

    monkeypatch.setattr(catalog_sync, "_refresh_token_in_place", refresh)

    with pytest.raises(MisconfigError, match="expired and refresh failed"):
        await catalog_sync._resolve_service_token_for_sync(connection, db)


@pytest.mark.asyncio
async def test_sync_catalog_upserts_reenables_and_marks_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()
    restored = SimpleNamespace(
        tool_name="restored",
        tool_schema={},
        enabled=False,
        disabled_reason="Removed from server catalog at yesterday",
        last_seen_at=None,
    )
    manual_disabled = SimpleNamespace(
        tool_name="manual",
        tool_schema={},
        enabled=False,
        disabled_reason="admin disabled",
        last_seen_at=None,
    )
    stale_enabled = SimpleNamespace(
        tool_name="missing",
        tool_schema={},
        enabled=True,
        disabled_reason=None,
        last_seen_at=None,
    )
    final_rows = [restored, manual_disabled, stale_enabled]
    db = _FakeDb([
        _FakeResult(rows=[restored, manual_disabled, stale_enabled]),
        _FakeResult(rows=final_rows),
    ])

    async def resolve(connection_arg, db_arg):
        assert connection_arg is connection
        assert db_arg is db
        return "access-token"

    monkeypatch.setattr(catalog_sync, "_resolve_service_token_for_sync", resolve)

    tools = [
        SimpleNamespace(name="restored", inputSchema={"type": "object"}),
        SimpleNamespace(name="created", inputSchema=_SchemaModel()),
    ]
    monkeypatch.setattr(
        catalog_sync.mcp_client_session,
        "open_session",
        lambda connection_arg, token: _FakeSession(tools=tools),
    )

    result = await catalog_sync.sync_catalog(connection, db)

    assert result == final_rows
    assert restored.enabled is True
    assert restored.disabled_reason is None
    assert restored.tool_schema == {"type": "object"}
    assert manual_disabled.enabled is False
    assert manual_disabled.disabled_reason == "admin disabled"
    assert stale_enabled.enabled is False
    assert stale_enabled.disabled_reason.startswith("Removed from server catalog at ")
    assert len(db.added) == 1
    assert db.added[0].tool_name == "created"
    assert db.added[0].tool_schema == {
        "type": "object",
        "properties": {"id": {"type": "string"}},
    }
    assert db.commits == 1


@pytest.mark.asyncio
async def test_sync_catalog_wraps_tools_list_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection()

    async def resolve(connection_arg, db_arg):
        return "access-token"

    monkeypatch.setattr(catalog_sync, "_resolve_service_token_for_sync", resolve)
    monkeypatch.setattr(
        catalog_sync.mcp_client_session,
        "open_session",
        lambda connection_arg, token: _FakeSession(error=RuntimeError("vendor down")),
    )

    with pytest.raises(ToolDispatchError, match="tools/list failed"):
        await catalog_sync.sync_catalog(connection, _FakeDb([]))
