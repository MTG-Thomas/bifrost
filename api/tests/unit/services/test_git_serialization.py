"""
Unit tests for Git serialization service.

Tests the serialization and deserialization of workspace content
between the database and git folder.
"""

from src.services.git_serialization import (
    UnresolvedRef,
    DeserializationResult,
    get_nested_value,
    set_nested_value,
    transform_path_refs_to_uuids,
    find_fields_with_value,
    compute_file_hash,
)


class TestNestedValueHelpers:
    """Tests for the nested value get/set helper functions."""

    def test_get_nested_value_simple_key(self):
        """Test getting a value with a simple key."""
        data = {"name": "test", "workflow_id": "abc-123"}
        assert get_nested_value(data, "workflow_id") == "abc-123"

    def test_get_nested_value_nested_dict(self):
        """Test getting a value from nested dictionary."""
        data = {"form_schema": {"fields": [{"name": "field1", "workflow_id": "uuid-1"}]}}
        assert get_nested_value(data, "form_schema.fields.0.workflow_id") == "uuid-1"

    def test_get_nested_value_missing_key(self):
        """Test getting a value from non-existent key."""
        data = {"name": "test"}
        assert get_nested_value(data, "missing") is None

    def test_get_nested_value_array_index(self):
        """Test getting a value from array by index."""
        data = {"items": ["first", "second", "third"]}
        assert get_nested_value(data, "items.1") == "second"

    def test_get_nested_value_out_of_bounds(self):
        """Test getting a value with out of bounds index."""
        data = {"items": ["first"]}
        assert get_nested_value(data, "items.5") is None

    def test_set_nested_value_simple_key(self):
        """Test setting a value with a simple key."""
        data = {"workflow_id": "old-value"}
        set_nested_value(data, "workflow_id", "new-value")
        assert data["workflow_id"] == "new-value"

    def test_set_nested_value_nested_dict(self):
        """Test setting a value in nested dictionary."""
        data = {"form_schema": {"fields": [{"workflow_id": "old"}]}}
        set_nested_value(data, "form_schema.fields.0.workflow_id", "new")
        assert data["form_schema"]["fields"][0]["workflow_id"] == "new"

    def test_set_nested_value_missing_path(self):
        """Test setting a value on non-existent path does nothing."""
        data = {"name": "test"}
        set_nested_value(data, "missing.key", "value")
        assert "missing" not in data

    def test_set_nested_value_array(self):
        """Test setting a value in array by index."""
        data = {"items": ["old1", "old2"]}
        set_nested_value(data, "items.1", "new2")
        assert data["items"][1] == "new2"


class TestFindFieldsWithValue:
    """Tests for finding field paths containing a value."""

    def test_find_simple_value(self):
        """Test finding a simple value."""
        data = {"workflow_id": "uuid-123"}
        fields = find_fields_with_value(data, "uuid-123")
        assert "workflow_id" in fields

    def test_find_nested_value(self):
        """Test finding nested values."""
        data = {
            "workflow_id": "uuid-123",
            "launch_workflow_id": "uuid-456",
            "form_schema": {
                "fields": [
                    {"data_provider_id": "uuid-123"},
                    {"data_provider_id": "uuid-789"},
                ]
            },
        }
        fields = find_fields_with_value(data, "uuid-123")
        assert "workflow_id" in fields
        assert "form_schema.fields.0.data_provider_id" in fields
        assert len(fields) == 2

    def test_find_no_match(self):
        """Test finding value that doesn't exist."""
        data = {"name": "test"}
        fields = find_fields_with_value(data, "uuid-999")
        assert fields == []


class TestTransformPathRefsToUuids:
    """Tests for transforming path refs back to UUIDs."""

    def test_transform_single_ref(self):
        """Test transforming a single workflow ref."""
        data = {"workflow_id": "workflows/my_workflow.py::run"}
        ref_to_uuid = {"workflows/my_workflow.py::run": "uuid-abc-123"}

        unresolved = transform_path_refs_to_uuids(
            data, ["workflow_id"], ref_to_uuid
        )

        assert data["workflow_id"] == "uuid-abc-123"
        assert unresolved == []

    def test_transform_multiple_refs(self):
        """Test transforming multiple workflow refs."""
        data = {
            "workflow_id": "workflows/wf1.py::main",
            "launch_workflow_id": "workflows/wf2.py::run",
        }
        ref_to_uuid = {
            "workflows/wf1.py::main": "uuid-1",
            "workflows/wf2.py::run": "uuid-2",
        }

        unresolved = transform_path_refs_to_uuids(
            data, ["workflow_id", "launch_workflow_id"], ref_to_uuid
        )

        assert data["workflow_id"] == "uuid-1"
        assert data["launch_workflow_id"] == "uuid-2"
        assert unresolved == []

    def test_transform_unresolved_ref(self):
        """Test handling of unresolved refs."""
        data = {"workflow_id": "workflows/missing.py::missing_func"}
        ref_to_uuid = {}

        unresolved = transform_path_refs_to_uuids(
            data, ["workflow_id"], ref_to_uuid
        )

        assert len(unresolved) == 1
        assert unresolved[0].ref == "workflows/missing.py::missing_func"
        assert unresolved[0].field == "workflow_id"

    def test_transform_nested_refs(self):
        """Test transforming refs in nested structures."""
        data = {
            "form_schema": {
                "fields": [
                    {"name": "field1", "data_provider_id": "dp/my_dp.py::get_data"},
                ]
            }
        }
        ref_to_uuid = {"dp/my_dp.py::get_data": "uuid-dp-1"}

        unresolved = transform_path_refs_to_uuids(
            data, ["form_schema.fields.0.data_provider_id"], ref_to_uuid
        )

        assert data["form_schema"]["fields"][0]["data_provider_id"] == "uuid-dp-1"
        assert unresolved == []

    def test_transform_preserves_non_ref_fields(self):
        """Test that non-ref fields are not modified."""
        data = {
            "name": "Test Form",
            "description": "A test form",
            "workflow_id": "workflows/wf.py::run",
        }
        ref_to_uuid = {"workflows/wf.py::run": "uuid-1"}

        transform_path_refs_to_uuids(data, ["workflow_id"], ref_to_uuid)

        assert data["name"] == "Test Form"
        assert data["description"] == "A test form"


class TestDeserializationResultModel:
    """Tests for the DeserializationResult model."""

    def test_create_success_result(self):
        """Test creating a successful result."""
        result = DeserializationResult(
            success=True,
            files_processed=5,
            unresolved_refs=[],
            errors=[],
        )
        assert result.success is True
        assert result.files_processed == 5
        assert result.unresolved_refs == []
        assert result.errors == []

    def test_create_failure_result_with_unresolved_refs(self):
        """Test creating a result with unresolved refs."""
        result = DeserializationResult(
            success=False,
            files_processed=3,
            unresolved_refs=[
                UnresolvedRef(
                    file="forms/my_form.form.json",
                    field="workflow_id",
                    ref="workflows/missing.py::missing_func",
                )
            ],
            errors=[],
        )
        assert result.success is False
        assert len(result.unresolved_refs) == 1
        assert result.unresolved_refs[0].file == "forms/my_form.form.json"

    def test_create_failure_result_with_errors(self):
        """Test creating a result with errors."""
        result = DeserializationResult(
            success=False,
            files_processed=2,
            unresolved_refs=[],
            errors=["File not found: missing.py", "Invalid JSON in bad.json"],
        )
        assert result.success is False
        assert len(result.errors) == 2


class TestComputeFileHash:
    """Tests for the hash computation function."""

    def test_hash_consistency(self):
        """Test that same content produces same hash."""
        content = b"test content"
        hash1 = compute_file_hash(content)
        hash2 = compute_file_hash(content)
        assert hash1 == hash2

    def test_hash_difference(self):
        """Test that different content produces different hash."""
        hash1 = compute_file_hash(b"content 1")
        hash2 = compute_file_hash(b"content 2")
        assert hash1 != hash2

    def test_hash_format(self):
        """Test that hash is a valid SHA-256 hex string."""
        hash_val = compute_file_hash(b"test")
        assert len(hash_val) == 64
        assert all(c in "0123456789abcdef" for c in hash_val)
