from __future__ import annotations

from uuid import uuid4

import pytest

from src.services.file_storage.entity_resolution import EntityResolutionService


class ScalarResult:
    def __init__(self, value: object | None) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object | None:
        return self.value


class FakeDb:
    def __init__(self, result: object | None) -> None:
        self.result = result
        self.statements: list[object] = []

    async def execute(self, statement: object) -> ScalarResult:
        self.statements.append(statement)
        return ScalarResult(self.result)


@pytest.mark.asyncio
async def test_get_workflow_by_id_rejects_invalid_uuid_without_query() -> None:
    db = FakeDb(result=object())

    assert await EntityResolutionService(db).get_workflow_by_id("not-a-uuid") is None
    assert db.statements == []


@pytest.mark.asyncio
async def test_get_workflow_by_id_returns_query_result() -> None:
    workflow = object()
    db = FakeDb(result=workflow)

    assert await EntityResolutionService(db).get_workflow_by_id(str(uuid4())) is workflow
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_get_agent_by_id_rejects_invalid_uuid_without_query() -> None:
    db = FakeDb(result=object())

    assert await EntityResolutionService(db).get_agent_by_id("not-a-uuid") is None
    assert db.statements == []


@pytest.mark.asyncio
async def test_get_agent_by_id_returns_query_result() -> None:
    agent = object()
    db = FakeDb(result=agent)

    assert await EntityResolutionService(db).get_agent_by_id(str(uuid4())) is agent
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_find_workflow_match_returns_active_name_match() -> None:
    workflow = object()
    db = FakeDb(result=workflow)

    assert await EntityResolutionService(db).find_workflow_match("Daily Sync") is workflow
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_find_workflow_match_returns_none_when_missing() -> None:
    db = FakeDb(result=None)

    assert await EntityResolutionService(db).find_workflow_match("Missing") is None
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_find_agent_match_returns_active_name_match() -> None:
    agent = object()
    db = FakeDb(result=agent)

    assert await EntityResolutionService(db).find_agent_match("Dispatcher") is agent
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_find_agent_match_returns_none_when_missing() -> None:
    db = FakeDb(result=None)

    assert await EntityResolutionService(db).find_agent_match("Missing") is None
    assert len(db.statements) == 1
