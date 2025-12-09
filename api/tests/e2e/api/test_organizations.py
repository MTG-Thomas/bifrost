"""
E2E tests for organization management.

Tests CRUD operations and access control for organizations.
"""

import pytest


@pytest.mark.e2e
class TestOrganizationCRUD:
    """Test organization CRUD operations."""

    def test_organization_created_via_fixture(self, org1):
        """Organization should be created via fixture."""
        assert org1["name"] == "Bifrost Dev Org"
        assert org1["domain"] == "gobifrost.dev"
        assert "id" in org1

    def test_second_organization_created(self, org2):
        """Second organization for isolation tests."""
        assert org2["name"] == "Second Test Org"
        assert org2["domain"] == "example.com"
        assert "id" in org2

    def test_list_organizations(self, e2e_client, platform_admin, org1, org2):
        """Platform admin can list all organizations."""
        response = e2e_client.get(
            "/api/organizations",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List orgs failed: {response.text}"
        orgs = response.json()
        assert len(orgs) >= 2
        org_names = [o["name"] for o in orgs]
        assert "Bifrost Dev Org" in org_names
        assert "Second Test Org" in org_names

    def test_get_organization_by_id(self, e2e_client, platform_admin, org1):
        """Platform admin can get specific organization."""
        response = e2e_client.get(
            f"/api/organizations/{org1['id']}",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Get org failed: {response.text}"
        org = response.json()
        assert org["id"] == org1["id"]
        assert org["name"] == "Bifrost Dev Org"


@pytest.mark.e2e
class TestOrganizationAccess:
    """Test organization access control."""

    def test_org_user_cannot_list_all_organizations(self, e2e_client, org1_user):
        """Org user should not be able to list all organizations."""
        response = e2e_client.get(
            "/api/organizations",
            headers=org1_user.headers,
        )
        assert response.status_code == 403

    def test_org_user_cannot_create_organization(self, e2e_client, org1_user):
        """Org user should not be able to create organizations."""
        response = e2e_client.post(
            "/api/organizations",
            headers=org1_user.headers,
            json={"name": "Unauthorized Org", "domain": "unauthorized.com"},
        )
        assert response.status_code == 403


@pytest.mark.e2e
class TestOrganizationIsolation:
    """Test organization isolation."""

    def test_org1_user_cannot_see_org2_data(self, e2e_client, org1_user, org2):
        """Org1 user should not be able to access org2's resources."""
        # Try to access org2's data using explicit org header
        response = e2e_client.get(
            "/api/forms",
            headers=org1_user.headers_with_org(org2["id"]),
        )
        assert response.status_code == 403, "Cross-org access should be denied"
