"""
E2E API test configuration.

Minimal fixtures - the E2E tests build their own state sequentially.
"""

import os


# API base URL - set by test.sh --e2e
# Default to api:8000 since tests run inside Docker network
API_BASE_URL = os.environ.get("TEST_API_URL", "http://api:8000")


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "e2e: mark test as end-to-end test"
    )
