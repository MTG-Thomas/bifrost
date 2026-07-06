from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.core.org_filter import OrgFilterType
from src.repositories.external_mcp import (
    MCPConnectionRepository,
    MCPConnectionToolRepository,
    MCPServerRepository,
    UserMCPCredentialRepository,
)


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        scalars = MagicMock()
        unique = MagicMock()
        unique.all.return_value = self._rows
        scalars.unique.return_value = unique
        scalars.all.return_value = self._rows
        return scalars

    def unique(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_mcp_connections_and_user_credentials_do_not_broaden_without_scope():
    session = AsyncMock()

    assert await MCPConnectionRepository(session, org_id=None).list_connections() == []
    assert await UserMCPCredentialRepository(session, user_id=None).list_credentials() == []
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_connection_repository_lists_and_filters_org_connections():
    connection = MagicMock()
    session = AsyncMock()
    session.execute.return_value = _ScalarsResult([connection])
    org_id = uuid4()
    server_id = uuid4()
    repo = MCPConnectionRepository(session, org_id=org_id)

    result = await repo.list_connections(server_id=server_id)

    assert result == [connection]
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_server_repository_handles_scope_variants():
    server = MagicMock()
    session = AsyncMock()
    session.execute.return_value = _ScalarsResult([server])
    repo = MCPServerRepository(session, org_id=uuid4(), is_superuser=True)

    assert await repo.list_servers(active_only=True) == [server]
    assert await repo.list_all_in_scope(OrgFilterType.GLOBAL_ONLY) == [server]
    assert await repo.list_all_in_scope(OrgFilterType.ORG_ONLY) == [server]
    assert await repo.list_all_in_scope(OrgFilterType.ORG_PLUS_GLOBAL) == [server]
    assert await repo.list_all_in_scope(OrgFilterType.ALL, active_only=True) == [server]
    assert session.execute.await_count == 5


@pytest.mark.asyncio
async def test_mcp_tool_and_credential_lookup_methods_return_scalar_results():
    row = MagicMock()
    session = AsyncMock()
    session.execute.return_value = _ScalarsResult([row])

    tool_repo = MCPConnectionToolRepository(session)
    assert await tool_repo.get_tool(uuid4()) is row
    assert await tool_repo.get_by_connection_and_name(uuid4(), "search") is row

    credential_repo = UserMCPCredentialRepository(session, user_id=uuid4())
    assert await credential_repo.get_credential(uuid4()) is row
    assert await credential_repo.get_by_user_and_connection(uuid4(), uuid4()) is row
