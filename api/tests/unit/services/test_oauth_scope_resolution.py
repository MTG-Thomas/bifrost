from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from src.services.oauth_scope_resolution import (
    get_oauth_provider_for_scope,
    get_oauth_token_for_scope,
)


class ScalarFirstResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalars(self) -> "ScalarFirstResult":
        return self

    def first(self) -> Any:
        return self.value


class FakeDb:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.executed: list[Any] = []

    async def execute(self, statement: Any) -> Any:
        self.executed.append(statement)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_get_oauth_provider_for_scope_returns_org_specific_provider_first() -> None:
    provider = SimpleNamespace(id=uuid4(), provider_name="microsoft")
    db = FakeDb([ScalarFirstResult(provider)])

    result = await get_oauth_provider_for_scope(db, "microsoft", uuid4())  # type: ignore[arg-type]

    assert result is provider
    assert len(db.executed) == 1


@pytest.mark.asyncio
async def test_get_oauth_provider_for_scope_falls_back_to_global_provider() -> None:
    provider = SimpleNamespace(id=uuid4(), provider_name="microsoft")
    db = FakeDb([ScalarFirstResult(None), ScalarFirstResult(provider)])

    result = await get_oauth_provider_for_scope(db, "microsoft", uuid4())  # type: ignore[arg-type]

    assert result is provider
    assert len(db.executed) == 2


@pytest.mark.asyncio
async def test_get_oauth_provider_for_global_scope_uses_only_global_lookup() -> None:
    provider = SimpleNamespace(id=uuid4(), provider_name="github")
    db = FakeDb([ScalarFirstResult(provider)])

    result = await get_oauth_provider_for_scope(db, "github", None)  # type: ignore[arg-type]

    assert result is provider
    assert len(db.executed) == 1


@pytest.mark.asyncio
async def test_get_oauth_token_for_scope_returns_org_scoped_userless_token() -> None:
    token = SimpleNamespace(id=uuid4(), access_token="org-token")
    db = FakeDb([ScalarFirstResult(token)])

    result = await get_oauth_token_for_scope(db, uuid4(), uuid4())  # type: ignore[arg-type]

    assert result is token
    assert len(db.executed) == 1


@pytest.mark.asyncio
async def test_get_oauth_token_for_global_scope_does_not_cross_into_org_tokens() -> None:
    token = SimpleNamespace(id=uuid4(), access_token="global-token")
    db = FakeDb([ScalarFirstResult(token)])

    result = await get_oauth_token_for_scope(db, uuid4(), None)  # type: ignore[arg-type]

    assert result is token
    assert len(db.executed) == 1
