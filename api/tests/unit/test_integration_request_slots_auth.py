"""Admission uses the SDK's existing scope and external-caller gates."""
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from src.core.principal import UserPrincipal
from src.models.contracts.cli import SDKIntegrationRequestSlotRequest
from src.routers import cli


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["global", str(uuid4())])
async def test_cross_org_and_global_denied_before_policy_read(monkeypatch, scope):
    monkeypatch.setattr(cli, "_is_provider_org", AsyncMock(return_value=False))
    user = UserPrincipal(user_id=uuid4(), email="user@example.test", organization_id=uuid4())
    request = SDKIntegrationRequestSlotRequest(name="Example", scope=scope, token=uuid4())
    with pytest.raises(HTTPException) as exc:
        await cli._request_slot_integration(request, user, AsyncMock())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_external_caller_cannot_contend_for_integration():
    user = UserPrincipal(user_id=uuid4(), email="guest@example.test", organization_id=uuid4(), is_external=True)
    request = SDKIntegrationRequestSlotRequest(name="Example", token=uuid4())
    with pytest.raises(HTTPException) as exc:
        await cli._request_slot_integration(request, user, AsyncMock())
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_declared_missing_integration_preserves_required_connection_error(monkeypatch):
    from src.repositories import integrations
    repo = AsyncMock()
    repo.get_request_slot_policy.return_value = None
    monkeypatch.setattr(integrations, "IntegrationsRepository", lambda db: repo)
    monkeypatch.setattr(cli, "_is_external_user_db", AsyncMock(return_value=False))
    monkeypatch.setattr(cli, "_connection_is_declared", AsyncMock(return_value=True))
    user = UserPrincipal(user_id=uuid4(), email="admin@example.test", organization_id=None, is_superuser=True)
    request = SDKIntegrationRequestSlotRequest(name="Example", token=uuid4(), solution=uuid4())
    with pytest.raises(HTTPException) as exc:
        await cli._request_slot_integration(request, user, AsyncMock())
    assert exc.value.status_code == 424


@pytest.mark.asyncio
async def test_owner_token_is_bound_to_authenticated_principal(monkeypatch):
    from src.repositories import integrations
    repo = AsyncMock()
    repo.get_request_slot_policy.return_value = (uuid4(), 2)
    monkeypatch.setattr(integrations, "IntegrationsRepository", lambda db: repo)
    monkeypatch.setattr(cli, "_is_external_user_db", AsyncMock(return_value=False))
    request = SDKIntegrationRequestSlotRequest(name="Example", token=uuid4())
    user1 = UserPrincipal(user_id=uuid4(), email="one@example.test", organization_id=None, is_superuser=True)
    user2 = UserPrincipal(user_id=uuid4(), email="two@example.test", organization_id=None, is_superuser=True)
    _, _, owner1 = await cli._request_slot_integration(request, user1, AsyncMock())
    _, _, owner2 = await cli._request_slot_integration(request, user2, AsyncMock())
    assert owner1 != owner2
