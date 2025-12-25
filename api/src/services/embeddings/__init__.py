"""
Embedding Service Abstraction Layer

Provides embedding generation for the knowledge store (RAG).
Uses OpenAI's text-embedding-3-small model by default.

Usage:
    from src.services.embeddings import get_embedding_client

    client = await get_embedding_client(session)
    embeddings = await client.embed(["text1", "text2"])
"""

from src.services.embeddings.base import BaseEmbeddingClient, EmbeddingConfig
from src.services.embeddings.factory import get_embedding_client

__all__ = [
    "BaseEmbeddingClient",
    "EmbeddingConfig",
    "get_embedding_client",
]
