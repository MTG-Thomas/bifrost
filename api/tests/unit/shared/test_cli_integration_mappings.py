from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from shared.cli_integration_mappings import (
    _resolve_mapping_org_id,
    get_cli_integration_mapping,
    list_cli_integration_mappings,
)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Session:
    def __init__(self, developer_context=None):
        self.developer_context = developer_context

    async def execute(self, _stmt):
        return _ScalarResult(self.developer_context)


class _Repo:
    def __init__(self, mappings):
        self.mappings = mappings
        self.calls = []

    async def list_mappings(self, integration_id, organization_id=None):
        self.calls.append((integration_id, organization_id))
        if organization_id is None:
            return self.mappings
        return [
            mapping for mapping in self.mappings
            if mapping.organization_id == organization_id
        ]


def _user(*, is_superuser=False, organization_id=None):
    return SimpleNamespace(
        is_superuser=is_superuser,
        organization_id=organization_id,
        user_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_resolve_mapping_org_id_rejects_invalid_scope():
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_mapping_org_id(
            _user(is_superuser=True),
            "not-a-uuid",
            _Session(),
        )

    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_resolve_mapping_org_id_uses_developer_context_default():
    default_org_id = uuid4()
    org_id = await _resolve_mapping_org_id(
        _user(is_superuser=True),
        None,
        _Session(SimpleNamespace(default_org_id=default_org_id)),
    )

    assert org_id == default_org_id


@pytest.mark.asyncio
async def test_resolve_mapping_org_id_allows_superuser_global_and_org_scope():
    scoped_org_id = uuid4()

    assert await _resolve_mapping_org_id(
        _user(is_superuser=True),
        "global",
        _Session(),
    ) is None
    assert await _resolve_mapping_org_id(
        _user(is_superuser=True),
        str(scoped_org_id),
        _Session(),
    ) == scoped_org_id


@pytest.mark.asyncio
async def test_resolve_mapping_org_id_rejects_non_superuser_cross_org_scope():
    with pytest.raises(HTTPException) as exc_info:
        await _resolve_mapping_org_id(
            _user(organization_id=uuid4()),
            str(uuid4()),
            _Session(),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_list_cli_integration_mappings_filters_by_resolved_org():
    integration_id = uuid4()
    org_id = uuid4()
    other_org_id = uuid4()
    visible_mapping = SimpleNamespace(entity_id="visible", organization_id=org_id)
    hidden_mapping = SimpleNamespace(entity_id="hidden", organization_id=other_org_id)
    repo = _Repo([visible_mapping, hidden_mapping])

    mappings = await list_cli_integration_mappings(
        repo,
        _user(organization_id=org_id),
        None,
        integration_id,
        _Session(),
    )

    assert mappings == [visible_mapping]
    assert repo.calls == [(integration_id, org_id)]


@pytest.mark.asyncio
async def test_get_cli_integration_mapping_selects_visible_entity_id():
    integration_id = uuid4()
    org_id = uuid4()
    target_mapping = SimpleNamespace(entity_id="target", organization_id=org_id)
    other_mapping = SimpleNamespace(entity_id="other", organization_id=org_id)
    repo = _Repo([other_mapping, target_mapping])

    mapping = await get_cli_integration_mapping(
        repo,
        _user(is_superuser=True),
        "global",
        integration_id,
        _Session(),
        "target",
    )

    assert mapping == target_mapping
