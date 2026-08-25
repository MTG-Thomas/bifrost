"""
Bifrost MCP Server

MCP server for Bifrost platform capabilities using FastMCP for HTTP access.

Architecture:
    - MCPContext: Holds user/org context for permission-scoped tool execution
    - BifrostMCPServer: Creates MCP servers with registered tools
    - Uses FastMCP for HTTP access (Claude Desktop, etc.)

Usage:
    # For external access (FastMCP HTTP)
    server = BifrostMCPServer(context)
    fastmcp_server = server.get_fastmcp_server()
    app = fastmcp_server.http_app()
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence
from uuid import UUID

from fastmcp.tools import ToolResult

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from src.services.tool_registry import RegisteredTool

from src.services.mcp_server.agent_scope import (
    get_scoped_agent_id as _get_agent_id_from_scope,
)
from src.services.mcp_server.tools import (
    TOOL_MODULES,
    register_all_tools,
    register_gateway_tools,
)
from src.services.mcp_server.tools.gateway import (
    GATEWAY_INSTRUCTIONS,
    GATEWAY_TOOL_NAMES,
)
from src.services.mcp_server.tool_result import error_result, success_result

logger = logging.getLogger(__name__)

# FastMCP for HTTP access - runtime import check
HAS_FASTMCP = False
_FastMCP: type["FastMCP"] | None = None
_Icon: type | None = None

try:
    from fastmcp import FastMCP as _FastMCPClass
    from mcp.types import Icon as _IconClass

    _FastMCP = _FastMCPClass
    _Icon = _IconClass
    HAS_FASTMCP = True
except ImportError as e:
    # fastmcp / mcp packages are optional; HTTP MCP support stays disabled
    logger.debug(f"fastmcp not available, MCP HTTP server disabled: {e}")

# Bifrost branding
BIFROST_ICON_URL = "https://bifrostintegrations.blob.core.windows.net/public/logo.svg"
BIFROST_WEBSITE_URL = "https://docs.gobifrost.com"


# =============================================================================
# Workflow Tool Name Mapping
# =============================================================================

# Workflow tools are registered with normalized names for MCP compatibility.
# The reverse mapping lets agent-scoped access resolve a workflow UUID to its
# current FastMCP tool name. Its values also track names during refresh.
_WORKFLOW_ID_TO_TOOL_NAME: dict[str, str] = {}
_WORKFLOW_CATALOG_DIGEST = hashlib.sha256(b"[]").hexdigest()
_WORKFLOW_CATALOG_REVISION = -1
_WORKFLOW_CATALOG_REFRESH_LOCK = asyncio.Lock()
_WORKFLOW_CATALOG_REFRESH_ATTEMPTS = 3

# Stored references for refresh_workflow_tools()
_fastmcp_instance: "FastMCP | None" = None


def _normalize_tool_name(name: str) -> str:
    """
    Convert workflow name to valid MCP tool name (snake_case).

    Examples:
        "Review Tickets" -> "review_tickets"
        "get-user-data" -> "get_user_data"
        "ProcessOrder123" -> "processorder123"
    """
    import re

    name = name.lower().strip()
    # Replace spaces, hyphens, and multiple underscores with single underscore
    name = re.sub(r"[\s\-]+", "_", name)
    # Remove any non-alphanumeric characters except underscores
    name = re.sub(r"[^a-z0-9_]", "", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)
    # Remove leading/trailing underscores
    name = name.strip("_")
    return name


def _workflow_identity_suffix(workflow_id: UUID | str) -> str:
    """Return a collision-free, replica-stable suffix for one workflow."""
    return UUID(str(workflow_id)).hex


def get_registered_tool_name(workflow_id: str) -> str | None:
    """
    Get the registered MCP tool name for a workflow ID.

    Args:
        workflow_id: The workflow UUID string

    Returns:
        Tool name string or None if not registered
    """
    return _WORKFLOW_ID_TO_TOOL_NAME.get(workflow_id)


@dataclass
class MCPContext:
    """
    Context for MCP tool execution.

    Provides user and organization scope for permission-aware tool execution.
    All MCP tools receive this context to enforce access control.
    """

    user_id: UUID | str
    org_id: UUID | str | None = None
    is_platform_admin: bool = False
    # External (portal/guest) principal — no global tier, no authenticated-
    # tier entitlement. Mirrors UserPrincipal.is_external (the claim is
    # already bypass-neutralized at token mint).
    is_external: bool = False
    user_email: str = ""
    user_name: str = ""
    operation_id: str | None = None

    # Knowledge namespaces accessible to this user (from agent.knowledge_sources)
    accessible_namespaces: list[str] = field(default_factory=list)

    # Database session from executor context (None when running via MCP server)
    session: Any = None

    def __post_init__(self) -> None:
        # JWT claims arrive as strings; downstream comparisons (e.g. against
        # ORM UUID columns) silently fail because `UUID == str` is False.
        # Normalize once at the boundary so org-scoped repos see real UUIDs.
        if isinstance(self.user_id, str) and self.user_id:
            self.user_id = UUID(self.user_id)
        if isinstance(self.org_id, str) and self.org_id:
            self.org_id = UUID(self.org_id)


def _has_http_request_context() -> bool:
    """Return whether FastMCP has an active HTTP request on this stack."""
    from fastmcp.server.dependencies import get_http_request

    try:
        get_http_request()
    except RuntimeError:
        return False
    return True
def _get_context_from_token() -> MCPContext:
    """
    Get MCPContext from authenticated FastMCP token.

    This extracts user information from the validated JWT token set by
    FastMCP's authentication middleware. Used by tool execution to get
    the actual authenticated user instead of the default startup context.

    Returns:
        MCPContext populated with authenticated user's information

    Raises:
        ToolError: If no authenticated user (token missing or invalid)
    """
    from fastmcp.exceptions import ToolError
    from fastmcp.server.dependencies import get_access_token

    token = get_access_token()
    if token is None:
        raise ToolError("Authentication required")

    return MCPContext(
        user_id=token.claims.get("user_id", ""),
        org_id=token.claims.get("org_id"),
        is_platform_admin=token.claims.get("is_superuser", False),
        is_external=token.claims.get("is_external", False),
        user_email=token.claims.get("email", ""),
        user_name=token.claims.get("name", ""),
    )


async def _get_runtime_context() -> MCPContext:
    """Build the per-request MCPContext used by tool execution.

    Populates ``accessible_namespaces`` only for the agent-scoped mount:

    - ``/mcp/{agent_id}`` (agent-scoped): namespaces == that agent's
      ``knowledge_sources`` only. Cross-namespace requests are rejected
      by the tool itself, so a session bound to an agent can only see
      that agent's knowledge.
    - ``/mcp`` (unscoped): the gateway resolves the selected agent and
      constructs a tool-specific context at dispatch time.
    """
    from fastmcp.exceptions import ToolError
    from fastmcp.server.dependencies import get_access_token
    from sqlalchemy.exc import SQLAlchemyError

    from src.core.database import get_db_context
    from src.services.mcp_server.tool_access import MCPToolAccessService

    token = get_access_token()
    if token is None:
        raise ToolError("Authentication required")

    user_roles = token.claims.get("roles", [])
    is_superuser = token.claims.get("is_superuser", False)
    is_external = token.claims.get("is_external", False)
    user_id = token.claims.get("user_id")
    org_id = token.claims.get("org_id")
    agent_id = _get_agent_id_from_scope()

    accessible_namespaces: list[str] = []
    if agent_id is not None:
        try:
            async with get_db_context() as db:
                service = MCPToolAccessService(db)
                agent_result = await service.get_tools_for_agent(
                    agent_id=agent_id,
                    user_roles=user_roles,
                    is_superuser=is_superuser,
                    user_id=user_id,
                    org_id=org_id,
                    is_external=is_external,
                )
                if agent_result is None:
                    raise ToolError("Agent not found or inaccessible")
                accessible_namespaces = list(agent_result.accessible_namespaces)
        except ToolError:
            raise
        except SQLAlchemyError as exc:
            logger.exception("Failed to resolve accessible namespaces")
            raise ToolError("Failed to resolve accessible namespaces") from exc
        except Exception:
            logger.exception("Unexpected error resolving accessible namespaces")
            raise

    return MCPContext(
        user_id=token.claims.get("user_id", ""),
        org_id=token.claims.get("org_id"),
        is_platform_admin=is_superuser,
        is_external=is_external,
        user_email=token.claims.get("email", ""),
        user_name=token.claims.get("name", ""),
        accessible_namespaces=accessible_namespaces,
    )


# =============================================================================
# BifrostMCPServer
# =============================================================================


class BifrostMCPServer:
    """
    Bifrost MCP Server using FastMCP.

    Creates MCP servers with tools registered based on user context and
    permissions. Uses FastMCP for HTTP access (Claude Desktop, etc.).

    Usage:
        # Create server with context
        context = MCPContext(user_id=user.id, org_id=user.org_id)
        server = BifrostMCPServer(context)

        # For FastMCP HTTP use
        fastmcp_server = server.get_fastmcp_server()
    """

    def __init__(
        self,
        context: MCPContext,
        *,
        name: str = "bifrost",
    ):
        """
        Initialize Bifrost MCP server.

        Args:
            context: MCP context with user/org information
            name: Server name (default: "bifrost")
        """
        self.context = context
        self._name = name

        # FastMCP server (lazy initialized)
        self._fastmcp: Any = None

    def get_fastmcp_server(self, auth: Any = None) -> "FastMCP":
        """
        Get FastMCP server for HTTP access.

        The server is cached for reuse. If auth is provided, a new server
        is created with authentication enabled.

        Args:
            auth: Optional authentication provider (e.g., token verifier).
                  If provided, creates a new server with auth.

        Returns:
            FastMCP server instance
        """
        if not HAS_FASTMCP:
            raise ImportError(
                "fastmcp is required for MCP access. "
                "Install it with: pip install 'fastmcp>=3.2.0,<4'"
            )

        # Build icon list for branding
        icons = []
        if _Icon is not None:
            icons = [
                _Icon(
                    src=BIFROST_ICON_URL,
                    mimeType="image/svg+xml",
                    sizes=["any"],
                )
            ]

        # Per-request context: pull user from the JWT and scope namespaces for
        # the agent-specific mount. Falls back to the server's
        # default_context only when there is no FastMCP request context at all
        # (e.g. tool introspection at startup); other failures (auth required,
        # DB error) are real errors and must not silently degrade to the
        # default admin context.
        default_context = self.context

        async def get_context_fn() -> MCPContext:
            from fastmcp.exceptions import ToolError

            try:
                return await _get_runtime_context()
            except ToolError:
                # ToolError is raised when no auth token exists in the
                # current call — for HTTP requests that means unauthenticated
                # and should bubble up; for tool introspection at startup
                # there is no HTTP context, so fall back to default.
                if _has_http_request_context():
                    raise
                return default_context

        # If auth is provided, always create a new server with auth
        if auth is not None:
            assert _FastMCP is not None
            mcp = _FastMCP(
                self._name,
                auth=auth,
                instructions=GATEWAY_INSTRUCTIONS,
                website_url=BIFROST_WEBSITE_URL,
                icons=icons,
            )
            register_gateway_tools(mcp, get_context_fn)
            register_all_tools(mcp, get_context_fn)
            tool_count = (
                len(GATEWAY_TOOL_NAMES)
                + sum(len(m.TOOLS) for m in TOOL_MODULES)
            )
            logger.info(f"Created FastMCP server with {tool_count} tools and auth")
            return mcp

        # Otherwise use cached server
        if self._fastmcp is None:
            assert _FastMCP is not None
            self._fastmcp = _FastMCP(
                self._name,
                instructions=GATEWAY_INSTRUCTIONS,
                website_url=BIFROST_WEBSITE_URL,
                icons=icons,
            )
            register_gateway_tools(self._fastmcp, get_context_fn)
            register_all_tools(self._fastmcp, get_context_fn)
            tool_count = (
                len(GATEWAY_TOOL_NAMES)
                + sum(len(m.TOOLS) for m in TOOL_MODULES)
            )
            logger.info(f"Created FastMCP server with {tool_count} tools")
        return self._fastmcp

def get_system_tools() -> list[dict[str, Any]]:
    """
    Get system tool metadata from tool modules.

    Returns list of dicts with id, name, description, and parameters for each tool.
    Used by /api/tools endpoint and agent executor for LLM tool definitions.
    """
    import inspect
    from typing import get_origin, get_args, Union

    def python_type_to_json_schema(annotation: Any) -> dict[str, Any]:
        """Convert Python type annotation to JSON Schema."""
        if annotation is inspect.Parameter.empty or annotation is None:
            return {"type": "string"}

        # Handle Optional types (Union[X, None])
        origin = get_origin(annotation)
        if origin is Union:
            args = [a for a in get_args(annotation) if a is not type(None)]
            if len(args) == 1:
                return python_type_to_json_schema(args[0])

        # Basic type mappings
        if annotation is str:
            return {"type": "string"}
        elif annotation is int:
            return {"type": "integer"}
        elif annotation is float:
            return {"type": "number"}
        elif annotation is bool:
            return {"type": "boolean"}
        elif annotation is dict or origin is dict:
            schema: dict[str, Any] = {"type": "object"}
            if origin is dict:
                args = get_args(annotation)
                if len(args) == 2:
                    schema["additionalProperties"] = python_type_to_json_schema(args[1])
            else:
                schema["additionalProperties"] = True
            return schema
        elif annotation is list or origin is list:
            # Gemini rejects array schemas without `items`; OpenAI/Anthropic
            # accept the field, so emit it unconditionally.
            array_schema: dict[str, Any] = {"type": "array"}
            if origin is list:
                args = get_args(annotation)
                if args:
                    array_schema["items"] = python_type_to_json_schema(args[0])
                else:
                    array_schema["items"] = {"type": "string"}
            else:
                array_schema["items"] = {"type": "string"}
            return array_schema

        return {"type": "string"}

    tools = []
    for module in TOOL_MODULES:
        if hasattr(module, "TOOLS"):
            # Build a mapping of tool_id -> function for this module
            tool_funcs: dict[str, Any] = {}
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if callable(attr) and not attr_name.startswith("_"):
                    tool_funcs[attr_name] = attr

            for tool_id, name, description in module.TOOLS:
                # Get the function and extract parameters
                func = tool_funcs.get(tool_id)
                parameters: dict[str, Any] = {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                }

                if func:
                    sig = inspect.signature(func)
                    params = list(sig.parameters.items())
                    # Skip first param (context)
                    for param_name, param in params[1:]:
                        prop = python_type_to_json_schema(param.annotation)
                        parameters["properties"][param_name] = prop
                        # Required if no default value
                        if param.default is inspect.Parameter.empty:
                            parameters["required"].append(param_name)

                if tool_id == "search_knowledge":
                    from src.services.knowledge.search_budget import (
                        MAX_KNOWLEDGE_RESULTS,
                    )

                    parameters["properties"]["limit"].update({
                        "minimum": 1,
                        "maximum": MAX_KNOWLEDGE_RESULTS,
                        "default": MAX_KNOWLEDGE_RESULTS,
                    })

                tools.append({
                    "id": tool_id,
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                })
    return tools


def get_system_tool_function(tool_id: str) -> Any | None:
    """
    Get the callable function for a system tool by ID.

    Args:
        tool_id: The tool ID (e.g., "execute_workflow", "list_agents")

    Returns:
        The async callable function, or None if not found.
    """
    for module in TOOL_MODULES:
        if hasattr(module, "TOOLS"):
            # Check if this tool is in this module
            tool_ids_in_module = [t[0] for t in module.TOOLS]
            if tool_id in tool_ids_in_module:
                # Get the function from the module
                if hasattr(module, tool_id):
                    return getattr(module, tool_id)
    return None


# =============================================================================
# Workflow Tools (Dynamic from Database)
# =============================================================================

# WorkflowTool class for FastMCP - wraps workflow execution
_WorkflowTool: type | None = None

if HAS_FASTMCP:
    from fastmcp.tools import Tool as _FastMCPTool

    class WorkflowTool(_FastMCPTool):
        """
        MCP Tool that executes a Bifrost workflow.

        Subclasses FastMCP's Tool to:
        1. Accept JSON Schema directly via `parameters` field
        2. Override `run()` to delegate to workflow execution

        This bypasses FastMCP's function signature inspection, allowing
        dynamic parameter schemas from workflow `parameters_schema`.

        The execution context is retrieved dynamically from the authenticated
        token at runtime via _get_context_from_token().

        Results are auto-wrapped in ToolResult:
        - If result is already ToolResult, pass through
        - If result is a JSON string, parse and wrap
        - If result has {"error": ...}, return error_result
        - Otherwise format as display text with structured data
        """

        workflow_id: str
        workflow_name: str

        model_config = {"arbitrary_types_allowed": True}

        async def run(self, arguments: dict[str, Any]) -> ToolResult:
            """Execute the workflow and wrap result in ToolResult."""
            try:
                context = _get_context_from_token()
            except Exception as e:
                return error_result(f"Authentication error: {e}")

            try:
                result = await _execute_workflow_tool_impl(
                    context,
                    self.workflow_id,
                    self.workflow_name,
                    **arguments,
                )

                # If user returned ToolResult, pass through unchanged
                if isinstance(result, ToolResult):
                    return result

                # Parse JSON string if needed (legacy workflow returns)
                if isinstance(result, str):
                    try:
                        parsed = json.loads(result)
                        result = parsed
                    except json.JSONDecodeError:
                        # Plain string result - return as success
                        return success_result(result, {"result": result})

                from pydantic import BaseModel

                if isinstance(result, BaseModel):
                    result = result.model_dump(mode="json")

                # Auto-wrap dict results
                if isinstance(result, dict):
                    if result.get("error"):
                        return error_result(str(result["error"]), result)
                    from src.services.chat_artifacts import find_artifact_refs

                    artifact_refs = find_artifact_refs(result)
                    if artifact_refs:
                        import base64

                        from mcp.types import ImageContent, ResourceLink, TextContent

                        from src.core.database import get_db_context
                        from src.services.artifacts import (
                            ArtifactAccessError,
                            ArtifactService,
                        )
                        from src.services.file_storage.service import (
                            get_file_storage_service,
                        )

                        content_blocks: list[Any] = [
                            TextContent(
                                type="text",
                                text=f"Created {len(artifact_refs)} artifact(s).",
                            )
                        ]
                        async with get_db_context() as db:
                            storage = get_file_storage_service(db)
                            artifact_service = ArtifactService(db)
                            for ref in artifact_refs:
                                try:
                                    artifact = await artifact_service.get_authorized(
                                        UUID(ref.id),
                                        user_id=UUID(str(context.user_id)),
                                        organization_id=(
                                            UUID(str(context.org_id))
                                            if context.org_id
                                            else None
                                        ),
                                        is_platform_admin=context.is_platform_admin,
                                    )
                                except (ArtifactAccessError, ValueError):
                                    return error_result(
                                        "ArtifactRef output was not found or is outside this MCP scope.",
                                        result,
                                    )
                                if ref.content_type.startswith("image/"):
                                    data = await artifact_service.read(artifact)
                                    content_blocks.append(
                                        ImageContent(
                                            type="image",
                                            data=base64.b64encode(data).decode("ascii"),
                                            mimeType=ref.content_type,
                                        )
                                    )
                                else:
                                    url = await storage.generate_presigned_download_url(
                                        artifact.s3_key
                                    )
                                    content_blocks.append(
                                        ResourceLink(
                                            type="resource_link",
                                            name=ref.filename,
                                            uri=url,
                                            mimeType=ref.content_type,
                                            size=ref.size_bytes,
                                        )
                                    )
                        return ToolResult(
                            content=content_blocks,
                            structured_content=result,
                        )
                    # Format as pretty JSON for display
                    display = json.dumps(result, indent=2, default=str)
                    return success_result(display, result)

                # Fallback for other types
                display = str(result)
                return success_result(display, {"result": result})

            except Exception as e:
                logger.exception(f"Error executing workflow tool {self.workflow_name}: {e}")
                return error_result(str(e))

    _WorkflowTool = WorkflowTool


@dataclass(frozen=True)
class _WorkflowCatalogEntry:
    """One deterministic workflow-tool definition ready for registration."""

    workflow_id: str
    workflow_name: str
    name: str
    description: str
    input_schema: dict[str, Any]

    def externally_visible_definition(self) -> dict[str, Any]:
        """Return exactly the fields this registration adds to ``tools/list``."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


def _normalized_workflow_catalog(
    entries: Sequence[_WorkflowCatalogEntry],
) -> bytes:
    """Serialize complete externally visible definitions canonically."""
    payload = [entry.externally_visible_definition() for entry in entries]
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _workflow_catalog_digest(entries: Sequence[_WorkflowCatalogEntry]) -> str:
    """Digest complete, canonically ordered externally visible definitions."""
    return hashlib.sha256(_normalized_workflow_catalog(entries)).hexdigest()


def _build_workflow_catalog(
    tools: Sequence["RegisteredTool"],
    native_tool_names: set[str] | frozenset[str],
) -> list[_WorkflowCatalogEntry]:
    """Normalize workflow tools into a replica-stable exposed catalog.

    Every workflow includes its complete stable UUID. Consequently an exposed
    identity never changes when another tenant creates or removes a colliding
    human-readable name, and inaccessible rows cannot influence a caller's
    visible tool identity.
    """
    from src.services.tool_registry import workflow_parameters_to_json_schema

    entries: list[_WorkflowCatalogEntry] = []
    for tool in tools:
        normalized = _normalize_tool_name(tool.name)
        candidate = normalized or "workflow"
        workflow_id = str(tool.id)
        exposed_name = f"{candidate}__{_workflow_identity_suffix(workflow_id)}"
        while exposed_name in native_tool_names:
            exposed_name = f"{exposed_name}_workflow"
        workflow_name = tool.name
        entries.append(
            _WorkflowCatalogEntry(
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                name=exposed_name,
                description=(
                    tool.description
                    or f"Execute the {workflow_name} workflow"
                ),
                input_schema=workflow_parameters_to_json_schema(
                    tool.parameters_schema
                ),
            )
        )

    return sorted(entries, key=lambda entry: (entry.name, entry.workflow_id))


async def _execute_workflow_tool_impl(
    context: MCPContext,
    workflow_id: str,
    workflow_name: str,
    **inputs: Any,
) -> Any:
    """Execute a specific workflow tool by ID."""
    from src.core.database import get_db_context
    from src.repositories.workflows import WorkflowRepository
    from src.services.execution.service import execute_tool

    try:
        async with get_db_context() as db:
            # Use context for proper org scoping and role-based access
            repo = WorkflowRepository(
                db,
                org_id=context.org_id,
                user_id=context.user_id,
                is_superuser=context.is_platform_admin,
                is_external=context.is_external,
            )
            workflow = await repo.get(id=workflow_id)

            if not workflow:
                return f"Error: Workflow '{workflow_name}' not found"

            if not workflow.is_active:
                return f"Error: Workflow '{workflow_name}' is not active"

            # Execute the workflow
            result = await execute_tool(
                workflow_id=str(workflow.id),
                workflow_name=workflow.name,
                parameters=inputs,
                user_id=str(context.user_id),
                user_email=context.user_email,
                user_name=context.user_name or "MCP User",
                org_id=str(context.org_id) if context.org_id else None,
                is_platform_admin=context.is_platform_admin,
            )

            success = result.status.value == "Success"
            if success:
                return result.result
            else:
                return f"Error: {result.error}"

    except Exception as e:
        logger.exception(f"Error executing workflow tool {workflow_name}: {e}")
        return f"Error executing workflow: {e}"


async def _notify_duplicate_workflow_names(duplicates: dict[str, list]) -> None:
    """Log warning about duplicate workflow names."""
    for name, workflows in duplicates.items():
        workflow_names = [w.name for w in workflows]
        logger.warning(
            f"Multiple workflows normalize to '{name}': {workflow_names}. "
            "Consider renaming to avoid confusion."
        )


def _replace_workflow_catalog(
    mcp: "FastMCP",
    catalog: Sequence[_WorkflowCatalogEntry],
) -> list[_WorkflowCatalogEntry]:
    """Replace the complete dynamic provider section in canonical order."""
    assert _WorkflowTool is not None

    prepared_tools: list[tuple[_WorkflowCatalogEntry, Any]] = []
    for entry in catalog:
        workflow_tool = _WorkflowTool(
            name=entry.name,
            description=entry.description,
            workflow_id=entry.workflow_id,
            workflow_name=entry.workflow_name,
            parameters=entry.input_schema,
        )
        prepared_tools.append((entry, workflow_tool))

    for old_name in sorted(set(_WORKFLOW_ID_TO_TOOL_NAME.values())):
        try:
            mcp.local_provider.remove_tool(old_name)
        except KeyError:
            logger.debug("Workflow tool already absent: %s", old_name)

    registered_entries: list[_WorkflowCatalogEntry] = []
    try:
        for entry, workflow_tool in prepared_tools:
            mcp.add_tool(workflow_tool)
            registered_entries.append(entry)
            logger.debug(
                f"Registered workflow tool: {entry.name} "
                f"(workflow: {entry.workflow_name}, id: {entry.workflow_id})"
            )
    except Exception:
        # FastMCP may insert a tool before raising while normalizing its schema.
        # Remove every prepared identity so the next refresh starts cleanly.
        for entry, _ in prepared_tools:
            try:
                mcp.local_provider.remove_tool(entry.name)
            except KeyError:
                # The failing add may have rejected the tool before insertion.
                pass
        raise
    return registered_entries


async def _register_workflow_tools(mcp: "FastMCP") -> int:
    """
    Register workflow tools with FastMCP server using human-readable names.

    Creates WorkflowTool instances for each workflow with is_tool=True,
    passing the parameters_schema directly as JSON Schema. This bypasses
    FastMCP's function signature inspection.

    Tool names combine a normalized workflow name with the complete workflow
    UUID so identity is stable across replicas and independent of hidden rows.

    Returns:
        Number of workflow tools registered
    """
    global _WORKFLOW_CATALOG_DIGEST, _WORKFLOW_ID_TO_TOOL_NAME, _fastmcp_instance

    # Store the live server so refresh_workflow_tools() can re-register tools.
    _fastmcp_instance = mcp

    if not HAS_FASTMCP or _WorkflowTool is None:
        logger.warning("FastMCP not available, skipping workflow tool registration")
        return 0

    from src.core.database import get_db_context
    from src.services.tool_registry import ToolRegistry

    try:
        async with get_db_context() as db:
            registry = ToolRegistry(db)
            tools = await registry.get_all_tools()

            # Group workflows by normalized name to detect duplicates
            name_groups: dict[str, list] = {}
            for tool in tools:
                normalized = _normalize_tool_name(tool.name)
                name_groups.setdefault(normalized or "workflow", []).append(tool)

            # Detect duplicates and notify admins
            duplicates = {name: wfs for name, wfs in name_groups.items() if len(wfs) > 1}
            if duplicates:
                await _notify_duplicate_workflow_names(duplicates)
                logger.warning(
                    f"Found {len(duplicates)} duplicate workflow names: "
                    f"{list(duplicates.keys())}"
                )

            # Get native tool names to avoid collisions
            native_tool_names = {
                tool_id for m in TOOL_MODULES for tool_id, _, _ in m.TOOLS
            } | set(GATEWAY_TOOL_NAMES)

            catalog = _build_workflow_catalog(tools, native_tool_names)

            # FastMCP replacement preserves existing dict positions, so the
            # helper removes and reinserts the complete dynamic section.
            registered_entries = _replace_workflow_catalog(mcp, catalog)

            count = len(registered_entries)

            _WORKFLOW_ID_TO_TOOL_NAME = {
                entry.workflow_id: entry.name for entry in registered_entries
            }
            _WORKFLOW_CATALOG_DIGEST = _workflow_catalog_digest(
                registered_entries
            )
            logger.info(
                f"Registered {count} workflow tools with FastMCP "
                f"(catalog digest: {_WORKFLOW_CATALOG_DIGEST})"
            )
            return count

    except Exception as e:
        logger.exception(f"Error registering workflow tools: {e}")
        raise


async def refresh_workflow_tools(
    *,
    mcp: "FastMCP | None" = None,
    target_revision: int | None = None,
    force: bool = False,
) -> int:
    """Reconcile this replica's provider with a stable shared revision.

    The durable database revision is read on both sides of each catalog
    snapshot. If it changes while the catalog is being rebuilt, the query may
    have raced a committed mutation, so reconciliation repeats before marking
    the replica current. A missed pub/sub message is harmless because request
    middleware supplies the durable shared revision as ``target_revision``.
    """
    global _WORKFLOW_CATALOG_REVISION, _fastmcp_instance

    if mcp is not None:
        _fastmcp_instance = mcp
    if not _fastmcp_instance:
        logger.debug("MCP not initialized, skipping workflow tool refresh")
        return 0

    async with _WORKFLOW_CATALOG_REFRESH_LOCK:
        from src.services.mcp_server.catalog_sync import (
            get_workflow_catalog_revision,
        )

        if (
            not force
            and target_revision is not None
            and _WORKFLOW_CATALOG_REVISION >= target_revision
        ):
            if _WORKFLOW_CATALOG_REVISION == target_revision:
                return len(_WORKFLOW_ID_TO_TOOL_NAME)

            # A delayed pub/sub message may carry an older revision after a
            # request already reconciled this replica. Only rebuild when the
            # durable database revision itself moved backwards (for example,
            # after a restore or replacement).
            shared_revision = await get_workflow_catalog_revision()
            if shared_revision >= _WORKFLOW_CATALOG_REVISION:
                return len(_WORKFLOW_ID_TO_TOOL_NAME)

        for attempt in range(1, _WORKFLOW_CATALOG_REFRESH_ATTEMPTS + 1):
            revision_before = await get_workflow_catalog_revision()
            old_tool_names = set(_WORKFLOW_ID_TO_TOOL_NAME.values())
            old_catalog_digest = _WORKFLOW_CATALOG_DIGEST
            count = await _register_workflow_tools(_fastmcp_instance)
            new_tool_names = set(_WORKFLOW_ID_TO_TOOL_NAME.values())

            revision_after = await get_workflow_catalog_revision()
            if revision_before != revision_after:
                if attempt == _WORKFLOW_CATALOG_REFRESH_ATTEMPTS:
                    raise RuntimeError(
                        "MCP workflow catalog kept changing during reconciliation"
                    )
                logger.info(
                    "MCP workflow catalog revision moved during refresh "
                    "(%s -> %s); reconciling again",
                    revision_before,
                    revision_after,
                )
                continue

            _WORKFLOW_CATALOG_REVISION = revision_after
            added = new_tool_names - old_tool_names
            removed = old_tool_names - new_tool_names
            if added or removed or old_catalog_digest != _WORKFLOW_CATALOG_DIGEST:
                logger.info(
                    f"MCP workflow tools refreshed: {count} total, "
                    f"+{len(added)} added, -{len(removed)} removed, "
                    f"revision: {_WORKFLOW_CATALOG_REVISION}, "
                    f"catalog digest: {_WORKFLOW_CATALOG_DIGEST}"
                )
            return count

        raise AssertionError("workflow catalog reconciliation loop exhausted")
