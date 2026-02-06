"""Shared fixtures for repository unit tests"""

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_context():
    """Mock ExecutionContext for scoped repositories"""
    context = MagicMock()
    context.org_id = "test-org-123"
    context.user_id = "test-user-456"
    context.scope = "test-org-123"
    context.email = "test@example.com"
    return context
