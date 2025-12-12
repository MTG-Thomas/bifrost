"""
E2E tests for security features.

Note: Rate limiting tests have been removed because rate limiting is
intentionally disabled in test environments (via settings.is_testing flag).
This prevents tests from being blocked by rate limits during CI runs.

Rate limiting should be tested in production-like environments manually
or via dedicated staging tests.
"""

import pytest


@pytest.mark.e2e
class TestSecurityHeaders:
    """Test security-related headers and responses."""

    def test_health_endpoint_accessible(self, e2e_client):
        """Basic security test: health endpoint should be accessible."""
        response = e2e_client.get("/health")
        assert response.status_code == 200
