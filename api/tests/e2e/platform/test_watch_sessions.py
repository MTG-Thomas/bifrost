"""E2E tests for CLI watch session endpoints."""


def test_watch_start(e2e_client, platform_admin):
    """Starting a watch session should succeed."""
    resp = e2e_client.post("/api/files/watch", headers=platform_admin.headers, json={
        "action": "start",
        "prefix": "apps/test-watch",
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_watch_heartbeat(e2e_client, platform_admin):
    """Heartbeating should succeed."""
    e2e_client.post("/api/files/watch", headers=platform_admin.headers, json={
        "action": "start",
        "prefix": "apps/test-heartbeat",
    })
    resp = e2e_client.post("/api/files/watch", headers=platform_admin.headers, json={
        "action": "heartbeat",
        "prefix": "apps/test-heartbeat",
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_watchers_list(e2e_client, platform_admin):
    """Active watchers should appear in the list."""
    e2e_client.post("/api/files/watch", headers=platform_admin.headers, json={
        "action": "start",
        "prefix": "apps/watcher-list-test",
    })
    resp = e2e_client.get("/api/files/watchers", headers=platform_admin.headers)
    assert resp.status_code == 200
    watchers = resp.json()["watchers"]
    prefixes = [w["prefix"] for w in watchers]
    assert "apps/watcher-list-test" in prefixes


def test_watch_stop(e2e_client, platform_admin):
    """Stopping a watch session should remove it from the list."""
    prefix = "apps/watch-stop-test"
    e2e_client.post("/api/files/watch", headers=platform_admin.headers, json={
        "action": "start",
        "prefix": prefix,
    })
    e2e_client.post("/api/files/watch", headers=platform_admin.headers, json={
        "action": "stop",
        "prefix": prefix,
    })
    resp = e2e_client.get("/api/files/watchers", headers=platform_admin.headers)
    watchers = resp.json()["watchers"]
    prefixes = [w["prefix"] for w in watchers]
    assert prefix not in prefixes
