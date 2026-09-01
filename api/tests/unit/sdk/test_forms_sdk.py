from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bifrost.forms import forms


def _form_payload(**overrides):
    payload = {
        "id": "form-1",
        "name": "Onboarding",
        "description": "Collect onboarding inputs",
        "confirmation_markdown": "## Submitted",
        "workflow_id": "workflow-1",
        "launch_workflow_id": "workflow-2",
        "default_launch_params": {"priority": "normal"},
        "allowed_query_params": ["ticket_id"],
        "form_schema": {"type": "object", "properties": {"ticket_id": {"type": "string"}}},
        "access_level": "organization",
        "organization_id": "org-1",
        "is_active": True,
        "file_path": "forms/onboarding.json",
        "created_at": None,
        "updated_at": None,
    }
    payload.update(overrides)
    return payload


def _response(status_code: int, body):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return response


@pytest.mark.asyncio
async def test_list_forms_returns_validated_models():
    fake = MagicMock()
    fake.get = AsyncMock(return_value=_response(200, [_form_payload(), _form_payload(id="form-2")]))

    with patch("bifrost.forms.get_client", return_value=fake), patch(
        "bifrost.forms.raise_for_status_with_detail"
    ) as raise_for_status:
        result = await forms.list()

    fake.get.assert_awaited_once_with("/api/forms")
    raise_for_status.assert_called_once()
    assert [form.id for form in result] == ["form-1", "form-2"]
    assert result[0].form_schema["properties"]["ticket_id"]["type"] == "string"


@pytest.mark.asyncio
async def test_get_form_returns_validated_model():
    fake = MagicMock()
    fake.get = AsyncMock(return_value=_response(200, _form_payload(name="Access Request")))

    with patch("bifrost.forms.get_client", return_value=fake), patch(
        "bifrost.forms.raise_for_status_with_detail"
    ):
        result = await forms.get("form-1")

    fake.get.assert_awaited_once_with("/api/forms/form-1")
    assert result.name == "Access Request"
    assert result.launch_workflow_id == "workflow-2"


@pytest.mark.asyncio
async def test_get_form_maps_not_found_to_value_error():
    fake = MagicMock()
    fake.get = AsyncMock(return_value=_response(404, {"detail": "not found"}))

    with patch("bifrost.forms.get_client", return_value=fake):
        with pytest.raises(ValueError, match="Form not found: form-missing"):
            await forms.get("form-missing")


@pytest.mark.asyncio
async def test_get_form_maps_forbidden_to_permission_error():
    fake = MagicMock()
    fake.get = AsyncMock(return_value=_response(403, {"detail": "forbidden"}))

    with patch("bifrost.forms.get_client", return_value=fake):
        with pytest.raises(PermissionError, match="Access denied to form: form-private"):
            await forms.get("form-private")
