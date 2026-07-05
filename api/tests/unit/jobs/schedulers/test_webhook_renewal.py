from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.jobs.schedulers import webhook_renewal


class _Db:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


class _Repo:
    expiring = []
    by_id = {}
    instances = []

    def __init__(self, db):
        self.db = db
        _Repo.instances.append(self)

    async def get_expiring_soon(self, *, within_hours):
        assert within_hours == webhook_renewal.RENEWAL_THRESHOLD_HOURS
        return list(_Repo.expiring)

    async def get_by_id(self, webhook_id):
        return _Repo.by_id.get(webhook_id)


def _webhook(*, adapter_name="graph", renewal=True, has_event_source=True):
    return SimpleNamespace(
        id=uuid4(),
        adapter_name=adapter_name,
        external_id=f"external-{adapter_name}",
        state={"old": True},
        integration_id=uuid4(),
        integration=SimpleNamespace(id=uuid4()),
        event_source_id=uuid4(),
        event_source=SimpleNamespace(error_message=None) if has_event_source else None,
        expires_at=datetime(2026, 7, 6, tzinfo=timezone.utc),
        updated_at=None,
        renewal=renewal,
    )


@pytest.fixture(autouse=True)
def patch_repository(monkeypatch):
    _Repo.expiring = []
    _Repo.by_id = {}
    _Repo.instances = []
    monkeypatch.setattr(
        "src.repositories.events.WebhookSourceRepository",
        _Repo,
    )


def _patch_db(monkeypatch):
    dbs = []

    @asynccontextmanager
    async def fake_context():
        db = _Db()
        dbs.append(db)
        yield db

    monkeypatch.setattr(webhook_renewal, "get_db_context", fake_context)
    return dbs


@pytest.mark.asyncio
async def test_renew_expiring_webhooks_persists_successful_renewal(monkeypatch):
    dbs = _patch_db(monkeypatch)
    webhook = _webhook()
    persisted = _webhook()
    _Repo.expiring = [webhook]
    _Repo.by_id = {webhook.id: persisted}
    expires_at = datetime(2026, 7, 10, tzinfo=timezone.utc)

    adapter = SimpleNamespace(
        renewal_interval=3600,
        renew=AsyncMock(
            return_value=SimpleNamespace(
                expires_at=expires_at,
                state={"fresh": True},
            )
        ),
    )
    monkeypatch.setattr(webhook_renewal, "get_adapter", lambda _name: adapter)

    result = await webhook_renewal.renew_expiring_webhooks()

    assert result["total_webhooks"] == 1
    assert result["needs_renewal"] == 1
    assert result["renewed_successfully"] == 1
    assert result["renewal_failed"] == 0
    adapter.renew.assert_awaited_once_with(
        external_id=webhook.external_id,
        state={"old": True},
        integration=webhook.integration,
    )
    assert persisted.expires_at == expires_at
    assert persisted.state == {"old": True, "fresh": True}
    assert persisted.updated_at is not None
    assert dbs[-1].committed is True


@pytest.mark.asyncio
async def test_renew_expiring_webhooks_tracks_unsupported_and_failed(monkeypatch):
    dbs = _patch_db(monkeypatch)
    unsupported = _webhook(adapter_name="unsupported")
    failed = _webhook(adapter_name="graph", has_event_source=True)
    _Repo.expiring = [unsupported, failed]
    _Repo.by_id = {failed.id: failed}

    adapters = {
        "unsupported": SimpleNamespace(renewal_interval=None),
        "graph": SimpleNamespace(
            renewal_interval=3600,
            renew=AsyncMock(return_value=None),
        ),
    }
    monkeypatch.setattr(webhook_renewal, "get_adapter", lambda name: adapters[name])

    result = await webhook_renewal.renew_expiring_webhooks()

    assert result["total_webhooks"] == 2
    assert result["needs_renewal"] == 1
    assert result["no_renewal_support"] == 1
    assert result["renewal_failed"] == 1
    assert result["errors"][0]["webhook_id"] == str(failed.id)
    assert failed.event_source.error_message == (
        "Webhook subscription expired and could not be renewed"
    )
    assert dbs[-1].committed is True


@pytest.mark.asyncio
async def test_renew_expiring_webhooks_records_adapter_exception(monkeypatch):
    _patch_db(monkeypatch)
    webhook = _webhook()
    _Repo.expiring = [webhook]

    adapter = SimpleNamespace(
        renewal_interval=3600,
        renew=AsyncMock(side_effect=RuntimeError("provider down")),
    )
    monkeypatch.setattr(webhook_renewal, "get_adapter", lambda _name: adapter)

    result = await webhook_renewal.renew_expiring_webhooks()

    assert result["renewal_failed"] == 1
    assert result["errors"] == [
        {
            "webhook_id": str(webhook.id),
            "adapter": webhook.adapter_name,
            "error": "provider down",
        }
    ]
