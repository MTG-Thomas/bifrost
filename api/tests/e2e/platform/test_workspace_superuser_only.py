"""The `workspace` location is superuser-only and bypasses file policies.

Regression guard for the CLI `sync`/`watch` 403: the file-policies feature
made `/api/files/list` and `/api/files/read` evaluate file policies even for
`location="workspace"`. With no policy row present (the normal case — nobody
sets policies on the shared codebase), the policy service default-denies, so a
superuser running `bifrost sync` got a 403. The CLI swallowed that and reported
every local file as "new locally" → a mass push.

`workspace` is the shared platform codebase: it must be reachable by any
superuser WITHOUT a granted file policy, and denied to non-superusers. These
tests assert that directly, with NO `grant_file_policy` scaffolding.
"""
import hashlib
import uuid


def _write(e2e_client, headers, path, content):
    return e2e_client.post("/api/files/write", headers=headers, json={
        "path": path,
        "content": content,
        "mode": "cloud",
        "location": "workspace",
        "binary": False,
    })


def test_superuser_lists_workspace_without_policy(e2e_client, platform_admin):
    """A superuser can list the workspace with no file policy granted."""
    resp = e2e_client.post("/api/files/list", headers=platform_admin.headers, json={
        "include_metadata": True,
        "mode": "cloud",
        "location": "workspace",
    })
    assert resp.status_code == 200, f"workspace list denied: {resp.status_code} {resp.text}"


def test_superuser_write_then_read_workspace_without_policy(e2e_client, platform_admin):
    """A superuser can write and read back a workspace file with no policy."""
    path = "modules/_ws_superuser_probe.py"
    content = "# workspace superuser probe\n"
    w = _write(e2e_client, platform_admin.headers, path, content)
    assert w.status_code == 204, f"workspace write denied: {w.status_code} {w.text}"

    r = e2e_client.post("/api/files/read", headers=platform_admin.headers, json={
        "path": path,
        "mode": "cloud",
        "location": "workspace",
        "binary": False,
    })
    assert r.status_code == 200, f"workspace read denied: {r.status_code} {r.text}"
    assert r.json()["content"] == content


def test_superuser_list_metadata_etag_matches_local_md5(e2e_client, platform_admin):
    """The metadata listing returns plain-MD5 etags the CLI diff relies on."""
    path = "modules/_ws_etag_probe.py"
    content = "print('etag')\n"
    assert _write(e2e_client, platform_admin.headers, path, content).status_code == 204

    resp = e2e_client.post("/api/files/list", headers=platform_admin.headers, json={
        "include_metadata": True,
        "mode": "cloud",
        "location": "workspace",
    })
    assert resp.status_code == 200, resp.text
    meta = {m["path"]: m for m in resp.json()["files_metadata"]}
    assert path in meta, f"{path} not in listing"
    expected = hashlib.md5(content.encode()).hexdigest()
    assert meta[path]["etag"] == expected


def test_non_superuser_denied_workspace(e2e_client, non_admin_user):
    """A non-superuser cannot touch the workspace location at all."""
    resp = e2e_client.post("/api/files/list", headers=non_admin_user.headers, json={
        "include_metadata": True,
        "mode": "cloud",
        "location": "workspace",
    })
    assert resp.status_code == 403, f"expected 403 for non-admin, got {resp.status_code}"


def test_access_test_endpoint_reports_workspace_superuser_only(e2e_client, platform_admin):
    """The Test Access endpoint must mirror real enforcement: workspace is
    superuser-only and ignores any policy row, so a superuser is allowed."""
    resp = e2e_client.post(
        "/api/files/policies/test",
        headers=platform_admin.headers,
        json={"path": "modules/anything.py", "location": "workspace", "action": "read"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["allowed"] is True
    assert body["matched_policy"] is None


def test_workspace_impact_candidate_guards_http_write(e2e_client, platform_admin):
    """Impact preview and checked write round-trip through the real API."""
    path = f"modules/_impact_e2e_{uuid.uuid4().hex}.py"
    content = "VALUE = 1\n"
    try:
        preview = e2e_client.post(
            "/api/files/impact",
            headers=platform_admin.headers,
            json={"path": path, "content": content, "direction": "both"},
        )
        assert preview.status_code == 200, preview.text
        impact = preview.json()
        assert impact["ready_to_write"] is True
        assert impact["path"] == path
        assert impact["candidate_id"].startswith("sha256:")

        write = e2e_client.post(
            "/api/files/write",
            headers=platform_admin.headers,
            json={
                "path": path,
                "content": content,
                "mode": "cloud",
                "location": "workspace",
                "binary": False,
                "impact_candidate_id": impact["candidate_id"],
            },
        )
        assert write.status_code == 204, write.text

        read = e2e_client.post(
            "/api/files/read",
            headers=platform_admin.headers,
            json={"path": path, "location": "workspace", "binary": False},
        )
        assert read.status_code == 200, read.text
        assert read.json()["content"] == content
    finally:
        e2e_client.post(
            "/api/files/delete",
            headers=platform_admin.headers,
            json={"path": path, "location": "workspace", "mode": "cloud"},
        )


def test_workspace_impact_rejects_non_python_path(e2e_client, platform_admin):
    response = e2e_client.post(
        "/api/files/impact",
        headers=platform_admin.headers,
        json={"path": "reports/not-python.txt", "content": "text"},
    )

    assert response.status_code == 422
