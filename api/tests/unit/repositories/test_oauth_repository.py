"""Unit tests for OAuthProviderRepository and OAuthTokenRepository.

The cross-tenant test in this file is the regression pin for the leak
that lived in ``IntegrationsRepository.get_provider_org_token`` prior to
the 2026-05 consolidation. That method took ``provider_id`` and returned
the first ``user_id IS NULL`` token with no ``organization_id`` filter
— meaning org A's CLI could surface org B's token. The new repositories
filter by org explicitly and fall back to global; they NEVER return
another org's row.

The tests use ``AsyncMock`` for the SQLAlchemy session because the
contract being asserted is "what queries does the repository make,"
not "does SQLAlchemy work" — full integration coverage lives in the
e2e suite.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.repositories.oauth import (
    OAuthProviderRepository,
    OAuthTokenRepository,
)


ORG_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ORG_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _result_returning(value) -> MagicMock:
    """Wrap a value in the ``execute().scalars().first()`` shape."""
    scalars = MagicMock()
    scalars.first = MagicMock(return_value=value)
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    return result


def _scalar_result(value) -> MagicMock:
    """Wrap a value in the ``execute().scalar_one_or_none()`` shape."""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=value)
    return result


def _all_scalars_result(values) -> MagicMock:
    """Wrap values in the ``execute().scalars().all()`` shape."""
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=values)
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    return result


@pytest.fixture
def session():
    s = AsyncMock()
    s.execute = AsyncMock()
    s.add = MagicMock()
    s.delete = AsyncMock()
    s.flush = AsyncMock()
    s.refresh = AsyncMock()
    return s


class TestOAuthTokenRepositoryCrossTenantIsolation:
    """The single most important test in this file. If any of these
    pass while a real cross-tenant leak exists, the test infrastructure
    is broken."""

    async def test_org_a_repo_returns_org_a_token(self, session) -> None:
        """Happy path: an org-scoped repo finds its own org's token."""
        provider_id = uuid4()
        org_a_token = MagicMock(organization_id=ORG_A)

        session.execute.return_value = _result_returning(org_a_token)

        repo = OAuthTokenRepository(session, org_id=ORG_A, is_superuser=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is org_a_token
        # The single query was the org-specific one; no fallback needed.
        assert session.execute.call_count == 1

    async def test_org_a_repo_does_not_return_org_b_token(self, session) -> None:
        """Cross-tenant isolation. Org A's repo MUST NOT see org B's row.

        We simulate the worst case: the SQL query was constructed wrong
        and the database returned org B's token. The repository's
        filter would catch this in real SQL, but the test pin asserts
        the filter is in place by inspecting what we asked the DB for.
        """
        provider_id = uuid4()

        # Simulate: org-specific query returns nothing (no org A token);
        # global fallback query returns nothing (no global token either).
        # Result: None. The repo MUST NOT have asked any query that would
        # match org B's row.
        session.execute.return_value = _result_returning(None)

        repo = OAuthTokenRepository(session, org_id=ORG_A, is_superuser=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is None
        # Both queries fired: org-specific then global fallback.
        assert session.execute.call_count == 2

        # Inspect the queries to confirm they filtered by ORG_A and NULL
        # only — never by ORG_B (the cross-tenant other) or by an
        # unfiltered organization_id condition.
        all_queries = [
            str(call.args[0].compile(compile_kwargs={"literal_binds": True}))
            for call in session.execute.call_args_list
        ]
        for query_str in all_queries:
            assert str(ORG_B) not in query_str, (
                f"Query referenced ORG_B's id — possible cross-tenant leak:\n{query_str}"
            )
            # The query must reference the org filter explicitly.
            assert "organization_id" in query_str, (
                f"Query did not filter by organization_id — leak risk:\n{query_str}"
            )

    async def test_global_token_used_when_no_org_specific(self, session) -> None:
        """Cascade fallback: org-specific miss falls back to global."""
        provider_id = uuid4()
        global_token = MagicMock(organization_id=None)

        # First call (org-specific): empty. Second call (global): hit.
        session.execute.side_effect = [
            _result_returning(None),
            _result_returning(global_token),
        ]

        repo = OAuthTokenRepository(session, org_id=ORG_A, is_superuser=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is global_token
        assert session.execute.call_count == 2

    async def test_org_specific_wins_over_global(self, session) -> None:
        """Cascade override: when both exist, the org-specific row wins."""
        provider_id = uuid4()
        org_a_token = MagicMock(organization_id=ORG_A)
        global_token = MagicMock(organization_id=None)

        # First call: org-specific hits — the repo MUST NOT proceed to
        # the global fallback.
        session.execute.side_effect = [
            _result_returning(org_a_token),
            _result_returning(global_token),
        ]

        repo = OAuthTokenRepository(session, org_id=ORG_A, is_superuser=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is org_a_token
        assert session.execute.call_count == 1, (
            "Repository should short-circuit on org-specific hit; "
            "if it queried global anyway, the cascade override is broken."
        )

    async def test_no_org_skips_org_specific_query(self, session) -> None:
        """When the repo has no org_id, only the global query fires."""
        provider_id = uuid4()
        global_token = MagicMock(organization_id=None)

        session.execute.return_value = _result_returning(global_token)

        repo = OAuthTokenRepository(session, org_id=None, is_superuser=True)
        result = await repo.get_org_level_for_provider(provider_id)

        assert result is global_token
        # Only the global query; org-specific is skipped when org_id is None.
        assert session.execute.call_count == 1

    async def test_for_update_locks_the_selected_token_row(self, session) -> None:
        """Rotating refresh-token callers can serialize on the token row."""
        provider_id = uuid4()
        global_token = MagicMock(organization_id=None)
        session.execute.return_value = _result_returning(global_token)

        repo = OAuthTokenRepository(session, org_id=None, is_superuser=True)
        result = await repo.get_org_level_for_provider(
            provider_id,
            for_update=True,
        )

        assert result is global_token
        query = str(session.execute.call_args.args[0])
        assert "FOR UPDATE" in query

    async def test_for_update_locks_org_token_without_global_fallback(
        self, session
    ) -> None:
        """An org token wins and is locked before any global lookup."""
        provider_id = uuid4()
        org_token = MagicMock(organization_id=ORG_A)
        session.execute.return_value = _result_returning(org_token)

        repo = OAuthTokenRepository(session, org_id=ORG_A, is_superuser=True)
        result = await repo.get_org_level_for_provider(
            provider_id,
            for_update=True,
        )

        assert result is org_token
        assert session.execute.call_count == 1
        query = str(session.execute.call_args.args[0])
        assert "FOR UPDATE" in query


class TestOAuthProviderRepositoryCascade:
    """Provider lookup also gets cascade for the same reasons as token."""

    async def test_repository_has_expected_model(self) -> None:
        from src.models.orm.oauth import OAuthProvider

        assert OAuthProviderRepository.model is OAuthProvider
        assert OAuthProviderRepository.role_table is None

    async def test_get_by_connection_name_prefers_integration_uuid_then_name(
        self, session
    ) -> None:
        """Connection names can be integration UUIDs or provider names."""
        provider = MagicMock()
        integration_id = uuid4()
        session.execute.return_value = _scalar_result(provider)
        repo = OAuthProviderRepository(session, org_id=ORG_A, is_superuser=True)

        assert await repo.get_by_connection_name(str(integration_id)) is provider
        session.execute.assert_called_once()

        session.execute.reset_mock()
        with pytest.MonkeyPatch.context() as mp:
            get = AsyncMock(return_value=provider)
            mp.setattr(repo, "get", get)
            assert await repo.get_by_connection_name("github") is provider

        session.execute.assert_not_called()
        get.assert_awaited_once_with(provider_name="github")

    async def test_create_connection_encrypts_secret_and_scopes(
        self, session, monkeypatch
    ) -> None:
        """Connection creation stores encrypted secrets and parsed scopes."""
        monkeypatch.setattr(
            "src.core.security.encrypt_secret",
            lambda value: f"encrypted:{value}",
        )
        repo = OAuthProviderRepository(session, org_id=ORG_A, is_superuser=True)

        provider = await repo.create_connection(
            connection_name="github",
            display_name="GitHub",
            description="Source control",
            oauth_flow_type="authorization_code",
            client_id="client-id",
            client_secret="secret",
            authorization_url="https://example.test/auth",
            token_url="https://example.test/token",
            scopes_csv="repo,user",
            created_by="admin@example.com",
            provider_metadata={"kind": "github"},
        )

        assert provider.organization_id == ORG_A
        assert provider.provider_name == "github"
        assert provider.encrypted_client_secret == b"encrypted:secret"
        assert provider.scopes == ["repo", "user"]
        assert provider.provider_metadata == {"kind": "github"}
        session.add.assert_called_once_with(provider)
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(provider)

    async def test_update_connection_mutates_only_supplied_fields(
        self, session, monkeypatch
    ) -> None:
        """Updating a connection changes supplied fields and keeps others intact."""
        from src.models.orm.oauth import OAuthProvider

        monkeypatch.setattr(
            "src.core.security.encrypt_secret",
            lambda value: f"encrypted:{value}",
        )
        provider = OAuthProvider(
            id=uuid4(),
            provider_name="github",
            display_name="Old",
            oauth_flow_type="authorization_code",
            client_id="old-client",
            encrypted_client_secret=b"old",
            scopes=["old"],
            provider_metadata={"old": True},
        )
        repo = OAuthProviderRepository(session, org_id=ORG_A, is_superuser=True)
        monkeypatch.setattr(
            repo, "get_by_connection_name", AsyncMock(return_value=provider)
        )

        result = await repo.update_connection(
            "github",
            name="New",
            client_id="new-client",
            client_secret="new-secret",
            scopes=["repo"],
            audience="api://aud",
            provider_metadata={"new": True},
        )

        assert result is provider
        assert provider.display_name == "New"
        assert provider.client_id == "new-client"
        assert provider.encrypted_client_secret == b"encrypted:new-secret"
        assert provider.scopes == ["repo"]
        assert provider.audience == "api://aud"
        assert provider.provider_metadata == {"new": True}
        assert provider.oauth_flow_type == "authorization_code"
        assert provider.updated_at is not None
        session.flush.assert_awaited_once()
        session.refresh.assert_awaited_once_with(provider)

    async def test_delete_connection_deletes_tokens_before_provider(
        self, session, monkeypatch
    ) -> None:
        """Deleting a provider removes associated token rows first."""
        provider = MagicMock(id=uuid4())
        tokens = [MagicMock(), MagicMock()]
        session.execute.return_value = _all_scalars_result(tokens)
        repo = OAuthProviderRepository(session, org_id=ORG_A, is_superuser=True)
        monkeypatch.setattr(
            repo, "get_by_connection_name", AsyncMock(return_value=provider)
        )

        assert await repo.delete_connection("github") is True

        assert session.delete.await_args_list[0].args == (tokens[0],)
        assert session.delete.await_args_list[1].args == (tokens[1],)
        assert session.delete.await_args_list[2].args == (provider,)
        session.flush.assert_awaited_once()

    async def test_status_token_and_detail_helpers_handle_missing_and_present(
        self, session, monkeypatch
    ) -> None:
        """Status, token lookup, and detail projection use provider scope."""
        from src.models.orm.oauth import OAuthProvider, OAuthToken

        provider = OAuthProvider(
            id=uuid4(),
            provider_name="github",
            display_name="GitHub",
            description="Source control",
            oauth_flow_type="authorization_code",
            client_id="client",
            encrypted_client_secret=b"",
            authorization_url="https://example.test/auth",
            token_url="https://example.test/token",
            scopes=["repo", "user"],
            audience="api://aud",
            provider_metadata={"kind": "github"},
            status="not_connected",
            status_message=None,
            created_by="admin@example.com",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        token = OAuthToken(
            id=uuid4(),
            provider_id=provider.id,
            organization_id=ORG_A,
            encrypted_access_token=b"access",
            encrypted_refresh_token=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        repo = OAuthProviderRepository(session, org_id=ORG_A, is_superuser=True)
        monkeypatch.setattr(
            repo, "get_by_connection_name", AsyncMock(return_value=provider)
        )

        assert await repo.update_status("github", "completed", "ok") is True
        assert provider.status == "completed"
        assert provider.status_message == "ok"

        session.execute.return_value = _scalar_result(token)
        assert await repo.get_token("github") is token

        monkeypatch.setattr(repo, "get_token", AsyncMock(return_value=token))
        detail = await repo.to_detail(provider)

        assert detail.connection_name == "github"
        assert detail.name == "GitHub"
        assert detail.scopes == "repo user"
        assert detail.expires_at == token.expires_at
        assert detail.created_by == "admin@example.com"

        missing_repo = OAuthProviderRepository(
            session, org_id=ORG_A, is_superuser=True
        )
        monkeypatch.setattr(
            missing_repo, "get_by_connection_name", AsyncMock(return_value=None)
        )
        assert await missing_repo.update_status("missing", "failed") is False
        assert await missing_repo.get_token("missing") is None
