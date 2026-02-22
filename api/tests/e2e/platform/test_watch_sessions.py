"""E2E tests for CLI watch session endpoints."""
import pytest


@pytest.mark.asyncio
async def test_watch_start(auth_client):
    """Starting a watch session should succeed."""
    resp = await auth_client.post("/api/files/watch", json={
        "action": "start",
        "prefix": "apps/test-watch",
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_watch_heartbeat(auth_client):
    """Heartbeating should succeed."""
    await auth_client.post("/api/files/watch", json={
        "action": "start",
        "prefix": "apps/test-heartbeat",
    })
    resp = await auth_client.post("/api/files/watch", json={
        "action": "heartbeat",
        "prefix": "apps/test-heartbeat",
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_watchers_list(auth_client):
    """Active watchers should appear in the list."""
    await auth_client.post("/api/files/watch", json={
        "action": "start",
        "prefix": "apps/watcher-list-test",
    })
    resp = await auth_client.get("/api/files/watchers")
    assert resp.status_code == 200
    watchers = resp.json()["watchers"]
    prefixes = [w["prefix"] for w in watchers]
    assert "apps/watcher-list-test" in prefixes


@pytest.mark.asyncio
async def test_watch_stop(auth_client):
    """Stopping a watch session should remove it from the list."""
    prefix = "apps/watch-stop-test"
    await auth_client.post("/api/files/watch", json={
        "action": "start",
        "prefix": prefix,
    })
    await auth_client.post("/api/files/watch", json={
        "action": "stop",
        "prefix": prefix,
    })
    resp = await auth_client.get("/api/files/watchers")
    watchers = resp.json()["watchers"]
    prefixes = [w["prefix"] for w in watchers]
    assert prefix not in prefixes
