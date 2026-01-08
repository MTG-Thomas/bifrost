"""
E2E tests for duplicate detection and conflict handling.

Tests how the system handles:
- Same path + same function name (should update, not duplicate)
- Same name at different paths (allowed - names can duplicate)
- Form duplicate path handling
- App duplicate slug handling
"""

import time
import pytest


@pytest.mark.e2e
class TestWorkflowDuplicateHandling:
    """Test workflow duplicate detection and handling."""

    def test_same_path_same_function_updates_not_duplicates(
        self, e2e_client, platform_admin
    ):
        """
        Writing to same path with same function name updates existing workflow.
        Should NOT create duplicate entries.
        """
        workflow_content_v1 = '''"""Version 1 Workflow"""
from bifrost import workflow

@workflow(
    name="duplicate_test_workflow",
    description="First version",
)
async def duplicate_test_workflow() -> str:
    return "v1"
'''
        # Create first version
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "duplicate_test.py",
                "content": workflow_content_v1,
                "encoding": "utf-8",
            },
        )
        time.sleep(2)

        # Get workflow ID
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        workflows = response.json()
        v1_workflow = next(
            (w for w in workflows if w["name"] == "duplicate_test_workflow"),
            None
        )
        assert v1_workflow is not None
        v1_id = v1_workflow["id"]

        # Count workflows with this name
        matching_v1 = [w for w in workflows if w["name"] == "duplicate_test_workflow"]
        assert len(matching_v1) == 1, "Should have exactly one workflow"

        # Update with new content (same path, same function)
        workflow_content_v2 = '''"""Version 2 Workflow"""
from bifrost import workflow

@workflow(
    name="duplicate_test_workflow",
    description="Second version - updated",
)
async def duplicate_test_workflow() -> str:
    return "v2"
'''
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "duplicate_test.py",
                "content": workflow_content_v2,
                "encoding": "utf-8",
            },
        )
        time.sleep(2)

        # Verify still only one workflow, same ID
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        workflows = response.json()
        matching_v2 = [w for w in workflows if w["name"] == "duplicate_test_workflow"]
        assert len(matching_v2) == 1, \
            f"Should still have exactly one workflow, got {len(matching_v2)}"

        v2_workflow = matching_v2[0]
        assert v2_workflow["id"] == v1_id, \
            "Workflow ID should remain stable across updates"
        assert "Second version" in v2_workflow.get("description", ""), \
            "Description should be updated"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=duplicate_test.py",
            headers=platform_admin.headers,
        )

    def test_same_name_different_paths_allowed(
        self, e2e_client, platform_admin
    ):
        """
        Same workflow name at different paths is allowed.
        Path + function_name is unique, not just name.
        """
        workflow_content = '''"""Workflow with common name"""
from bifrost import workflow

@workflow(
    name="common_workflow_name",
    description="At path {path}",
)
async def common_workflow_name() -> str:
    return "result"
'''
        # Create at first path
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "path1/workflow.py",
                "content": workflow_content.replace("{path}", "path1"),
                "encoding": "utf-8",
            },
        )
        time.sleep(2)

        # Create at second path (same name, different path)
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "path2/workflow.py",
                "content": workflow_content.replace("{path}", "path2"),
                "encoding": "utf-8",
            },
        )
        time.sleep(2)

        # Should have two workflows with same name
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        workflows = response.json()
        matching = [w for w in workflows if w["name"] == "common_workflow_name"]

        # Both should exist
        assert len(matching) == 2, \
            f"Should have two workflows with same name, got {len(matching)}"

        # They should have different IDs
        ids = [w["id"] for w in matching]
        assert len(set(ids)) == 2, "Should have unique IDs"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=path1",
            headers=platform_admin.headers,
        )
        e2e_client.delete(
            "/api/files/editor?path=path2",
            headers=platform_admin.headers,
        )

    def test_multiple_workflows_in_same_file(
        self, e2e_client, platform_admin
    ):
        """
        Multiple @workflow decorators in same file creates multiple entries.
        """
        multi_workflow_content = '''"""Multiple Workflows in One File"""
from bifrost import workflow

@workflow(name="multi_workflow_1")
async def multi_workflow_1() -> str:
    return "first"

@workflow(name="multi_workflow_2")
async def multi_workflow_2() -> str:
    return "second"

@workflow(name="multi_workflow_3")
async def multi_workflow_3() -> str:
    return "third"
'''
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "multi_workflow.py",
                "content": multi_workflow_content,
                "encoding": "utf-8",
            },
        )
        time.sleep(2)

        # Should create three workflow entries
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        workflows = response.json()
        multi_workflows = [
            w for w in workflows
            if w["name"] in ["multi_workflow_1", "multi_workflow_2", "multi_workflow_3"]
        ]

        assert len(multi_workflows) == 3, \
            f"Should have 3 workflows from multi-workflow file, got {len(multi_workflows)}"

        # All should have same source file path but different function names
        paths = [w.get("source_file_path") for w in multi_workflows]
        assert all("multi_workflow.py" in (p or "") for p in paths), \
            "All workflows should reference the same file"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=multi_workflow.py",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestFormDuplicateHandling:
    """Test form duplicate detection and handling."""

    def test_form_names_can_duplicate(
        self, e2e_client, platform_admin
    ):
        """
        Multiple forms with same name are allowed (IDs are unique).
        """
        # Create first form
        response1 = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Duplicate Name Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response1.status_code == 201
        form1 = response1.json()

        # Create second form with same name
        response2 = e2e_client.post(
            "/api/forms",
            headers=platform_admin.headers,
            json={
                "name": "Duplicate Name Form",
                "workflow_id": None,
                "form_schema": {"fields": []},
                "access_level": "authenticated",
            },
        )
        assert response2.status_code == 201
        form2 = response2.json()

        # Should have different IDs
        assert form1["id"] != form2["id"], \
            "Forms with same name should have different IDs"

        # List should show both
        response = e2e_client.get(
            "/api/forms",
            headers=platform_admin.headers,
        )
        forms = response.json()
        matching = [f for f in forms if f["name"] == "Duplicate Name Form"]
        assert len(matching) >= 2, \
            "Should have at least 2 forms with same name"

        # Cleanup
        e2e_client.delete(f"/api/forms/{form1['id']}", headers=platform_admin.headers)
        e2e_client.delete(f"/api/forms/{form2['id']}", headers=platform_admin.headers)


@pytest.mark.e2e
class TestDataProviderDuplicateHandling:
    """Test data provider duplicate detection."""

    def test_same_path_same_function_updates_provider(
        self, e2e_client, platform_admin
    ):
        """
        Same path + function name updates existing data provider.
        """
        dp_v1 = '''"""DP Version 1"""
from bifrost import data_provider

@data_provider(
    name="dup_test_provider",
    description="Version 1",
)
async def dup_test_provider():
    return [{"v": 1}]
'''
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "dup_test_provider.py",
                "content": dp_v1,
                "encoding": "utf-8",
            },
        )
        time.sleep(2)

        # Get first version
        response = e2e_client.get(
            "/api/workflows?type=data_provider",
            headers=platform_admin.headers,
        )
        providers = response.json()
        v1_provider = next(
            (p for p in providers if p["name"] == "dup_test_provider"),
            None
        )
        assert v1_provider is not None
        v1_id = v1_provider["id"]

        # Update with new content
        dp_v2 = '''"""DP Version 2"""
from bifrost import data_provider

@data_provider(
    name="dup_test_provider",
    description="Version 2 - updated",
)
async def dup_test_provider():
    return [{"v": 2}]
'''
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "dup_test_provider.py",
                "content": dp_v2,
                "encoding": "utf-8",
            },
        )
        time.sleep(2)

        # Should still be one provider with same ID
        response = e2e_client.get(
            "/api/workflows?type=data_provider",
            headers=platform_admin.headers,
        )
        providers = response.json()
        matching = [p for p in providers if p["name"] == "dup_test_provider"]
        assert len(matching) == 1, \
            f"Should have exactly one provider, got {len(matching)}"
        assert matching[0]["id"] == v1_id, \
            "Provider ID should remain stable"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=dup_test_provider.py",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestMixedEntityDuplicates:
    """Test handling of same name across different entity types."""

    def test_same_name_workflow_and_data_provider(
        self, e2e_client, platform_admin
    ):
        """
        Same name for workflow and data provider is allowed
        (they are different entity types).
        """
        # Create workflow
        workflow_content = '''"""Workflow with shared name"""
from bifrost import workflow

@workflow(name="shared_name_entity")
async def shared_name_entity() -> str:
    return "workflow"
'''
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "shared_name_workflow.py",
                "content": workflow_content,
                "encoding": "utf-8",
            },
        )
        time.sleep(2)

        # Create data provider with same name
        dp_content = '''"""Data Provider with shared name"""
from bifrost import data_provider

@data_provider(name="shared_name_entity")
async def shared_name_entity():
    return [{"type": "dp"}]
'''
        e2e_client.put(
            "/api/files/editor/content",
            headers=platform_admin.headers,
            json={
                "path": "shared_name_dp.py",
                "content": dp_content,
                "encoding": "utf-8",
            },
        )
        time.sleep(2)

        # Both should exist
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        workflows = response.json()
        wf = next((w for w in workflows if w["name"] == "shared_name_entity"), None)
        assert wf is not None, "Workflow should exist"

        response = e2e_client.get(
            "/api/workflows?type=data_provider",
            headers=platform_admin.headers,
        )
        providers = response.json()
        dp = next((p for p in providers if p["name"] == "shared_name_entity"), None)
        assert dp is not None, "Data provider should exist"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=shared_name_workflow.py",
            headers=platform_admin.headers,
        )
        e2e_client.delete(
            "/api/files/editor?path=shared_name_dp.py",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestIdempotentWrites:
    """Test that repeated writes are idempotent."""

    def test_repeated_writes_same_content_idempotent(
        self, e2e_client, platform_admin
    ):
        """
        Writing same content multiple times doesn't create duplicates.
        """
        workflow_content = '''"""Idempotent Workflow"""
from bifrost import workflow

@workflow(name="idempotent_workflow")
async def idempotent_workflow() -> str:
    return "stable"
'''
        # Write 3 times
        for i in range(3):
            response = e2e_client.put(
                "/api/files/editor/content",
                headers=platform_admin.headers,
                json={
                    "path": "idempotent_test.py",
                    "content": workflow_content,
                    "encoding": "utf-8",
                },
            )
            assert response.status_code == 200
            time.sleep(0.5)

        time.sleep(2)

        # Should have exactly one workflow
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        workflows = response.json()
        matching = [w for w in workflows if w["name"] == "idempotent_workflow"]
        assert len(matching) == 1, \
            f"Repeated writes should be idempotent, got {len(matching)} entries"

        # Cleanup
        e2e_client.delete(
            "/api/files/editor?path=idempotent_test.py",
            headers=platform_admin.headers,
        )
