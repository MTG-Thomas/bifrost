"""Exercise atomic admission through the real authenticated API and Redis."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest


@pytest.mark.e2e
def test_shared_integration_admission_and_owner_release(e2e_client, platform_admin):
    headers = platform_admin.headers
    name = f"slots-{uuid4().hex}"
    created = e2e_client.post("/api/integrations", headers=headers, json={"name": name})
    assert created.status_code == 201, created.text
    integration_id = created.json()["id"]
    bodies = [{"name": name, "scope": "global", "token": str(uuid4())} for _ in range(8)]
    try:
        configured = e2e_client.put(
            f"/api/integrations/{integration_id}/config", headers=headers,
            json={"config": {"request_concurrency_limit": 2}},
        )
        assert configured.status_code == 200, configured.text

        def acquire(body):
            response = e2e_client.post("/api/sdk/integrations/request-slot/acquire", headers=headers, json=body)
            assert response.status_code == 200, response.text
            return response.json()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(acquire, bodies))
        winners = [body for body, result in zip(bodies, results) if result["acquired"]]
        assert len(winners) == 2
        assert all(result["enabled"] for result in results)
        # Retrying one acquire is idempotent and does not consume a third slot.
        assert acquire(winners[0])["acquired"]
        stranger = {"name": name, "scope": "global", "token": str(uuid4())}
        released = e2e_client.post("/api/sdk/integrations/request-slot/release", headers=headers, json=stranger)
        assert released.status_code == 200
        assert not acquire(stranger)["acquired"]
        released = e2e_client.post("/api/sdk/integrations/request-slot/release", headers=headers, json=winners[0])
        assert released.status_code == 200
        assert acquire(stranger)["acquired"]
        bodies.append(stranger)
        invalid = e2e_client.put(
            f"/api/integrations/{integration_id}/config", headers=headers,
            json={"config": {"request_concurrency_limit": 0}},
        )
        assert invalid.status_code == 422
    finally:
        for body in bodies:
            e2e_client.post("/api/sdk/integrations/request-slot/release", headers=headers, json=body)
        e2e_client.delete(f"/api/integrations/{integration_id}", headers=headers)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_expired_token_cannot_release_new_owner():
    from src.core.cache import get_redis
    from src.services import integration_request_slots as slots

    integration_id = uuid4()
    key = f"bifrost:integration-request-slots:{integration_id}"
    try:
        acquired, _ = await slots.acquire(integration_id, "expired-owner", 1)
        assert acquired
        async with get_redis() as redis:
            # Expire the precise token without wall-clock sleeps.
            await redis.zadd(key, {"expired-owner": 1})
        acquired, _ = await slots.acquire(integration_id, "new-owner", 1)
        assert acquired
        await slots.release(integration_id, "expired-owner")
        acquired, _ = await slots.acquire(integration_id, "third-owner", 1)
        assert not acquired
    finally:
        async with get_redis() as redis:
            await redis.delete(key)
