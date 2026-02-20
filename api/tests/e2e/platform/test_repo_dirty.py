"""E2E tests for repo dirty flag and repo-status endpoint."""
import pytest


@pytest.mark.asyncio
async def test_repo_status_clean_by_default(auth_client):
    """Repo should be clean by default (no platform writes)."""
    resp = await auth_client.get("/api/github/repo-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dirty"] is False
    assert data["dirty_since"] is None


@pytest.mark.asyncio
async def test_repo_status_dirty_after_editor_write(auth_client):
    """Writing via the editor endpoint should mark repo dirty."""
    # Write a file through the editor endpoint (platform write)
    await auth_client.put("/api/files/editor/content", json={
        "path": "test-dirty-flag.py",
        "content": "# test dirty flag",
    })
    resp = await auth_client.get("/api/github/repo-status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dirty"] is True
    assert data["dirty_since"] is not None


@pytest.mark.asyncio
async def test_repo_status_clean_after_cli_push(auth_client):
    """CLI push should NOT mark repo dirty (skip_dirty_flag=True)."""
    # First ensure clean state by clearing any existing dirty flag
    from src.core.repo_dirty import clear_repo_dirty
    await clear_repo_dirty()

    # Push via CLI endpoint
    await auth_client.post("/api/files/push", json={
        "files": {"test-push-clean.py": "# test push"},
    })
    resp = await auth_client.get("/api/github/repo-status")
    data = resp.json()
    assert data["dirty"] is False
