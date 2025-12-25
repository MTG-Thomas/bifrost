"""
Knowledge Store SDK for Bifrost - API-only implementation.

Provides Python API for semantic search and RAG (Retrieval Augmented Generation).
Uses pgvector for vector similarity search with org-scoped namespaces.

All operations go through HTTP API endpoints.
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

from typing import Any

from .client import get_client
from .models import KnowledgeDocument, NamespaceInfo


class knowledge:
    """
    Knowledge store operations.

    Provides semantic search and storage for RAG.
    Documents are scoped to organizations with global fallback.

    All operations are performed via HTTP API endpoints.
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
        client = get_client()
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
        client = get_client()
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
        client = get_client()
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
            KnowledgeDocument.model_validate(doc)
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
        client = get_client()
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
        client = get_client()
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
        client = get_client()
        response = await client.get(
            "/api/cli/knowledge/namespaces",
            params={"org_id": org_id, "include_global": include_global},
        )
        response.raise_for_status()
        return [
            NamespaceInfo.model_validate(ns)
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
        client = get_client()
        response = await client.get(
            "/api/cli/knowledge/get",
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
        return KnowledgeDocument.model_validate(response.json())
