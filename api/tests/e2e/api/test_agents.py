"""
Agents E2E Tests.

Tests agent CRUD operations and role assignment.
"""

import logging

import pytest

logger = logging.getLogger(__name__)


class TestAgentsCRUD:
    """Test agent CRUD operations."""

    def test_list_agents_empty(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test listing agents when none exist."""
        response = e2e_client.get(
            "/api/agents",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        # May have pre-existing agents from other tests
        data = response.json()
        assert isinstance(data, list)

    def test_create_agent(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test creating an agent."""
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": "Test Assistant",
                "description": "A helpful test assistant",
                "system_prompt": "You are a helpful assistant for testing.",
                "channels": ["chat"],
                "access_level": "public",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 201, f"Create agent failed: {response.text}"

        data = response.json()
        assert data["name"] == "Test Assistant"
        assert data["description"] == "A helpful test assistant"
        assert data["system_prompt"] == "You are a helpful assistant for testing."
        assert data["is_active"] is True
        assert "id" in data

    def test_get_agent(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test getting an agent by ID."""
        response = e2e_client.get(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["id"] == test_agent["id"]
        assert data["name"] == test_agent["name"]

    def test_update_agent(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test updating an agent."""
        response = e2e_client.put(
            f"/api/agents/{test_agent['id']}",
            json={
                "name": "Updated Assistant",
                "description": "An updated description",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Update agent failed: {response.text}"

        data = response.json()
        assert data["name"] == "Updated Assistant"
        assert data["description"] == "An updated description"

    def test_delete_agent(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test soft deleting an agent."""
        response = e2e_client.delete(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 204

        # Verify it's inactive
        response = e2e_client.get(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_list_agents_excludes_inactive_by_default(
        self,
        e2e_client,
        platform_admin,
        test_agent,
    ):
        """Test that inactive agents are excluded from list by default."""
        # First delete the agent
        e2e_client.delete(
            f"/api/agents/{test_agent['id']}",
            headers=platform_admin.headers,
        )

        # List should not include it
        response = e2e_client.get(
            "/api/agents",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        data = response.json()
        agent_ids = [a["id"] for a in data]
        assert test_agent["id"] not in agent_ids

    def test_get_agent_not_found(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting non-existent agent returns 404."""
        import uuid
        fake_id = str(uuid.uuid4())
        response = e2e_client.get(
            f"/api/agents/{fake_id}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 404


class TestAgentsAccessControl:
    """Test agent access control."""

    def test_org_user_cannot_create_agent(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot create agents."""
        response = e2e_client.post(
            "/api/agents",
            json={
                "name": "Unauthorized Agent",
                "system_prompt": "Test prompt",
                "channels": ["chat"],
                "access_level": "public",
            },
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_cannot_update_agent(
        self,
        e2e_client,
        org1_user,
        test_agent,
    ):
        """Test that org users cannot update agents."""
        response = e2e_client.put(
            f"/api/agents/{test_agent['id']}",
            json={"name": "Hacked Name"},
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_cannot_delete_agent(
        self,
        e2e_client,
        org1_user,
        test_agent,
    ):
        """Test that org users cannot delete agents."""
        response = e2e_client.delete(
            f"/api/agents/{test_agent['id']}",
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_can_list_public_agents(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users can list agents."""
        response = e2e_client.get(
            "/api/agents",
            headers=org1_user.headers,
        )
        # Should succeed - access control filters results
        assert response.status_code == 200


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_agent(e2e_client, platform_admin):
    """Create a test agent for use in tests."""
    response = e2e_client.post(
        "/api/agents",
        json={
            "name": "E2E Test Agent",
            "description": "Agent for E2E testing",
            "system_prompt": "You are a test assistant.",
            "channels": ["chat"],
            "access_level": "public",
        },
        headers=platform_admin.headers,
    )
    assert response.status_code == 201, f"Failed to create test agent: {response.text}"
    agent = response.json()

    yield agent

    # Cleanup - delete the agent
    try:
        e2e_client.delete(
            f"/api/agents/{agent['id']}",
            headers=platform_admin.headers,
        )
    except Exception:
        pass
