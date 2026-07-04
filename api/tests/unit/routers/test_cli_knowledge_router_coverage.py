from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from src.core.auth import UserPrincipal
from src.models.contracts.cli import (
    CLIKnowledgeDeleteRequest,
    CLIKnowledgeSearchRequest,
    CLIKnowledgeStoreManyRequest,
    CLIKnowledgeStoreRequest,
)
from src.routers import cli


ORG_ID = "11111111-1111-1111-1111-111111111111"


def _principal(*, is_external: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="admin@example.com",
        organization_id=uuid4(),
        is_superuser=False,
        is_external=is_external,
    )


def _db() -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    return db


def _embedding_client() -> MagicMock:
    client = MagicMock()
    client.embed_single = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return client


class TestCLIKnowledgeStore:
    @pytest.mark.asyncio
    async def test_store_uses_resolved_org_embedder_and_commits(self):
        db = _db()
        repo = MagicMock()
        repo.store_chunked = AsyncMock(return_value=["doc-1"])
        embedder = _embedding_client()
        request = CLIKnowledgeStoreRequest(
            content="Runbook text",
            namespace="ops",
            key="runbook",
            metadata={"source": "test"},
            scope=ORG_ID,
        )
        user = _principal()

        with (
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=ORG_ID)),
            patch(
                "src.services.embeddings.get_embedding_client",
                AsyncMock(return_value=embedder),
            ),
            patch(
                "src.repositories.knowledge.KnowledgeRepository",
                return_value=repo,
            ) as repo_cls,
        ):
            result = await cli.cli_knowledge_store(request, user, db)

        assert result == {"id": "doc-1"}
        repo_cls.assert_called_once()
        assert repo_cls.call_args.kwargs["org_id"].hex == ORG_ID.replace("-", "")
        repo.store_chunked.assert_awaited_once_with(
            content="Runbook text",
            namespace="ops",
            key="runbook",
            metadata={"source": "test"},
            created_by=user.user_id,
            embedder=embedder,
        )
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_store_maps_missing_embedding_config_to_503(self):
        db = _db()
        request = CLIKnowledgeStoreRequest(content="text")

        with (
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=None)),
            patch(
                "src.services.embeddings.get_embedding_client",
                AsyncMock(side_effect=ValueError("No embedding configuration found")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await cli.cli_knowledge_store(request, _principal(), db)

        assert exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_store_many_persists_each_document(self):
        db = _db()
        repo = MagicMock()
        repo.store_chunked = AsyncMock(side_effect=[["doc-1"], ["doc-2"]])
        embedder = _embedding_client()
        request = CLIKnowledgeStoreManyRequest(
            namespace="kb",
            documents=[
                {"content": "one", "key": "a", "metadata": {"i": 1}},
                {"content": "two", "key": "b"},
            ],
        )

        with (
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=None)),
            patch(
                "src.services.embeddings.get_embedding_client",
                AsyncMock(return_value=embedder),
            ),
            patch("src.repositories.knowledge.KnowledgeRepository", return_value=repo),
        ):
            result = await cli.cli_knowledge_store_many(request, _principal(), db)

        assert result == {"ids": ["doc-1", "doc-2"]}
        assert repo.store_chunked.await_count == 2
        assert repo.store_chunked.await_args_list[0].kwargs["content"] == "one"
        assert repo.store_chunked.await_args_list[1].kwargs["metadata"] is None
        db.commit.assert_awaited_once()


class TestCLIKnowledgeSearchAndDelete:
    @pytest.mark.asyncio
    async def test_search_returns_response_models_with_iso_created_at(self):
        db = _db()
        created_at = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)
        repo = MagicMock()
        repo.search = AsyncMock(
            return_value=[
                SimpleNamespace(
                    id="doc-1",
                    namespace="kb",
                    content="answer",
                    metadata={"kind": "runbook"},
                    score=0.91,
                    organization_id=ORG_ID,
                    key="runbook",
                    created_at=created_at,
                )
            ]
        )
        embedder = _embedding_client()
        request = CLIKnowledgeSearchRequest(
            query="how do I fix it?",
            namespace=["kb"],
            limit=3,
            min_score=0.5,
            metadata_filter={"kind": "runbook"},
            fallback=False,
            scope=ORG_ID,
        )

        with (
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=ORG_ID)),
            patch(
                "src.services.embeddings.get_embedding_client",
                AsyncMock(return_value=embedder),
            ),
            patch("src.repositories.knowledge.KnowledgeRepository", return_value=repo),
        ):
            result = await cli.cli_knowledge_search(request, _principal(), db)

        assert result[0].id == "doc-1"
        assert result[0].created_at == "2026-07-04T12:00:00+00:00"
        embedder.embed_single.assert_awaited_once_with("how do I fix it?")
        repo.search.assert_awaited_once_with(
            query_embedding=[0.1, 0.2, 0.3],
            namespace=["kb"],
            limit=3,
            min_score=0.5,
            metadata_filter={"kind": "runbook"},
            fallback=False,
        )

    @pytest.mark.asyncio
    async def test_delete_by_key_commits_deleted_count(self):
        db = _db()
        repo = MagicMock()
        repo.delete_by_key = AsyncMock(return_value=2)
        request = CLIKnowledgeDeleteRequest(key="runbook", namespace="kb")

        with (
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=None)),
            patch("src.repositories.knowledge.KnowledgeRepository", return_value=repo),
        ):
            result = await cli.cli_knowledge_delete(request, _principal(), db)

        assert result == {"deleted": 2}
        repo.delete_by_key.assert_awaited_once_with(key="runbook", namespace="kb")
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_namespace_and_list_namespaces(self):
        db = _db()
        repo = MagicMock()
        repo.delete_namespace = AsyncMock(return_value=4)
        repo.list_namespaces = AsyncMock(
            return_value=[
                SimpleNamespace(
                    namespace="kb",
                    scopes={"global": 1, "org": 2, "total": 3},
                )
            ]
        )

        with (
            patch.object(cli, "_is_external_user_db", AsyncMock(return_value=False)),
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=ORG_ID)),
            patch("src.repositories.knowledge.KnowledgeRepository", return_value=repo),
        ):
            deleted = await cli.cli_knowledge_delete_namespace(
                "kb",
                scope=ORG_ID,
                current_user=_principal(),
                db=db,
            )
            listed = await cli.cli_knowledge_list_namespaces(
                scope=ORG_ID,
                include_global=False,
                current_user=_principal(),
                db=db,
            )

        assert deleted == {"deleted_count": 4}
        assert listed[0].namespace == "kb"
        assert listed[0].scopes == {"global": 1, "org": 2, "total": 3}
        repo.delete_namespace.assert_awaited_once_with(namespace="kb")
        repo.list_namespaces.assert_awaited_once_with(include_global=False)

    @pytest.mark.asyncio
    async def test_get_returns_document_or_404(self):
        db = _db()
        repo = MagicMock()
        repo.get_by_key = AsyncMock(
            return_value=SimpleNamespace(
                id="doc-1",
                namespace="kb",
                content="answer",
                metadata={},
                organization_id=None,
                key="runbook",
                created_at=None,
            )
        )

        with (
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=None)),
            patch("src.repositories.knowledge.KnowledgeRepository", return_value=repo),
        ):
            result = await cli.cli_knowledge_get(
                key="runbook",
                namespace="kb",
                current_user=_principal(),
                db=db,
            )

        assert result.id == "doc-1"
        assert result.created_at is None

        repo.get_by_key = AsyncMock(return_value=None)
        with (
            patch.object(cli, "_resolve_sdk_org_id", AsyncMock(return_value=None)),
            patch("src.repositories.knowledge.KnowledgeRepository", return_value=repo),
        ):
            with pytest.raises(HTTPException) as exc:
                await cli.cli_knowledge_get(
                    key="missing",
                    namespace="kb",
                    current_user=_principal(),
                    db=db,
                )

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND
