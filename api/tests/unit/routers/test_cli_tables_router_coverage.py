from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from src.models.contracts.cli import SDKTableCreateRequest, SDKTableListRequest
from src.routers import cli


ORG_ID = "11111111-1111-1111-1111-111111111111"


def _user(*, is_external: bool = False):
    return SimpleNamespace(
        user_id=uuid4(),
        email="operator@example.com",
        organization_id=uuid4(),
        is_superuser=False,
        is_external=is_external,
        app_id=None,
    )


def _request(*, query_params=None, headers=None):
    query_params = query_params or {}
    headers = headers or {}
    return SimpleNamespace(
        query_params=query_params,
        headers=headers,
        app_id=headers.get("X-Bifrost-App"),
        solution_id=query_params.get("solution"),
    )


def _db(value=None) -> AsyncMock:
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: value))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_create_table_rejects_solution_query_before_repo_work() -> None:
    with patch.object(cli, "_resolve_sdk_org_id", AsyncMock()) as resolve_scope:
        with pytest.raises(HTTPException) as exc:
            await cli.cli_create_table(
                SDKTableCreateRequest(name="tickets"),
                _request(query_params={"solution": str(uuid4())}),
                _user(),
                _db(),
            )

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc.value.detail == "Tables must be declared by the solution manifest"
    resolve_scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_table_rejects_solution_declared_app_header() -> None:
    app_id = uuid4()
    solution_id = uuid4()

    with patch.object(cli, "_resolve_sdk_org_id", AsyncMock()) as resolve_scope:
        with pytest.raises(HTTPException) as exc:
            await cli.cli_create_table(
                SDKTableCreateRequest(name="tickets"),
                _request(headers={"X-Bifrost-App": str(app_id)}),
                _user(),
                _db(solution_id),
            )

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    assert exc.value.detail == "Tables must be declared by the solution manifest"
    resolve_scope.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_table_reports_exact_scope_duplicate() -> None:
    db = _db(SimpleNamespace(id=uuid4(), name="tickets"))

    with patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=ORG_ID)):
        with pytest.raises(HTTPException) as exc:
            await cli.cli_create_table(
                SDKTableCreateRequest(name="tickets", scope=ORG_ID),
                _request(),
                _user(),
                db,
            )

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    assert exc.value.detail == "Table 'tickets' already exists"
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_table_allows_bad_app_header_and_returns_created_table() -> None:
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db = _db(None)

    async def refresh(row):
        row.id = uuid4()
        row.created_at = created
        row.updated_at = created

    db.refresh = AsyncMock(side_effect=refresh)

    with patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=ORG_ID)):
        result = await cli.cli_create_table(
            SDKTableCreateRequest(
                name="tickets",
                table_schema={"columns": [{"name": "id", "type": "text"}]},
                description="Ticket cache",
                scope=ORG_ID,
            ),
            _request(headers={"X-Bifrost-App": "not-a-uuid"}),
            _user(),
            db,
        )

    assert result.name == "tickets"
    assert result.organization_id == ORG_ID
    assert result.table_schema == {"columns": [{"name": "id", "type": "text"}]}
    assert result.description == "Ticket cache"
    assert result.created_at == created.isoformat()
    assert result.updated_at == created.isoformat()
    assert db.add.call_count == 1
    created_table = db.add.call_args.args[0]
    assert created_table.name == "tickets"
    assert created_table.organization_id.hex == ORG_ID.replace("-", "")
    assert created_table.created_by == "operator@example.com"
    assert created_table.access
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(created_table)


@pytest.mark.asyncio
async def test_list_tables_sorts_tables_and_uses_external_trust_flags() -> None:
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tables = [
        SimpleNamespace(
            id=uuid4(),
            name="zeta",
            organization_id=None,
            schema={"columns": []},
            description=None,
            created_at=created,
            updated_at=created,
        ),
        SimpleNamespace(
            id=uuid4(),
            name="alpha",
            organization_id=uuid4(),
            schema={"columns": [{"name": "id"}]},
            description="Alpha",
            created_at=created,
            updated_at=created,
        ),
    ]
    repo = MagicMock()
    repo.list = AsyncMock(return_value=tables)

    with (
        patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=ORG_ID)),
        patch("src.repositories.tables.TableRepository", return_value=repo) as repo_cls,
    ):
        result = await cli.cli_list_tables(
            SDKTableListRequest(scope=ORG_ID),
            _user(is_external=True),
            AsyncMock(),
        )

    assert [table.name for table in result] == ["alpha", "zeta"]
    assert result[0].description == "Alpha"
    assert result[1].organization_id is None
    repo_cls.assert_called_once()
    assert repo_cls.call_args.kwargs["org_id"].hex == ORG_ID.replace("-", "")
    assert repo_cls.call_args.kwargs["is_superuser"] is False
    assert repo_cls.call_args.kwargs["is_external"] is True
    repo.list.assert_awaited_once()
