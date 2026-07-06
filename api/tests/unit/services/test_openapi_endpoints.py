"""
Unit tests for OpenAPI endpoint generation service.

Tests the dynamic OpenAPI schema generation for workflow endpoints,
including parameter mapping, method handling, and schema structure.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.openapi_endpoints import (
    generate_workflow_openapi_schema,
    _param_to_openapi_schema,
    get_endpoint_enabled_workflows,
    register_workflow_endpoint,
    register_workflow_endpoints,
    refresh_workflow_endpoint,
    remove_workflow_endpoint,
    _install_custom_openapi,
    TYPE_TO_OPENAPI,
)
from src.routers.endpoints import EndpointExecuteResponse


class MutableRoutesApp:
    """Small app double for service code that assigns app.routes directly."""

    def __init__(self):
        self.routes = []
        self.openapi_schema = {"cached": True}

    def api_route(
        self,
        path,
        methods,
        response_model=None,
        summary=None,
        description=None,
        operation_id=None,
        tags=None,
        name=None,
    ):
        def decorator(handler):
            route = MagicMock()
            route.path = path
            route.methods = set(methods)
            route.endpoint = handler
            route.response_model = response_model
            route.summary = summary
            route.description = description
            route.operation_id = operation_id
            route.tags = tags
            route.name = name
            self.routes.append(route)
            return handler

        return decorator


class TestParamToOpenAPISchema:
    """Tests for parameter type conversion to OpenAPI schema."""

    def test_string_type(self):
        """String parameters map to OpenAPI string type."""
        param = {"name": "message", "type": "string", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "string"}

    def test_int_type(self):
        """Int parameters map to OpenAPI integer type."""
        param = {"name": "count", "type": "int", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "integer"}

    def test_float_type(self):
        """Float parameters map to OpenAPI number type."""
        param = {"name": "price", "type": "float", "required": False}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "number"}

    def test_bool_type(self):
        """Bool parameters map to OpenAPI boolean type."""
        param = {"name": "enabled", "type": "bool", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "boolean"}

    def test_list_type(self):
        """List parameters map to OpenAPI array type."""
        param = {"name": "items", "type": "list", "required": False}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "array", "items": {"type": "string"}}

    def test_json_type(self):
        """JSON/dict parameters map to OpenAPI object type."""
        param = {"name": "data", "type": "json", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "object"}

    def test_default_value_included(self):
        """Default values are included in schema."""
        param = {
            "name": "count",
            "type": "int",
            "required": False,
            "default_value": 5,
        }
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "integer", "default": 5}

    def test_unknown_type_defaults_to_string(self):
        """Unknown types default to string."""
        param = {"name": "custom", "type": "custom_type", "required": True}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "string"}

    def test_missing_type_defaults_to_string(self):
        """Parameters without an explicit type default to string."""
        param = {"name": "message", "required": False}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "string"}

    def test_none_default_value_is_not_included(self):
        """None defaults are omitted instead of advertised as default null."""
        param = {"name": "message", "type": "string", "default_value": None}
        schema = _param_to_openapi_schema(param)
        assert schema == {"type": "string"}


class TestGenerateWorkflowOpenAPISchema:
    """Tests for generating complete OpenAPI path schemas for workflows."""

    @pytest.fixture
    def mock_workflow(self):
        """Create a mock workflow with endpoint configuration."""
        workflow = MagicMock()
        workflow.name = "test_workflow"
        workflow.description = "A test workflow for unit testing"
        workflow.endpoint_enabled = True
        workflow.allowed_methods = ["GET", "POST"]
        workflow.parameters_schema = [
            {"name": "message", "type": "string", "required": True, "label": "Message"},
            {
                "name": "count",
                "type": "int",
                "required": False,
                "label": "Count",
                "default_value": 1,
            },
        ]
        return workflow

    def test_generates_operations_for_allowed_methods(self, mock_workflow):
        """Schema includes operations for each allowed HTTP method."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert "get" in schema
        assert "post" in schema
        assert "put" not in schema
        assert "delete" not in schema

    def test_operation_has_correct_summary(self, mock_workflow):
        """Operations have workflow name as summary."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["summary"] == "test_workflow"
        assert schema["post"]["summary"] == "test_workflow"

    def test_operation_has_description(self, mock_workflow):
        """Operations include workflow description."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["description"] == "A test workflow for unit testing"

    def test_operation_has_operation_id(self, mock_workflow):
        """Operations have unique operation IDs."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["operationId"] == "execute_test_workflow_get"
        assert schema["post"]["operationId"] == "execute_test_workflow_post"

    def test_operations_tagged_as_workflow_endpoints(self, mock_workflow):
        """All operations are tagged as Workflow Endpoints."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["tags"] == ["Workflow Endpoints"]
        assert schema["post"]["tags"] == ["Workflow Endpoints"]

    def test_operations_require_api_key_security(self, mock_workflow):
        """Operations require BifrostApiKey security."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert schema["get"]["security"] == [{"BifrostApiKey": []}]
        assert schema["post"]["security"] == [{"BifrostApiKey": []}]

    def test_get_has_query_parameters(self, mock_workflow):
        """GET operations have query parameters."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        params = schema["get"]["parameters"]
        assert len(params) == 2

        message_param = next(p for p in params if p["name"] == "message")
        assert message_param["in"] == "query"
        assert message_param["required"] is True
        assert message_param["schema"] == {"type": "string"}
        assert message_param["description"] == "Message"

        count_param = next(p for p in params if p["name"] == "count")
        assert count_param["in"] == "query"
        assert count_param["required"] is False
        assert count_param["schema"] == {"type": "integer", "default": 1}

    def test_post_has_request_body(self, mock_workflow):
        """POST operations have request body schema."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert "requestBody" in schema["post"]
        request_body = schema["post"]["requestBody"]
        assert request_body["required"] is False

        body_schema = request_body["content"]["application/json"]["schema"]
        assert body_schema["type"] == "object"
        assert "message" in body_schema["properties"]
        assert "count" in body_schema["properties"]
        assert "message" in body_schema["required"]
        assert "count" not in body_schema["required"]

    def test_operations_have_responses(self, mock_workflow):
        """Operations define response schemas."""
        schema = generate_workflow_openapi_schema(mock_workflow)

        responses = schema["get"]["responses"]
        assert "200" in responses
        assert "401" in responses
        assert "404" in responses
        assert "405" in responses

        # 200 response references EndpointExecuteResponse
        assert responses["200"]["content"]["application/json"]["schema"]["$ref"] == \
            "#/components/schemas/EndpointExecuteResponse"

    def test_delete_method_no_request_body(self, mock_workflow):
        """DELETE operations don't have request body."""
        mock_workflow.allowed_methods = ["DELETE"]
        schema = generate_workflow_openapi_schema(mock_workflow)

        assert "requestBody" not in schema["delete"]

    def test_workflow_with_no_parameters(self):
        """Workflows without parameters still generate valid schema."""
        workflow = MagicMock()
        workflow.name = "simple_workflow"
        workflow.description = "A simple workflow"
        workflow.endpoint_enabled = True
        workflow.allowed_methods = ["POST"]
        workflow.parameters_schema = []

        schema = generate_workflow_openapi_schema(workflow)

        # Should still have post operation
        assert "post" in schema

        # Parameters should be empty or not present
        params = schema["post"].get("parameters", [])
        assert len(params) == 0

        # Request body should have empty properties
        body_schema = schema["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert body_schema["properties"] == {}

    def test_workflow_with_default_methods(self):
        """Workflows with None allowed_methods default to POST."""
        workflow = MagicMock()
        workflow.name = "default_workflow"
        workflow.description = None
        workflow.endpoint_enabled = True
        workflow.allowed_methods = None  # Should default to POST
        workflow.parameters_schema = []

        schema = generate_workflow_openapi_schema(workflow)

        assert "post" in schema
        assert len(schema) == 1

    def test_missing_description_uses_fallback(self):
        """Workflows without description use fallback text."""
        workflow = MagicMock()
        workflow.name = "no_desc_workflow"
        workflow.description = None
        workflow.endpoint_enabled = True
        workflow.allowed_methods = ["GET"]
        workflow.parameters_schema = []

        schema = generate_workflow_openapi_schema(workflow)

        assert schema["get"]["description"] == "Execute no_desc_workflow workflow"

    def test_none_parameters_schema_defaults_to_empty_list(self):
        """Workflows with None parameters_schema generate valid empty schemas."""
        workflow = MagicMock()
        workflow.name = "no_params_workflow"
        workflow.description = None
        workflow.endpoint_enabled = True
        workflow.allowed_methods = ["GET", "POST"]
        workflow.parameters_schema = None

        schema = generate_workflow_openapi_schema(workflow)

        assert "parameters" not in schema["get"]
        assert "parameters" not in schema["post"]
        body_schema = schema["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert body_schema == {"type": "object", "properties": {}}

    def test_put_and_patch_include_request_body_with_required_properties(self):
        """PUT and PATCH operations include body schemas, like POST."""
        workflow = MagicMock()
        workflow.name = "update_workflow"
        workflow.description = "Update workflow"
        workflow.endpoint_enabled = True
        workflow.allowed_methods = ["PUT", "PATCH"]
        workflow.parameters_schema = [
            {"name": "payload", "type": "json", "required": True},
            {"name": "dry_run", "type": "bool", "required": False, "default_value": False},
        ]

        schema = generate_workflow_openapi_schema(workflow)

        for method in ("put", "patch"):
            body_schema = schema[method]["requestBody"]["content"]["application/json"]["schema"]
            assert body_schema["properties"]["payload"] == {"type": "object"}
            assert body_schema["properties"]["dry_run"] == {"type": "boolean", "default": False}
            assert body_schema["required"] == ["payload"]

    def test_parameter_without_label_has_no_description(self, mock_workflow):
        """Parameter descriptions are only added when a label is present."""
        mock_workflow.parameters_schema = [
            {"name": "message", "type": "string", "required": True},
        ]

        schema = generate_workflow_openapi_schema(mock_workflow)

        assert "description" not in schema["get"]["parameters"][0]


class TestGetEndpointEnabledWorkflows:
    """Tests for querying endpoint-enabled workflows from database."""

    @pytest.mark.asyncio
    async def test_queries_active_endpoint_enabled_workflows(self):
        """Query filters for endpoint_enabled=True and is_active=True."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        with patch("src.services.openapi_endpoints.select") as mock_select:
            mock_query = MagicMock()
            mock_select.return_value.where.return_value = mock_query

            result = await get_endpoint_enabled_workflows(mock_db)

            assert result == []
            mock_db.execute.assert_called_once()


class TestWorkflowEndpointRegistration:
    """Tests for registering, refreshing, and removing FastAPI workflow routes."""

    @pytest.fixture
    def mock_workflow(self):
        """Create a mock workflow for route registration tests."""
        workflow = MagicMock()
        workflow.name = "route_workflow"
        workflow.description = "Route workflow"
        workflow.endpoint_enabled = True
        workflow.allowed_methods = ["GET", "POST"]
        workflow.parameters_schema = [
            {"name": "message", "type": "string", "required": True, "label": "Message"},
        ]
        return workflow

    def test_register_workflow_endpoint_adds_route_and_schema(self, mock_workflow):
        """Registering a workflow adds a FastAPI route and workflow schema."""
        app = FastAPI()

        register_workflow_endpoint(app, mock_workflow)

        route = next(
            r for r in app.routes
            if getattr(r, "path", None) == "/api/endpoints/route_workflow"
        )
        assert set(route.methods) == {"GET", "POST"}
        assert route.name == "execute_route_workflow"
        assert app.openapi_schema is None
        assert app._workflow_schemas["route_workflow"]["get"]["parameters"][0]["name"] == "message"

    def test_register_workflow_endpoint_replaces_existing_route(self, mock_workflow):
        """Re-registering the same workflow replaces the old route definition."""
        app = FastAPI()

        register_workflow_endpoint(app, mock_workflow)
        mock_workflow.allowed_methods = ["DELETE"]
        register_workflow_endpoint(app, mock_workflow)

        matching_routes = [
            r for r in app.routes if getattr(r, "path", None) == "/api/endpoints/route_workflow"
        ]
        assert len(matching_routes) == 1
        assert matching_routes[0].methods == {"DELETE"}

    def test_remove_workflow_endpoint_removes_route_and_schema(self, mock_workflow):
        """Removing an existing workflow clears its route and cached schema entry."""
        app = FastAPI()
        register_workflow_endpoint(app, mock_workflow)
        app.openapi_schema = {"stale": True}

        remove_workflow_endpoint(app, "route_workflow")

        assert not any(
            getattr(r, "path", None) == "/api/endpoints/route_workflow" for r in app.routes
        )
        assert app.openapi_schema is None
        assert "route_workflow" not in app._workflow_schemas

    def test_remove_missing_workflow_endpoint_leaves_cache_unchanged(self):
        """Removing a missing route does not invalidate the OpenAPI cache."""
        app = FastAPI()
        app.openapi_schema = {"cached": True}
        app._workflow_schemas = {"other": {"get": {}}}

        remove_workflow_endpoint(app, "missing")

        assert app.openapi_schema == {"cached": True}
        assert app._workflow_schemas == {"other": {"get": {}}}

    def test_refresh_enabled_workflow_registers_endpoint(self, mock_workflow):
        """Refreshing an enabled workflow registers its endpoint."""
        app = FastAPI()

        refresh_workflow_endpoint(app, mock_workflow)

        assert any(
            getattr(r, "path", None) == "/api/endpoints/route_workflow" for r in app.routes
        )

    def test_refresh_disabled_workflow_removes_endpoint(self, mock_workflow):
        """Refreshing a disabled workflow removes an existing endpoint."""
        app = FastAPI()
        register_workflow_endpoint(app, mock_workflow)
        mock_workflow.endpoint_enabled = False

        refresh_workflow_endpoint(app, mock_workflow)

        assert not any(
            getattr(r, "path", None) == "/api/endpoints/route_workflow" for r in app.routes
        )


class TestCustomOpenAPI:
    """Tests for the custom OpenAPI generator installed by the service."""

    @pytest.fixture
    def app_with_workflow_schema(self):
        """Create a FastAPI app with an endpoint route and detailed workflow schema."""
        app = FastAPI()

        @app.post("/api/endpoints/schema_workflow")
        async def schema_workflow():
            return {"ok": True}

        app._workflow_schemas = {
            "schema_workflow": {
                "post": {
                    "parameters": [
                        {
                            "name": "message",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        }
                    ],
                    "requestBody": {
                        "description": "Workflow input parameters",
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"message": {"type": "string"}},
                                    "required": ["message"],
                                }
                            }
                        },
                    },
                    "security": [{"BifrostApiKey": []}],
                }
            },
            "missing_route": {
                "post": {
                    "parameters": [
                        {
                            "name": "ignored",
                            "in": "query",
                            "schema": {"type": "string"},
                        }
                    ],
                }
            },
        }
        return app

    def test_install_custom_openapi_adds_security_response_and_workflow_details(
        self, app_with_workflow_schema
    ):
        """Custom OpenAPI adds shared components and injects workflow-specific details."""
        _install_custom_openapi(app_with_workflow_schema)

        schema = app_with_workflow_schema.openapi()

        security = schema["components"]["securitySchemes"]["BifrostApiKey"]
        assert security == {
            "type": "apiKey",
            "in": "header",
            "name": "X-Bifrost-Key",
            "description": "Workflow API key for endpoint access",
        }
        response_schema = schema["components"]["schemas"]["EndpointExecuteResponse"]
        assert response_schema["required"] == ["execution_id", "status"]

        operation = schema["paths"]["/api/endpoints/schema_workflow"]["post"]
        assert operation["parameters"][0]["name"] == "message"
        body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
        assert body_schema["required"] == ["message"]
        assert operation["security"] == [{"BifrostApiKey": []}]
        assert "/api/endpoints/missing_route" not in schema["paths"]

    def test_custom_openapi_returns_cached_schema(self, app_with_workflow_schema):
        """The custom generator returns an existing cached schema without regenerating."""
        cached_schema = {"cached": True}
        app_with_workflow_schema.openapi_schema = cached_schema

        _install_custom_openapi(app_with_workflow_schema)

        assert app_with_workflow_schema.openapi() is cached_schema


class TestRegisterWorkflowEndpoints:
    """Tests for startup registration of all endpoint-enabled workflows."""

    @pytest.mark.asyncio
    async def test_register_workflow_endpoints_registers_each_workflow_and_installs_openapi(self):
        """Startup registration returns the count and installs the custom OpenAPI generator."""
        app = FastAPI()
        workflow_a = MagicMock()
        workflow_a.name = "workflow_a"
        workflow_a.description = None
        workflow_a.endpoint_enabled = True
        workflow_a.allowed_methods = ["POST"]
        workflow_a.parameters_schema = []
        workflow_b = MagicMock()
        workflow_b.name = "workflow_b"
        workflow_b.description = "Workflow B"
        workflow_b.endpoint_enabled = True
        workflow_b.allowed_methods = ["GET"]
        workflow_b.parameters_schema = []
        mock_db = AsyncMock()

        with patch(
            "src.services.openapi_endpoints.get_endpoint_enabled_workflows",
            new=AsyncMock(return_value=[workflow_a, workflow_b]),
        ) as mock_get:
            count = await register_workflow_endpoints(app, mock_db)

        assert count == 2
        mock_get.assert_awaited_once_with(mock_db)
        assert any(getattr(r, "path", None) == "/api/endpoints/workflow_a" for r in app.routes)
        assert any(getattr(r, "path", None) == "/api/endpoints/workflow_b" for r in app.routes)

        schema = app.openapi()
        assert "BifrostApiKey" in schema["components"]["securitySchemes"]


class TestRegisterWorkflowEndpoint:
    """Tests for dynamic route registration."""

    def test_registered_route_delegates_with_workflow_id(self):
        """Dynamic route passes the registered workflow name as workflow_id."""
        app = FastAPI()
        workflow = MagicMock()
        workflow.name = "dynamic_workflow"
        workflow.description = "Dynamic workflow"
        workflow.allowed_methods = ["POST"]
        workflow.parameters_schema = []

        async def fake_execute_endpoint(workflow_id, request, x_bifrost_key):
            return EndpointExecuteResponse(
                execution_id=f"exec-{workflow_id}",
                status="queued",
                message=x_bifrost_key,
            )

        with patch(
            "src.routers.endpoints.execute_endpoint",
            side_effect=fake_execute_endpoint,
        ) as mock_execute:
            register_workflow_endpoint(app, workflow)
            response = TestClient(app).post(
                "/api/endpoints/dynamic_workflow",
                headers={"X-Bifrost-Key": "test-key"},
                json={},
            )

        assert response.status_code == 200
        assert response.json()["execution_id"] == "exec-dynamic_workflow"
        mock_execute.assert_called_once()
        assert mock_execute.call_args.kwargs["workflow_id"] == "dynamic_workflow"
        assert mock_execute.call_args.kwargs["x_bifrost_key"] == "test-key"


class TestTypeMapping:
    """Tests for the TYPE_TO_OPENAPI mapping."""

    def test_all_expected_types_mapped(self):
        """All common types have OpenAPI mappings."""
        expected_types = [
            "string", "str",
            "int", "integer",
            "float", "number",
            "bool", "boolean",
            "list", "array",
            "json", "dict", "object",
        ]

        for type_name in expected_types:
            assert type_name in TYPE_TO_OPENAPI, f"Missing mapping for {type_name}"

    def test_string_variants_consistent(self):
        """String type variants produce same schema."""
        assert TYPE_TO_OPENAPI["string"] == TYPE_TO_OPENAPI["str"]

    def test_integer_variants_consistent(self):
        """Integer type variants produce same schema."""
        assert TYPE_TO_OPENAPI["int"] == TYPE_TO_OPENAPI["integer"]

    def test_boolean_variants_consistent(self):
        """Boolean type variants produce same schema."""
        assert TYPE_TO_OPENAPI["bool"] == TYPE_TO_OPENAPI["boolean"]

    def test_array_variants_consistent(self):
        """Array type variants produce same schema."""
        assert TYPE_TO_OPENAPI["list"] == TYPE_TO_OPENAPI["array"]

    def test_object_variants_consistent(self):
        """Object type variants produce same schema."""
        assert TYPE_TO_OPENAPI["json"] == TYPE_TO_OPENAPI["dict"] == TYPE_TO_OPENAPI["object"]
