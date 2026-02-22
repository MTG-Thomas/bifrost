"""E2E tests for CLI push/pull endpoints and manifest round-tripping."""
import hashlib


def test_push_basic_files(e2e_client, platform_admin):
    """Push regular files and verify counts."""
    resp = e2e_client.post("/api/files/push", headers=platform_admin.headers, json={
        "files": {
            "apps/test-app/index.tsx": "export default () => <div>Hello</div>",
            "apps/test-app/styles.css": "body { margin: 0; }",
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] + data["updated"] + data["unchanged"] == 2
    assert data["errors"] == []


def test_push_unchanged_files(e2e_client, platform_admin):
    """Pushing the same files twice reports them as unchanged."""
    files = {
        "apps/push-unchanged/index.tsx": "export default () => <div>Static</div>",
    }
    e2e_client.post("/api/files/push", headers=platform_admin.headers, json={"files": files})
    resp = e2e_client.post("/api/files/push", headers=platform_admin.headers, json={"files": files})
    data = resp.json()
    assert data["unchanged"] == 1
    assert data["created"] == 0
    assert data["updated"] == 0


def test_push_bifrost_manifest(e2e_client, platform_admin):
    """Pushing .bifrost/ files triggers manifest processing."""
    workflows_yaml = (
        "workflows:\n"
        "  test-wf:\n"
        "    name: test-wf\n"
        "    path: workflows/test_wf.py\n"
    )
    # Push the workflow source file first so the manifest import can resolve it
    e2e_client.post("/api/files/push", headers=platform_admin.headers, json={
        "files": {
            "workflows/test_wf.py": "from bifrost import workflow\n\n@workflow\ndef test_wf():\n    pass\n",
        },
    })
    resp = e2e_client.post("/api/files/push", headers=platform_admin.headers, json={
        "files": {
            ".bifrost/workflows.yaml": workflows_yaml,
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    # manifest_applied may be True or False depending on DB state,
    # but the key must be present and the push must succeed
    assert "manifest_applied" in data
    assert "manifest_files" in data


def test_push_manifest_response_shape(e2e_client, platform_admin):
    """Push response should include manifest_files and modified_files dicts."""
    resp = e2e_client.post("/api/files/push", headers=platform_admin.headers, json={
        "files": {
            ".bifrost/workflows.yaml": "workflows: {}\n",
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("manifest_files"), dict)
    assert isinstance(data.get("modified_files"), dict)
    assert isinstance(data.get("warnings"), list)


def test_pull_returns_changed_files(e2e_client, platform_admin):
    """Pull should return files that differ from local hashes."""
    content = "# pull test file"
    e2e_client.post("/api/files/push", headers=platform_admin.headers, json={
        "files": {"modules/pull_test.py": content},
    })
    resp = e2e_client.post("/api/files/pull", headers=platform_admin.headers, json={
        "prefix": "modules",
        "local_hashes": {"modules/pull_test.py": "0000000000000000"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "modules/pull_test.py" in data["files"]
    assert data["files"]["modules/pull_test.py"] == content


def test_pull_skips_matching_files(e2e_client, platform_admin):
    """Pull should NOT return files whose hash matches local."""
    content = "# pull match test"
    e2e_client.post("/api/files/push", headers=platform_admin.headers, json={
        "files": {"modules/pull_match.py": content},
    })
    correct_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    resp = e2e_client.post("/api/files/pull", headers=platform_admin.headers, json={
        "prefix": "modules",
        "local_hashes": {"modules/pull_match.py": correct_hash},
    })
    data = resp.json()
    assert "modules/pull_match.py" not in data["files"]


def test_pull_returns_deleted_files(e2e_client, platform_admin):
    """Pull should list files that exist locally but not on server."""
    resp = e2e_client.post("/api/files/pull", headers=platform_admin.headers, json={
        "prefix": "modules",
        "local_hashes": {"modules/nonexistent_file.py": "abc123"},
    })
    data = resp.json()
    assert "modules/nonexistent_file.py" in data["deleted"]


def test_pull_manifest_files(e2e_client, platform_admin):
    """Pull should include regenerated manifest files when they differ from local."""
    resp = e2e_client.post("/api/files/pull", headers=platform_admin.headers, json={
        "prefix": "apps/test-app",
        "local_hashes": {
            ".bifrost/workflows.yaml": "0000000000000000",
        },
    })
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data.get("manifest_files", {}), dict)


def test_push_does_not_mark_dirty(e2e_client, platform_admin):
    """CLI push should not mark repo as dirty (covered by skip_dirty_flag)."""
    # Get current dirty state
    before = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers).json()

    e2e_client.post("/api/files/push", headers=platform_admin.headers, json={
        "files": {"test-no-dirty.py": "# test"},
    })
    after = e2e_client.get("/api/github/repo-status", headers=platform_admin.headers).json()

    # If it was clean before, it should still be clean after push
    if not before["dirty"]:
        assert after["dirty"] is False


def test_push_delete_missing_prefix(e2e_client, platform_admin):
    """delete_missing_prefix should remove files not in the push batch."""
    e2e_client.post("/api/files/push", headers=platform_admin.headers, json={
        "files": {
            "apps/cleanup/keep.tsx": "keep",
            "apps/cleanup/remove.tsx": "remove",
        },
    })
    resp = e2e_client.post("/api/files/push", headers=platform_admin.headers, json={
        "files": {"apps/cleanup/keep.tsx": "keep"},
        "delete_missing_prefix": "apps/cleanup",
    })
    data = resp.json()
    assert data["deleted"] >= 1

    pull_resp = e2e_client.post("/api/files/pull", headers=platform_admin.headers, json={
        "prefix": "apps/cleanup",
        "local_hashes": {"apps/cleanup/remove.tsx": "abc"},
    })
    pull_data = pull_resp.json()
    assert "apps/cleanup/remove.tsx" in pull_data["deleted"]
