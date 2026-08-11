"""HTTP contract coverage for authoritative workspace _repo changesets."""

import base64
from uuid import UUID, uuid4

import pytest
from git import Repo as GitRepo
from sqlalchemy import delete, select

from src.models.orm.config import SystemConfig
from src.models.orm.users import User
from src.models.orm.workspace_repo_changesets import WorkspaceRepoChangeset
from src.services.github_config import save_github_config
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


@pytest.mark.e2e
def test_workspace_repo_git_closure_retry_requires_authentication(e2e_client):
    response = e2e_client.post(
        f"/api/workspace-repo-changesets/{uuid4()}/retry-git-closure",
        json={"commit_message": "e2e retry", "push": True},
    )

    assert response.status_code in {401, 403}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_workspace_repo_git_closure_retry_is_org_scoped_and_state_guarded(
    e2e_client, platform_admin, org1, db_session
):
    admin = (
        await db_session.execute(
            select(User).where(User.id == platform_admin.user_id)
        )
    ).scalar_one()
    assert admin.organization_id is not None
    cross_org = WorkspaceRepoChangeset(
        organization_id=UUID(org1["id"]),
        scope=f"test_changesets_{uuid4().hex}",
        base_revision="0" * 64,
        base_files={},
        mutations=[],
        status="activated",
        created_by=admin.id,
        failure_detail={"phase": "git_closure", "state": "failed"},
    )
    db_session.add(cross_org)
    await db_session.commit()
    try:
        scoped = e2e_client.post(
            f"/api/workspace-repo-changesets/{cross_org.id}/retry-git-closure",
            headers=platform_admin.headers,
            json={"commit_message": "e2e retry", "push": True},
        )
        assert scoped.status_code == 404, scoped.text

        started = e2e_client.post(
            "/api/workspace-repo-changesets",
            headers=platform_admin.headers,
            json={"scope": f"test_changesets_{uuid4().hex}"},
        )
        assert started.status_code == 201, started.text
        invalid = e2e_client.post(
            f"/api/workspace-repo-changesets/{started.json()['id']}/retry-git-closure",
            headers=platform_admin.headers,
            json={"commit_message": "e2e retry", "push": True},
        )
        assert invalid.status_code == 422, invalid.text
    finally:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(
                WorkspaceRepoChangeset.id.in_([cross_org.id, started.json()["id"]])
            )
        )
        await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_workspace_repo_git_closure_retry_succeeds_without_reactivation(
    e2e_client, platform_admin, db_session, tmp_path
):
    admin = (
        await db_session.execute(
            select(User).where(User.id == platform_admin.user_id)
        )
    ).scalar_one()
    assert admin.organization_id is not None
    storage = RepoStorage()
    pollution_path = f"workflows/preexisting-invalid-{uuid4().hex}.py"
    pollution_content = b"def intentionally_invalid(:\n"
    await storage.write(pollution_path, pollution_content)
    original_files = {
        path: await storage.read(path) for path in await storage.list()
    }
    for path in original_files:
        await storage.delete(path)
    await save_github_config(
        db_session,
        admin.organization_id,
        "e2e-placeholder-token",
        "https://github.com/example/repo",
        "main",
        "e2e",
    )
    repo = GitRepo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Bifrost E2E")
        config.set_value("user", "email", "e2e@example.test")
    seed = tmp_path / "README.md"
    seed.write_text("e2e git closure seed\n")
    repo.git.add(A=True)
    repo.index.commit("seed e2e repository")
    for repo_path in tmp_path.rglob("*"):
        if repo_path.is_file():
            relative = repo_path.relative_to(tmp_path).as_posix()
            await storage.write(relative, repo_path.read_bytes())
    assert await storage.exists(".git/HEAD")
    row = WorkspaceRepoChangeset(
        organization_id=admin.organization_id,
        scope=f"test_changesets_{uuid4().hex}",
        base_revision="0" * 64,
        base_files={},
        mutations=[],
        status="activated",
        created_by=admin.id,
        activated_revision="a" * 64,
        failure_detail={"phase": "git_closure", "state": "failed"},
    )
    db_session.add(row)
    await db_session.commit()
    try:
        retried = e2e_client.post(
            f"/api/workspace-repo-changesets/{row.id}/retry-git-closure",
            headers=platform_admin.headers,
            json={"commit_message": "e2e retry", "push": False},
        )

        assert retried.status_code == 200, retried.text
        assert retried.json()["status"] == "committed", retried.text
        assert retried.json()["activated_revision"] == "a" * 64
        assert retried.json()["failure_detail"] is None
    finally:
        await db_session.execute(
            delete(WorkspaceRepoChangeset).where(WorkspaceRepoChangeset.id == row.id)
        )
        await db_session.execute(
            delete(SystemConfig).where(
                SystemConfig.organization_id == admin.organization_id,
                SystemConfig.category == "github",
                SystemConfig.key == "integration",
            )
        )
        await db_session.commit()
        for path in await storage.list():
            await storage.delete(path)
        for path, content in original_files.items():
            await storage.write(path, content)
        assert await storage.read(pollution_path) == pollution_content
        await storage.delete(pollution_path)
