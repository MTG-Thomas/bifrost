from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models.contracts.knowledge import (
    KnowledgeDocumentBulkScopeUpdate,
    KnowledgeDocumentUpdate,
    KnowledgeNamespaceRoleCreate,
)
from src.routers import knowledge_sources


class _ScalarResult:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return self._values

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalar_one(self):
        return self._values[0]


class _ExecuteResult:
    def __init__(self, values=(), *, rowcount=0, scalar=None):
        self._values = list(values)
        self.rowcount = rowcount
        self._scalar = scalar

    def scalars(self):
        return _ScalarResult(self._values)

    def scalar_one_or_none(self):
        if self._scalar is not None:
            return self._scalar
        return self._values[0] if self._values else None


class _Db:
    def __init__(self, *results):
        self._results = list(results)
        self.statements = []
        self.added = []
        self.flushed = 0

    async def execute(self, stmt):
        self.statements.append(stmt)
        return self._results.pop(0) if self._results else _ExecuteResult()

    def add(self, obj):
        self.added.append(obj)

    def add_all(self, objects):
        self.added.extend(objects)

    async def flush(self):
        self.flushed += 1


def _user(**overrides):
    values = {
        "email": "admin@example.test",
        "user_id": uuid4(),
        "organization_id": uuid4(),
        "is_superuser": True,
        "is_external": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _doc(**overrides):
    values = {
        "id": uuid4(),
        "namespace": "docs",
        "key": "handbook.md",
        "content": "A" * 250,
        "doc_metadata": {"source": "unit"},
        "organization_id": uuid4(),
        "created_at": datetime(2026, 7, 5, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 5, tzinfo=timezone.utc),
        "created_by": uuid4(),
        "chunk_index": 0,
        "chunk_count": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_external_users_are_denied_direct_knowledge_access():
    with pytest.raises(HTTPException) as exc:
        await knowledge_sources.list_all_documents(
            _Db(),
            _user(is_external=True),
        )

    assert exc.value.status_code == 403
    assert "External users" in exc.value.detail


@pytest.mark.asyncio
async def test_list_namespaces_maps_repository_scope_counts(monkeypatch):
    calls = []

    class Repo:
        def __init__(self, session, org_id):
            calls.append(("init", session, org_id))

        async def list_all_namespaces(self):
            calls.append(("all",))
            return [
                SimpleNamespace(
                    namespace="docs",
                    scopes={"global": 2, "org": 3, "total": 5},
                )
            ]

    monkeypatch.setattr(knowledge_sources, "KnowledgeRepository", Repo)

    result = await knowledge_sources.list_namespaces(_Db(), _user(), scope=None)

    assert result[0].namespace == "docs"
    assert result[0].document_count == 5
    assert result[0].global_count == 2
    assert result[0].org_count == 3
    assert calls[1] == ("all",)


@pytest.mark.asyncio
async def test_role_assignment_skips_invalid_missing_and_duplicate_roles():
    existing_assignment = object()
    db = _Db(
        _ExecuteResult([object()]),
        _ExecuteResult([]),
        _ExecuteResult([]),
        _ExecuteResult([existing_assignment]),
    )
    valid_role = uuid4()
    duplicate_role = uuid4()

    result = await knowledge_sources.assign_namespace_roles(
        KnowledgeNamespaceRoleCreate(
            namespace="docs",
            role_ids=["not-a-uuid", str(valid_role), str(duplicate_role)],
            organization_id=None,
        ),
        db,
        _user(email="owner@example.test"),
    )

    assert len(result) == 1
    assert result[0].namespace == "docs"
    assert result[0].role_id == str(valid_role)
    assert result[0].assigned_by == "owner@example.test"
    assert len(db.added) == 1
    assert db.flushed == 1


@pytest.mark.asyncio
async def test_remove_namespace_role_404s_or_deletes_existing_assignment():
    missing_db = _Db(_ExecuteResult([]))
    assignment_id = uuid4()

    with pytest.raises(HTTPException) as exc:
        await knowledge_sources.remove_namespace_role(
            assignment_id,
            missing_db,
            _user(),
        )

    assert exc.value.status_code == 404

    db = _Db(_ExecuteResult([object()]), _ExecuteResult(rowcount=1))
    await knowledge_sources.remove_namespace_role(assignment_id, db, _user())

    assert db.flushed == 1
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_list_documents_returns_summaries_with_preview_and_filters():
    doc = _doc(content="preview text", organization_id=None)
    db = _Db(_ExecuteResult([doc]))

    result = await knowledge_sources.list_all_documents(
        db,
        _user(),
        scope="global",
        namespace="docs",
        search="preview",
        limit=100,
        offset=0,
    )

    assert len(result) == 1
    assert result[0].id == str(doc.id)
    assert result[0].namespace == "docs"
    assert result[0].content_preview == "preview text"
    assert result[0].metadata == {"source": "unit"}
    assert result[0].organization_id is None


@pytest.mark.asyncio
async def test_bulk_scope_update_rejects_invalid_ids_and_conflicts():
    data = KnowledgeDocumentBulkScopeUpdate(
        document_ids=["not-a-uuid"],
        scope="global",
    )
    with pytest.raises(HTTPException) as exc:
        await knowledge_sources.bulk_update_document_scope(data, _Db(), _user())

    assert exc.value.status_code == 422
    assert "Invalid document ID" in exc.value.detail

    source = _doc(key="handbook.md", organization_id=uuid4())
    conflict = _doc(key="handbook.md", organization_id=None)
    db = _Db(_ExecuteResult([source]), _ExecuteResult([conflict]))

    with pytest.raises(HTTPException) as conflict_exc:
        await knowledge_sources.bulk_update_document_scope(
            KnowledgeDocumentBulkScopeUpdate(
                document_ids=[str(source.id)],
                scope="global",
                replace=False,
            ),
            db,
            _user(),
        )

    assert conflict_exc.value.status_code == 409
    assert conflict_exc.value.detail["conflicting_keys"] == ["docs/handbook.md"]


@pytest.mark.asyncio
async def test_bulk_scope_update_replace_deletes_conflicts_and_updates_rows():
    source = _doc(key="handbook.md", organization_id=uuid4())
    conflict = _doc(key="handbook.md", organization_id=None)
    db = _Db(
        _ExecuteResult([source]),
        _ExecuteResult([conflict]),
        _ExecuteResult(rowcount=1),
        _ExecuteResult(rowcount=1),
    )

    result = await knowledge_sources.bulk_update_document_scope(
        KnowledgeDocumentBulkScopeUpdate(
            document_ids=[str(source.id)],
            scope="global",
            replace=True,
        ),
        db,
        _user(),
    )

    assert result == {"updated": 1}
    assert db.flushed == 1
    assert len(db.statements) == 4


@pytest.mark.asyncio
async def test_update_document_handles_not_found_embedding_error_and_conflict(monkeypatch):
    doc_id = uuid4()
    with pytest.raises(HTTPException) as missing:
        await knowledge_sources.update_document(
            "docs",
            doc_id,
            KnowledgeDocumentUpdate(content="new"),
            _Db(_ExecuteResult([])),
            _user(),
        )
    assert missing.value.status_code == 404

    doc = _doc(id=doc_id)
    db = _Db(
        _ExecuteResult([doc]),
        _ExecuteResult(scalar=doc.id),
        _ExecuteResult([doc]),
    )

    async def no_embedder(_db):
        raise ValueError("no provider")

    import src.services.embeddings.factory as factory

    monkeypatch.setattr(factory, "get_embedding_client", no_embedder)

    with pytest.raises(HTTPException) as unavailable:
        await knowledge_sources.update_document(
            "docs",
            doc_id,
            KnowledgeDocumentUpdate(content="new"),
            db,
            _user(),
        )
    assert unavailable.value.status_code == 503

    class Embedder:
        async def embed(self, content):
            return [0.1, 0.2]

    async def embedder(_db):
        return Embedder()

    monkeypatch.setattr(factory, "get_embedding_client", embedder)
    conflict_id = uuid4()
    db = _Db(
        _ExecuteResult([doc]),
        _ExecuteResult(scalar=doc.id),
        _ExecuteResult([doc]),
        _ExecuteResult(scalar=conflict_id),
    )
    with pytest.raises(HTTPException) as conflict:
        await knowledge_sources.update_document(
            "docs",
            doc_id,
            KnowledgeDocumentUpdate(content="new", metadata={"m": 1}),
            db,
            _user(),
            scope="global",
            replace=False,
        )

    assert conflict.value.status_code == 409
    assert conflict.value.detail["conflicting_id"] == str(conflict_id)


@pytest.mark.asyncio
async def test_update_delete_and_delete_namespace_success_paths(monkeypatch):
    import src.services.embeddings.factory as factory

    doc = _doc()

    class Embedder:
        async def embed(self, chunks):
            return [[0.3, 0.4] for _chunk in chunks]

    async def embedder(_db):
        return Embedder()

    monkeypatch.setattr(factory, "get_embedding_client", embedder)
    db = _Db(
        _ExecuteResult([doc]),
        _ExecuteResult(scalar=doc.id),
        _ExecuteResult([doc]),
        _ExecuteResult(),
        _ExecuteResult([doc]),
        _ExecuteResult(),
        _ExecuteResult([doc]),
    )

    updated = await knowledge_sources.update_document(
        "docs",
        doc.id,
        KnowledgeDocumentUpdate(content="replacement", metadata={"kind": "runbook"}),
        db,
        _user(),
        scope="global",
        replace=True,
    )

    assert updated.content == "replacement"
    assert updated.metadata == {"kind": "runbook"}
    assert updated.organization_id is None
    assert doc.embedding == [0.3, 0.4]
    assert db.flushed == 2

    delete_db = _Db(_ExecuteResult([doc]), _ExecuteResult(rowcount=1))
    await knowledge_sources.delete_document("docs", doc.id, delete_db, _user())
    assert delete_db.flushed == 1

    class Repo:
        def __init__(self, session, org_id):
            self.session = session
            self.org_id = org_id

        async def delete_namespace(self, namespace, organization_id):
            assert namespace == "docs"
            assert organization_id is None
            return 2

    monkeypatch.setattr(knowledge_sources, "KnowledgeRepository", Repo)
    namespace_db = _Db(_ExecuteResult(rowcount=2))
    await knowledge_sources.delete_namespace(
        "docs",
        namespace_db,
        _user(),
        scope="global",
    )

    assert namespace_db.flushed == 1


@pytest.mark.asyncio
async def test_get_document_uses_repository_and_checks_namespace(monkeypatch):
    doc_id = uuid4()
    found = SimpleNamespace(
        id=str(doc_id),
        namespace="docs",
        key="handbook.md",
        content="body",
        metadata={"a": 1},
        organization_id=None,
        created_at=None,
    )

    class Repo:
        def __init__(self, session, org_id):
            self.org_id = org_id

        async def get_by_id(self, requested_id):
            assert requested_id == doc_id
            return found

    monkeypatch.setattr(knowledge_sources, "KnowledgeRepository", Repo)

    result = await knowledge_sources.get_document("docs", doc_id, _Db(), _user())

    assert result.id == str(doc_id)
    assert result.content == "body"

    with pytest.raises(HTTPException) as exc:
        await knowledge_sources.get_document("other", doc_id, _Db(), _user())

    assert exc.value.status_code == 404
