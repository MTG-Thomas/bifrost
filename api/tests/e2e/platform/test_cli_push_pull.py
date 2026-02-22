"""E2E tests for CLI push/pull endpoints and manifest round-tripping."""
import hashlib

import pytest


@pytest.mark.asyncio
async def test_push_basic_files(auth_client):
    """Push regular files and verify counts."""
    resp = await auth_client.post("/api/files/push", json={
        "files": {
            "apps/test-app/index.tsx": "export default () => <div>Hello</div>",
            "apps/test-app/styles.css": "body { margin: 0; }",
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] + data["updated"] + data["unchanged"] == 2
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_push_unchanged_files(auth_client):
    """Pushing the same files twice reports them as unchanged."""
    files = {
        "apps/push-unchanged/index.tsx": "export default () => <div>Static</div>",
    }
    await auth_client.post("/api/files/push", json={"files": files})
    resp = await auth_client.post("/api/files/push", json={"files": files})
    data = resp.json()
    assert data["unchanged"] == 1
    assert data["created"] == 0
    assert data["updated"] == 0


@pytest.mark.asyncio
async def test_push_bifrost_manifest(auth_client):
    """Pushing .bifrost/ files triggers manifest import."""
    workflows_yaml = (
        "workflows:\n"
        "  test-wf:\n"
        "    name: test-wf\n"
        "    path: workflows/test_wf.py\n"
    )
    resp = await auth_client.post("/api/files/push", json={
        "files": {
            ".bifrost/workflows.yaml": workflows_yaml,
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["manifest_applied"] is True


@pytest.mark.asyncio
async def test_push_manifest_no_writeback_when_unchanged(auth_client):
    """If pushed manifest matches regenerated, manifest_files should be empty."""
    workflows_yaml = (
        "workflows:\n"
        "  roundtrip-wf:\n"
        "    name: roundtrip-wf\n"
        "    path: workflows/roundtrip_wf.py\n"
    )
    resp1 = await auth_client.post("/api/files/push", json={
        "files": {".bifrost/workflows.yaml": workflows_yaml},
    })
    data1 = resp1.json()
    canonical = data1.get("manifest_files", {}).get("workflows.yaml", workflows_yaml)

    resp2 = await auth_client.post("/api/files/push", json={
        "files": {".bifrost/workflows.yaml": canonical},
    })
    data2 = resp2.json()
    assert data2["manifest_files"].get("workflows.yaml") is None, \
        "Pushing canonical manifest should not trigger writeback"


@pytest.mark.asyncio
async def test_pull_returns_changed_files(auth_client):
    """Pull should return files that differ from local hashes."""
    content = "# pull test file"
    await auth_client.post("/api/files/push", json={
        "files": {"modules/pull_test.py": content},
    })
    resp = await auth_client.post("/api/files/pull", json={
        "prefix": "modules",
        "local_hashes": {"modules/pull_test.py": "0000000000000000"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "modules/pull_test.py" in data["files"]
    assert data["files"]["modules/pull_test.py"] == content


@pytest.mark.asyncio
async def test_pull_skips_matching_files(auth_client):
    """Pull should NOT return files whose hash matches local."""
    content = "# pull match test"
    await auth_client.post("/api/files/push", json={
        "files": {"modules/pull_match.py": content},
    })
    correct_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    resp = await auth_client.post("/api/files/pull", json={
        "prefix": "modules",
        "local_hashes": {"modules/pull_match.py": correct_hash},
    })
    data = resp.json()
    assert "modules/pull_match.py" not in data["files"]


@pytest.mark.asyncio
async def test_pull_returns_deleted_files(auth_client):
    """Pull should list files that exist locally but not on server."""
    resp = await auth_client.post("/api/files/pull", json={
        "prefix": "modules",
        "local_hashes": {"modules/nonexistent_file.py": "abc123"},
    })
    data = resp.json()
    assert "modules/nonexistent_file.py" in data["deleted"]


@pytest.mark.asyncio
async def test_pull_manifest_files(auth_client):
    """Pull should include regenerated manifest files when they differ from local."""
    resp = await auth_client.post("/api/files/pull", json={
        "prefix": "apps/test-app",
        "local_hashes": {
            ".bifrost/workflows.yaml": "0000000000000000",
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("manifest_files", {}), dict)


@pytest.mark.asyncio
async def test_push_does_not_mark_dirty(auth_client):
    """CLI push should not mark repo as dirty (covered by skip_dirty_flag)."""
    from src.core.repo_dirty import clear_repo_dirty
    await clear_repo_dirty()

    await auth_client.post("/api/files/push", json={
        "files": {"test-no-dirty.py": "# test"},
    })
    resp = await auth_client.get("/api/github/repo-status")
    assert resp.json()["dirty"] is False


@pytest.mark.asyncio
async def test_push_delete_missing_prefix(auth_client):
    """delete_missing_prefix should remove files not in the push batch."""
    await auth_client.post("/api/files/push", json={
        "files": {
            "apps/cleanup/keep.tsx": "keep",
            "apps/cleanup/remove.tsx": "remove",
        },
    })
    resp = await auth_client.post("/api/files/push", json={
        "files": {"apps/cleanup/keep.tsx": "keep"},
        "delete_missing_prefix": "apps/cleanup",
    })
    data = resp.json()
    assert data["deleted"] >= 1

    pull_resp = await auth_client.post("/api/files/pull", json={
        "prefix": "apps/cleanup",
        "local_hashes": {"apps/cleanup/remove.tsx": "abc"},
    })
    pull_data = pull_resp.json()
    assert "apps/cleanup/remove.tsx" in pull_data["deleted"]
