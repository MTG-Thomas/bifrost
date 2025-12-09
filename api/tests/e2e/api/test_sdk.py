"""
E2E tests for SDK API key management.

Tests SDK key creation, context retrieval, and revocation.
"""

import pytest


@pytest.mark.e2e
class TestSDKKeyManagement:
    """Test SDK API key management."""

    @pytest.fixture
    def sdk_key(self, e2e_client, platform_admin):
        """Create an SDK API key and clean up after."""
        response = e2e_client.post(
            "/api/sdk/keys",
            headers=platform_admin.headers,
            json={
                "name": "E2E Test SDK Key",
                "expires_in_days": 30,
            },
        )
        assert response.status_code == 201, f"Create SDK key failed: {response.text}"
        key_data = response.json()

        yield key_data

        # Cleanup - delete the key
        e2e_client.delete(
            f"/api/sdk/keys/{key_data['id']}",
            headers=platform_admin.headers,
        )

    def test_create_sdk_api_key(self, e2e_client, platform_admin):
        """Platform admin can create an SDK API key."""
        response = e2e_client.post(
            "/api/sdk/keys",
            headers=platform_admin.headers,
            json={
                "name": "E2E Create Test Key",
                "expires_in_days": 30,
            },
        )
        assert response.status_code == 201, f"Create failed: {response.text}"
        data = response.json()

        assert "id" in data
        assert "key" in data
        assert data["name"] == "E2E Create Test Key"
        assert data["key"].startswith("bfsk_")
        assert data["is_active"] is True

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/keys/{data['id']}",
            headers=platform_admin.headers,
        )

    def test_list_sdk_api_keys(self, e2e_client, platform_admin, sdk_key):
        """Platform admin can list SDK API keys."""
        response = e2e_client.get(
            "/api/sdk/keys",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"List failed: {response.text}"
        data = response.json()
        # API returns {"keys": [...]}
        keys = data.get("keys", data)
        assert isinstance(keys, list)
        key_names = [k["name"] for k in keys]
        assert "E2E Test SDK Key" in key_names

    def test_revoke_sdk_api_key(self, e2e_client, platform_admin):
        """Platform admin can revoke an SDK API key."""
        # Create a key to revoke
        create_resp = e2e_client.post(
            "/api/sdk/keys",
            headers=platform_admin.headers,
            json={"name": "Key to Revoke", "expires_in_days": 30},
        )
        assert create_resp.status_code == 201, f"Create failed: {create_resp.text}"
        key_data = create_resp.json()
        key_id = key_data["id"]

        # Revoke it using PATCH
        response = e2e_client.patch(
            f"/api/sdk/keys/{key_id}/revoke",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Revoke failed: {response.text}"

        # Verify the key is inactive
        revoked_key = response.json()
        assert revoked_key["is_active"] is False

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/keys/{key_id}",
            headers=platform_admin.headers,
        )


@pytest.mark.e2e
class TestSDKDownload:
    """Test SDK package download."""

    def test_download_sdk_package(self, e2e_client, platform_admin):
        """Platform admin can download SDK package."""
        response = e2e_client.get(
            "/api/sdk/download",
            headers=platform_admin.headers,
        )
        # Should return file or redirect
        assert response.status_code in [200, 302, 307], f"Download failed: {response.status_code}"


@pytest.mark.e2e
class TestSDKAccess:
    """Test SDK access control."""

    def test_any_user_can_create_sdk_keys(self, e2e_client, org1_user):
        """Any authenticated user can create SDK API keys."""
        # Note: SDK key creation is available to all authenticated users,
        # not just superusers. Each user manages their own keys.
        response = e2e_client.post(
            "/api/sdk/keys",
            headers=org1_user.headers,
            json={"name": "Org User Key", "expires_in_days": 30},
        )
        assert response.status_code == 201, f"Create failed: {response.text}"
        key_data = response.json()

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/keys/{key_data['id']}",
            headers=org1_user.headers,
        )


@pytest.mark.e2e
class TestSDKContext:
    """Test SDK context operations using API key authentication."""

    @pytest.fixture
    def sdk_key(self, e2e_client, platform_admin):
        """Create an SDK API key for context testing."""
        response = e2e_client.post(
            "/api/sdk/keys",
            headers=platform_admin.headers,
            json={
                "name": "E2E SDK Context Test Key",
                "expires_in_days": 30,
            },
        )
        assert response.status_code == 201, f"Create SDK key failed: {response.text}"
        key_data = response.json()

        yield key_data

        # Cleanup - delete the key
        e2e_client.delete(
            f"/api/sdk/keys/{key_data['id']}",
            headers=platform_admin.headers,
        )

    def test_get_context_with_api_key(self, e2e_client, platform_admin, sdk_key):
        """Test getting developer context using SDK API key via Authorization header."""
        # Get context using the SDK API key
        response = e2e_client.get(
            "/api/sdk/context",
            headers={
                "Authorization": f"Bearer {sdk_key['key']}",
            },
        )
        assert response.status_code == 200, f"Get context failed: {response.text}"

        data = response.json()

        # Verify response structure
        assert "user" in data, "Response should contain user info"
        assert "organization" in data, "Response should contain organization"
        assert "default_parameters" in data, "Response should contain default_parameters"
        assert "track_executions" in data, "Response should contain track_executions"

        # Verify user information matches platform_admin
        assert data["user"]["email"] == platform_admin.email
        assert data["user"]["name"] == platform_admin.name
        assert "id" in data["user"]

        # Verify defaults
        assert data["organization"] is None, "Should have no default organization initially"
        assert isinstance(data["default_parameters"], dict)
        assert data["track_executions"] is True, "Should track executions by default"

    def test_update_developer_context(self, e2e_client, platform_admin, org1, sdk_key):
        """Test updating developer context settings via bearer token."""
        # Update context with new settings
        response = e2e_client.put(
            "/api/sdk/context",
            headers=platform_admin.headers,
            json={
                "default_org_id": org1["id"],
                "default_parameters": {
                    "test_param": "test_value",
                    "another_param": 42,
                },
                "track_executions": False,
            },
        )
        assert response.status_code == 200, f"Update context failed: {response.text}"

        data = response.json()

        # Verify updates were applied
        assert data["default_parameters"]["test_param"] == "test_value"
        assert data["default_parameters"]["another_param"] == 42
        assert data["track_executions"] is False
        assert data["organization"] is not None
        assert data["organization"]["id"] == org1["id"]
        assert data["organization"]["name"] == org1["name"]

    def test_context_persists_between_calls(self, e2e_client, platform_admin, org1, sdk_key):
        """Test that context updates persist across multiple API calls."""
        # First, update context with specific settings
        update_response = e2e_client.put(
            "/api/sdk/context",
            headers=platform_admin.headers,
            json={
                "default_org_id": org1["id"],
                "default_parameters": {
                    "persistent_key": "persistent_value",
                },
                "track_executions": False,
            },
        )
        assert update_response.status_code == 200, f"Update failed: {update_response.text}"

        # Now retrieve context using API key
        get_response = e2e_client.get(
            "/api/sdk/context",
            headers={
                "Authorization": f"Bearer {sdk_key['key']}",
            },
        )
        assert get_response.status_code == 200, f"Get context failed: {get_response.text}"

        data = get_response.json()

        # Verify persisted values
        assert data["default_parameters"]["persistent_key"] == "persistent_value"
        assert data["track_executions"] is False
        assert data["organization"]["id"] == org1["id"]

    def test_revoked_key_cannot_get_context(self, e2e_client, platform_admin):
        """Test that a revoked SDK API key cannot access context endpoint."""
        # Create a key to revoke
        create_resp = e2e_client.post(
            "/api/sdk/keys",
            headers=platform_admin.headers,
            json={"name": "Key to Test Revocation", "expires_in_days": 30},
        )
        assert create_resp.status_code == 201, f"Create failed: {create_resp.text}"
        key_data = create_resp.json()
        key_id = key_data["id"]
        full_key = key_data["key"]

        # Verify key works before revocation
        response = e2e_client.get(
            "/api/sdk/context",
            headers={
                "Authorization": f"Bearer {full_key}",
            },
        )
        assert response.status_code == 200, f"Key should work before revocation: {response.text}"

        # Revoke the key
        revoke_resp = e2e_client.patch(
            f"/api/sdk/keys/{key_id}/revoke",
            headers=platform_admin.headers,
        )
        assert revoke_resp.status_code == 200, f"Revoke failed: {revoke_resp.text}"
        revoked_key = revoke_resp.json()
        assert revoked_key["is_active"] is False

        # Verify key no longer works
        response = e2e_client.get(
            "/api/sdk/context",
            headers={
                "Authorization": f"Bearer {full_key}",
            },
        )
        assert response.status_code == 401, f"Revoked key should return 401: {response.text}"

        # Cleanup
        e2e_client.delete(
            f"/api/sdk/keys/{key_id}",
            headers=platform_admin.headers,
        )
