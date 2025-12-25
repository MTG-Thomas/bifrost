"""
Unit tests for Bifrost Integrations SDK module.

Note: The integrations SDK no longer has a dual-mode architecture.
Most unit tests have been removed as the SDK now operates through HTTP API only.

See test_sdk_platform_mode.py for platform integration tests.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from bifrost._context import clear_execution_context


# All tests removed - integrations SDK behavior is tested through integration tests
