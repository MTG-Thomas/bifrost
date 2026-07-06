from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet

from src.services import github_config
from src.services.github_config import (
    GitHubConfig,
    _parse_org_id,
    delete_github_config,
    get_github_config,
    save_github_config,
)


class ScalarFirstResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def scalars(self) -> "ScalarFirstResult":
        return self

    def first(self) -> Any:
        return self.value


class FakeDb:
    def __init__(self, results: list[Any]) -> None:
        self.results = results
        self.executed: list[Any] = []
        self.added: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any) -> Any:
        self.executed.append(statement)
        return self.results.pop(0)

    def add(self, value: Any) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def fernet(monkeypatch: pytest.MonkeyPatch) -> Fernet:
    settings = SimpleNamespace(secret_key="test-secret-key-for-github-config")
    monkeypatch.setattr(github_config, "get_settings", lambda: settings)
    return github_config._get_fernet()


def _config(value_json: dict[str, Any] | None) -> SimpleNamespace:
    return SimpleNamespace(value_json=value_json, updated_at=None, updated_by=None)


def test_parse_org_id_accepts_global_none_uuid_and_invalid() -> None:
    org_uuid = uuid4()

    assert _parse_org_id(None) is None
    assert _parse_org_id("GLOBAL") is None
    assert _parse_org_id(org_uuid) == org_uuid
    assert _parse_org_id(str(org_uuid)) == org_uuid
    assert _parse_org_id("not-a-uuid") is None


@pytest.mark.asyncio
async def test_get_github_config_prefers_org_config_and_decrypts_token(fernet: Fernet) -> None:
    encrypted = fernet.encrypt(b"ghp_secret").decode()
    db = FakeDb(
        [
            ScalarFirstResult(
                _config(
                    {
                        "repo_url": "https://github.com/example/repo",
                        "encrypted_token": encrypted,
                        "branch": "develop",
                        "status": "syncing",
                        "last_synced_at": "2026-07-05T04:00:00Z",
                    }
                )
            )
        ]
    )

    result = await get_github_config(db, uuid4())  # type: ignore[arg-type]

    assert result == GitHubConfig(
        repo_url="https://github.com/example/repo",
        token="ghp_secret",
        branch="develop",
        status="syncing",
        last_synced_at="2026-07-05T04:00:00Z",
    )
    assert len(db.executed) == 1


@pytest.mark.asyncio
async def test_get_github_config_falls_back_to_global_when_org_missing(fernet: Fernet) -> None:
    encrypted = fernet.encrypt(b"global-token").decode()
    db = FakeDb(
        [
            ScalarFirstResult(None),
            ScalarFirstResult(
                _config(
                    {
                        "encrypted_token": encrypted,
                        "repo_url": None,
                    }
                )
            ),
        ]
    )

    result = await get_github_config(db, str(uuid4()))  # type: ignore[arg-type]

    assert result is not None
    assert result.token == "global-token"
    assert result.repo_url is None
    assert result.branch == "main"
    assert result.status == "connected"
    assert len(db.executed) == 2


@pytest.mark.asyncio
async def test_get_github_config_returns_none_without_global_fallback_for_global_scope() -> None:
    db = FakeDb([ScalarFirstResult(None)])

    assert await get_github_config(db, "GLOBAL") is None  # type: ignore[arg-type]
    assert len(db.executed) == 1


@pytest.mark.asyncio
async def test_get_github_config_tolerates_bad_encrypted_token(caplog: pytest.LogCaptureFixture) -> None:
    db = FakeDb([ScalarFirstResult(_config({"encrypted_token": "not-fernet"}))])

    result = await get_github_config(db, None)  # type: ignore[arg-type]

    assert result is not None
    assert result.token is None
    assert result.branch == "main"
    assert "Failed to decrypt GitHub token" in caplog.text


@pytest.mark.asyncio
async def test_save_github_config_updates_existing_config(fernet: Fernet) -> None:
    existing = _config({})
    db = FakeDb([ScalarFirstResult(existing)])
    org_uuid = uuid4()

    await save_github_config(
        db, org_uuid, "new-token", "https://github.com/example/repo", "feature", "admin@example.com"
    )  # type: ignore[arg-type]

    assert db.added == []
    assert db.commits == 1
    assert existing.updated_by == "admin@example.com"
    assert existing.value_json["repo_url"] == "https://github.com/example/repo"
    assert existing.value_json["branch"] == "feature"
    assert existing.value_json["status"] == "connected"
    assert fernet.decrypt(existing.value_json["encrypted_token"].encode()).decode() == "new-token"


@pytest.mark.asyncio
async def test_save_github_config_creates_global_config(fernet: Fernet) -> None:
    db = FakeDb([ScalarFirstResult(None)])

    await save_github_config(db, "GLOBAL", "token", None, "main", "admin@example.com")  # type: ignore[arg-type]

    assert db.commits == 1
    assert len(db.added) == 1
    added = db.added[0]
    assert added.organization_id is None
    assert added.category == "github"
    assert added.key == "integration"
    assert added.value_json["repo_url"] is None
    assert fernet.decrypt(added.value_json["encrypted_token"].encode()).decode() == "token"


@pytest.mark.asyncio
async def test_delete_github_config_commits_delete() -> None:
    db = FakeDb([None])

    await delete_github_config(db, UUID("00000000-0000-0000-0000-000000000001"))  # type: ignore[arg-type]

    assert len(db.executed) == 1
    assert db.commits == 1
