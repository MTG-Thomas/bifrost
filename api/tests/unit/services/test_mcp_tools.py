"""
Unit tests for MCP Tools.

Tests the MCP tools for the Bifrost platform:
- get_form_schema: Returns form schema documentation
- validate_form_schema: Validates form JSON structures
- list_workflows: Lists registered workflows
- list_forms: Lists forms with org scoping
- search_knowledge: Searches the knowledge base

Uses mocked database access for fast, isolated testing.

Note: The MCP tools use the @tool decorator from claude_agent_sdk which wraps
the inner function. We test the inner logic directly by accessing the decorated
function's callable or by testing the underlying logic functions.
"""

import json
from typing import Any

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from src.services.mcp.server import MCPContext


async def call_tool(tool: Any, args: dict[str, Any]) -> dict[str, Any]:
    """
    Call an MCP tool regardless of whether claude-agent-sdk is installed.

    When SDK is installed, tool is SdkMcpTool with .handler attribute.
    When SDK is not installed (stub decorator), tool is the raw function.
    """
    if hasattr(tool, "handler"):
        # SDK installed - tool is SdkMcpTool
        return await tool.handler(args)
    else:
        # SDK not installed - tool is raw function
        return await tool(args)


# ==================== Fixtures ====================


@pytest.fixture
def platform_admin_context() -> MCPContext:
    """Create an MCPContext for a platform admin user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=None,  # Platform admin has no org scope
        is_platform_admin=True,
    )


@pytest.fixture
def org_user_context() -> MCPContext:
    """Create an MCPContext for a regular org user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_admin=False,
    )


@pytest.fixture
def mock_workflow():
    """Create a mock workflow ORM object."""
    mock = MagicMock()
    mock.id = uuid4()
    mock.name = "test_workflow"
    mock.description = "A test workflow for testing"
    mock.category = "automation"
    mock.is_tool = False
    mock.schedule = None
    mock.endpoint_enabled = True
    mock.file_path = "/tmp/bifrost/workspace/workflows/test_workflow.py"
    mock.is_active = True
    return mock


@pytest.fixture
def mock_form():
    """Create a mock form ORM object."""
    mock = MagicMock()
    mock.id = uuid4()
    mock.name = "Test Form"
    mock.description = "A test form"
    mock.workflow_id = str(uuid4())
    mock.launch_workflow_id = None
    mock.is_active = True
    mock.access_level = MagicMock(value="authenticated")
    mock.file_path = "forms/test-form.form.json"

    # Mock fields
    field = MagicMock()
    field.name = "email"
    field.label = "Email Address"
    field.type = "email"
    field.required = True
    field.position = 0
    mock.fields = [field]

    return mock


@pytest.fixture
def mock_knowledge_document():
    """Create a mock knowledge document."""
    from src.repositories.knowledge import KnowledgeDocument

    return KnowledgeDocument(
        id=str(uuid4()),
        namespace="bifrost_docs",
        content="This is documentation about the SDK",
        metadata={"source": "docs", "title": "SDK Guide"},
        score=0.85,
        organization_id=None,
        key="sdk-guide",
        created_at=datetime.utcnow(),
    )


# ==================== get_form_schema Tests ====================


class TestGetFormSchema:
    """Tests for the get_form_schema MCP tool."""

    def test_documentation_content(self):
        """Should contain comprehensive form schema documentation."""
        from src.services.mcp.tools.get_form_schema import FORM_SCHEMA_DOCUMENTATION

        # Check that documentation contains key sections
        assert "FormFieldType" in FORM_SCHEMA_DOCUMENTATION
        assert "FormField Properties" in FORM_SCHEMA_DOCUMENTATION
        assert "FormSchema Structure" in FORM_SCHEMA_DOCUMENTATION
        assert "Example Form JSON" in FORM_SCHEMA_DOCUMENTATION

    def test_includes_all_field_types(self):
        """Should include documentation for all field types."""
        from src.services.mcp.tools.get_form_schema import FORM_SCHEMA_DOCUMENTATION

        # Verify all field types are documented
        field_types = [
            "text",
            "email",
            "number",
            "select",
            "checkbox",
            "textarea",
            "radio",
            "datetime",
            "markdown",
            "html",
            "file",
        ]
        for field_type in field_types:
            assert (
                f"`{field_type}`" in FORM_SCHEMA_DOCUMENTATION
            ), f"Field type {field_type} not documented"

    def test_includes_field_properties(self):
        """Should document all important field properties."""
        from src.services.mcp.tools.get_form_schema import FORM_SCHEMA_DOCUMENTATION

        properties = [
            "name",
            "label",
            "type",
            "required",
            "validation",
            "data_provider_id",
            "options",
            "content",
        ]
        for prop in properties:
            assert (
                f"`{prop}`" in FORM_SCHEMA_DOCUMENTATION
            ), f"Property {prop} not documented"

    def test_includes_example_json(self):
        """Should include a valid JSON example."""
        from src.services.mcp.tools.get_form_schema import FORM_SCHEMA_DOCUMENTATION

        # Find the example JSON block
        assert "```json" in FORM_SCHEMA_DOCUMENTATION
        assert '"fields"' in FORM_SCHEMA_DOCUMENTATION
        assert '"type": "text"' in FORM_SCHEMA_DOCUMENTATION


# ==================== validate_form_schema Tests ====================


class TestValidateFormSchema:
    """Tests for the validate_form_schema MCP tool.

    These tests directly test the validation logic using Pydantic models.
    """

    def test_valid_form_schema_fields(self):
        """Should validate a correct form schema structure."""
        from src.models.contracts.forms import FormSchema

        valid_schema = {
            "fields": [
                {"name": "email", "label": "Email", "type": "email", "required": True}
            ]
        }

        # Should not raise
        schema = FormSchema.model_validate(valid_schema)
        assert len(schema.fields) == 1
        assert schema.fields[0].name == "email"

    def test_valid_form_create(self):
        """Should validate a full FormCreate structure."""
        from src.models.contracts.forms import FormCreate

        valid_form = {
            "name": "Test Form",
            "workflow_id": str(uuid4()),
            "form_schema": {
                "fields": [
                    {"name": "email", "label": "Email", "type": "email", "required": True}
                ]
            },
        }

        # Should not raise
        form = FormCreate.model_validate(valid_form)
        assert form.name == "Test Form"
        assert len(form.form_schema.fields) == 1

    def test_rejects_invalid_json_parsing(self):
        """Should reject invalid JSON when parsed."""
        invalid_json = "{ invalid json }"

        with pytest.raises(json.JSONDecodeError):
            json.loads(invalid_json)

    def test_rejects_missing_label_for_input_fields(self):
        """Should reject input fields missing label."""
        from pydantic import ValidationError

        from src.models.contracts.forms import FormSchema

        invalid_schema = {
            "fields": [{"name": "email", "type": "email"}]  # Missing label
        }

        with pytest.raises(ValidationError) as exc_info:
            FormSchema.model_validate(invalid_schema)

        # Check that label is mentioned in the error
        errors = exc_info.value.errors()
        assert any("label" in str(e).lower() for e in errors)

    def test_rejects_duplicate_field_names(self):
        """Should reject forms with duplicate field names."""
        from pydantic import ValidationError

        from src.models.contracts.forms import FormSchema

        duplicate_names = {
            "fields": [
                {"name": "email", "label": "Email", "type": "email"},
                {"name": "email", "label": "Email 2", "type": "text"},  # Duplicate
            ]
        }

        with pytest.raises(ValidationError) as exc_info:
            FormSchema.model_validate(duplicate_names)

        errors = exc_info.value.errors()
        assert any("unique" in str(e).lower() for e in errors)

    def test_accepts_markdown_without_label(self):
        """Should accept markdown fields with content instead of label."""
        from src.models.contracts.forms import FormSchema

        markdown_field = {
            "fields": [{"name": "intro", "type": "markdown", "content": "# Welcome"}]
        }

        schema = FormSchema.model_validate(markdown_field)
        assert schema.fields[0].type.value == "markdown"
        assert schema.fields[0].content == "# Welcome"

    def test_rejects_markdown_without_content(self):
        """Should reject markdown fields without content."""
        from pydantic import ValidationError

        from src.models.contracts.forms import FormSchema

        invalid_markdown = {
            "fields": [{"name": "intro", "type": "markdown"}]  # Missing content
        }

        with pytest.raises(ValidationError):
            FormSchema.model_validate(invalid_markdown)


# ==================== list_workflows Tests ====================


class TestListWorkflows:
    """Tests for the list_workflows MCP tool."""

    @pytest.mark.asyncio
    async def test_lists_workflows(self, org_user_context, mock_workflow):
        """Should list registered workflows."""
        from src.services.mcp.tools.list_workflows import list_workflows_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            # Set up async context manager
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.search = AsyncMock(return_value=[mock_workflow])
                mock_repo.count_active = AsyncMock(return_value=1)
                mock_repo_cls.return_value = mock_repo

                tool = list_workflows_tool(org_user_context)

                # Call the tool function directly
                result = await call_tool(tool, {"query": None, "category": None})

        text = result["content"][0]["text"]
        assert "Registered Workflows" in text
        assert "test_workflow" in text
        assert "A test workflow for testing" in text
        assert "Endpoint: Enabled" in text

    @pytest.mark.asyncio
    async def test_returns_empty_message(self, org_user_context):
        """Should return helpful message when no workflows found."""
        from src.services.mcp.tools.list_workflows import list_workflows_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.search = AsyncMock(return_value=[])
                mock_repo.count_active = AsyncMock(return_value=0)
                mock_repo_cls.return_value = mock_repo

                tool = list_workflows_tool(org_user_context)
                result = await call_tool(tool, {})

        text = result["content"][0]["text"]
        assert "No workflows found" in text
        assert "/tmp/bifrost/workspace" in text
        assert "@workflow" in text

    @pytest.mark.asyncio
    async def test_filters_by_category(self, org_user_context, mock_workflow):
        """Should pass category filter to repository."""
        from src.services.mcp.tools.list_workflows import list_workflows_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.repositories.workflows.WorkflowRepository"
            ) as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.search = AsyncMock(return_value=[mock_workflow])
                mock_repo.count_active = AsyncMock(return_value=1)
                mock_repo_cls.return_value = mock_repo

                tool = list_workflows_tool(org_user_context)
                await call_tool(tool, {"category": "automation"})

                # Verify category was passed to search
                mock_repo.search.assert_called_once_with(
                    query=None,
                    category="automation",
                    limit=100,
                )

    @pytest.mark.asyncio
    async def test_handles_database_error(self, org_user_context):
        """Should return error message on database failure."""
        from src.services.mcp.tools.list_workflows import list_workflows_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_db_ctx.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Database connection failed")
            )
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = list_workflows_tool(org_user_context)
            result = await call_tool(tool, {})

        text = result["content"][0]["text"]
        assert "Error listing workflows" in text


# ==================== list_forms Tests ====================


class TestListForms:
    """Tests for the list_forms MCP tool."""

    @pytest.mark.asyncio
    async def test_lists_forms_for_org_user(self, org_user_context, mock_form):
        """Should list forms for org user with ORG_PLUS_GLOBAL filter."""
        from src.core.org_filter import OrgFilterType
        from src.services.mcp.tools.list_forms import list_forms_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("src.repositories.forms.FormRepository") as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.list_forms = AsyncMock(return_value=[mock_form])
                mock_repo_cls.return_value = mock_repo

                tool = list_forms_tool(org_user_context)
                result = await call_tool(tool, {"active_only": True})

                # Verify org user gets ORG_PLUS_GLOBAL filter
                mock_repo.list_forms.assert_called_once_with(
                    filter_type=OrgFilterType.ORG_PLUS_GLOBAL,
                    active_only=True,
                )

        text = result["content"][0]["text"]
        assert "Forms" in text
        assert "Test Form" in text
        assert "A test form" in text

    @pytest.mark.asyncio
    async def test_lists_forms_for_platform_admin(
        self, platform_admin_context, mock_form
    ):
        """Should list all forms for platform admin."""
        from src.core.org_filter import OrgFilterType
        from src.services.mcp.tools.list_forms import list_forms_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("src.repositories.forms.FormRepository") as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.list_forms = AsyncMock(return_value=[mock_form])
                mock_repo_cls.return_value = mock_repo

                tool = list_forms_tool(platform_admin_context)
                await call_tool(tool, {})

                # Verify platform admin gets ALL filter
                mock_repo.list_forms.assert_called_once_with(
                    filter_type=OrgFilterType.ALL,
                    active_only=True,
                )

    @pytest.mark.asyncio
    async def test_returns_empty_message(self, org_user_context):
        """Should return helpful message when no forms found."""
        from src.services.mcp.tools.list_forms import list_forms_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("src.repositories.forms.FormRepository") as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.list_forms = AsyncMock(return_value=[])
                mock_repo_cls.return_value = mock_repo

                tool = list_forms_tool(org_user_context)
                result = await call_tool(tool, {})

        text = result["content"][0]["text"]
        assert "No forms found" in text
        assert "validate_form_schema" in text

    @pytest.mark.asyncio
    async def test_handles_database_error(self, org_user_context):
        """Should return error message on database failure."""
        from src.services.mcp.tools.list_forms import list_forms_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_db_ctx.return_value.__aenter__ = AsyncMock(
                side_effect=Exception("Database connection failed")
            )
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            tool = list_forms_tool(org_user_context)
            result = await call_tool(tool, {})

        text = result["content"][0]["text"]
        assert "Error listing forms" in text


# ==================== search_knowledge Tests ====================


class TestSearchKnowledge:
    """Tests for the search_knowledge MCP tool."""

    @pytest.mark.asyncio
    async def test_searches_knowledge_base(
        self, org_user_context, mock_knowledge_document
    ):
        """Should search knowledge base and return results."""
        from src.services.mcp.tools.search_knowledge import search_knowledge_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.embeddings.get_embedding_client"
            ) as mock_embed_client:
                mock_client = AsyncMock()
                mock_client.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
                mock_embed_client.return_value = mock_client

                with patch(
                    "src.repositories.knowledge.KnowledgeRepository"
                ) as mock_repo_cls:
                    mock_repo = MagicMock()
                    mock_repo.search = AsyncMock(return_value=[mock_knowledge_document])
                    mock_repo_cls.return_value = mock_repo

                    tool = search_knowledge_tool(org_user_context)
                    result = await call_tool(tool, {"query": "SDK documentation"})

                    # Verify search was called with correct params
                    mock_repo.search.assert_called_once_with(
                        query_embedding=[0.1, 0.2, 0.3],
                        namespace="bifrost_docs",
                        organization_id=org_user_context.org_id,
                        limit=5,
                        fallback=True,
                    )

        text = result["content"][0]["text"]
        assert "Knowledge Search Results" in text
        assert "SDK documentation" in text
        assert "This is documentation about the SDK" in text
        assert "85.0%" in text  # Score

    @pytest.mark.asyncio
    async def test_returns_no_results_message(self, org_user_context):
        """Should return helpful message when no results found."""
        from src.services.mcp.tools.search_knowledge import search_knowledge_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.embeddings.get_embedding_client"
            ) as mock_embed_client:
                mock_client = AsyncMock()
                mock_client.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
                mock_embed_client.return_value = mock_client

                with patch(
                    "src.repositories.knowledge.KnowledgeRepository"
                ) as mock_repo_cls:
                    mock_repo = MagicMock()
                    mock_repo.search = AsyncMock(return_value=[])
                    mock_repo_cls.return_value = mock_repo

                    tool = search_knowledge_tool(org_user_context)
                    result = await call_tool(tool, {"query": "nonexistent topic"})

        text = result["content"][0]["text"]
        assert "No results found" in text
        assert "nonexistent topic" in text
        assert "bifrost_docs" in text

    @pytest.mark.asyncio
    async def test_handles_missing_query(self, org_user_context):
        """Should return error when query is missing."""
        from src.services.mcp.tools.search_knowledge import search_knowledge_tool

        tool = search_knowledge_tool(org_user_context)
        result = await call_tool(tool, {})  # No query provided

        text = result["content"][0]["text"]
        assert "query is required" in text

    @pytest.mark.asyncio
    async def test_handles_embedding_unavailable(self, org_user_context):
        """Should return error when embedding service is unavailable."""
        from src.services.mcp.tools.search_knowledge import search_knowledge_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.embeddings.get_embedding_client"
            ) as mock_embed_client:
                mock_embed_client.side_effect = ValueError(
                    "No embedding configuration found"
                )

                tool = search_knowledge_tool(org_user_context)
                result = await call_tool(tool, {"query": "test query"})

        text = result["content"][0]["text"]
        assert "Knowledge search unavailable" in text
        assert "embedding" in text.lower()

    @pytest.mark.asyncio
    async def test_uses_custom_namespace_and_limit(
        self, org_user_context, mock_knowledge_document
    ):
        """Should respect custom namespace and limit parameters."""
        from src.services.mcp.tools.search_knowledge import search_knowledge_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.embeddings.get_embedding_client"
            ) as mock_embed_client:
                mock_client = AsyncMock()
                mock_client.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
                mock_embed_client.return_value = mock_client

                with patch(
                    "src.repositories.knowledge.KnowledgeRepository"
                ) as mock_repo_cls:
                    mock_repo = MagicMock()
                    mock_repo.search = AsyncMock(return_value=[mock_knowledge_document])
                    mock_repo_cls.return_value = mock_repo

                    tool = search_knowledge_tool(org_user_context)
                    await call_tool(tool, 
                        {
                            "query": "custom search",
                            "namespace": "my_custom_ns",
                            "limit": 3,
                        }
                    )

                    # Verify custom params used
                    mock_repo.search.assert_called_once_with(
                        query_embedding=[0.1, 0.2, 0.3],
                        namespace="my_custom_ns",
                        organization_id=org_user_context.org_id,
                        limit=3,
                        fallback=True,
                    )

    @pytest.mark.asyncio
    async def test_caps_limit_at_10(self, org_user_context, mock_knowledge_document):
        """Should cap limit at 10 even if higher value requested."""
        from src.services.mcp.tools.search_knowledge import search_knowledge_tool

        with patch("src.core.database.get_db_context") as mock_db_ctx:
            mock_session = AsyncMock()
            mock_db_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.embeddings.get_embedding_client"
            ) as mock_embed_client:
                mock_client = AsyncMock()
                mock_client.embed = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
                mock_embed_client.return_value = mock_client

                with patch(
                    "src.repositories.knowledge.KnowledgeRepository"
                ) as mock_repo_cls:
                    mock_repo = MagicMock()
                    mock_repo.search = AsyncMock(return_value=[mock_knowledge_document])
                    mock_repo_cls.return_value = mock_repo

                    tool = search_knowledge_tool(org_user_context)
                    await call_tool(tool, {"query": "test", "limit": 50})  # Request 50

                    # Verify limit capped at 10
                    call_args = mock_repo.search.call_args
                    assert call_args.kwargs["limit"] == 10
