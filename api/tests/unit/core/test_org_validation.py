"""
Unit tests for organization validation helper.

Tests that validate_org_assignment correctly rejects:
- Non-existent organizations
- Inactive organizations
- Invalid UUID strings

And accepts:
- Valid, active organizations
- None (global scope)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from fastapi import HTTPException

from src.core.org_validation import validate_org_assignment


def _make_org(org_id: UUID, name: str = "Test Org", is_active: bool = True):
    """Build a minimal Organization-like object for mocking."""
    org = MagicMock()
    org.id = org_id
    org.name = name
    org.is_active = is_active
    return org


def _mock_session(return_value=None):
    """Build an AsyncSession mock whose execute().scalar_one_or_none() returns *return_value*."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = return_value
    session = AsyncMock()
    session.execute.return_value = result
    return session


class TestValidateOrgAssignment:
    """Tests for the validate_org_assignment helper."""

    @pytest.mark.asyncio
    async def test_none_returns_none(self):
        """None org_id means global scope — no DB call, returns None."""
        db = _mock_session()
        result = await validate_org_assignment(db, None, entity_label="workflow")
        assert result is None
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_valid_active_org_uuid(self):
        """Valid UUID of an active org → returns the UUID."""
        org_id = uuid4()
        db = _mock_session(_make_org(org_id))
        result = await validate_org_assignment(db, org_id, entity_label="workflow")
        assert result == org_id

    @pytest.mark.asyncio
    async def test_valid_active_org_string(self):
        """String UUID of an active org → parsed and returned."""
        org_id = uuid4()
        db = _mock_session(_make_org(org_id))
        result = await validate_org_assignment(db, str(org_id), entity_label="form")
        assert result == org_id

    @pytest.mark.asyncio
    async def test_invalid_uuid_string_raises_400(self):
        """Non-UUID string raises HTTP 400."""
        db = _mock_session()
        with pytest.raises(HTTPException) as exc_info:
            await validate_org_assignment(db, "not-a-uuid", entity_label="event source")
        assert exc_info.value.status_code == 400
        assert "not a valid UUID" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_nonexistent_org_raises_404(self):
        """UUID that doesn't match any org raises HTTP 404."""
        db = _mock_session(None)
        fake_id = uuid4()
        with pytest.raises(HTTPException) as exc_info:
            await validate_org_assignment(db, fake_id, entity_label="workflow")
        assert exc_info.value.status_code == 404
        assert "not found" in exc_info.value.detail
        assert "workflow" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_inactive_org_raises_400(self):
        """Existing but inactive org raises HTTP 400."""
        org_id = uuid4()
        db = _mock_session(_make_org(org_id, name="Dead Org", is_active=False))
        with pytest.raises(HTTPException) as exc_info:
            await validate_org_assignment(db, org_id, entity_label="form")
        assert exc_info.value.status_code == 400
        assert "inactive" in exc_info.value.detail
        assert "Dead Org" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_entity_label_appears_in_messages(self):
        """The entity_label kwarg is included in error messages."""
        db = _mock_session(None)
        with pytest.raises(HTTPException) as exc_info:
            await validate_org_assignment(db, uuid4(), entity_label="event source")
        assert "event source" in exc_info.value.detail
