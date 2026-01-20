"""
Integration tests for the execution logs list endpoint.

Tests the admin-only endpoint GET /api/executions/logs for listing logs
across all executions with filtering and pagination.
"""

import pytest
import pytest_asyncio
from datetime import datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Execution, ExecutionLog
from src.models.enums import ExecutionStatus
from src.models.orm.users import User
from src.models.orm.organizations import Organization
from src.core.auth import UserPrincipal


@pytest_asyncio.fixture
async def test_organization(db_session: AsyncSession):
    """Create a test organization."""
    org = Organization(
        id=uuid4(),
        name=f"Test Logs Org {uuid4().hex[:8]}",
        domain=f"test-logs-{uuid4().hex[:8]}.com",
        created_by="test@example.com",
    )
    db_session.add(org)
    await db_session.flush()
    return org


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession, test_organization):
    """Create a test user."""
    user = User(
        id=uuid4(),
        email=f"test_{uuid4().hex[:8]}@example.com",
        name="Test User",
        organization_id=test_organization.id,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def admin_user(db_session: AsyncSession):
    """Create an admin user (superuser)."""
    user = User(
        id=uuid4(),
        email=f"admin_{uuid4().hex[:8]}@platform.com",
        name="Platform Admin",
        organization_id=None,  # Platform admins don't need org
        is_superuser=True,
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def sample_execution_with_logs(
    db_session: AsyncSession,
    test_user: User,
    test_organization: Organization,
):
    """Create a sample execution with logs for testing."""
    execution = Execution(
        id=uuid4(),
        workflow_name="test_workflow",
        status=ExecutionStatus.SUCCESS,
        parameters={},
        started_at=datetime.utcnow(),
        completed_at=datetime.utcnow(),
        executed_by=test_user.id,
        executed_by_name=test_user.name,
        organization_id=test_organization.id,
    )
    db_session.add(execution)
    await db_session.flush()

    # Add various log levels
    logs = [
        ExecutionLog(
            execution_id=execution.id,
            level="INFO",
            message="Workflow started",
            timestamp=datetime.utcnow(),
            sequence=1,
        ),
        ExecutionLog(
            execution_id=execution.id,
            level="WARNING",
            message="Resource limit approaching",
            timestamp=datetime.utcnow(),
            sequence=2,
        ),
        ExecutionLog(
            execution_id=execution.id,
            level="ERROR",
            message="Connection timeout",
            timestamp=datetime.utcnow(),
            sequence=3,
        ),
        ExecutionLog(
            execution_id=execution.id,
            level="INFO",
            message="Workflow completed",
            timestamp=datetime.utcnow(),
            sequence=4,
        ),
    ]
    for log in logs:
        db_session.add(log)
    await db_session.flush()

    return execution


@pytest.mark.integration
@pytest.mark.asyncio
class TestLogsListEndpoint:
    """Tests for GET /api/executions/logs endpoint."""

    async def test_list_logs_requires_admin(
        self,
        db_session: AsyncSession,
        test_user: User,
    ):
        """Non-admin users should get 403."""
        from fastapi import HTTPException
        from src.routers.executions import list_logs

        # Create a mock context for a regular user
        regular_user = UserPrincipal(
            user_id=test_user.id,
            email=test_user.email,
            organization_id=test_user.organization_id,
            is_superuser=False,
        )

        # We need to create a minimal Context mock
        class MockContext:
            def __init__(self, user, db):
                self.user = user
                self.db = db

        ctx = MockContext(regular_user, db_session)

        # Pass all optional parameters explicitly to avoid Query() default issues
        with pytest.raises(HTTPException) as exc_info:
            await list_logs(
                ctx=ctx,
                organization_id=None,
                workflow_name=None,
                levels=None,
                message_search=None,
                start_date=None,
                end_date=None,
                limit=50,
                continuation_token=None,
            )

        assert exc_info.value.status_code == 403
        assert "Admin access required" in exc_info.value.detail

    async def test_list_logs_returns_paginated_results(
        self,
        db_session: AsyncSession,
        admin_user: User,
        sample_execution_with_logs: Execution,
    ):
        """Admin can list logs with pagination."""
        from src.routers.executions import list_logs

        admin = UserPrincipal(
            user_id=admin_user.id,
            email=admin_user.email,
            organization_id=None,
            is_superuser=True,
        )

        class MockContext:
            def __init__(self, user, db):
                self.user = user
                self.db = db

        ctx = MockContext(admin, db_session)

        # Pass all optional parameters explicitly to avoid Query() default issues
        result = await list_logs(
            ctx=ctx,
            organization_id=None,
            workflow_name=None,
            levels=None,
            message_search=None,
            start_date=None,
            end_date=None,
            limit=10,
            continuation_token=None,
        )

        assert hasattr(result, "logs")
        assert isinstance(result.logs, list)
        assert len(result.logs) >= 1  # At least one log from our fixture
        # continuation_token may or may not be present

    async def test_list_logs_filters_by_level(
        self,
        db_session: AsyncSession,
        admin_user: User,
        sample_execution_with_logs: Execution,
    ):
        """Can filter logs by level."""
        from src.routers.executions import list_logs

        admin = UserPrincipal(
            user_id=admin_user.id,
            email=admin_user.email,
            organization_id=None,
            is_superuser=True,
        )

        class MockContext:
            def __init__(self, user, db):
                self.user = user
                self.db = db

        ctx = MockContext(admin, db_session)

        # Pass all optional parameters explicitly to avoid Query() default issues
        result = await list_logs(
            ctx=ctx,
            organization_id=None,
            workflow_name=None,
            levels="ERROR,WARNING",
            message_search=None,
            start_date=None,
            end_date=None,
            limit=50,
            continuation_token=None,
        )

        assert hasattr(result, "logs")
        # All returned logs should be ERROR or WARNING
        for log in result.logs:
            assert log.level in ["ERROR", "WARNING"]

    async def test_list_logs_filters_by_workflow_name(
        self,
        db_session: AsyncSession,
        admin_user: User,
        sample_execution_with_logs: Execution,
    ):
        """Can filter logs by workflow name."""
        from src.routers.executions import list_logs

        admin = UserPrincipal(
            user_id=admin_user.id,
            email=admin_user.email,
            organization_id=None,
            is_superuser=True,
        )

        class MockContext:
            def __init__(self, user, db):
                self.user = user
                self.db = db

        ctx = MockContext(admin, db_session)

        # Pass all optional parameters explicitly to avoid Query() default issues
        result = await list_logs(
            ctx=ctx,
            organization_id=None,
            workflow_name="test_workflow",
            levels=None,
            message_search=None,
            start_date=None,
            end_date=None,
            limit=50,
            continuation_token=None,
        )

        assert hasattr(result, "logs")
        # All returned logs should be from test_workflow
        for log in result.logs:
            assert log.workflow_name == "test_workflow"

    async def test_list_logs_message_search(
        self,
        db_session: AsyncSession,
        admin_user: User,
        sample_execution_with_logs: Execution,
    ):
        """Can search in log message content."""
        from src.routers.executions import list_logs

        admin = UserPrincipal(
            user_id=admin_user.id,
            email=admin_user.email,
            organization_id=None,
            is_superuser=True,
        )

        class MockContext:
            def __init__(self, user, db):
                self.user = user
                self.db = db

        ctx = MockContext(admin, db_session)

        # Pass all optional parameters explicitly to avoid Query() default issues
        result = await list_logs(
            ctx=ctx,
            organization_id=None,
            workflow_name=None,
            levels=None,
            message_search="timeout",
            start_date=None,
            end_date=None,
            limit=50,
            continuation_token=None,
        )

        assert hasattr(result, "logs")
        # All returned logs should contain "timeout" in message
        for log in result.logs:
            assert "timeout" in log.message.lower()
