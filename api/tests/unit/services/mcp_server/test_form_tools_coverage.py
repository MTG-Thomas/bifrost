from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.services.mcp_server.tools import forms


def _context(*, admin: bool = False, org_id=None, user_id=None) -> SimpleNamespace:
    return SimpleNamespace(
        is_platform_admin=admin,
        org_id=org_id if org_id is not None else uuid4(),
        user_id=user_id if user_id is not None else uuid4(),
        is_external=False,
        user_email="admin@example.com" if admin else "user@example.com",
    )


def _context_without_org(*, admin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        is_platform_admin=admin,
        org_id=None,
        user_id=uuid4(),
        is_external=False,
        user_email="admin@example.com" if admin else "user@example.com",
    )


def _form(**overrides):
    row = SimpleNamespace(
        id=uuid4(),
        name="Ticket intake",
        description="Collect ticket details",
        workflow_id=uuid4(),
        fields=[],
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _fake_tool_db(db):
    @asynccontextmanager
    async def fake_get_tool_db(_context):
        yield db

    return fake_get_tool_db


class TestFormToolHelpers:
    @pytest.mark.asyncio
    async def test_schema_tool_includes_form_and_file_upload_documentation(self):
        with patch(
            "src.services.mcp_server.schema_utils.models_to_markdown",
            return_value="# Generated forms\n",
        ) as models_to_markdown:
            result = await forms.get_form_schema(_context())

        assert "Form Schema Documentation" in models_to_markdown.call_args.args[1]
        assert "Using Data Providers in Forms" in result.structured_content["schema"]
        assert "File Upload Fields" in result.structured_content["schema"]


class TestListFormsTool:
    @pytest.mark.asyncio
    async def test_admin_lists_all_forms(self):
        db = AsyncMock()
        repo = MagicMock()
        repo.list_all_in_scope = AsyncMock(return_value=[_form()])

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.forms.FormRepository", return_value=repo) as repo_cls,
        ):
            result = await forms.list_forms(_context(admin=True, org_id=None))

        assert result.structured_content["count"] == 1
        assert result.structured_content["forms"][0]["name"] == "Ticket intake"
        repo.list_all_in_scope.assert_awaited_once_with(active_only=True)
        repo.list_forms.assert_not_called()
        assert repo_cls.call_args.kwargs["is_superuser"] is True

    @pytest.mark.asyncio
    async def test_org_user_lists_accessible_forms_with_uuid_coercion(self):
        db = AsyncMock()
        repo = MagicMock()
        repo.list_forms = AsyncMock(return_value=[_form(description=None)])
        org_id = uuid4()
        user_id = uuid4()

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.forms.FormRepository", return_value=repo) as repo_cls,
        ):
            result = await forms.list_forms(
                _context(admin=False, org_id=str(org_id), user_id=str(user_id))
            )

        assert result.structured_content["count"] == 1
        repo.list_forms.assert_awaited_once_with(active_only=True)
        assert repo_cls.call_args.kwargs["org_id"] == org_id
        assert repo_cls.call_args.kwargs["user_id"] == user_id
        assert repo_cls.call_args.kwargs["is_superuser"] is False

    @pytest.mark.asyncio
    async def test_list_forms_reports_repository_failures(self):
        db = AsyncMock()
        repo = MagicMock()
        repo.list_forms = AsyncMock(side_effect=RuntimeError("database down"))

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.forms.FormRepository", return_value=repo),
        ):
            result = await forms.list_forms(_context_without_org())

        assert "Error listing forms" in result.structured_content["error"]
        assert "database down" in result.structured_content["error"]


class TestFormMutationValidation:
    @pytest.mark.asyncio
    async def test_create_form_rejects_invalid_inputs_before_db_access(self):
        ctx = _context(admin=False)
        workflow_id = str(uuid4())

        result = await forms.create_form(ctx, name="", workflow_id=workflow_id, fields=[{}])
        assert "name is required" in result.structured_content["error"]

        result = await forms.create_form(ctx, name="Ticket", workflow_id="", fields=[{}])
        assert "workflow_id is required" in result.structured_content["error"]

        result = await forms.create_form(ctx, name="Ticket", workflow_id=workflow_id, fields=[])
        assert "fields array is required" in result.structured_content["error"]

        result = await forms.create_form(
            ctx,
            name="x" * 201,
            workflow_id=workflow_id,
            fields=[{}],
        )
        assert "200 characters" in result.structured_content["error"]

        result = await forms.create_form(
            ctx,
            name="Ticket",
            workflow_id=workflow_id,
            fields=[{}],
            scope="tenant",
        )
        assert "scope must be" in result.structured_content["error"]

        result = await forms.create_form(
            _context_without_org(),
            name="Ticket",
            workflow_id=workflow_id,
            fields=[{}],
        )
        assert "organization_id is required" in result.structured_content["error"]

        result = await forms.create_form(
            _context(admin=True),
            name="Ticket",
            workflow_id=workflow_id,
            fields=[{}],
            organization_id="not-a-uuid",
        )
        assert "not a valid UUID" in result.structured_content["error"]

        result = await forms.create_form(
            ctx,
            name="Ticket",
            workflow_id="not-a-uuid",
            fields=[{}],
        )
        assert "workflow_id 'not-a-uuid' is not a valid UUID" in (
            result.structured_content["error"]
        )

        result = await forms.create_form(
            ctx,
            name="Ticket",
            workflow_id=workflow_id,
            fields=[{}],
            launch_workflow_id="not-a-uuid",
        )
        assert "launch_workflow_id 'not-a-uuid' is not a valid UUID" in (
            result.structured_content["error"]
        )

    @pytest.mark.asyncio
    async def test_get_form_rejects_missing_or_invalid_identifier_before_db_access(self):
        result = await forms.get_form(_context(), form_id=None, form_name=None)
        assert "Either form_id or form_name is required" in result.structured_content["error"]

        result = await forms.get_form(_context(), form_id="not-a-uuid")
        assert "'not-a-uuid' is not a valid UUID" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_update_form_rejects_missing_or_invalid_id_before_db_access(self):
        result = await forms.update_form(_context(), form_id="")
        assert "form_id is required" in result.structured_content["error"]

        result = await forms.update_form(_context(), form_id="not-a-uuid")
        assert "'not-a-uuid' is not a valid UUID" in result.structured_content["error"]
