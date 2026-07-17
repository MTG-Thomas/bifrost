"""HTTP contract coverage for authoritative workspace _repo changesets."""

import base64
from uuid import uuid4

import pytest

from src.services.repo_storage import RepoStorage


@pytest.mark.e2e
def test_workspace_repo_changeset_stages_validates_and_activates_atomically(
    e2e_client, platform_admin
):
    scope = f"test_changesets_{uuid4().hex}"
    path = f"{scope}/hello.txt"
    headers = platform_admin.headers
    content = b"hello from a changeset\n"
    try:
        state = e2e_client.get(
            "/api/workspace-repo-changesets/state",
            headers=headers,
            params={"scope": scope},
        )
        assert state.status_code == 200, state.text
        assert state.json()["storage_root"] == "_repo"

        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=headers,
            json={
                "scope": scope,
                "base_revision": state.json()["revision"],
                "worker_id": "e2e-worker",
            },
        )
        assert started.status_code == 201, started.text
        changeset_id = started.json()["id"]

        staged = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/files",
            headers=headers,
            json={
                "path": path,
                "operation": "write",
                "content_base64": base64.b64encode(content).decode(),
            },
        )
        assert staged.status_code == 200, staged.text
        assert staged.json()["status"] == "staged"

        # Staging never exposes content through the authoritative files API.
        absent = e2e_client.post(
            "/api/files/exists", headers=headers, json={"path": path}
        )
        assert absent.status_code == 200
        assert absent.json()["exists"] is False

        validated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/validate", headers=headers
        )
        assert validated.status_code == 200, validated.text
        assert validated.json()["valid"] is True

        activated = e2e_client.post(
            f"/api/workspace-repo-changesets/{changeset_id}/activate",
            headers=headers,
            json={},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["status"] == "activated"

        read = e2e_client.post("/api/files/read", headers=headers, json={"path": path})
        assert read.status_code == 200, read.text
        assert read.json()["content"] == content.decode()
    finally:
        # The test owns this unique prefix and may clean it directly.
        import asyncio

        asyncio.run(RepoStorage().delete(path))
