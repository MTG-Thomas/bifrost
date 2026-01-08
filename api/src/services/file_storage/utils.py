"""
Utility functions for file storage operations.

Includes serialization helpers for platform entities.
"""

import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.models import Form, Agent


def serialize_form_to_json(form: "Form") -> bytes:
    """
    Serialize a Form (with fields) to JSON bytes.

    Uses the same format as _write_form_to_file in routers/forms.py
    for consistency with file-based storage.

    Args:
        form: Form ORM instance with fields relationship loaded

    Returns:
        JSON serialized as UTF-8 bytes
    """
    # Convert fields to form_schema format (matches _fields_to_form_schema in forms.py)
    fields_data = []
    for field in form.fields:
        field_data: dict[str, Any] = {
            "name": field.name,
            "type": field.type,
            "required": field.required,
        }

        # Add optional fields if they're set
        if field.label:
            field_data["label"] = field.label
        if field.placeholder:
            field_data["placeholder"] = field.placeholder
        if field.help_text:
            field_data["help_text"] = field.help_text
        if field.default_value is not None:
            field_data["default_value"] = field.default_value
        if field.options:
            field_data["options"] = field.options
        if field.data_provider_id:
            field_data["data_provider_id"] = str(field.data_provider_id)
        if field.data_provider_inputs:
            field_data["data_provider_inputs"] = field.data_provider_inputs
        if field.visibility_expression:
            field_data["visibility_expression"] = field.visibility_expression
        if field.validation:
            field_data["validation"] = field.validation
        if field.allowed_types:
            field_data["allowed_types"] = field.allowed_types
        if field.multiple is not None:
            field_data["multiple"] = field.multiple
        if field.max_size_mb:
            field_data["max_size_mb"] = field.max_size_mb
        if field.content:
            field_data["content"] = field.content

        fields_data.append(field_data)

    form_schema = {"fields": fields_data}

    # Build form JSON (matches _write_form_to_file format)
    # Note: org_id, is_global, access_level are NOT written to JSON
    # These are environment-specific and should only be set in the database
    form_data = {
        "id": str(form.id),
        "name": form.name,
        "description": form.description,
        "workflow_id": form.workflow_id,
        "launch_workflow_id": form.launch_workflow_id,
        "form_schema": form_schema,
        "is_active": form.is_active,
        "created_by": form.created_by,
        "created_at": form.created_at.isoformat() + "Z",
        "updated_at": form.updated_at.isoformat() + "Z",
        "allowed_query_params": form.allowed_query_params,
        "default_launch_params": form.default_launch_params,
    }

    return json.dumps(form_data, indent=2).encode("utf-8")


def serialize_agent_to_json(agent: "Agent") -> bytes:
    """
    Serialize an Agent to JSON bytes.

    Args:
        agent: Agent ORM instance

    Returns:
        JSON serialized as UTF-8 bytes
    """
    agent_data = {
        "id": str(agent.id),
        "name": agent.name,
        "description": agent.description,
        "system_prompt": agent.system_prompt,
        "channels": agent.channels,
        "access_level": agent.access_level.value if agent.access_level else None,
        "is_active": agent.is_active,
        "is_coding_mode": agent.is_coding_mode,
        "is_system": agent.is_system,
        "knowledge_sources": agent.knowledge_sources,
        "system_tools": agent.system_tools,
        "created_by": agent.created_by,
        "created_at": agent.created_at.isoformat() + "Z",
        "updated_at": agent.updated_at.isoformat() + "Z",
    }

    return json.dumps(agent_data, indent=2).encode("utf-8")
