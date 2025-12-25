"""
Unit tests for Bifrost Knowledge SDK module.

Tests both platform mode (inside workflows) and external mode (CLI).
Uses mocked dependencies for fast, isolated testing.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from bifrost._context import set_execution_context, clear_execution_context


@pytest.fixture
def test_org_id():
    """Return a test organization ID."""
    return str(uuid4())


@pytest.fixture
def test_context(test_org_id):
    """Create execution context for platform mode testing."""
    from src.sdk.context import ExecutionContext, Organization

    org = Organization(id=test_org_id, name="Test Org", is_active=True)
    return ExecutionContext(
        user_id="test-user",
        email="test@example.com",
        name="Test User",
        scope=test_org_id,
        organization=org,
        is_platform_admin=False,
        is_function_key=False,
        execution_id="test-exec-123",
    )


@pytest.fixture
def admin_context(test_org_id):
    """Create platform admin execution context."""
    from src.sdk.context import ExecutionContext, Organization

    org = Organization(id=test_org_id, name="Test Org", is_active=True)
    return ExecutionContext(
        user_id="admin-user",
        email="admin@example.com",
        name="Admin User",
        scope=test_org_id,
        organization=org,
        is_platform_admin=True,
        is_function_key=False,
        execution_id="admin-exec-456",
    )


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return AsyncMock()


@pytest.fixture
def mock_embedding_client():
    """Create a mock embedding client."""
    client = AsyncMock()
    client.embed = AsyncMock(return_value=[0.1] * 1536)  # 1536-dim embedding
    return client


class TestKnowledgePlatformMode:
    """Test knowledge SDK methods in platform mode (inside workflows)."""

    @pytest.fixture(autouse=True)
    def cleanup_context(self):
        """Ensure context is cleared after each test."""
        yield
        clear_execution_context()

    @pytest.mark.asyncio
    async def test_store_creates_document(self, test_context, mock_db, mock_embedding_client):
        """Test knowledge.store() creates a document with embedding."""
        from bifrost import knowledge
        from bifrost.knowledge import KnowledgeDocument

        set_execution_context(test_context)

        mock_repo = AsyncMock()
        mock_repo.store = AsyncMock(return_value=MagicMock(
            id=uuid4(),
            content="Test document",
            namespace="test-ns",
            key="doc-1",
            metadata={},
            created_at=None,
        ))

        with patch("bifrost._internal.get_context") as mock_get_context:
            mock_get_context.return_value = MagicMock(
                db=mock_db,
                org_id=test_context.scope,
                scope=test_context.scope,
            )
            with patch("bifrost.knowledge.get_embedding_client", AsyncMock(return_value=mock_embedding_client)):
                with patch("bifrost.knowledge.KnowledgeRepository", return_value=mock_repo):
                    result = await knowledge.store(
                        content="Test document",
                        namespace="test-ns",
                        key="doc-1",
                    )

        assert isinstance(result, KnowledgeDocument)
        mock_embedding_client.embed.assert_called_once_with("Test document")
        mock_repo.store.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_with_metadata(self, test_context, mock_db, mock_embedding_client):
        """Test knowledge.store() with metadata."""
        from bifrost import knowledge

        set_execution_context(test_context)

        mock_repo = AsyncMock()
        mock_repo.store = AsyncMock(return_value=MagicMock(
            id=uuid4(),
            content="Test",
            namespace="ns",
            key="k",
            metadata={"ticket_id": 123},
            created_at=None,
        ))

        with patch("bifrost._internal.get_context") as mock_get_context:
            mock_get_context.return_value = MagicMock(
                db=mock_db,
                org_id=test_context.scope,
                scope=test_context.scope,
            )
            with patch("bifrost.knowledge.get_embedding_client", AsyncMock(return_value=mock_embedding_client)):
                with patch("bifrost.knowledge.KnowledgeRepository", return_value=mock_repo):
                    await knowledge.store(
                        content="Test",
                        namespace="ns",
                        key="k",
                        metadata={"ticket_id": 123},
                    )

        call_kwargs = mock_repo.store.call_args[1]
        assert call_kwargs["metadata"] == {"ticket_id": 123}

    @pytest.mark.asyncio
    async def test_store_global_scope(self, admin_context, mock_db, mock_embedding_client):
        """Test knowledge.store() with global scope."""
        from bifrost import knowledge

        set_execution_context(admin_context)

        mock_repo = AsyncMock()
        mock_repo.store = AsyncMock(return_value=MagicMock(
            id=uuid4(),
            content="Global doc",
            namespace="global-ns",
            key="g1",
            metadata={},
            created_at=None,
        ))

        with patch("bifrost._internal.get_context") as mock_get_context:
            mock_get_context.return_value = MagicMock(
                db=mock_db,
                org_id=admin_context.scope,
                scope=admin_context.scope,
            )
            with patch("bifrost.knowledge.get_embedding_client", AsyncMock(return_value=mock_embedding_client)):
                with patch("bifrost.knowledge.KnowledgeRepository", return_value=mock_repo):
                    await knowledge.store(
                        content="Global doc",
                        namespace="global-ns",
                        key="g1",
                        scope="global",
                    )

        call_kwargs = mock_repo.store.call_args[1]
        assert call_kwargs["org_id"] is None  # Global scope means no org_id

    @pytest.mark.asyncio
    async def test_search_returns_documents(self, test_context, mock_db, mock_embedding_client):
        """Test knowledge.search() returns matching documents."""
        from bifrost import knowledge
        from bifrost.knowledge import KnowledgeDocument

        set_execution_context(test_context)

        mock_docs = [
            MagicMock(
                id=uuid4(),
                content="Doc 1",
                namespace="ns",
                key="k1",
                metadata={},
                score=0.95,
                created_at=None,
            ),
            MagicMock(
                id=uuid4(),
                content="Doc 2",
                namespace="ns",
                key="k2",
                metadata={},
                score=0.85,
                created_at=None,
            ),
        ]

        mock_repo = AsyncMock()
        mock_repo.search = AsyncMock(return_value=mock_docs)

        with patch("bifrost._internal.get_context") as mock_get_context:
            mock_get_context.return_value = MagicMock(
                db=mock_db,
                org_id=test_context.scope,
                scope=test_context.scope,
            )
            with patch("bifrost.knowledge.get_embedding_client", AsyncMock(return_value=mock_embedding_client)):
                with patch("bifrost.knowledge.KnowledgeRepository", return_value=mock_repo):
                    results = await knowledge.search(
                        "test query",
                        namespace="ns",
                    )

        assert len(results) == 2
        assert all(isinstance(r, KnowledgeDocument) for r in results)
        mock_embedding_client.embed.assert_called_once_with("test query")

    @pytest.mark.asyncio
    async def test_search_with_metadata_filter(self, test_context, mock_db, mock_embedding_client):
        """Test knowledge.search() with metadata filter."""
        from bifrost import knowledge

        set_execution_context(test_context)

        mock_repo = AsyncMock()
        mock_repo.search = AsyncMock(return_value=[])

        with patch("bifrost._internal.get_context") as mock_get_context:
            mock_get_context.return_value = MagicMock(
                db=mock_db,
                org_id=test_context.scope,
                scope=test_context.scope,
            )
            with patch("bifrost.knowledge.get_embedding_client", AsyncMock(return_value=mock_embedding_client)):
                with patch("bifrost.knowledge.KnowledgeRepository", return_value=mock_repo):
                    await knowledge.search(
                        "query",
                        namespace="tickets",
                        metadata_filter={"user_id": "user-123"},
                    )

        call_kwargs = mock_repo.search.call_args[1]
        assert call_kwargs["metadata_filter"] == {"user_id": "user-123"}

    @pytest.mark.asyncio
    async def test_search_with_limit(self, test_context, mock_db, mock_embedding_client):
        """Test knowledge.search() respects limit parameter."""
        from bifrost import knowledge

        set_execution_context(test_context)

        mock_repo = AsyncMock()
        mock_repo.search = AsyncMock(return_value=[])

        with patch("bifrost._internal.get_context") as mock_get_context:
            mock_get_context.return_value = MagicMock(
                db=mock_db,
                org_id=test_context.scope,
                scope=test_context.scope,
            )
            with patch("bifrost.knowledge.get_embedding_client", AsyncMock(return_value=mock_embedding_client)):
                with patch("bifrost.knowledge.KnowledgeRepository", return_value=mock_repo):
                    await knowledge.search(
                        "query",
                        namespace="ns",
                        limit=3,
                    )

        call_kwargs = mock_repo.search.call_args[1]
        assert call_kwargs["limit"] == 3

    @pytest.mark.asyncio
    async def test_delete_removes_document(self, test_context, mock_db):
        """Test knowledge.delete() removes a document."""
        from bifrost import knowledge

        set_execution_context(test_context)

        mock_repo = AsyncMock()
        mock_repo.delete = AsyncMock(return_value=True)

        with patch("bifrost._internal.get_context") as mock_get_context:
            mock_get_context.return_value = MagicMock(
                db=mock_db,
                org_id=test_context.scope,
                scope=test_context.scope,
            )
            with patch("bifrost.knowledge.KnowledgeRepository", return_value=mock_repo):
                result = await knowledge.delete(
                    key="doc-1",
                    namespace="ns",
                )

        assert result is True
        mock_repo.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_namespace_removes_all(self, test_context, mock_db):
        """Test knowledge.delete_namespace() removes all documents in namespace."""
        from bifrost import knowledge

        set_execution_context(test_context)

        mock_repo = AsyncMock()
        mock_repo.delete_namespace = AsyncMock(return_value=10)

        with patch("bifrost._internal.get_context") as mock_get_context:
            mock_get_context.return_value = MagicMock(
                db=mock_db,
                org_id=test_context.scope,
                scope=test_context.scope,
            )
            with patch("bifrost.knowledge.KnowledgeRepository", return_value=mock_repo):
                result = await knowledge.delete_namespace("old-ns")

        assert result == 10
        mock_repo.delete_namespace.assert_called_once_with(namespace="old-ns", org_id=test_context.scope)

    @pytest.mark.asyncio
    async def test_list_namespaces(self, test_context, mock_db):
        """Test knowledge.list_namespaces() returns namespace info."""
        from bifrost import knowledge
        from bifrost.knowledge import NamespaceInfo

        set_execution_context(test_context)

        mock_ns_info = [
            MagicMock(namespace="ns1", scopes={"global": 10, "org": 5, "total": 15}),
            MagicMock(namespace="ns2", scopes={"global": 0, "org": 20, "total": 20}),
        ]

        mock_repo = AsyncMock()
        mock_repo.list_namespaces = AsyncMock(return_value=mock_ns_info)

        with patch("bifrost._internal.get_context") as mock_get_context:
            mock_get_context.return_value = MagicMock(
                db=mock_db,
                org_id=test_context.scope,
                scope=test_context.scope,
            )
            with patch("bifrost.knowledge.KnowledgeRepository", return_value=mock_repo):
                results = await knowledge.list_namespaces()

        assert len(results) == 2
        assert all(isinstance(r, NamespaceInfo) for r in results)


class TestKnowledgeExternalMode:
    """Test knowledge SDK methods in external mode (CLI with API key)."""

    @pytest.fixture(autouse=True)
    def clear_context_and_client(self):
        """Ensure no platform context and clean client state."""
        clear_execution_context()
        from bifrost.client import BifrostClient
        BifrostClient._instance = None
        yield
        BifrostClient._instance = None

    @pytest.mark.asyncio
    async def test_store_calls_api_endpoint(self):
        """Test knowledge.store() calls API endpoint in external mode."""
        from bifrost import knowledge

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": str(uuid4()),
            "content": "Test doc",
            "namespace": "ns",
            "key": "k1",
            "metadata": {},
            "created_at": None,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("bifrost.knowledge._get_client", return_value=mock_client):
            result = await knowledge.store(
                content="Test doc",
                namespace="ns",
                key="k1",
            )

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/api/cli/knowledge/store"
        assert call_args[1]["json"]["content"] == "Test doc"
        assert call_args[1]["json"]["namespace"] == "ns"

    @pytest.mark.asyncio
    async def test_search_calls_api_endpoint(self):
        """Test knowledge.search() calls API endpoint in external mode."""
        from bifrost import knowledge

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {
                "id": str(uuid4()),
                "content": "Result 1",
                "namespace": "ns",
                "key": "k1",
                "metadata": {},
                "score": 0.9,
                "created_at": None,
            }
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("bifrost.knowledge._get_client", return_value=mock_client):
            results = await knowledge.search(
                "search query",
                namespace="ns",
            )

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/api/cli/knowledge/search"
        assert call_args[1]["json"]["query"] == "search query"
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_delete_calls_api_endpoint(self):
        """Test knowledge.delete() calls API endpoint in external mode."""
        from bifrost import knowledge

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = True
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("bifrost.knowledge._get_client", return_value=mock_client):
            result = await knowledge.delete(
                key="doc-1",
                namespace="ns",
            )

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/api/cli/knowledge/delete"
        assert result is True

    @pytest.mark.asyncio
    async def test_list_namespaces_calls_api_endpoint(self):
        """Test knowledge.list_namespaces() calls API endpoint in external mode."""
        from bifrost import knowledge

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"namespace": "ns1", "scopes": {"global": 10, "org": 5, "total": 15}},
        ]
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("bifrost.knowledge._get_client", return_value=mock_client):
            results = await knowledge.list_namespaces()

        mock_client.get.assert_called_once()
        assert len(results) == 1


class TestKnowledgeContextDetection:
    """Test that knowledge SDK correctly detects platform vs external mode."""

    def test_is_platform_context_true_when_context_set(self):
        """Test _is_platform_context() returns True when context is set."""
        from bifrost.knowledge import _is_platform_context
        from src.sdk.context import ExecutionContext, Organization

        org = Organization(id="test-org", name="Test", is_active=True)
        context = ExecutionContext(
            user_id="user",
            email="user@test.com",
            name="User",
            scope="test-org",
            organization=org,
            is_platform_admin=False,
            is_function_key=False,
            execution_id="exec-123",
        )

        try:
            set_execution_context(context)
            assert _is_platform_context() is True
        finally:
            clear_execution_context()

    def test_is_platform_context_false_when_no_context(self):
        """Test _is_platform_context() returns False when no context."""
        from bifrost.knowledge import _is_platform_context

        clear_execution_context()
        assert _is_platform_context() is False
