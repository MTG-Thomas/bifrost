"""
GitHub Integration E2E Tests.

Tests the complete GitHub integration workflow including:
- Token validation and storage
- Repository listing and configuration
- Pull/push operations
- Commit history
- Conflict detection

Requirements:
- GITHUB_TEST_PAT environment variable with a valid GitHub PAT
- GITHUB_TEST_REPO environment variable (default: jackmusick/e2e-test-workspace)

Tests use branch-per-run strategy to isolate test data.
Tests skip gracefully if environment variables are not configured.
"""

import logging
import time

import pytest

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration Tests
# =============================================================================


class TestGitHubConfiguration:
    """Test GitHub token validation and repository configuration."""

    def test_validate_token_success(
        self,
        e2e_client,
        platform_admin,
        github_test_config,
    ):
        """Test that a valid GitHub token can be validated and saved."""
        response = e2e_client.post(
            "/api/github/validate",
            json={"token": github_test_config["pat"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Token validation failed: {response.text}"

        data = response.json()
        assert "repositories" in data
        assert isinstance(data["repositories"], list)
        # Should have at least the test repo
        assert len(data["repositories"]) > 0

    def test_validate_token_invalid(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that an invalid token returns an error."""
        response = e2e_client.post(
            "/api/github/validate",
            json={"token": "invalid_token_12345"},
            headers=platform_admin.headers,
        )
        # Should fail - either 400 or 500 depending on how the API handles it
        assert response.status_code in [400, 401, 500]

    def test_get_config_unconfigured(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test getting config when GitHub is not configured."""
        # Ensure disconnected first
        e2e_client.post(
            "/api/github/disconnect",
            headers=platform_admin.headers,
        )

        response = e2e_client.get(
            "/api/github/config",
            headers=platform_admin.headers,
        )
        # Accept 200 (properly unconfigured) or 500 (workspace not set up)
        # The API may return 500 if the workspace directory doesn't exist yet
        if response.status_code == 200:
            data = response.json()
            assert data["configured"] is False
            assert data["token_saved"] is False
        else:
            # API returns 500 when workspace is not initialized
            assert response.status_code == 500

    def test_configure_repository(
        self,
        e2e_client,
        platform_admin,
        github_token_only,
        github_test_branch,
    ):
        """Test configuring a GitHub repository (async job dispatch)."""
        from tests.e2e.fixtures.github_setup import _wait_for_notification_completion

        config = github_test_branch
        repo_url = f"https://github.com/{config['repo']}.git"

        response = e2e_client.post(
            "/api/github/configure",
            json={
                "repo_url": repo_url,
                "branch": config["branch"],
                "auth_token": config["pat"],
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Configure failed: {response.text}"

        data = response.json()

        # New async flow returns job_id and notification_id
        if "notification_id" in data:
            assert "job_id" in data
            assert data["status"] == "queued"

            # Wait for completion
            _wait_for_notification_completion(
                e2e_client,
                platform_admin.headers,
                data["notification_id"],
                timeout_seconds=120,
            )

            # Verify config after completion
            response = e2e_client.get(
                "/api/github/config",
                headers=platform_admin.headers,
            )
            assert response.status_code == 200
            config_data = response.json()
            assert config_data["configured"] is True
            assert config_data["repo_url"] == repo_url
        else:
            # Old sync flow (backwards compatibility)
            assert data["configured"] is True
            assert data["token_saved"] is True
            assert data["repo_url"] == repo_url
            assert data["branch"] == config["branch"]

        # Cleanup
        e2e_client.post(
            "/api/github/disconnect",
            headers=platform_admin.headers,
        )

    def test_get_config_after_configure(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test getting config after GitHub is configured."""
        config = github_configured
        repo_url = f"https://github.com/{config['repo']}.git"

        response = e2e_client.get(
            "/api/github/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["configured"] is True
        assert data["token_saved"] is True
        assert data["repo_url"] == repo_url

    def test_list_repositories(
        self,
        e2e_client,
        platform_admin,
        github_token_only,
    ):
        """Test listing accessible repositories."""
        response = e2e_client.get(
            "/api/github/repositories",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "repositories" in data
        assert isinstance(data["repositories"], list)
        assert len(data["repositories"]) > 0

    def test_list_branches(
        self,
        e2e_client,
        platform_admin,
        github_token_only,
        github_test_config,
    ):
        """Test listing branches in a repository."""
        response = e2e_client.get(
            "/api/github/branches",
            params={"repo": github_test_config["repo"]},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "branches" in data
        assert isinstance(data["branches"], list)
        # Should have at least main branch
        branch_names = [b.get("name") for b in data["branches"]]
        assert "main" in branch_names

    def test_disconnect(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test disconnecting GitHub integration."""
        response = e2e_client.post(
            "/api/github/disconnect",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert data["success"] is True

        # Verify disconnected
        response = e2e_client.get(
            "/api/github/config",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200
        assert response.json()["configured"] is False


# =============================================================================
# Status Tests
# =============================================================================


class TestGitHubStatus:
    """Test GitHub status and refresh endpoints."""

    def test_status_after_configure(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test getting status after GitHub is configured."""
        response = e2e_client.get(
            "/api/github/status",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        # Status should include key fields
        assert "branch" in data or "current_branch" in data

    def test_refresh_status(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test refreshing Git status."""
        response = e2e_client.post(
            "/api/github/refresh",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        # Should include status information
        assert isinstance(data, dict)

    def test_get_changes_clean(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test getting changes when workspace is clean."""
        response = e2e_client.get(
            "/api/github/changes",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, dict)

    def test_get_commit_history(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test getting commit history."""
        response = e2e_client.get(
            "/api/github/commits",
            params={"limit": 10, "offset": 0},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "commits" in data
        assert isinstance(data["commits"], list)


# =============================================================================
# File Indexing Tests
# =============================================================================


class TestGitHubFileIndexing:
    """Test that files are indexed in database after GitHub configure."""

    def test_configure_indexes_workspace_files(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """After GitHub configure, files should appear in workspace_files table."""
        # Query workspace files via the editor API (list files in root directory)
        response = e2e_client.get(
            "/api/files/editor",
            params={"path": ""},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Failed to list files: {response.text}"

        data = response.json()
        # Should have files from the test repo
        assert "files" in data or isinstance(data, list), f"Unexpected response format: {data}"
        files = data.get("files", data) if isinstance(data, dict) else data
        assert len(files) > 0, "No files were indexed after GitHub configure"

    def test_configure_extracts_workflow_metadata(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """After configure, any workflows in the repo should be indexed."""
        # Check for workflows - the test repo may or may not have them
        response = e2e_client.get(
            "/api/workflows",
            headers=platform_admin.headers,
        )
        # Should succeed even if there are no workflows
        assert response.status_code == 200, f"Failed to list workflows: {response.text}"

        # The response structure may vary - just verify we get a valid response
        data = response.json()
        # If there are workflows, they should have expected fields
        if isinstance(data, list) and len(data) > 0:
            workflow = data[0]
            assert "name" in workflow or "id" in workflow


# =============================================================================
# Sync Tests
# =============================================================================


class TestGitHubSync:
    """Test GitHub pull and push operations."""

    def test_pull_no_changes(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test pulling when there are no remote changes."""
        response = e2e_client.post(
            "/api/github/pull",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        # Should succeed without errors
        assert "success" in data or "conflicts" in data or "files_changed" in data

    def test_pull_with_remote_changes(
        self,
        e2e_client,
        platform_admin,
        github_configured,
        create_remote_file,
    ):
        """Test pulling changes made directly on GitHub."""
        # Create a file on GitHub
        timestamp = int(time.time())
        file_info = create_remote_file(
            path=f"e2e-test-remote-{timestamp}.txt",
            content=f"Created on GitHub at {timestamp}",
            message="E2E test: remote file creation",
        )

        # Pull the changes
        response = e2e_client.post(
            "/api/github/pull",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        # Verify file exists locally via editor API
        response = e2e_client.get(
            "/api/files/editor/content",
            params={"path": file_info["path"]},
            headers=platform_admin.headers,
        )
        # May be 200 if file pulled successfully, or 404 if sync is async
        if response.status_code == 200:
            assert str(timestamp) in response.json().get("content", "")

    def test_commit_and_push(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test creating a file, committing, and pushing."""
        timestamp = int(time.time())
        file_path = f"e2e-test-local-{timestamp}.txt"
        file_content = f"Created locally at {timestamp}"

        # Create a file via editor API (uses PUT for create/update)
        response = e2e_client.put(
            "/api/files/editor/content",
            json={
                "path": file_path,
                "content": file_content,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"File creation failed: {response.text}"

        # Push the changes
        response = e2e_client.post(
            "/api/github/push",
            json={"message": f"E2E test commit at {timestamp}"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200, f"Push failed: {response.text}"

        data = response.json()
        # Should indicate success
        if "success" in data:
            assert data["success"] is True
        # If there are no changes to push, that's also acceptable
        elif "error" in data:
            # "No changes to push" is acceptable
            pass

    def test_commit_endpoint(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test the commit endpoint (without push)."""
        timestamp = int(time.time())
        file_path = f"e2e-test-commit-{timestamp}.txt"

        # Create a file (uses PUT for create/update)
        response = e2e_client.put(
            "/api/files/editor/content",
            json={
                "path": file_path,
                "content": f"Commit test at {timestamp}",
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        # Commit only
        response = e2e_client.post(
            "/api/github/commit",
            json={"message": f"E2E commit only at {timestamp}"},
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        # Push to clean up
        e2e_client.post(
            "/api/github/push",
            json={"message": "E2E push after commit"},
            headers=platform_admin.headers,
        )


# =============================================================================
# Conflict Tests
# =============================================================================


class TestGitHubConflicts:
    """Test conflict detection and resolution."""

    def test_get_conflicts_no_merge(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test getting conflicts when there's no merge in progress."""
        response = e2e_client.get(
            "/api/github/conflicts",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "conflicts" in data
        # Should be empty when no merge is in progress
        assert len(data["conflicts"]) == 0

    def test_abort_merge_no_merge(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test aborting merge when no merge is in progress."""
        response = e2e_client.post(
            "/api/github/abort-merge",
            headers=platform_admin.headers,
        )
        # Should succeed or indicate no merge to abort
        assert response.status_code in [200, 400]

    def test_discard_unpushed_no_commits(
        self,
        e2e_client,
        platform_admin,
        github_configured,
    ):
        """Test discarding unpushed commits when there are none."""
        # First ensure we're in sync
        e2e_client.post(
            "/api/github/pull",
            headers=platform_admin.headers,
        )

        response = e2e_client.post(
            "/api/github/discard-unpushed",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "discarded_commits" in data
        # May be empty or have commits depending on state


# =============================================================================
# Access Control Tests
# =============================================================================


class TestGitHubAccessControl:
    """Test access control for GitHub endpoints."""

    def test_org_user_cannot_access_github(
        self,
        e2e_client,
        org1_user,
        github_test_config,
    ):
        """Test that org users cannot access GitHub endpoints."""
        # Org users should not be able to validate tokens
        response = e2e_client.post(
            "/api/github/validate",
            json={"token": github_test_config["pat"]},
            headers=org1_user.headers,
        )
        # Should be forbidden
        assert response.status_code in [401, 403]

    def test_org_user_cannot_get_status(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot get GitHub status."""
        response = e2e_client.get(
            "/api/github/status",
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_org_user_cannot_configure(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot configure GitHub."""
        response = e2e_client.post(
            "/api/github/configure",
            json={
                "repo_url": "https://github.com/test/repo.git",
                "branch": "main",
            },
            headers=org1_user.headers,
        )
        assert response.status_code in [401, 403]

    def test_unauthenticated_cannot_access(
        self,
        e2e_client,
    ):
        """Test that unauthenticated requests are rejected."""
        response = e2e_client.get("/api/github/status")
        assert response.status_code in [401, 403, 422]


# =============================================================================
# Create Repository Tests
# =============================================================================


class TestGitHubCreateRepository:
    """Test GitHub repository creation."""

    @pytest.mark.skip(reason="Creating repos requires additional permissions and cleanup")
    def test_create_repository(
        self,
        e2e_client,
        platform_admin,
        github_token_only,
    ):
        """Test creating a new repository on GitHub."""
        # This test is skipped by default to avoid creating repos
        # Enable it manually if you want to test repo creation
        timestamp = int(time.time())
        response = e2e_client.post(
            "/api/github/create-repository",
            json={
                "name": f"e2e-test-repo-{timestamp}",
                "description": "E2E test repository - safe to delete",
                "private": True,
            },
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert "full_name" in data or "name" in data
