"""
Knowledge Store SDK for Bifrost.

Provides Python API for semantic search and RAG (Retrieval Augmented Generation).
Uses pgvector for vector similarity search with org-scoped namespaces.

Works in two modes:
1. Platform context (inside workflows): Direct database access
2. External context (via dev API key): API calls to SDK endpoints

All methods are async and must be awaited.

Usage:
    from bifrost import knowledge

    # Store a document
    await knowledge.store(
        content="Our refund policy allows returns within 30 days.",
        namespace="policies",
        key="refund-policy",
        metadata={"source": "handbook"}
    )

    # Search for similar documents
    results = await knowledge.search(
        "What is the return window?",
        namespace="policies",
        limit=5
    )
    for doc in results:
        print(doc.content, doc.score)

    # List namespaces
    namespaces = await knowledge.list_namespaces()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ._context import _execution_context

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeDocument:
    """
    Document from knowledge store.

    Attributes:
        id: Document UUID
        namespace: Namespace the document belongs to
        content: Text content
        metadata: Associated metadata dict
        score: Similarity score (0-1) when returned from search
        organization_id: Organization scope (None for global)
        key: User-provided key (if any)
        created_at: Creation timestamp
    """

    id: str
    namespace: str
    content: str
    metadata: dict[str, Any]
    score: float | None = None
    organization_id: str | None = None
    key: str | None = None
    created_at: datetime | None = None


@dataclass
class NamespaceInfo:
    """
    Information about a namespace.

    Attributes:
        namespace: Namespace name
        scopes: Dict with counts - {"global": N, "org": N, "total": N}
    """

    namespace: str
    scopes: dict[str, int]


def _is_platform_context() -> bool:
    """Check if running inside platform execution context."""
    return _execution_context.get() is not None


def _get_client():
    """Get the BifrostClient for API calls."""
    from .client import get_client
    return get_client()


def _get_org_id(org_id: str | None, scope: str | None) -> UUID | None:
    """
    Resolve organization ID from parameters or context.

    Args:
        org_id: Explicit org ID
        scope: Scope string ("global" for None)

    Returns:
        UUID or None for global scope
    """
    if scope == "global":
        return None
    if org_id:
        return UUID(org_id) if isinstance(org_id, str) else org_id

    if _is_platform_context():
        from ._internal import get_context
        context = get_context()
        target_org_id = getattr(context, 'org_id', None) or getattr(context, 'scope', None)
        if target_org_id:
            return UUID(target_org_id) if isinstance(target_org_id, str) else target_org_id

    return None


class knowledge:
    """
    Knowledge store operations.

    Provides semantic search and storage for RAG.
    Documents are scoped to organizations with global fallback.
    """

    @staticmethod
    async def store(
        content: str,
        *,
        namespace: str = "default",
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
        org_id: str | None = None,
        scope: str | None = None,
    ) -> str:
        """
        Store a document in the knowledge store.

        If key is provided and exists, updates the existing document (upsert).

        Args:
            content: Text content to store and embed
            namespace: Namespace for organization (defaults to "default")
            key: Optional key for upserts (e.g., "ticket-123")
            metadata: Optional metadata dict
            org_id: Organization scope (defaults to current org)
            scope: "global" for global scope, otherwise uses org_id

        Returns:
            Document ID (UUID as string)

        Example:
            >>> from bifrost import knowledge
            >>> doc_id = await knowledge.store(
            ...     "Our refund policy allows returns within 30 days.",
            ...     namespace="policies",
            ...     key="refund-policy",
            ...     metadata={"source": "handbook"}
            ... )
        """
        if _is_platform_context():
            # Direct database access
            from ._internal import get_context
            from src.repositories.knowledge import KnowledgeRepository
            from src.services.embeddings import get_embedding_client

            context = get_context()
            target_org_id = _get_org_id(org_id, scope)
            user_id = getattr(context, 'user_id', None)
            if user_id and isinstance(user_id, str):
                user_id = UUID(user_id)

            # Generate embedding
            embedding_client = await get_embedding_client(context.db)
            embedding = await embedding_client.embed_single(content)

            # Store in database
            repo = KnowledgeRepository(context.db)
            doc_id = await repo.store(
                content=content,
                embedding=embedding,
                namespace=namespace,
                key=key,
                metadata=metadata,
                organization_id=target_org_id,
                created_by=user_id,
            )

            logger.debug(
                f"knowledge.store: namespace={namespace}, key={key}, "
                f"org_id={target_org_id}, doc_id={doc_id}"
            )

            return doc_id
        else:
            # API call (external mode)
            client = _get_client()
            response = await client.post(
                "/api/cli/knowledge/store",
                json={
                    "content": content,
                    "namespace": namespace,
                    "key": key,
                    "metadata": metadata,
                    "org_id": org_id,
                    "scope": scope,
                }
            )
            response.raise_for_status()
            return response.json()["id"]

    @staticmethod
    async def store_many(
        documents: list[dict[str, Any]],
        *,
        namespace: str = "default",
        org_id: str | None = None,
        scope: str | None = None,
    ) -> list[str]:
        """
        Store multiple documents efficiently.

        Each document dict should have:
        - content (required): Text content
        - key (optional): Key for upserts
        - metadata (optional): Metadata dict

        Args:
            documents: List of document dicts
            namespace: Namespace for all documents
            org_id: Organization scope
            scope: "global" for global scope

        Returns:
            List of document IDs

        Example:
            >>> ids = await knowledge.store_many([
            ...     {"content": "Doc 1", "key": "doc-1", "metadata": {"type": "faq"}},
            ...     {"content": "Doc 2", "key": "doc-2", "metadata": {"type": "faq"}},
            ... ], namespace="faq")
        """
        if _is_platform_context():
            from ._internal import get_context
            from src.repositories.knowledge import KnowledgeRepository
            from src.services.embeddings import get_embedding_client

            context = get_context()
            target_org_id = _get_org_id(org_id, scope)
            user_id = getattr(context, 'user_id', None)
            if user_id and isinstance(user_id, str):
                user_id = UUID(user_id)

            # Extract contents for batch embedding
            contents = [doc["content"] for doc in documents]

            # Batch generate embeddings
            embedding_client = await get_embedding_client(context.db)
            embeddings = await embedding_client.embed(contents)

            # Store each document
            repo = KnowledgeRepository(context.db)
            doc_ids = []
            for doc, embedding in zip(documents, embeddings):
                doc_id = await repo.store(
                    content=doc["content"],
                    embedding=embedding,
                    namespace=namespace,
                    key=doc.get("key"),
                    metadata=doc.get("metadata"),
                    organization_id=target_org_id,
                    created_by=user_id,
                )
                doc_ids.append(doc_id)

            return doc_ids
        else:
            client = _get_client()
            response = await client.post(
                "/api/cli/knowledge/store-many",
                json={
                    "documents": documents,
                    "namespace": namespace,
                    "org_id": org_id,
                    "scope": scope,
                }
            )
            response.raise_for_status()
            return response.json()["ids"]

    @staticmethod
    async def search(
        query: str,
        *,
        namespace: str | list[str] = "default",
        limit: int = 5,
        min_score: float | None = None,
        metadata_filter: dict[str, Any] | None = None,
        org_id: str | None = None,
        fallback: bool = True,
    ) -> list[KnowledgeDocument]:
        """
        Search for similar documents.

        Uses semantic similarity (vector search) to find relevant documents.

        Args:
            query: Search query (will be embedded)
            namespace: Namespace(s) to search
            limit: Maximum results (default 5)
            min_score: Minimum similarity score (0-1)
            metadata_filter: Filter by metadata fields (e.g., {"status": "open"})
            org_id: Organization scope (defaults to current org)
            fallback: If True, also search global scope (default True)

        Returns:
            List of KnowledgeDocument sorted by similarity

        Example:
            >>> results = await knowledge.search(
            ...     "password reset",
            ...     namespace="tickets",
            ...     metadata_filter={"status": "open"},
            ...     limit=10
            ... )
            >>> for doc in results:
            ...     print(f"{doc.score:.2f}: {doc.content[:100]}")
        """
        if _is_platform_context():
            from ._internal import get_context
            from src.repositories.knowledge import KnowledgeRepository
            from src.services.embeddings import get_embedding_client

            context = get_context()
            target_org_id = _get_org_id(org_id, None)

            # Generate query embedding
            embedding_client = await get_embedding_client(context.db)
            query_embedding = await embedding_client.embed_single(query)

            # Search
            repo = KnowledgeRepository(context.db)
            results = await repo.search(
                query_embedding=query_embedding,
                namespace=namespace,
                organization_id=target_org_id,
                limit=limit,
                min_score=min_score,
                metadata_filter=metadata_filter,
                fallback=fallback,
            )

            # Convert to SDK dataclass
            return [
                KnowledgeDocument(
                    id=doc.id,
                    namespace=doc.namespace,
                    content=doc.content,
                    metadata=doc.metadata,
                    score=doc.score,
                    organization_id=doc.organization_id,
                    key=doc.key,
                    created_at=doc.created_at,
                )
                for doc in results
            ]
        else:
            client = _get_client()
            response = await client.post(
                "/api/cli/knowledge/search",
                json={
                    "query": query,
                    "namespace": namespace if isinstance(namespace, list) else [namespace],
                    "limit": limit,
                    "min_score": min_score,
                    "metadata_filter": metadata_filter,
                    "org_id": org_id,
                    "fallback": fallback,
                }
            )
            response.raise_for_status()
            return [
                KnowledgeDocument(
                    id=doc["id"],
                    namespace=doc["namespace"],
                    content=doc["content"],
                    metadata=doc.get("metadata", {}),
                    score=doc.get("score"),
                    organization_id=doc.get("organization_id"),
                    key=doc.get("key"),
                    created_at=doc.get("created_at"),
                )
                for doc in response.json()
            ]

    @staticmethod
    async def delete(
        key: str,
        *,
        namespace: str = "default",
        org_id: str | None = None,
        scope: str | None = None,
    ) -> bool:
        """
        Delete a document by key.

        Args:
            key: Document key
            namespace: Namespace
            org_id: Organization scope
            scope: "global" for global scope

        Returns:
            True if deleted, False if not found

        Example:
            >>> deleted = await knowledge.delete("ticket-123", namespace="tickets")
        """
        if _is_platform_context():
            from ._internal import get_context
            from src.repositories.knowledge import KnowledgeRepository

            context = get_context()
            target_org_id = _get_org_id(org_id, scope)

            repo = KnowledgeRepository(context.db)
            return await repo.delete_by_key(
                key=key,
                namespace=namespace,
                organization_id=target_org_id,
            )
        else:
            client = _get_client()
            response = await client.post(
                "/api/cli/knowledge/delete",
                json={
                    "key": key,
                    "namespace": namespace,
                    "org_id": org_id,
                    "scope": scope,
                }
            )
            response.raise_for_status()
            return response.json()["deleted"]

    @staticmethod
    async def delete_namespace(
        namespace: str,
        *,
        org_id: str | None = None,
        scope: str | None = None,
    ) -> int:
        """
        Delete all documents in a namespace.

        Args:
            namespace: Namespace to delete
            org_id: Organization scope
            scope: "global" for global scope

        Returns:
            Number of documents deleted

        Example:
            >>> count = await knowledge.delete_namespace("old-data")
            >>> print(f"Deleted {count} documents")
        """
        if _is_platform_context():
            from ._internal import get_context
            from src.repositories.knowledge import KnowledgeRepository

            context = get_context()
            target_org_id = _get_org_id(org_id, scope)

            repo = KnowledgeRepository(context.db)
            return await repo.delete_namespace(
                namespace=namespace,
                organization_id=target_org_id,
            )
        else:
            client = _get_client()
            response = await client.delete(
                f"/api/cli/knowledge/namespace/{namespace}",
                params={"org_id": org_id, "scope": scope},
            )
            response.raise_for_status()
            return response.json()["deleted_count"]

    @staticmethod
    async def list_namespaces(
        org_id: str | None = None,
        include_global: bool = True,
    ) -> list[NamespaceInfo]:
        """
        List available namespaces with document counts per scope.

        Args:
            org_id: Filter to specific org (defaults to current org)
            include_global: If True, include global namespaces (default True)

        Returns:
            List of NamespaceInfo with scope counts

        Example:
            >>> namespaces = await knowledge.list_namespaces()
            >>> for ns in namespaces:
            ...     print(f"{ns.namespace}: {ns.scopes['total']} docs")
        """
        if _is_platform_context():
            from ._internal import get_context
            from src.repositories.knowledge import KnowledgeRepository

            context = get_context()
            target_org_id = _get_org_id(org_id, None)

            repo = KnowledgeRepository(context.db)
            results = await repo.list_namespaces(
                organization_id=target_org_id,
                include_global=include_global,
            )

            return [
                NamespaceInfo(
                    namespace=ns.namespace,
                    scopes=ns.scopes,
                )
                for ns in results
            ]
        else:
            client = _get_client()
            response = await client.get(
                "/api/cli/knowledge/namespaces",
                params={"org_id": org_id, "include_global": include_global},
            )
            response.raise_for_status()
            return [
                NamespaceInfo(
                    namespace=ns["namespace"],
                    scopes=ns["scopes"],
                )
                for ns in response.json()
            ]

    @staticmethod
    async def get(
        key: str,
        *,
        namespace: str = "default",
        org_id: str | None = None,
        scope: str | None = None,
    ) -> KnowledgeDocument | None:
        """
        Get a document by key.

        Args:
            key: Document key
            namespace: Namespace
            org_id: Organization scope
            scope: "global" for global scope

        Returns:
            KnowledgeDocument or None if not found

        Example:
            >>> doc = await knowledge.get("refund-policy", namespace="policies")
            >>> if doc:
            ...     print(doc.content)
        """
        if _is_platform_context():
            from ._internal import get_context
            from src.repositories.knowledge import KnowledgeRepository

            context = get_context()
            target_org_id = _get_org_id(org_id, scope)

            repo = KnowledgeRepository(context.db)
            result = await repo.get_by_key(
                key=key,
                namespace=namespace,
                organization_id=target_org_id,
            )

            if not result:
                return None

            return KnowledgeDocument(
                id=result.id,
                namespace=result.namespace,
                content=result.content,
                metadata=result.metadata,
                organization_id=result.organization_id,
                key=result.key,
                created_at=result.created_at,
            )
        else:
            client = _get_client()
            response = await client.get(
                f"/api/cli/knowledge/get",
                params={
                    "key": key,
                    "namespace": namespace,
                    "org_id": org_id,
                    "scope": scope,
                },
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            doc = response.json()
            return KnowledgeDocument(
                id=doc["id"],
                namespace=doc["namespace"],
                content=doc["content"],
                metadata=doc.get("metadata", {}),
                organization_id=doc.get("organization_id"),
                key=doc.get("key"),
                created_at=doc.get("created_at"),
            )
