"""
E2E tests for security features.

These tests run LAST because they may trigger rate limiting or other
security measures that could affect subsequent tests.

File naming with 'z' prefix or pytest markers can be used to ensure
these run last, but it's best to run them in a separate test invocation.
"""

import pytest


@pytest.mark.e2e
class TestRateLimiting:
    """
    Test rate limiting on login endpoint.

    WARNING: These tests trigger rate limiting which can affect
    other tests that need to authenticate. Run these separately
    or ensure they run last.
    """

    def test_rate_limit_login(self, e2e_client):
        """
        Security: Login endpoint should be rate limited.

        Note: This test intentionally triggers rate limiting.
        Run it separately from other tests or ensure it runs last.
        """
        # Make requests until we hit rate limit or confirm it works
        # Rate limit is 10 requests per minute
        hit_rate_limit = False

        for i in range(12):  # Try 12 requests
            response = e2e_client.post(
                "/auth/login",
                data={
                    "username": "nonexistent@example.com",
                    "password": "wrongpassword",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if response.status_code == 429:
                hit_rate_limit = True
                # Check for Retry-After header
                retry_after = response.headers.get("retry-after") or response.headers.get("Retry-After")
                assert retry_after is not None, "Should have Retry-After header"
                break

        # We should hit rate limit within 12 requests (limit is 10)
        assert hit_rate_limit, "Rate limit should be enforced on login endpoint"
