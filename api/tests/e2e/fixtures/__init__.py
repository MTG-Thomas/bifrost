"""
E2E test fixtures.

This package provides session-scoped fixtures for E2E testing:
- E2EUser dataclass for tracking user state
- Pre-authenticated platform admin, org users
- Test organizations
"""

from tests.e2e.fixtures.users import E2EUser

__all__ = ["E2EUser"]
