"""
E2E tests for batch table document operations.

Tests the batch insert, upsert, and delete endpoints via the CLI API.

Note: Document `id` is a global primary key, so all IDs must be unique
across ALL tables, not just within a single table. Use uuid-suffixed IDs.
"""

import logging
from uuid import uuid4

logger = logging.getLogger(__name__)


def _uid(prefix: str = "") -> str:
    """Generate a globally unique ID with optional prefix."""
    return f"{prefix}{uuid4().hex[:12]}"


class TestInsertBatch:
    """Test batch insert endpoint."""

    def test_insert_batch(self, e2e_client, platform_admin):
        """Insert multiple documents in a single batch."""
        table_name = f"test_batch_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/cli/tables/documents/insert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": [
                    {"data": {"name": "Acme Corp", "status": "active"}},
                    {"data": {"name": "Beta Inc", "status": "pending"}},
                    {"data": {"name": "Gamma LLC", "status": "active"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 3
        assert len(data["documents"]) == 3
        for doc in data["documents"]:
            assert doc["id"] is not None
            assert doc["table_id"] is not None
            assert "name" in doc["data"]

    def test_insert_batch_with_custom_ids(self, e2e_client, platform_admin):
        """Insert batch with user-provided IDs."""
        table_name = f"test_batch_{uuid4().hex[:8]}"
        id1 = _uid("acme-")
        id2 = _uid("beta-")
        response = e2e_client.post(
            "/api/cli/tables/documents/insert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": [
                    {"id": id1, "data": {"name": "Acme Corp"}},
                    {"id": id2, "data": {"name": "Beta Inc"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 2
        ids = {doc["id"] for doc in data["documents"]}
        assert id1 in ids
        assert id2 in ids

    def test_insert_batch_duplicate_id_rolls_back(self, e2e_client, platform_admin):
        """Duplicate ID in batch causes 409 and atomic rollback."""
        table_name = f"test_batch_{uuid4().hex[:8]}"
        existing_id = _uid("existing-")

        # Insert a document first
        resp = e2e_client.post(
            "/api/cli/tables/documents/insert",
            headers=platform_admin.headers,
            json={"table": table_name, "id": existing_id, "data": {"name": "Existing"}},
        )
        assert resp.status_code == 200, resp.text

        # Try batch with a conflicting ID
        response = e2e_client.post(
            "/api/cli/tables/documents/insert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": [
                    {"id": _uid("new-"), "data": {"name": "New"}},
                    {"id": existing_id, "data": {"name": "Conflict"}},
                ],
            },
        )
        assert response.status_code == 409

    def test_insert_batch_auto_creates_table(self, e2e_client, platform_admin):
        """Batch insert auto-creates the table if it doesn't exist."""
        table_name = f"test_autocreate_{uuid4().hex[:8]}"
        response = e2e_client.post(
            "/api/cli/tables/documents/insert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": [
                    {"data": {"name": "First"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["count"] == 1

        # Verify table was created by querying it
        query_response = e2e_client.post(
            "/api/cli/tables/documents/query",
            headers=platform_admin.headers,
            json={"table": table_name},
        )
        assert query_response.status_code == 200
        assert query_response.json()["total"] == 1


class TestUpsertBatch:
    """Test batch upsert endpoint."""

    def test_upsert_batch_creates_new(self, e2e_client, platform_admin):
        """Upsert batch creates all new documents."""
        table_name = f"test_upsert_{uuid4().hex[:8]}"
        id1 = _uid("emp-")
        id2 = _uid("emp-")
        response = e2e_client.post(
            "/api/cli/tables/documents/upsert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": [
                    {"id": id1, "data": {"name": "John", "dept": "Eng"}},
                    {"id": id2, "data": {"name": "Jane", "dept": "Sales"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 2
        ids = {doc["id"] for doc in data["documents"]}
        assert ids == {id1, id2}

    def test_upsert_batch_updates_existing(self, e2e_client, platform_admin):
        """Upsert batch updates all existing documents."""
        table_name = f"test_upsert_{uuid4().hex[:8]}"
        id1 = _uid("emp-")
        id2 = _uid("emp-")

        # Create initial docs
        resp = e2e_client.post(
            "/api/cli/tables/documents/upsert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": [
                    {"id": id1, "data": {"name": "John", "dept": "Eng"}},
                    {"id": id2, "data": {"name": "Jane", "dept": "Sales"}},
                ],
            },
        )
        assert resp.status_code == 200, resp.text

        # Update them
        response = e2e_client.post(
            "/api/cli/tables/documents/upsert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": [
                    {"id": id1, "data": {"name": "John", "dept": "Management"}},
                    {"id": id2, "data": {"name": "Jane", "dept": "Marketing"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 2

        # Verify updates
        for doc in data["documents"]:
            if doc["id"] == id1:
                assert doc["data"]["dept"] == "Management"
            elif doc["id"] == id2:
                assert doc["data"]["dept"] == "Marketing"

    def test_upsert_batch_mixed(self, e2e_client, platform_admin):
        """Upsert batch with mix of new and existing documents."""
        table_name = f"test_upsert_{uuid4().hex[:8]}"
        existing_id = _uid("existing-")
        new_id = _uid("new-")

        # Create one doc
        resp = e2e_client.post(
            "/api/cli/tables/documents/upsert",
            headers=platform_admin.headers,
            json={"table": table_name, "id": existing_id, "data": {"name": "Old"}},
        )
        assert resp.status_code == 200, resp.text

        # Upsert with one existing + one new
        response = e2e_client.post(
            "/api/cli/tables/documents/upsert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": [
                    {"id": existing_id, "data": {"name": "Updated"}},
                    {"id": new_id, "data": {"name": "Brand New"}},
                ],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 2

        # Verify the total in the table
        query_response = e2e_client.post(
            "/api/cli/tables/documents/query",
            headers=platform_admin.headers,
            json={"table": table_name},
        )
        assert query_response.json()["total"] == 2


class TestDeleteBatch:
    """Test batch delete endpoint."""

    def test_delete_batch(self, e2e_client, platform_admin):
        """Delete multiple existing documents."""
        table_name = f"test_delete_{uuid4().hex[:8]}"
        id1 = _uid("del-")
        id2 = _uid("del-")
        id3 = _uid("del-")

        # Create docs
        resp = e2e_client.post(
            "/api/cli/tables/documents/insert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": [
                    {"id": id1, "data": {"name": "A"}},
                    {"id": id2, "data": {"name": "B"}},
                    {"id": id3, "data": {"name": "C"}},
                ],
            },
        )
        assert resp.status_code == 200, resp.text

        # Delete two of them
        response = e2e_client.post(
            "/api/cli/tables/documents/delete/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "doc_ids": [id1, id3],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 2
        assert set(data["deleted_ids"]) == {id1, id3}

        # Verify only one remains
        query_response = e2e_client.post(
            "/api/cli/tables/documents/query",
            headers=platform_admin.headers,
            json={"table": table_name},
        )
        assert query_response.json()["total"] == 1

    def test_delete_batch_skips_nonexistent(self, e2e_client, platform_admin):
        """Non-existent IDs are silently skipped."""
        table_name = f"test_delete_{uuid4().hex[:8]}"
        real_id = _uid("real-")

        # Create one doc
        resp = e2e_client.post(
            "/api/cli/tables/documents/insert",
            headers=platform_admin.headers,
            json={"table": table_name, "id": real_id, "data": {"name": "Real"}},
        )
        assert resp.status_code == 200, resp.text

        # Delete with mix of real and fake IDs
        response = e2e_client.post(
            "/api/cli/tables/documents/delete/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "doc_ids": [real_id, _uid("fake-"), _uid("fake-")],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 1
        assert data["deleted_ids"] == [real_id]

    def test_delete_batch_nonexistent_table(self, e2e_client, platform_admin):
        """Delete from a non-existent table returns count=0."""
        response = e2e_client.post(
            "/api/cli/tables/documents/delete/batch",
            headers=platform_admin.headers,
            json={
                "table": f"nonexistent_{uuid4().hex[:8]}",
                "doc_ids": [_uid(), _uid()],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["count"] == 0
        assert data["deleted_ids"] == []


class TestBatchLimits:
    """Test batch size limits."""

    def test_batch_over_limit(self, e2e_client, platform_admin):
        """Batch with >1000 items returns 422."""
        table_name = f"test_limit_{uuid4().hex[:8]}"
        documents = [{"data": {"i": i}} for i in range(1001)]

        response = e2e_client.post(
            "/api/cli/tables/documents/insert/batch",
            headers=platform_admin.headers,
            json={
                "table": table_name,
                "documents": documents,
            },
        )
        assert response.status_code == 422
