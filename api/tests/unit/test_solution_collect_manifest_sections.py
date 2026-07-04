from __future__ import annotations

import click
import pytest

from bifrost.commands.solution import (
    _collect_agents,
    _collect_claims,
    _collect_config_schemas,
    _collect_connection_schemas,
    _collect_events,
    _collect_file_locations,
    _collect_forms,
    _collect_readme,
    _collect_tables,
    _workspace_child_file,
)


def _write(path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_collect_tables_preserves_schema_and_optional_policies(tmp_path):
    _write(
        tmp_path / ".bifrost" / "tables.yaml",
        "tables:\n"
        "  tickets:\n"
        "    name: Tickets\n"
        "    description: Helpdesk tickets\n"
        "    schema:\n"
        "      columns:\n"
        "        - name: ticket_id\n"
        "          type: string\n"
        "    policies:\n"
        "      read: role:Support\n",
    )

    assert _collect_tables(tmp_path) == [
        {
            "id": "tickets",
            "name": "Tickets",
            "description": "Helpdesk tickets",
            "schema": {"columns": [{"name": "ticket_id", "type": "string"}]},
            "policies": {"read": "role:Support"},
        }
    ]


def test_collect_config_schemas_defaults_and_coerces_manifest_fields(tmp_path):
    _write(
        tmp_path / ".bifrost" / "configs.yaml",
        "configs:\n"
        "  API_KEY:\n"
        "    description: API token\n"
        "    required: true\n"
        "    position: '7'\n"
        "  MODE:\n"
        "    key: RUN_MODE\n"
        "    type: enum\n"
        "    default: dry-run\n",
    )

    assert _collect_config_schemas(tmp_path) == [
        {
            "id": "API_KEY",
            "key": "API_KEY",
            "type": "string",
            "required": True,
            "description": "API token",
            "default": None,
            "position": 7,
        },
        {
            "id": "MODE",
            "key": "RUN_MODE",
            "type": "enum",
            "required": False,
            "description": None,
            "default": "dry-run",
            "position": 0,
        },
    ]


def test_collect_file_locations_validates_manifest_list(tmp_path):
    _write(
        tmp_path / ".bifrost" / "files.yaml",
        "locations:\n"
        "  - attachments\n"
        "  - ticket-exports\n",
    )

    assert _collect_file_locations(tmp_path) == ["attachments", "ticket-exports"]

    _write(tmp_path / ".bifrost" / "files.yaml", "locations: not-a-list\n")
    with pytest.raises(ValueError, match="locations must be a list"):
        _collect_file_locations(tmp_path)


def test_collect_connection_schemas_keeps_template_and_position(tmp_path):
    _write(
        tmp_path / ".bifrost" / "connections.yaml",
        "connections:\n"
        "  halo:\n"
        "    integration_name: Halo PSA\n"
        "    position: '3'\n"
        "    template:\n"
        "      base_url: https://example.test\n"
        "  ninja: {}\n",
    )

    assert _collect_connection_schemas(tmp_path) == [
        {
            "integration_name": "Halo PSA",
            "template": {"base_url": "https://example.test"},
            "position": 3,
        },
        {"integration_name": "ninja", "template": {}, "position": 0},
    ]


def test_collect_readme_returns_markdown_or_none(tmp_path):
    assert _collect_readme(tmp_path) is None

    _write(tmp_path / "README.md", "# Solution\n")

    assert _collect_readme(tmp_path) == "# Solution\n"


def test_collect_claims_requires_query_and_defaults_type(tmp_path):
    _write(
        tmp_path / ".bifrost" / "claims.yaml",
        "claims:\n"
        "  campus_ids:\n"
        "    name: campus_ids\n"
        "    description: Campus scope\n"
        "    query:\n"
        "      table: memberships\n"
        "      select: campus_id\n",
    )

    assert _collect_claims(tmp_path) == [
        {
            "id": "campus_ids",
            "name": "campus_ids",
            "description": "Campus scope",
            "type": "list",
            "query": {"table": "memberships", "select": "campus_id"},
        }
    ]


def test_collect_inline_manifest_entities_for_forms_agents_and_events(tmp_path):
    _write(
        tmp_path / ".bifrost" / "forms.yaml",
        "forms:\n"
        "  intake:\n"
        "    title: Intake\n"
        "    fields:\n"
        "      - name: subject\n",
    )
    _write(
        tmp_path / ".bifrost" / "agents.yaml",
        "agents:\n"
        "  helper:\n"
        "    name: Helper\n"
        "    system_prompt: Help the operator\n",
    )
    _write(
        tmp_path / ".bifrost" / "events.yaml",
        "events:\n"
        "  ticket-created:\n"
        "    name: Ticket Created\n"
        "    source_type: webhook\n",
    )

    assert _collect_forms(tmp_path) == [
        {"id": "intake", "title": "Intake", "fields": [{"name": "subject"}]}
    ]
    assert _collect_agents(tmp_path) == [
        {"id": "helper", "name": "Helper", "system_prompt": "Help the operator"}
    ]
    assert _collect_events(tmp_path) == [
        {"id": "ticket-created", "name": "Ticket Created", "source_type": "webhook"}
    ]


def test_workspace_child_file_confines_descriptor_paths(tmp_path):
    _write(tmp_path / "logo.svg", "<svg />\n")

    assert _workspace_child_file(tmp_path, "logo.svg", "logo") == tmp_path / "logo.svg"

    with pytest.raises(click.ClickException, match="escapes the workspace"):
        _workspace_child_file(tmp_path, "../logo.svg", "logo")

    with pytest.raises(click.ClickException, match="file not found"):
        _workspace_child_file(tmp_path, "missing.svg", "logo")
