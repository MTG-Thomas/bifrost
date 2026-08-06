"""Tests for KnowledgeRepository chunked storage and search."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy import func, select

from src.models.orm.knowledge import KnowledgeStore
from src.repositories.knowledge import KnowledgeDocument, KnowledgeRepository


class _FakeEmbedder:
    """Stub embedder returning deterministic small vectors."""

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(i % self.dim) for i in range(self.dim)] for _ in texts]

    async def embed_single(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


@pytest.mark.asyncio
async def test_store_short_content_produces_one_row(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()

    await repo.store_chunked(
        content="Short document.",
        namespace="test-ns",
        key="doc-1",
        embedder=embedder,
    )

    rows = (
        await db_session.execute(
            select(KnowledgeStore).where(KnowledgeStore.key == "doc-1")
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].chunk_index == 0
    assert rows[0].chunk_count == 1
    assert rows[0].content == "Short document."


@pytest.mark.asyncio
async def test_store_long_content_produces_multiple_rows(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long = ("Sentence number X. " * 500).strip()

    await repo.store_chunked(
        content=long,
        namespace="test-ns",
        key="doc-2",
        metadata={"client_id": "acme"},
        embedder=embedder,
    )

    rows = (
        await db_session.execute(
            select(KnowledgeStore)
            .where(KnowledgeStore.key == "doc-2")
            .order_by(KnowledgeStore.chunk_index)
        )
    ).scalars().all()
    assert len(rows) >= 4
    assert [row.chunk_index for row in rows] == list(range(len(rows)))
    assert all(row.chunk_count == len(rows) for row in rows)
    assert all(row.doc_metadata == {"client_id": "acme"} for row in rows)
    assert len(embedder.calls) == 1
    assert len(embedder.calls[0]) == len(rows)


@pytest.mark.asyncio
async def test_store_replaces_existing_chunks_atomically(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long_v1 = ("Version one. " * 500).strip()
    long_v2 = ("Version two. " * 500).strip()

    await repo.store_chunked(content=long_v1, namespace="ns", key="k", embedder=embedder)
    count_v1 = (
        await db_session.execute(
            select(func.count()).select_from(KnowledgeStore).where(KnowledgeStore.key == "k")
        )
    ).scalar_one()
    assert count_v1 >= 4

    await repo.store_chunked(content=long_v2, namespace="ns", key="k", embedder=embedder)
    rows = (
        await db_session.execute(select(KnowledgeStore).where(KnowledgeStore.key == "k"))
    ).scalars().all()
    assert all("Version two" in row.content for row in rows)
    assert all("Version one" not in row.content for row in rows)


@pytest.mark.asyncio
async def test_store_without_key_inserts_chunk_rows(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long = ("Anonymous doc. " * 500).strip()

    await repo.store_chunked(content=long, namespace="ns-keyless", key=None, embedder=embedder)
    rows = (
        await db_session.execute(
            select(KnowledgeStore)
            .where(KnowledgeStore.namespace == "ns-keyless")
            .order_by(KnowledgeStore.chunk_index)
        )
    ).scalars().all()
    assert len(rows) >= 4
    assert all(row.key is None for row in rows)
    assert all(row.chunk_count == len(rows) for row in rows)


@pytest.mark.asyncio
async def test_search_dedups_by_key_by_default(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()

    long = ("Subject of interest. " * 500).strip()
    await repo.store_chunked(content=long, namespace="ns-search", key="big", embedder=embedder)
    for index in range(3):
        await repo.store_chunked(
            content=f"Short doc {index}.",
            namespace="ns-search",
            key=f"small-{index}",
            embedder=embedder,
        )
    await db_session.flush()

    query_embedding = await embedder.embed_single("anything")
    results = await repo.search(
        query_embedding=query_embedding,
        namespace="ns-search",
        limit=5,
    )

    keys = [result.key for result in results]
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_search_group_by_key_false_returns_raw_chunks(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long = ("Subject of interest. " * 500).strip()
    await repo.store_chunked(content=long, namespace="ns-raw", key="big", embedder=embedder)
    await db_session.flush()

    query_embedding = await embedder.embed_single("anything")
    results = await repo.search(
        query_embedding=query_embedding,
        namespace="ns-raw",
        limit=10,
        group_by_key=False,
    )

    assert len([result for result in results if result.key == "big"]) > 1


@pytest.mark.asyncio
async def test_search_metadata_filter_applies_to_chunks(db_session):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)
    embedder = _FakeEmbedder()
    long = ("Body text. " * 500).strip()
    await repo.store_chunked(
        content=long,
        namespace="ns-filter",
        key="acme-doc",
        metadata={"client_id": "acme"},
        embedder=embedder,
    )
    await repo.store_chunked(
        content=long,
        namespace="ns-filter",
        key="other-doc",
        metadata={"client_id": "other"},
        embedder=embedder,
    )
    await db_session.flush()

    query_embedding = await embedder.embed_single("anything")
    results = await repo.search(
        query_embedding=query_embedding,
        namespace="ns-filter",
        metadata_filter={"client_id": "acme"},
        limit=5,
    )

    assert all(result.metadata.get("client_id") == "acme" for result in results)
    assert any(result.key == "acme-doc" for result in results)


def test_search_rows_to_documents_filters_deduplicates_and_limits():
    duplicate = type(
        "Doc",
        (),
        {
            "id": "doc-1",
            "namespace": "docs",
            "organization_id": None,
            "key": "handbook.md",
            "content": "first",
            "doc_metadata": {"page": 1},
            "created_at": None,
        },
    )()
    lower_score_duplicate = type(
        "Doc",
        (),
        {
            "id": "doc-2",
            "namespace": "docs",
            "organization_id": None,
            "key": "handbook.md",
            "content": "second",
            "doc_metadata": {"page": 2},
            "created_at": None,
        },
    )()
    below_threshold = type(
        "Doc",
        (),
        {
            "id": "doc-3",
            "namespace": "docs",
            "organization_id": None,
            "key": "other.md",
            "content": "third",
            "doc_metadata": {},
            "created_at": None,
        },
    )()

    docs = KnowledgeRepository._search_rows_to_documents(
        [
            (duplicate, 0.95),
            (lower_score_duplicate, 0.90),
            (below_threshold, 0.10),
        ],
        min_score=0.5,
        group_by_key=True,
        limit=5,
    )

    assert docs == [
        KnowledgeDocument(
            id="doc-1",
            namespace="docs",
            content="first",
            metadata={"page": 1},
            score=0.95,
            organization_id=None,
            key="handbook.md",
            created_at=None,
        )
    ]


def test_search_rows_to_documents_keeps_keyless_chunks_and_respects_limit():
    rows = []
    for index in range(3):
        rows.append(
            (
                type(
                    "Doc",
                    (),
                    {
                        "id": f"doc-{index}",
                        "namespace": "docs",
                        "organization_id": None,
                        "key": None,
                        "content": f"chunk {index}",
                        "doc_metadata": {},
                        "created_at": None,
                    },
                )(),
                0.8 - (index * 0.1),
            )
        )

    docs = KnowledgeRepository._search_rows_to_documents(
        rows,
        min_score=None,
        group_by_key=True,
        limit=2,
    )

    assert [doc.content for doc in docs] == ["chunk 0", "chunk 1"]


@pytest.mark.asyncio
async def test_store_chunked_requires_embedder_and_matching_embedding_count():
    repo = KnowledgeRepository(object(), org_id=None, is_superuser=True)

    with pytest.raises(ValueError, match="requires an embedder"):
        await repo.store_chunked(content="body", namespace="docs")

    class BadEmbedder:
        async def embed(self, texts):
            return []

    with pytest.raises(ValueError, match="returned 0 embeddings"):
        await repo.store_chunked(
            content="body",
            namespace="docs",
            embedder=BadEmbedder(),
        )


def test_below_min_score_and_dedup_key_cover_edge_cases():
    assert KnowledgeRepository._below_min_score(0.49, 0.5) is True
    assert KnowledgeRepository._below_min_score(0.50, 0.5) is False
    assert KnowledgeRepository._below_min_score(0.10, None) is False

    keyed = type(
        "Doc",
        (),
        {
            "namespace": "docs",
            "organization_id": UUID("00000000-0000-0000-0000-000000000001"),
            "key": "handbook.md",
        },
    )()
    keyless = type(
        "Doc",
        (),
        {"namespace": "docs", "organization_id": None, "key": None},
    )()

    assert KnowledgeRepository._dedup_key(keyed, group_by_key=True) == (
        "docs",
        "00000000-0000-0000-0000-000000000001",
        "handbook.md",
    )
    assert KnowledgeRepository._dedup_key(keyed, group_by_key=False) is None
    assert KnowledgeRepository._dedup_key(keyless, group_by_key=True) is None


class _AllResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        scalars = MagicMock()
        scalars.all.return_value = self._rows
        return scalars


class _ScalarOneOrNoneResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _OneOrNoneResult:
    def __init__(self, row):
        self._row = row

    def one_or_none(self):
        return self._row


@pytest.mark.asyncio
async def test_list_namespaces_aggregates_global_and_org_counts():
    session = AsyncMock()
    session.execute.return_value = _AllResult(
        [
            ("alpha", None, 2),
            ("alpha", UUID("00000000-0000-0000-0000-000000000001"), 3),
            ("beta", UUID("00000000-0000-0000-0000-000000000001"), 1),
        ]
    )
    repo = KnowledgeRepository(
        session,
        org_id=UUID("00000000-0000-0000-0000-000000000001"),
        is_superuser=False,
    )

    namespaces = await repo.list_namespaces()

    assert [(item.namespace, item.scopes) for item in namespaces] == [
        ("alpha", {"global": 2, "org": 3, "total": 5}),
        ("beta", {"global": 0, "org": 1, "total": 1}),
    ]


@pytest.mark.asyncio
async def test_list_all_namespaces_aggregates_without_scope_filter():
    session = AsyncMock()
    session.execute.return_value = _AllResult(
        [
            ("alpha", None, 2),
            ("alpha", UUID("00000000-0000-0000-0000-000000000001"), 3),
            ("zeta", UUID("00000000-0000-0000-0000-000000000002"), 4),
        ]
    )
    repo = KnowledgeRepository(session, org_id=None, is_superuser=True)

    namespaces = await repo.list_all_namespaces()

    assert [(item.namespace, item.scopes) for item in namespaces] == [
        ("alpha", {"global": 2, "org": 3, "total": 5}),
        ("zeta", {"global": 0, "org": 4, "total": 4}),
    ]


@pytest.mark.asyncio
async def test_get_by_key_and_namespace_listing_convert_rows_to_documents():
    doc_id = UUID("00000000-0000-0000-0000-000000000010")
    org_id = UUID("00000000-0000-0000-0000-000000000011")
    doc = SimpleNamespace(
        id=doc_id,
        namespace="docs",
        content="body",
        doc_metadata={"source": "test"},
        organization_id=org_id,
        key="handbook.md",
        created_at=None,
        chunk_index=0,
        chunk_count=1,
    )
    session = AsyncMock()
    session.execute.side_effect = [
        _ScalarsResult([doc]),
        _ScalarsResult([doc, SimpleNamespace(**{**doc.__dict__, "key": None})]),
    ]
    repo = KnowledgeRepository(session, org_id=org_id, is_superuser=False)

    by_key = await repo.get_by_key("handbook.md", "docs")
    by_namespace = await repo.get_all_by_namespace("docs")

    assert by_key == KnowledgeDocument(
        id=str(doc_id),
        namespace="docs",
        content="body",
        metadata={"source": "test"},
        organization_id=str(org_id),
        key="handbook.md",
        created_at=None,
    )
    assert list(by_namespace) == ["handbook.md"]
    assert by_namespace["handbook.md"].content == "body"


@pytest.mark.asyncio
async def test_get_by_key_returns_none_when_missing():
    session = AsyncMock()
    session.execute.return_value = _ScalarsResult([])
    repo = KnowledgeRepository(session, org_id=None, is_superuser=True)

    assert await repo.get_by_key("missing.md", "docs") is None


@pytest.mark.asyncio
async def test_get_by_id_applies_scope_and_converts_document():
    doc_id = UUID("00000000-0000-0000-0000-000000000020")
    org_id = UUID("00000000-0000-0000-0000-000000000021")
    doc = SimpleNamespace(
        id=doc_id,
        namespace="docs",
        content="body",
        doc_metadata={},
        organization_id=None,
        key="global.md",
        created_at=None,
        chunk_index=0,
        chunk_count=1,
    )
    session = AsyncMock()
    session.execute.return_value = _ScalarOneOrNoneResult(doc)
    repo = KnowledgeRepository(session, org_id=org_id, is_superuser=False)

    result = await repo.get_by_id(doc_id)

    assert result == KnowledgeDocument(
        id=str(doc_id),
        namespace="docs",
        content="body",
        metadata={},
        organization_id=None,
        key="global.md",
        created_at=None,
    )
    compiled = session.execute.call_args[0][0].compile()
    assert "organization_id IS NULL" in str(compiled)


@pytest.mark.asyncio
async def test_get_by_id_returns_none_when_not_visible_or_missing():
    session = AsyncMock()
    session.execute.return_value = _ScalarOneOrNoneResult(None)
    repo = KnowledgeRepository(session, org_id=None, is_superuser=True)

    assert await repo.get_by_id(UUID("00000000-0000-0000-0000-000000000020")) is None


@pytest.mark.asyncio
async def test_delete_by_key_and_namespace_return_rowcount_semantics():
    org_id = UUID("00000000-0000-0000-0000-000000000031")
    session = AsyncMock()
    deleted = MagicMock()
    deleted.rowcount = 1
    empty = MagicMock()
    empty.rowcount = 0
    namespace_deleted = MagicMock()
    namespace_deleted.rowcount = 7
    session.execute.side_effect = [deleted, empty, namespace_deleted]
    repo = KnowledgeRepository(session, org_id=org_id, is_superuser=False)

    assert await repo.delete_by_key("handbook.md", "docs") is True
    assert await repo.delete_by_key("missing.md", "docs") is False
    assert await repo.delete_namespace("docs") == 7

    first_stmt = session.execute.call_args_list[0][0][0].compile()
    assert first_stmt.params["key_1"] == "handbook.md"
    assert first_stmt.params["namespace_1"] == "docs"
    assert first_stmt.params["organization_id_1"] == org_id


@pytest.mark.asyncio
async def test_list_documents_by_namespace_converts_rows_and_applies_filters():
    org_id = UUID("00000000-0000-0000-0000-000000000041")
    doc = SimpleNamespace(
        id=UUID("00000000-0000-0000-0000-000000000042"),
        namespace="docs",
        content="body",
        doc_metadata={"kind": "manual"},
        organization_id=org_id,
        key="handbook.md",
        created_at=None,
    )
    session = AsyncMock()
    session.execute.return_value = _ScalarsResult([doc])
    repo = KnowledgeRepository(session, org_id=org_id, is_superuser=False)

    docs = await repo.list_documents_by_namespace(
        namespace="docs",
        include_global=False,
        limit=10,
        offset=5,
        search="handbook",
    )

    assert docs == [
        KnowledgeDocument(
            id=str(doc.id),
            namespace="docs",
            content="body",
            metadata={"kind": "manual"},
            organization_id=str(org_id),
            key="handbook.md",
            created_at=None,
        )
    ]
    compiled = session.execute.call_args[0][0].compile()
    assert compiled.params["namespace_1"] == "docs"
    assert compiled.params["organization_id_1"] == org_id
    assert {compiled.params["param_1"], compiled.params["param_2"]} == {5, 10}


@pytest.mark.asyncio
async def test_delete_orphaned_docs_is_safe_on_empty_keys_and_returns_rowcount():
    session = AsyncMock()
    delete_result = MagicMock()
    delete_result.rowcount = 4
    session.execute.return_value = delete_result
    repo = KnowledgeRepository(session, org_id=None, is_superuser=True)

    assert await repo.delete_orphaned_docs("docs", valid_keys=set()) == 0
    session.execute.assert_not_awaited()

    assert await repo.delete_orphaned_docs("docs", valid_keys={"keep.md"}) == 4
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_hybrid_search_recovers_exact_lexical_match_missed_by_vector_candidates(
    db_session,
):
    repo = KnowledgeRepository(db_session, org_id=None, is_superuser=True)

    for index in range(21):
        db_session.add(
            KnowledgeStore(
                namespace="ns-hybrid",
                key=f"distractor-{index}",
                content=f"Generic account configuration article {index}.",
                embedding=[1.0, 0.0, 0.0],
            )
        )
    db_session.add(
        KnowledgeStore(
            namespace="ns-hybrid",
            key="contact-types",
            content=(
                "Use Contact Types to identify the Technical POC and Billing "
                "POC for a customer."
            ),
            embedding=[-1.0, 0.0, 0.0],
        )
    )
    await db_session.flush()

    vector_only = await repo.search(
        query_embedding=[1.0, 0.0, 0.0],
        namespace="ns-hybrid",
        limit=5,
    )
    hybrid = await repo.search(
        query_embedding=[1.0, 0.0, 0.0],
        query_text="Where do I set the Technical POC and Billing POC?",
        namespace="ns-hybrid",
        limit=5,
    )

    assert "contact-types" not in [doc.key for doc in vector_only]
    assert hybrid[0].key == "contact-types"
    assert hybrid[0].score is not None
    assert 0.0 <= hybrid[0].score <= 1.0
