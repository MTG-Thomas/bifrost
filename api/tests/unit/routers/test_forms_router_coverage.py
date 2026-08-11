from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.models import FileUploadRequest
from src.routers import forms


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


def test_form_schema_to_fields_preserves_file_and_data_provider_options():
    form_id = uuid4()
    provider_id = str(uuid4())

    fields = forms._form_schema_to_fields(
        {
            "fields": [
                {
                    "name": "attachment",
                    "label": "Attachment",
                    "type": "file",
                    "required": True,
                    "allowed_types": [".pdf", "image/*"],
                    "max_size_mb": 5,
                    "multiple": True,
                    "allow_as_query_param": True,
                },
                {
                    "name": "choice",
                    "label": "Choice",
                    "type": "select",
                    "data_provider_id": provider_id,
                    "data_provider_inputs": {
                        "tenant": {"mode": "fieldRef", "field_name": "tenant_id"}
                    },
                },
            ]
        },
        form_id,
    )

    assert [field.position for field in fields] == [0, 1]
    assert fields[0].form_id == form_id
    assert fields[0].type == "file"
    assert fields[0].allowed_types == [".pdf", "image/*"]
    assert fields[0].max_size_mb == 5
    assert fields[0].multiple is True
    assert fields[0].allow_as_query_param is True
    assert str(fields[1].data_provider_id) == provider_id
    assert fields[1].data_provider_inputs == {
        "tenant": {
            "mode": "fieldRef",
            "value": None,
            "field_name": "tenant_id",
            "expression": None,
        }
    }


@pytest.mark.asyncio
async def test_validate_form_references_aggregates_invalid_references():
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await forms._validate_form_references(
            db,
            workflow_id="not-a-uuid",
            launch_workflow_id="also-bad",
            form_schema={
                "fields": [
                    {"name": "tenant", "data_provider_id": "bad-provider-id"}
                ]
            },
        )

    assert exc.value.status_code == 422
    assert exc.value.detail["message"] == "Invalid form references"
    assert len(exc.value.detail["errors"]) == 3
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_validate_form_references_rejects_wrong_active_types():
    workflow_id = str(uuid4())
    launch_id = str(uuid4())
    provider_id = str(uuid4())
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _Result(SimpleNamespace(type="data_provider")),
            _Result(SimpleNamespace(type="data_provider")),
            _Result(SimpleNamespace(type="workflow")),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await forms._validate_form_references(
            db,
            workflow_id=workflow_id,
            launch_workflow_id=launch_id,
            form_schema={
                "fields": [
                    {"name": "customer", "data_provider_id": provider_id}
                ]
            },
        )

    assert exc.value.status_code == 422
    assert f"workflow_id '{workflow_id}' references a data_provider" in exc.value.detail["errors"][0]
    assert f"launch_workflow_id '{launch_id}' references a data_provider" in exc.value.detail["errors"][1]
    assert "references a workflow, not a data_provider" in exc.value.detail["errors"][2]


def test_sanitize_filename_and_mime_allow_list_branches():
    assert forms._sanitize_filename("../bad\\name:.pdf\x00") == "badname.pdf"
    assert forms._sanitize_filename("...   ") == "unnamed_file"

    assert forms._check_mime_type_allowed("image/png", ["image/*"])
    assert forms._check_mime_type_allowed("application/pdf", [".PDF"])
    assert forms._check_mime_type_allowed("text/csv", ["text/csv"])
    assert not forms._check_mime_type_allowed("application/x-msdownload", [".pdf", "image/*"])


@pytest.mark.asyncio
async def test_generate_upload_url_validates_field_constraints_and_returns_metadata():
    form_id = uuid4()
    org_id = uuid4()
    form = SimpleNamespace(
        id=form_id,
        is_active=True,
        fields=[
            SimpleNamespace(
                name="attachment",
                type="file",
                allowed_types=[".pdf"],
                max_size_mb=1,
            )
        ],
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(form))
    ctx = SimpleNamespace(
        org_id=org_id,
        user=SimpleNamespace(
            embed=False,
            user_id=uuid4(),
            is_superuser=False,
            is_external=False,
        ),
    )
    storage = MagicMock()
    storage.generate_presigned_upload_url = AsyncMock(return_value="https://upload")
    storage.presigned_upload_headers.return_value = {"Content-Type": "application/pdf"}

    with (
        patch.object(forms, "_authorize_form_runtime", AsyncMock()),
        patch.object(forms, "_limit_embed_action", AsyncMock()),
        patch.object(forms, "uuid4", return_value=uuid4()),
        patch("src.services.file_storage.FileStorageService", return_value=storage),
    ):
        response = await forms.generate_upload_url(
            form_id,
            SimpleNamespace(),
            FileUploadRequest(
                file_name="../Quarterly Report.pdf",
                content_type="application/pdf",
                file_size=1024,
                field_name="attachment",
            ),
            ctx,
            ctx.user,
            db,
        )

    assert response.upload_url == "https://upload"
    assert response.upload_headers == {"Content-Type": "application/pdf"}
    assert response.blob_uri.endswith("/Quarterly Report.pdf")
    assert response.file_metadata.container == "uploads"
    storage.generate_presigned_upload_url.assert_awaited_once()
    assert f"uploads/{org_id}/" in storage.generate_presigned_upload_url.await_args.kwargs["path"]


@pytest.mark.asyncio
async def test_generate_upload_url_rejects_disallowed_type_and_oversize():
    form_id = uuid4()
    form = SimpleNamespace(
        id=form_id,
        is_active=True,
        fields=[
            SimpleNamespace(
                name="attachment",
                type="file",
                allowed_types=[".pdf"],
                max_size_mb=1,
            )
        ],
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result(form))
    ctx = SimpleNamespace(
        org_id=uuid4(),
        user=SimpleNamespace(
            embed=False,
            user_id=uuid4(),
            is_superuser=False,
            is_external=False,
        ),
    )

    with (
        patch.object(forms, "_authorize_form_runtime", AsyncMock()),
        patch.object(forms, "_limit_embed_action", AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc:
            await forms.generate_upload_url(
                form_id,
                SimpleNamespace(),
                FileUploadRequest(
                    file_name="bad.exe",
                    content_type="application/x-msdownload",
                    file_size=1,
                    field_name="attachment",
                ),
                ctx,
                ctx.user,
                db,
            )
    assert exc.value.status_code == 400
    assert "not allowed" in exc.value.detail

    with (
        patch.object(forms, "_authorize_form_runtime", AsyncMock()),
        patch.object(forms, "_limit_embed_action", AsyncMock()),
    ):
        with pytest.raises(HTTPException) as exc:
            await forms.generate_upload_url(
                form_id,
                SimpleNamespace(),
                FileUploadRequest(
                    file_name="big.pdf",
                    content_type="application/pdf",
                    file_size=2 * 1024 * 1024,
                    field_name="attachment",
                ),
                ctx,
                ctx.user,
                db,
            )
    assert exc.value.status_code == 400
    assert "exceeds maximum" in exc.value.detail
