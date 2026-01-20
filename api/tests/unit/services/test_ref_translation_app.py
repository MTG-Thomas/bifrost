"""
Unit tests for app source workflow reference transformation.

Tests transform_app_source_uuids_to_refs and transform_app_source_refs_to_uuids.
"""

from src.services.file_storage.ref_translation import (
    transform_app_source_uuids_to_refs,
    transform_app_source_refs_to_uuids,
)


class TestTransformAppSourceUuidsToRefs:
    """Tests for UUID -> portable ref transformation."""

    def test_transforms_single_use_workflow(self):
        """Single useWorkflow call with matching UUID is transformed."""
        source = """
import { useWorkflow } from '@bifrost/sdk';

export function MyComponent() {
    const { execute } = useWorkflow('550e8400-e29b-41d4-a716-446655440000');
    return <button onClick={execute}>Run</button>;
}
"""
        workflow_map = {
            "550e8400-e29b-41d4-a716-446655440000": "workflows/onboarding.py::provision_user"
        }

        result, transformed = transform_app_source_uuids_to_refs(source, workflow_map)

        assert "useWorkflow('workflows/onboarding.py::provision_user')" in result
        assert "550e8400-e29b-41d4-a716-446655440000" not in result
        assert "550e8400-e29b-41d4-a716-446655440000" in transformed

    def test_transforms_double_quoted_uuid(self):
        """Double-quoted UUID strings are also transformed."""
        source = 'const w = useWorkflow("550e8400-e29b-41d4-a716-446655440000");'
        workflow_map = {
            "550e8400-e29b-41d4-a716-446655440000": "workflows/test.py::my_func"
        }

        result, transformed = transform_app_source_uuids_to_refs(source, workflow_map)

        assert 'useWorkflow("workflows/test.py::my_func")' in result
        assert len(transformed) == 1

    def test_transforms_multiple_use_workflow_calls(self):
        """Multiple useWorkflow calls in same file are all transformed."""
        source = """
const w1 = useWorkflow('uuid-1111-1111-1111-111111111111');
const w2 = useWorkflow('uuid-2222-2222-2222-222222222222');
"""
        workflow_map = {
            "uuid-1111-1111-1111-111111111111": "workflows/a.py::func_a",
            "uuid-2222-2222-2222-222222222222": "workflows/b.py::func_b",
        }

        result, transformed = transform_app_source_uuids_to_refs(source, workflow_map)

        assert "useWorkflow('workflows/a.py::func_a')" in result
        assert "useWorkflow('workflows/b.py::func_b')" in result
        assert len(transformed) == 2

    def test_ignores_uuid_not_in_map(self):
        """UUIDs not in the workflow map are left unchanged."""
        source = "const w = useWorkflow('unknown-uuid-not-in-map-00000');"
        workflow_map = {}

        result, transformed = transform_app_source_uuids_to_refs(source, workflow_map)

        assert result == source
        assert len(transformed) == 0

    def test_preserves_non_use_workflow_content(self):
        """Non-useWorkflow code is preserved unchanged."""
        source = """
const uuid = '550e8400-e29b-41d4-a716-446655440000';
const other = someFunction('550e8400-e29b-41d4-a716-446655440000');
const w = useWorkflow('550e8400-e29b-41d4-a716-446655440000');
"""
        workflow_map = {
            "550e8400-e29b-41d4-a716-446655440000": "workflows/test.py::func"
        }

        result, _ = transform_app_source_uuids_to_refs(source, workflow_map)

        # Only the useWorkflow call should be transformed
        assert "const uuid = '550e8400-e29b-41d4-a716-446655440000'" in result
        assert "someFunction('550e8400-e29b-41d4-a716-446655440000')" in result
        assert "useWorkflow('workflows/test.py::func')" in result

    def test_empty_source(self):
        """Empty source returns empty result."""
        result, transformed = transform_app_source_uuids_to_refs("", {})

        assert result == ""
        assert len(transformed) == 0

    def test_empty_workflow_map(self):
        """Empty workflow map leaves source unchanged."""
        source = "const w = useWorkflow('some-uuid');"

        result, transformed = transform_app_source_uuids_to_refs(source, {})

        assert result == source
        assert len(transformed) == 0


class TestTransformAppSourceRefsToUuids:
    """Tests for portable ref -> UUID transformation."""

    def test_transforms_single_portable_ref(self):
        """Single portable ref is resolved to UUID."""
        source = """
const { execute } = useWorkflow('workflows/onboarding.py::provision_user');
"""
        ref_to_uuid = {
            "workflows/onboarding.py::provision_user": "550e8400-e29b-41d4-a716-446655440000"
        }

        result, unresolved = transform_app_source_refs_to_uuids(source, ref_to_uuid)

        assert "useWorkflow('550e8400-e29b-41d4-a716-446655440000')" in result
        assert "workflows/onboarding.py::provision_user" not in result
        assert len(unresolved) == 0

    def test_transforms_double_quoted_ref(self):
        """Double-quoted portable refs are also transformed."""
        source = 'const w = useWorkflow("workflows/test.py::my_func");'
        ref_to_uuid = {
            "workflows/test.py::my_func": "uuid-1234"
        }

        result, unresolved = transform_app_source_refs_to_uuids(source, ref_to_uuid)

        assert 'useWorkflow("uuid-1234")' in result
        assert len(unresolved) == 0

    def test_collects_unresolved_refs(self):
        """Refs not in the map are collected as unresolved."""
        source = """
const w1 = useWorkflow('workflows/exists.py::func');
const w2 = useWorkflow('workflows/missing.py::not_found');
"""
        ref_to_uuid = {
            "workflows/exists.py::func": "uuid-exists"
        }

        result, unresolved = transform_app_source_refs_to_uuids(source, ref_to_uuid)

        assert "useWorkflow('uuid-exists')" in result
        # Missing ref stays as-is
        assert "useWorkflow('workflows/missing.py::not_found')" in result
        assert len(unresolved) == 1
        assert unresolved[0] == "workflows/missing.py::not_found"

    def test_uuid_strings_pass_through(self):
        """Already-resolved UUIDs pass through unchanged."""
        source = "const w = useWorkflow('550e8400-e29b-41d4-a716-446655440000');"
        ref_to_uuid = {}

        result, unresolved = transform_app_source_refs_to_uuids(source, ref_to_uuid)

        # UUID strings that look like UUIDs should pass through
        assert result == source
        assert len(unresolved) == 0

    def test_multiple_refs_resolved(self):
        """Multiple refs are all resolved."""
        source = """
const w1 = useWorkflow('workflows/a.py::func_a');
const w2 = useWorkflow('workflows/b.py::func_b');
"""
        ref_to_uuid = {
            "workflows/a.py::func_a": "uuid-a",
            "workflows/b.py::func_b": "uuid-b",
        }

        result, unresolved = transform_app_source_refs_to_uuids(source, ref_to_uuid)

        assert "useWorkflow('uuid-a')" in result
        assert "useWorkflow('uuid-b')" in result
        assert len(unresolved) == 0

    def test_empty_source(self):
        """Empty source returns empty result."""
        result, unresolved = transform_app_source_refs_to_uuids("", {})

        assert result == ""
        assert len(unresolved) == 0

    def test_preserves_non_use_workflow_content(self):
        """Non-useWorkflow code is preserved unchanged."""
        source = """
const ref = 'workflows/test.py::func';
const other = someFunction('workflows/test.py::func');
const w = useWorkflow('workflows/test.py::func');
"""
        ref_to_uuid = {
            "workflows/test.py::func": "uuid-1234"
        }

        result, _ = transform_app_source_refs_to_uuids(source, ref_to_uuid)

        # Only the useWorkflow call should be transformed
        assert "const ref = 'workflows/test.py::func'" in result
        assert "someFunction('workflows/test.py::func')" in result
        assert "useWorkflow('uuid-1234')" in result
