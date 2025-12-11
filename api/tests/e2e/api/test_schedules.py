"""
Schedules E2E Tests.

Tests the workflow scheduling endpoints including:
- Listing scheduled workflows
- Schedule metadata enrichment (next run, cron validation)
- Execution history included in response
- Access control (platform admin only)

Note: Schedules are read-only - they're created by adding @schedule
decorators to workflow files, which are discovered automatically.
"""

import logging

logger = logging.getLogger(__name__)


class TestSchedulesList:
    """Test scheduled workflows listing."""

    def test_list_schedules_as_admin(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test listing scheduled workflows as platform admin."""
        response = e2e_client.get(
            "/api/schedules",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)

        # Each schedule should have expected fields
        for schedule in data:
            assert "id" in schedule
            assert "name" in schedule
            assert "schedule" in schedule  # cron expression

    def test_schedule_metadata_fields(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that schedule metadata includes enrichment fields."""
        response = e2e_client.get(
            "/api/schedules",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        if len(data) > 0:
            schedule = data[0]
            # Should include validation status
            assert "validation_status" in schedule
            assert schedule["validation_status"] in ["valid", "warning", "error"]
            # Should include human readable description
            assert "human_readable" in schedule
            # Should include next run time
            assert "next_run_at" in schedule
            # Should include execution history
            assert "execution_count" in schedule

    def test_schedule_execution_history(
        self,
        e2e_client,
        platform_admin,
    ):
        """Test that schedules include execution history."""
        response = e2e_client.get(
            "/api/schedules",
            headers=platform_admin.headers,
        )
        assert response.status_code == 200

        data = response.json()
        for schedule in data:
            # Should have execution count
            assert "execution_count" in schedule
            assert isinstance(schedule["execution_count"], int)
            assert schedule["execution_count"] >= 0

            # May have last run info
            # last_run_at can be None if never executed
            assert "last_run_at" in schedule


class TestSchedulesAccessControl:
    """Test access control for schedule endpoints."""

    def test_org_user_cannot_list_schedules(
        self,
        e2e_client,
        org1_user,
    ):
        """Test that org users cannot access schedules."""
        response = e2e_client.get(
            "/api/schedules",
            headers=org1_user.headers,
        )
        # Should be forbidden for non-superusers
        assert response.status_code in [401, 403]

    def test_unauthenticated_cannot_list_schedules(
        self,
        e2e_client,
    ):
        """Test that unauthenticated requests are rejected."""
        response = e2e_client.get("/api/schedules")
        assert response.status_code in [401, 403, 422]
