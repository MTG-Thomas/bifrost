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
        launch_workflow_id=None,
        is_active=True,
        access_level="role_based",
        organization_id=uuid4(),
        fields=[],
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _field(**overrides):
    row = SimpleNamespace(
        name="summary",
        type="text",
        label="Summary",
        required=True,
        placeholder="Short summary",
        help_text="Help",
        default_value=None,
        options=None,
        data_provider_id=None,
        data_provider_inputs=None,
        visibility_expression=None,
        validation={"minLength": 3},
        allowed_types=None,
        multiple=False,
        max_size_mb=None,
        content=None,
        auto_fill=None,
        position=1,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def _fake_tool_db(db):
    @asynccontextmanager
    async def fake_get_tool_db(_context):
        yield db

    return fake_get_tool_db


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


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

        with patch.object(forms, "get_tool_db", _fake_tool_db(AsyncMock())):
            result = await forms.get_form(_context(), form_id="not-a-uuid")
        assert "'not-a-uuid' is not a valid UUID" in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_update_form_rejects_missing_or_invalid_id_before_db_access(self):
        result = await forms.update_form(_context(), form_id="")
        assert "form_id is required" in result.structured_content["error"]

        result = await forms.update_form(_context(), form_id="not-a-uuid")
        assert "'not-a-uuid' is not a valid UUID" in result.structured_content["error"]


class TestCreateFormTool:
    @pytest.mark.asyncio
    async def test_create_form_returns_workflow_and_launch_serialization(self):
        db = MagicMock()
        db.flush = AsyncMock()
        created_forms = []

        def add(row):
            created_forms.append(row)
            if row.__class__.__name__ == "Form":
                row.id = uuid4()

        db.add.side_effect = add
        db.execute = AsyncMock(side_effect=lambda *_args, **_kwargs: _ScalarResult(created_forms[0]))

        workflow_id = str(uuid4())
        launch_workflow_id = str(uuid4())
        workflow_repo = MagicMock()
        workflow_repo.get = AsyncMock(
            side_effect=[
                SimpleNamespace(name="Submit ticket"),
                SimpleNamespace(name="Prefill ticket"),
            ]
        )
        fields = [{"name": "summary", "type": "text", "label": "Summary", "required": True}]

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=workflow_repo) as repo_cls,
            patch("src.routers.forms._form_schema_to_fields", return_value=[_field()]) as to_fields,
        ):
            result = await forms.create_form(
                _context(admin=True, org_id=None),
                name="Ticket intake",
                description="Collect ticket details",
                workflow_id=workflow_id,
                launch_workflow_id=launch_workflow_id,
                fields=fields,
                scope="global",
            )

        assert result.structured_content == {
            "success": True,
            "id": str(created_forms[0].id),
            "name": "Ticket intake",
            "url": f"/forms/{created_forms[0].id}",
            "workflow_id": workflow_id,
            "workflow_name": "Submit ticket",
            "field_count": 1,
            "launch_workflow_id": launch_workflow_id,
            "launch_workflow_name": "Prefill ticket",
        }
        assert created_forms[0].organization_id is None
        assert created_forms[0].created_by == "admin@example.com"
        assert db.flush.await_count == 2
        to_fields.assert_called_once_with({"fields": fields}, created_forms[0].id)
        assert repo_cls.call_args.kwargs["is_superuser"] is True

    @pytest.mark.asyncio
    async def test_create_form_reports_missing_workflow_launch_workflow_and_schema_errors(self):
        db = AsyncMock()
        fields = [{"name": "summary", "type": "text", "label": "Summary", "required": True}]
        workflow_id = str(uuid4())
        launch_workflow_id = str(uuid4())

        missing_workflow_repo = MagicMock()
        missing_workflow_repo.get = AsyncMock(return_value=None)
        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=missing_workflow_repo),
        ):
            missing_workflow = await forms.create_form(
                _context(),
                name="Ticket",
                workflow_id=workflow_id,
                fields=fields,
            )
        assert f"Workflow '{workflow_id}' not found" in missing_workflow.structured_content["error"]

        missing_launch_repo = MagicMock()
        missing_launch_repo.get = AsyncMock(side_effect=[SimpleNamespace(name="Submit"), None])
        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=missing_launch_repo),
        ):
            missing_launch = await forms.create_form(
                _context(),
                name="Ticket",
                workflow_id=workflow_id,
                launch_workflow_id=launch_workflow_id,
                fields=fields,
            )
        assert f"Launch workflow '{launch_workflow_id}' not found" in missing_launch.structured_content["error"]

        schema_repo = MagicMock()
        schema_repo.get = AsyncMock(return_value=SimpleNamespace(name="Submit"))
        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=schema_repo),
        ):
            invalid_schema = await forms.create_form(
                _context(),
                name="Ticket",
                workflow_id=workflow_id,
                fields=[{"name": "summary"}],
            )
        assert "Invalid form schema" in invalid_schema.structured_content["error"]

    @pytest.mark.asyncio
    async def test_create_form_reports_database_errors(self):
        db = MagicMock()
        db.flush = AsyncMock()
        workflow_repo = MagicMock()
        workflow_repo.get = AsyncMock(return_value=SimpleNamespace(name="Submit"))
        db.add.side_effect = RuntimeError("write failed")

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=workflow_repo),
        ):
            result = await forms.create_form(
                _context(),
                name="Ticket",
                workflow_id=str(uuid4()),
                fields=[{"name": "summary", "type": "text", "label": "Summary"}],
            )

        assert "Error creating form" in result.structured_content["error"]
        assert "write failed" in result.structured_content["error"]


class TestGetFormTool:
    @pytest.mark.asyncio
    async def test_get_form_serializes_sorted_fields_and_workflow_names(self):
        form_id = uuid4()
        workflow_id = uuid4()
        launch_workflow_id = uuid4()
        form = _form(
            id=form_id,
            workflow_id=str(workflow_id),
            launch_workflow_id=str(launch_workflow_id),
            fields=[_field(name="second", position=2), _field(name="first", position=1)],
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ScalarResult(form))
        workflow_repo = MagicMock()
        workflow_repo.get = AsyncMock(
            side_effect=[SimpleNamespace(name="Submit ticket"), SimpleNamespace(name="Prefill ticket")]
        )

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository", return_value=workflow_repo),
        ):
            result = await forms.get_form(_context(org_id=form.organization_id), form_id=str(form_id))

        assert result.structured_content["id"] == str(form_id)
        assert result.structured_content["workflow_name"] == "Submit ticket"
        assert result.structured_content["launch_workflow_name"] == "Prefill ticket"
        assert [field["name"] for field in result.structured_content["fields"]] == ["first", "second"]
        assert result.structured_content["fields"][0]["validation"] == {"minLength": 3}

    @pytest.mark.asyncio
    async def test_get_form_handles_name_lookup_not_found_and_lookup_errors(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ScalarResult(None))
        with patch.object(forms, "get_tool_db", _fake_tool_db(db)):
            missing = await forms.get_form(_context(), form_name="Missing")
        assert "Form 'Missing' not found" in missing.structured_content["error"]

        broken_db = AsyncMock()
        broken_db.execute = AsyncMock(side_effect=RuntimeError("read failed"))
        with patch.object(forms, "get_tool_db", _fake_tool_db(broken_db)):
            failed = await forms.get_form(_context(), form_name="Ticket")
        assert "Error getting form" in failed.structured_content["error"]
        assert "read failed" in failed.structured_content["error"]

    @pytest.mark.asyncio
    async def test_get_form_ignores_unresolvable_workflow_refs(self):
        form = _form(workflow_id="portable-ref", launch_workflow_id="also-portable", fields=[])
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ScalarResult(form))

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.repositories.workflows.WorkflowRepository") as repo_cls,
        ):
            result = await forms.get_form(_context(org_id=form.organization_id), form_id=str(form.id))

        assert result.structured_content["workflow_name"] is None
        assert result.structured_content["launch_workflow_name"] is None
        repo_cls.return_value.get.assert_not_called()


class TestUpdateFormTool:
    @pytest.mark.asyncio
    async def test_update_form_rejects_permission_and_empty_update_paths(self):
        other_org_form = _form(organization_id=uuid4())
        global_form = _form(organization_id=None)
        own_org = uuid4()
        own_form = _form(organization_id=own_org)

        for form, ctx, expected in [
            (other_org_form, _context(org_id=uuid4()), "don't have permission"),
            (global_form, _context(org_id=own_org), "Only platform admins"),
            (own_form, _context(org_id=own_org), "No updates provided"),
        ]:
            db = AsyncMock()
            db.execute = AsyncMock(return_value=_ScalarResult(form))
            with (
                patch.object(forms, "get_tool_db", _fake_tool_db(db)),
                patch("src.services.solutions.guard.is_solution_managed", return_value=False),
            ):
                result = await forms.update_form(ctx, form_id=str(form.id))
            assert expected in result.structured_content["error"]

    @pytest.mark.asyncio
    async def test_update_form_rejects_solution_managed_before_mutation(self):
        form = _form()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ScalarResult(form))

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=True),
            patch("src.services.solutions.guard.SOLUTION_MANAGED_MESSAGE", "managed form"),
        ):
            result = await forms.update_form(_context(admin=True), form_id=str(form.id), name="New")

        assert result.structured_content["error"] == "managed form"
        assert form.name == "Ticket intake"
        db.flush.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_form_applies_fields_launch_clear_and_serializes_updates(self):
        form = _form(organization_id=uuid4(), launch_workflow_id=str(uuid4()))
        db = MagicMock()
        db.flush = AsyncMock()
        db.execute = AsyncMock(side_effect=[_ScalarResult(form), _ScalarResult(None), _ScalarResult(form)])
        fields = [{"name": "summary", "type": "text", "label": "Summary"}]

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=False),
            patch("src.routers.forms._form_schema_to_fields", return_value=[_field(name="replacement")]) as to_fields,
        ):
            result = await forms.update_form(
                _context(org_id=form.organization_id),
                form_id=str(form.id),
                name="New intake",
                description="Updated",
                launch_workflow_id="",
                is_active=False,
                fields=fields,
            )

        assert result.structured_content == {
            "success": True,
            "id": str(form.id),
            "name": "New intake",
            "updates": ["name", "description", "launch_workflow_id", "is_active", "fields"],
        }
        assert form.description == "Updated"
        assert form.launch_workflow_id is None
        assert form.is_active is False
        assert db.flush.await_count == 1
        assert db.add.call_count == 1
        to_fields.assert_called_once_with({"fields": fields}, form.id)

    @pytest.mark.asyncio
    async def test_update_form_reports_workflow_validation_and_database_errors(self):
        missing_db = AsyncMock()
        missing_db.execute = AsyncMock(return_value=_ScalarResult(None))
        with patch.object(forms, "get_tool_db", _fake_tool_db(missing_db)):
            missing = await forms.update_form(_context(admin=True), form_id=str(uuid4()))

        form = _form()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_ScalarResult(form))

        with (
            patch.object(forms, "get_tool_db", _fake_tool_db(db)),
            patch("src.services.solutions.guard.is_solution_managed", return_value=False),
        ):
            too_long = await forms.update_form(_context(admin=True), form_id=str(form.id), name="x" * 201)
            bad_workflow = await forms.update_form(
                _context(admin=True), form_id=str(form.id), workflow_id="not-a-uuid"
            )
            bad_launch = await forms.update_form(
                _context(admin=True), form_id=str(form.id), launch_workflow_id="not-a-uuid"
            )
            bad_fields = await forms.update_form(
                _context(admin=True), form_id=str(form.id), fields=[{"name": "summary"}]
            )

        assert "Form" in missing.structured_content["error"]
        assert "200 characters" in too_long.structured_content["error"]
        assert "workflow_id 'not-a-uuid' is not a valid UUID" in bad_workflow.structured_content["error"]
        assert "launch_workflow_id 'not-a-uuid' is not a valid UUID" in bad_launch.structured_content["error"]
        assert "Invalid form schema" in bad_fields.structured_content["error"]

        failed_db = AsyncMock()
        failed_db.execute = AsyncMock(side_effect=RuntimeError("database unavailable"))
        with patch.object(forms, "get_tool_db", _fake_tool_db(failed_db)):
            failed = await forms.update_form(_context(admin=True), form_id=str(uuid4()), name="New")
        assert "Error updating form" in failed.structured_content["error"]
        assert "database unavailable" in failed.structured_content["error"]


def test_register_tools_registers_form_tools():
    mcp = MagicMock()
    get_context = MagicMock()

    with patch(
        "src.services.mcp_server.generators.fastmcp_generator.register_tool_with_context"
    ) as register:
        forms.register_tools(mcp, get_context)

    assert [call.args[2] for call in register.call_args_list] == [
        "list_forms",
        "create_form",
        "get_form",
        "update_form",
    ]
    assert all(call.args[0] is mcp and call.args[4] is get_context for call in register.call_args_list)
