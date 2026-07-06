from __future__ import annotations

import json

import pytest

from src.services import sdk_generator


def test_name_helpers_sanitize_edge_cases_and_pluralization() -> None:
    assert sdk_generator.to_snake_case("HTTPStatus-Code") == "http_status_code"
    assert sdk_generator.to_pascal_case("2.0 customer-id") == "CustomerId"
    assert sdk_generator.to_pascal_case("123 status") == "Api123Status"
    assert sdk_generator.to_pascal_case("!!!") == "ApiClient"
    assert sdk_generator.sanitize_class_name("2.0 123 bad-name") == "Api123badname"
    assert sdk_generator.sanitize_class_name("!!!") == "ApiClient"

    assert sdk_generator.sanitize_field_name("class") == "class_"
    assert sdk_generator.sanitize_field_name("123-name") == "field_123_name"
    assert sdk_generator.sanitize_field_name("$bad") == "_bad"

    assert sdk_generator.pluralize("") == ""
    assert sdk_generator.pluralize("company") == "companies"
    assert sdk_generator.pluralize("box") == "boxes"
    assert sdk_generator.pluralize("leaf") == "leaves"
    assert sdk_generator.pluralize("knife") == "knives"
    assert sdk_generator.pluralize("hero") == "heroes"
    assert sdk_generator.pluralize("photo") == "photos"
    assert sdk_generator.pluralize("status") == "statuses"


def test_sanitize_spec_normalizes_common_non_openapi_type_names() -> None:
    spec = {
        "components": {
            "schemas": {
                "Item": {
                    "type": "dict",
                    "properties": {
                        "ids": {"type": "List<int>"},
                        "created": {"type": "DateTime"},
                        "due": {"type": "date"},
                        "enabled": {"type": "bool"},
                    },
                }
            }
        }
    }

    sanitized = sdk_generator.sanitize_spec(spec)
    item = sanitized["components"]["schemas"]["Item"]

    assert item["type"] == "object"
    assert item["properties"]["ids"] == {"type": "array", "items": {"type": "integer"}}
    assert item["properties"]["created"] == {"type": "string", "format": "date-time"}
    assert item["properties"]["due"] == {"type": "string", "format": "date"}
    assert item["properties"]["enabled"] == {"type": "boolean"}


def test_python_type_and_model_generation_collect_nested_inline_models() -> None:
    inline_schemas: dict[str, dict] = {}

    assert sdk_generator.python_type_from_schema({}, {}, inline_schemas) == "Any"
    assert sdk_generator.python_type_from_schema({"$ref": "#/components/schemas/user-profile"}, {}) == "UserProfile"
    assert sdk_generator.python_type_from_schema(
        {"type": "array", "items": {"type": "integer"}},
        {},
        inline_schemas,
        "Numbers",
    ) == "List[int]"
    assert sdk_generator.python_type_from_schema(
        {"type": "object", "properties": {"name": {"type": "string"}}},
        {},
        inline_schemas,
        "owner",
    ) == "Owner"
    assert "Owner" in inline_schemas

    model = sdk_generator.generate_model(
        "user-profile",
        {
            "type": "object",
            "description": "A user",
            "required": ["id"],
            "properties": {
                "id": {"type": "string", "description": "Identifier"},
                "display-name": {"type": "string"},
                "metadata": {"type": "object", "properties": {"tier": {"type": "string"}}},
            },
        },
        {},
        inline_schemas,
    )

    assert model.name == "UserProfile"
    assert [field.name for field in model.fields] == ["id", "display_name", "metadata"]
    assert model.fields[0].type == "str"
    assert model.fields[0].default is None
    assert model.fields[1].type == "Optional[str]"
    assert model.fields[1].default == "None"
    assert model.fields[2].type == "Optional[UserProfileMetadata]"


def test_extract_models_and_methods_handles_inline_responses_and_duplicate_names() -> None:
    spec = {
        "openapi": "3.0.0",
        "components": {
            "schemas": {
                "Error": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                },
                "Ignored": {"type": "string"},
            }
        },
        "paths": {
            "/users": {
                "get": {
                    "summary": "List users",
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {"id": {"type": "string"}},
                                        },
                                    }
                                }
                            }
                        }
                    },
                },
                "post": {
                    "operationId": "createUser",
                    "responses": {"201": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}}},
                },
            },
            "/users/{user-id}": {
                "get": {"responses": {"204": {}}},
                "delete": {"responses": {"default": {"description": "deleted"}}},
            },
            "/users/{user-id}/orders": {
                "get": {"responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}}},
                "patch": {"responses": {}},
            },
        },
    }

    models, methods = sdk_generator.extract_models_and_methods(spec, "ExampleClient")

    assert {model.name for model in models} >= {"Error", "ListUsersResponseItem"}
    by_name = {method.name: method for method in methods}
    assert by_name["list_users"].return_type == "List[ListUsersResponseItem]"
    assert by_name["create_users"].params == "data: Dict[str, Any] = None, **kwargs"
    assert by_name["create_users"].return_type == "Error"
    assert by_name["get_users"].url_template == "/users/{user_id}"
    assert by_name["get_users"].params == "user_id: str, **kwargs"
    assert by_name["delete_users"].return_type == "Any"
    assert by_name["get_orders"].return_type == "Dict[str, Any]"
    assert by_name["patch_orders"].params == "user_id: str, data: Dict[str, Any] = None, **kwargs"


def test_load_spec_helpers_parse_json_yaml_and_remote_content(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sdk_generator.load_spec_from_content('{"openapi":"3.0.0"}', "json") == {"openapi": "3.0.0"}
    assert sdk_generator.load_spec_from_content("openapi: 3.0.0", "yaml") == {"openapi": "3.0.0"}

    calls = []

    class Response:
        def __init__(self, *, headers, text, body=None):
            self.headers = headers
            self.text = text
            self._body = body

        def raise_for_status(self):
            calls.append("raise_for_status")

        def json(self):
            return self._body

    def fake_validate(url: str):
        assert url.startswith("https://api.example.test/")
        return sdk_generator.ValidatedSpecUrl(url)

    monkeypatch.setattr(sdk_generator, "_validate_spec_url", fake_validate)
    monkeypatch.setattr(
        sdk_generator.requests,
        "get",
        lambda url, **kwargs: Response(
            headers={"Content-Type": "application/json"},
            text="ignored",
            body={"from": "json"},
        ),
    )
    assert sdk_generator.load_spec_from_url("https://api.example.test/openapi") == {"from": "json"}
    assert calls == ["raise_for_status"]

    monkeypatch.setattr(
        sdk_generator.requests,
        "get",
        lambda url, **kwargs: Response(
            headers={"Content-Type": "text/yaml"},
            text="from: yaml",
        ),
    )
    assert sdk_generator.load_spec_from_url("https://api.example.test/openapi.yaml") == {"from": "yaml"}


def test_type_and_model_helpers_cover_primitives_empty_models_and_methods() -> None:
    assert sdk_generator.python_type_from_schema({"type": "number"}, {}) == "float"
    assert sdk_generator.python_type_from_schema({"type": "boolean"}, {}) == "bool"
    assert sdk_generator.python_type_from_schema({"type": "string"}, {}) == "str"
    assert sdk_generator.python_type_from_schema({"type": "unknown"}, {}) == "Any"

    empty = sdk_generator.generate_model("empty", {"type": "object"}, {})
    assert empty.name == "Empty"
    assert empty.fields == []

    assert sdk_generator.generate_method_name("/", "get") == "list_resources"
    assert sdk_generator.generate_method_name("/people", "put") == "update_people"
    assert sdk_generator.generate_method_name("/people", "patch") == "patch_people"
    assert sdk_generator.generate_method_name("/people", "delete") == "delete_people"
    assert sdk_generator.generate_method_name("/people", "options") == "options_people"


def test_generate_sdk_result_wrappers_count_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = {
        "info": {"title": "2.0 Support API"},
        "paths": {
            "/tickets": {"get": {}, "post": {}},
            "/tickets/{id}": {"patch": {}, "delete": {}},
        },
        "components": {"schemas": {"Ticket": {"type": "object"}}},
    }

    monkeypatch.setattr(
        sdk_generator,
        "generate_sdk",
        lambda loaded_spec, integration_name, auth_type, module_name=None: (
            json.dumps(
                {
                    "title": loaded_spec["info"]["title"],
                    "integration": integration_name,
                    "auth": auth_type,
                    "module": module_name,
                }
            ),
            module_name or "support_api",
        ),
    )

    result = sdk_generator._generate_sdk_result(spec, "Support", "bearer", None)
    assert json.loads(result.code)["integration"] == "Support"
    assert result.module_name == "support_api"
    assert result.class_name == "SupportAPI"
    assert result.endpoint_count == 4
    assert result.schema_count == 1

    monkeypatch.setattr(sdk_generator, "load_spec_from_url", lambda url: spec)
    from_url = sdk_generator.generate_sdk_from_url(
        "https://api.example.test/openapi.json",
        "Support",
        "api_key",
        "support_client",
    )
    assert json.loads(from_url.code)["auth"] == "api_key"
    assert from_url.module_name == "support_client"

    monkeypatch.setattr(sdk_generator, "load_spec_from_content", lambda content, content_type: spec)
    from_content = sdk_generator.generate_sdk_from_content(
        "openapi: 3.0.0",
        "yaml",
        "Support",
        "oauth",
    )
    assert json.loads(from_content.code)["auth"] == "oauth"
    assert from_content.endpoint_count == 4
