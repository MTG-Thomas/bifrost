"""
Unit tests for Bifrost Tables SDK module.

Tests SDK module structure and API method signatures.
Integration tests for actual API calls are in tests/integration/platform/.
"""

from __future__ import annotations

from typing import Any

import pytest


class TestTablesSDKImports:
    """Test that tables SDK can be imported correctly."""

    def test_import_bifrost_tables(self):
        """Test importing tables module."""
        from bifrost import tables

        # Verify module has expected methods
        assert hasattr(tables, 'create')
        assert hasattr(tables, 'list')
        assert hasattr(tables, 'delete')
        assert hasattr(tables, 'insert')
        assert hasattr(tables, 'get')
        assert hasattr(tables, 'update')
        assert hasattr(tables, 'delete_document')
        assert hasattr(tables, 'query')
        assert hasattr(tables, 'count')

    def test_import_bifrost_tables_batch_methods(self):
        """Test importing batch methods from tables module."""
        from bifrost import tables

        assert hasattr(tables, 'insert_batch')
        assert hasattr(tables, 'upsert_batch')
        assert hasattr(tables, 'delete_batch')

    def test_import_table_models(self):
        """Test importing table models."""
        from bifrost import TableInfo, DocumentData, DocumentList

        assert TableInfo is not None
        assert DocumentData is not None
        assert DocumentList is not None

    def test_import_batch_models(self):
        """Test importing batch result models."""
        from bifrost import BatchResult, BatchDeleteResult

        assert BatchResult is not None
        assert BatchDeleteResult is not None

    def test_table_info_model_fields(self):
        """Test TableInfo model has expected fields."""
        from bifrost import TableInfo

        # Create instance to verify fields work
        table = TableInfo(
            id="test-id",
            name="customers",
            description="Customer data",
            table_schema={"type": "object"},
            organization_id="org-123",
            created_by="test@example.com",
        )

        assert table.id == "test-id"
        assert table.name == "customers"
        assert table.description == "Customer data"
        assert table.table_schema == {"type": "object"}
        assert table.organization_id == "org-123"

    def test_document_data_model_fields(self):
        """Test DocumentData model has expected fields."""
        from bifrost import DocumentData

        doc = DocumentData(
            id="doc-id",
            table_id="table-id",
            data={"name": "Acme", "status": "active"},
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        assert doc.id == "doc-id"
        assert doc.table_id == "table-id"
        assert doc.data["name"] == "Acme"
        assert doc.data["status"] == "active"

    def test_document_list_model_fields(self):
        """Test DocumentList model has expected fields."""
        from bifrost import DocumentData, DocumentList

        doc = DocumentData(
            id="doc-id",
            table_id="table-id",
            data={"name": "Acme"},
        )

        doc_list = DocumentList(
            documents=[doc],
            total=100,
            limit=10,
            offset=0,
        )

        assert len(doc_list.documents) == 1
        assert doc_list.total == 100
        assert doc_list.limit == 10
        assert doc_list.offset == 0


    def test_batch_result_model_fields(self):
        """Test BatchResult model has expected fields."""
        from bifrost import BatchResult, DocumentData

        doc = DocumentData(
            id="doc-id",
            table_id="table-id",
            data={"name": "Acme"},
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )

        result = BatchResult(documents=[doc], count=1)

        assert len(result.documents) == 1
        assert result.count == 1
        assert result.documents[0].id == "doc-id"

    def test_batch_delete_result_model_fields(self):
        """Test BatchDeleteResult model has expected fields."""
        from bifrost import BatchDeleteResult

        result = BatchDeleteResult(deleted_ids=["id-1", "id-2"], count=2)

        assert result.deleted_ids == ["id-1", "id-2"]
        assert result.count == 2


class TestTablesSDKWithoutContext:
    """Test SDK behavior without execution context."""

    @pytest.mark.asyncio
    async def test_tables_create_without_context_raises_error(self, monkeypatch: pytest.MonkeyPatch):
        """Test that tables.create raises error when not authenticated."""
        from bifrost import tables
        from bifrost.client import _clear_client
        from bifrost._context import clear_execution_context
        import importlib

        # Ensure no context is set and no client injected
        clear_execution_context()
        _clear_client()
        monkeypatch.setattr(
            importlib.import_module("bifrost.tables"),
            "get_client",
            lambda: (_ for _ in ()).throw(RuntimeError("Not logged in")),
        )

        # Attempting to use SDK should raise RuntimeError about not being logged in
        with pytest.raises(RuntimeError, match="Not logged in"):
            await tables.create("test_table")

    @pytest.mark.asyncio
    async def test_tables_list_without_context_raises_error(self, monkeypatch: pytest.MonkeyPatch):
        """Test that tables.list raises error when not authenticated."""
        from bifrost import tables
        from bifrost.client import _clear_client
        from bifrost._context import clear_execution_context
        import importlib

        clear_execution_context()
        _clear_client()
        monkeypatch.setattr(
            importlib.import_module("bifrost.tables"),
            "get_client",
            lambda: (_ for _ in ()).throw(RuntimeError("Not logged in")),
        )

        with pytest.raises(RuntimeError, match="Not logged in"):
            await tables.list()

    @pytest.mark.asyncio
    async def test_tables_insert_without_context_raises_error(self, monkeypatch: pytest.MonkeyPatch):
        """Test that tables.insert raises error when not authenticated."""
        from bifrost import tables
        from bifrost.client import _clear_client
        from bifrost._context import clear_execution_context
        import importlib

        clear_execution_context()
        _clear_client()
        monkeypatch.setattr(
            importlib.import_module("bifrost.tables"),
            "get_client",
            lambda: (_ for _ in ()).throw(RuntimeError("Not logged in")),
        )

        with pytest.raises(RuntimeError, match="Not logged in"):
            await tables.insert("customers", {"name": "Test"})

    @pytest.mark.asyncio
    async def test_tables_query_without_context_raises_error(self, monkeypatch: pytest.MonkeyPatch):
        """Test that tables.query raises error when not authenticated."""
        from bifrost import tables
        from bifrost.client import _clear_client
        from bifrost._context import clear_execution_context
        import importlib

        clear_execution_context()
        _clear_client()
        monkeypatch.setattr(
            importlib.import_module("bifrost.tables"),
            "get_client",
            lambda: (_ for _ in ()).throw(RuntimeError("Not logged in")),
        )

        with pytest.raises(RuntimeError, match="Not logged in"):
            await tables.query("customers")


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeTablesClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    async def post(self, url: str, json: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append(("POST", url, json))
        return self.responses.pop(0)

    async def get(self, url: str) -> FakeResponse:
        self.calls.append(("GET", url, None))
        return self.responses.pop(0)

    async def patch(self, url: str, json: dict[str, Any] | None = None) -> FakeResponse:
        self.calls.append(("PATCH", url, json))
        return self.responses.pop(0)

    async def delete(self, url: str) -> FakeResponse:
        self.calls.append(("DELETE", url, None))
        return self.responses.pop(0)


def _table_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "table-1",
        "name": "customers",
        "description": None,
        "table_schema": None,
        "organization_id": None,
        "app_id": None,
        "created_by": "user-1",
        "created_at": "2026-07-04T00:00:00Z",
        "updated_at": "2026-07-04T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def _doc_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "id": "doc-1",
        "table_id": "table-1",
        "data": {"name": "Acme"},
        "created_at": "2026-07-04T00:00:00Z",
        "updated_at": "2026-07-04T00:00:00Z",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def tables_module(monkeypatch: pytest.MonkeyPatch):
    import importlib

    module = importlib.import_module("bifrost.tables")
    monkeypatch.setattr(module, "resolve_scope", lambda scope: scope)
    return module


class TestTablesSDKRequests:
    @pytest.mark.asyncio
    async def test_create_list_and_delete_table(self, tables_module, monkeypatch: pytest.MonkeyPatch):
        from bifrost import tables

        client = FakeTablesClient(
            [
                FakeResponse(200, _table_payload(description="Customer data")),
                FakeResponse(200, [_table_payload(), _table_payload(id="table-2", name="orders")]),
                FakeResponse(204, None),
            ]
        )
        monkeypatch.setattr(tables_module, "get_client", lambda: client)

        created = await tables.create("customers", description="Customer data", scope="org-1", app="app-1")
        listed = await tables.list(scope="org-1", app="app-1")
        deleted = await tables.delete("table-1")

        assert created.description == "Customer data"
        assert [table.name for table in listed] == ["customers", "orders"]
        assert deleted is True
        assert client.calls[0] == (
            "POST",
            "/api/sdk/tables/create",
            {
                "name": "customers",
                "description": "Customer data",
                "table_schema": None,
                "scope": "org-1",
                "app": "app-1",
            },
        )
        assert client.calls[2] == ("DELETE", "/api/tables/table-1", None)

    @pytest.mark.asyncio
    async def test_insert_and_upsert_auto_create_on_404(self, tables_module, monkeypatch: pytest.MonkeyPatch):
        from bifrost import tables

        client = FakeTablesClient(
            [
                FakeResponse(404, {"detail": "missing"}),
                FakeResponse(409, {"detail": "exists"}),
                FakeResponse(200, _doc_payload(id="inserted")),
                FakeResponse(404, {"detail": "missing"}),
                FakeResponse(200, _table_payload()),
                FakeResponse(200, _doc_payload(id="upserted")),
            ]
        )
        monkeypatch.setattr(tables_module, "get_client", lambda: client)

        inserted = await tables.insert("customers", {"name": "Acme"}, id="acme", scope="org-1")
        upserted = await tables.upsert("customers", "acme", {"name": "Acme 2"}, scope="org-1")

        assert inserted.id == "inserted"
        assert upserted.id == "upserted"
        assert [call[1] for call in client.calls] == [
            "/api/tables/customers/documents?scope=org-1",
            "/api/tables?scope=org-1",
            "/api/tables/customers/documents?scope=org-1",
            "/api/tables/customers/documents/upsert?scope=org-1",
            "/api/tables?scope=org-1",
            "/api/tables/customers/documents/upsert?scope=org-1",
        ]

    @pytest.mark.asyncio
    async def test_get_update_and_delete_document_not_found_paths(self, tables_module, monkeypatch: pytest.MonkeyPatch):
        from bifrost import tables

        client = FakeTablesClient(
            [
                FakeResponse(200, _doc_payload(id="doc-1")),
                FakeResponse(404, None),
                FakeResponse(200, _doc_payload(id="doc-1", data={"status": "active"})),
                FakeResponse(404, None),
                FakeResponse(204, None),
                FakeResponse(404, None),
            ]
        )
        monkeypatch.setattr(tables_module, "get_client", lambda: client)

        found = await tables.get("customers", "doc-1", scope="org-1")
        missing = await tables.get("customers", "missing", scope="org-1")
        updated = await tables.update("customers", "doc-1", {"status": "active"}, scope="org-1")
        missing_update = await tables.update("customers", "missing", {}, scope="org-1")
        deleted = await tables.delete_document("customers", "doc-1", scope="org-1")
        missing_delete = await tables.delete_document("customers", "missing", scope="org-1")

        assert found is not None and found.id == "doc-1"
        assert missing is None
        assert updated is not None and updated.data == {"status": "active"}
        assert missing_update is None
        assert deleted is True
        assert missing_delete is False

    @pytest.mark.asyncio
    async def test_batch_query_and_count_methods(self, tables_module, monkeypatch: pytest.MonkeyPatch):
        from bifrost import tables

        client = FakeTablesClient(
            [
                FakeResponse(200, {"documents": [_doc_payload(id="a")], "inserted": 1}),
                FakeResponse(200, {"documents": [_doc_payload(id="b")], "inserted": 1}),
                FakeResponse(200, {"deleted_ids": ["a", "b"], "deleted": 2}),
                FakeResponse(404, None),
                FakeResponse(200, {"documents": [_doc_payload(id="a")], "total": 1, "limit": 10, "offset": 0}),
                FakeResponse(404, None),
                FakeResponse(200, {"count": 5}),
                FakeResponse(200, {"documents": [], "total": 3, "limit": 1, "offset": 0}),
            ]
        )
        monkeypatch.setattr(tables_module, "get_client", lambda: client)

        inserted = await tables.insert_batch("customers", [{"name": "Acme"}], scope="org-1")
        upserted = await tables.upsert_batch(
            "customers",
            [{"id": "b", "data": {"name": "Beta"}}],
            scope="org-1",
            updated_by="user-1",
        )
        deleted = await tables.delete_batch("customers", ["a", "b"], scope="org-1")
        missing_delete = await tables.delete_batch("customers", ["missing"], scope="org-1")
        queried = await tables.query("customers", where={"status": "active"}, limit=10, scope="org-1")
        missing_query = await tables.query("missing", limit=10, scope="org-1")
        unfiltered_count = await tables.count("customers", scope="org-1")
        filtered_count = await tables.count("customers", where={"status": "active"}, scope="org-1")

        assert inserted.count == 1
        assert upserted.documents[0].id == "b"
        assert deleted.deleted_ids == ["a", "b"]
        assert missing_delete.count == 0
        assert queried.total == 1
        assert missing_query.documents == []
        assert unfiltered_count == 5
        assert filtered_count == 3

    @pytest.mark.asyncio
    async def test_create_rejects_solution_context(self, tables_module, monkeypatch: pytest.MonkeyPatch):
        from bifrost import tables

        monkeypatch.setattr(tables_module, "_has_solution_context", lambda: True)
        monkeypatch.setattr(tables_module, "get_client", lambda: FakeTablesClient([]))

        with pytest.raises(RuntimeError, match="Solution executions cannot create tables"):
            await tables.create("runtime_table")
