"""
Workflow indexer for extracting and indexing workflows and data providers from Python files.

Handles AST-based parsing to extract metadata from @workflow, @tool, and @data_provider
decorators without importing the module.
"""

import ast
import logging
import re
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.models import Workflow

logger = logging.getLogger(__name__)


class WorkflowIndexer:
    """
    Indexes Python files containing workflows and data providers.

    Uses AST-based parsing to extract metadata from @workflow, @tool, and @data_provider
    decorators. Also manages deactivation protection and workflow endpoint registration.
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize the workflow indexer.

        Args:
            db: Database session for querying and updating workflow records
        """
        self.db = db
        self._prefetch_cache: dict[tuple[str, str], Workflow] | None = None

    def set_prefetch_cache(self, cache: dict[tuple[str, str], Workflow]) -> None:
        """Set a prefetch cache of {(path, function_name): Workflow} to avoid per-function DB lookups."""
        self._prefetch_cache = cache

    async def extract_metadata(
        self,
        path: str,
        content: bytes,
    ) -> dict[str, Any] | None:
        """
        Extract workflow metadata from Python file content.

        This is a quick scan to detect if the file contains SDK decorators,
        used for entity type detection.

        Args:
            path: File path
            content: File content bytes

        Returns:
            Metadata dict if workflows/providers found, None otherwise
        """
        try:
            content_str = content.decode("utf-8", errors="replace")
        except Exception:
            return None

        # Fast regex check - if no decorator-like patterns, skip AST parsing
        if (
            "@workflow" not in content_str
            and "@data_provider" not in content_str
            and "@tool" not in content_str
        ):
            return None

        # AST verification - confirm decorators are actually used
        try:
            tree = ast.parse(content_str)
        except SyntaxError:
            return None

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for decorator in node.decorator_list:
                decorator_info = self._parse_decorator(decorator)
                if decorator_info:
                    decorator_name, _ = decorator_info
                    if decorator_name in ("workflow", "data_provider", "tool"):
                        return {"has_decorators": True}

        return None

    async def index_python_file(
        self,
        path: str,
        content: bytes,
        cached_ast: ast.Module | None = None,
        cached_content_str: str | None = None,
    ) -> None:
        """
        Enrich existing workflow/provider records from Python file content.

        Uses AST-based parsing to extract metadata from @workflow, @tool, and
        @data_provider decorators without importing the module.

        Enrich-only: only updates existing DB records. Unregistered functions
        (no matching DB record) are skipped. Use register_workflow() to create
        new records.

        Args:
            path: File path
            content: File content bytes
            cached_ast: Pre-parsed AST tree (avoids re-parsing large files)
            cached_content_str: Pre-decoded content string (avoids re-decoding)
        """
        # Use cached values if available (avoids re-decoding/re-parsing 4MB files)
        content_str = cached_content_str or content.decode("utf-8", errors="replace")

        tree = cached_ast
        if tree is None:
            try:
                tree = ast.parse(content_str, filename=path)
            except SyntaxError as e:
                logger.warning(f"Syntax error parsing {log_safe(path)}: {log_safe(e)}")
                return

        now = datetime.now(timezone.utc)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for decorator in node.decorator_list:
                decorator_info = self._parse_decorator(decorator)
                if not decorator_info:
                    continue

                decorator_name, kwargs = decorator_info

                if decorator_name in ("workflow", "tool"):
                    if decorator_name == "tool":
                        kwargs["is_tool"] = True

                    function_name = node.name

                    # Look up existing workflow by path + function_name
                    # Use prefetch cache if available, otherwise query DB
                    if self._prefetch_cache is not None:
                        existing_workflow = self._prefetch_cache.get((path, function_name))
                    else:
                        # Include inactive rows so we can reactivate them. Scope to
                        # _repo/ rows (solution_id IS NULL): this indexer manages
                        # WORKSPACE files only — a solution-managed workflow at the
                        # same (path, function) is written solely by deploy, and
                        # without this filter scalar_one_or_none() would raise
                        # MultipleResultsFound on a _repo/+solution path collision,
                        # or touch the solution row (Codex #14).
                        stmt = select(Workflow).where(
                            Workflow.path == path,
                            Workflow.function_name == function_name,
                            Workflow.solution_id.is_(None),
                        )
                        result = await self.db.execute(stmt)
                        existing_workflow = result.scalar_one_or_none()

                    if not existing_workflow:
                        # Not registered — skip. Use register_workflow() to register.
                        logger.debug(
                            f"Skipping unregistered function {log_safe(function_name)} in {log_safe(path)}"
                        )
                        continue

                    workflow_uuid = existing_workflow.id

                    # Get workflow name from decorator or function name
                    workflow_name = kwargs.get("name") or node.name

                    # Track description source: decorator kwarg vs docstring
                    decorator_description = kwargs.get("description")
                    docstring_description = None
                    if decorator_description is None:
                        docstring = ast.get_docstring(node)
                        if docstring:
                            docstring_description = docstring.strip().split("\n")[0].strip()

                    is_tool = kwargs.get("is_tool", False)
                    workflow_type = "tool" if is_tool else "workflow"
                    parameters_schema = self._extract_parameters_from_ast(node)

                    # Only update code-derived fields and valid decorator params.
                    # Operational settings (execution_mode, timeout_seconds,
                    # endpoint_enabled, allowed_methods, time_saved, value,
                    # tool_description) are API/UI-only — never set from code.
                    was_inactive = not existing_workflow.is_active

                    update_values: dict[str, Any] = {
                        "function_name": function_name,
                        "path": path,
                        "parameters_schema": parameters_schema,
                        "type": workflow_type,
                        "is_active": True,
                        "is_orphaned": False,
                        "last_seen_at": now,
                        "updated_at": now,
                    }

                    # name/description/category/tags: only set initial values
                    # (when DB field is NULL). YAML manifest and UI edits are
                    # the source of truth — the indexer never overwrites them.
                    if existing_workflow.name is None:
                        update_values["name"] = workflow_name
                    if existing_workflow.description is None:
                        initial_desc = decorator_description or docstring_description
                        if initial_desc:
                            update_values["description"] = initial_desc

                    if existing_workflow.category is None or existing_workflow.category == "General":
                        code_category = kwargs.get("category", "General")
                        if code_category != "General":
                            update_values["category"] = code_category

                    if not existing_workflow.tags:
                        tags_from_decorator = kwargs.get("tags")
                        if tags_from_decorator:
                            update_values["tags"] = tags_from_decorator

                    if was_inactive:
                        logger.info(f"Reactivating workflow: {log_safe(workflow_name)} ({log_safe(function_name)}) from {log_safe(path)}")

                    # Enrich existing record with content-derived fields
                    stmt = (
                        update(Workflow)
                        .where(Workflow.id == workflow_uuid)
                        .values(**update_values)
                    )
                    await self.db.execute(stmt)
                    logger.debug(f"Enriched workflow: {log_safe(workflow_name)} ({log_safe(function_name)}) from {log_safe(path)}")

                    # Re-fetch to get merged DB values (decorator + API settings)
                    result = await self.db.execute(
                        select(Workflow).where(Workflow.id == workflow_uuid)
                    )
                    workflow = result.scalar_one()

                    # Refresh endpoint registration if endpoint_enabled
                    if workflow.endpoint_enabled:
                        await self.refresh_workflow_endpoint(workflow)

                    # Update Redis caches with merged values from DB
                    try:
                        from src.core.redis_client import get_redis_client
                        redis_client = get_redis_client()
                        await redis_client.invalidate_endpoint_workflow_cache(str(workflow_uuid))
                        await redis_client.set_workflow_metadata_cache(
                            workflow_id=str(workflow_uuid),
                            name=workflow.name,
                            file_path=workflow.path,
                            timeout_seconds=workflow.timeout_seconds,
                            time_saved=workflow.time_saved,
                            value=workflow.value,
                            execution_mode=workflow.execution_mode,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to update caches for workflow {log_safe(workflow_name)}: {log_safe(e)}")

                elif decorator_name == "data_provider":
                    provider_name = kwargs.get("name") or node.name
                    function_name = node.name

                    # Look up existing data_provider — use prefetch cache if available
                    if self._prefetch_cache is not None:
                        existing_dp = self._prefetch_cache.get((path, function_name))
                    else:
                        # Include inactive for reactivation; _repo/-scoped — a
                        # solution data_provider at the same path is deploy-owned
                        # (Codex #14), and the bare query would otherwise raise
                        # MultipleResultsFound on a collision.
                        stmt = select(Workflow).where(
                            Workflow.path == path,
                            Workflow.function_name == function_name,
                            Workflow.solution_id.is_(None),
                        )
                        result = await self.db.execute(stmt)
                        existing_dp = result.scalar_one_or_none()

                    if not existing_dp:
                        logger.debug(
                            f"Skipping unregistered data_provider {log_safe(function_name)} in {log_safe(path)}"
                        )
                        continue

                    parameters_schema = self._extract_parameters_from_ast(node)

                    if not existing_dp.is_active:
                        logger.info(f"Reactivating data provider: {log_safe(provider_name)} ({log_safe(function_name)}) from {log_safe(path)}")

                    # Only update code-derived fields and valid decorator params.
                    # Operational settings (timeout_seconds, cache_ttl_seconds)
                    # are API/UI-only — never set from code.
                    dp_update_values: dict[str, Any] = {
                        "parameters_schema": parameters_schema,
                        "type": "data_provider",
                        "is_active": True,
                        "is_orphaned": False,
                        "last_seen_at": now,
                        "updated_at": now,
                    }

                    # name/description/category/tags: only set initial values
                    # (when DB field is NULL). YAML manifest and UI edits are
                    # the source of truth — the indexer never overwrites them.
                    if existing_dp.name is None:
                        dp_update_values["name"] = provider_name
                    if existing_dp.description is None:
                        desc = kwargs.get("description")
                        if desc:
                            dp_update_values["description"] = desc

                    if existing_dp.category is None or existing_dp.category == "General":
                        code_category = kwargs.get("category", "General")
                        if code_category != "General":
                            dp_update_values["category"] = code_category

                    if not existing_dp.tags:
                        tags_from_decorator = kwargs.get("tags")
                        if tags_from_decorator:
                            dp_update_values["tags"] = tags_from_decorator

                    stmt = (
                        update(Workflow)
                        .where(Workflow.id == existing_dp.id)
                        .values(**dp_update_values)
                    )
                    await self.db.execute(stmt)
                    logger.debug(f"Enriched data provider: {log_safe(provider_name)} ({log_safe(function_name)}) from {log_safe(path)}")

        # Note: workspace_files update removed — file_index is the sole search index.
        # Entity type/ID routing is handled by path conventions, not DB columns.

    async def refresh_workflow_endpoint(self, workflow: Workflow) -> None:
        """
        Refresh the dynamic endpoint registration for an endpoint-enabled workflow.

        This is called when a workflow with endpoint_enabled=True is indexed,
        allowing live updates to the OpenAPI spec without restarting the API.

        Args:
            workflow: The Workflow ORM model that was just indexed
        """
        try:
            from src.services.openapi_endpoints import refresh_workflow_endpoint
            from src.main import app

            refresh_workflow_endpoint(app, workflow)
            logger.info(f"Refreshed endpoint for workflow: {log_safe(workflow.name)}")
        except ImportError:
            # App not fully initialized yet (during startup)
            pass
        except Exception as e:
            # Log but don't fail the file write
            logger.warning(f"Failed to refresh endpoint for {log_safe(workflow.name)}: {log_safe(e)}")

    # ==================== AST PARSING HELPERS ====================

    def _parse_decorator(self, decorator: ast.AST) -> tuple[str, dict[str, Any]] | None:
        """
        Parse a decorator AST node to extract name and keyword arguments.

        Returns:
            Tuple of (decorator_name, kwargs_dict) or None if not a workflow/provider decorator
        """
        # Handle @workflow (no parentheses)
        if isinstance(decorator, ast.Name):
            if decorator.id in ("workflow", "tool", "data_provider"):
                return decorator.id, {}
            return None

        # Handle @workflow(...) (with parentheses)
        if isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Name):
                decorator_name = decorator.func.id
            elif isinstance(decorator.func, ast.Attribute):
                # Handle module.workflow (e.g., bifrost.workflow)
                decorator_name = decorator.func.attr
            else:
                return None

            if decorator_name not in ("workflow", "tool", "data_provider"):
                return None

            # Extract keyword arguments
            kwargs = {}
            for keyword in decorator.keywords:
                if keyword.arg:
                    value = self._ast_value_to_python(keyword.value)
                    if value is not None:
                        kwargs[keyword.arg] = value

            return decorator_name, kwargs

        return None

    def _ast_value_to_python(self, node: ast.AST) -> Any:
        """Convert an AST node to a Python value."""
        if isinstance(node, ast.Constant):
            return node.value
        elif isinstance(node, ast.List):
            return [self._ast_value_to_python(elt) for elt in node.elts]
        elif isinstance(node, ast.Dict):
            return {
                self._ast_value_to_python(k): self._ast_value_to_python(v)
                for k, v in zip(node.keys, node.values)
                if k is not None
            }
        elif isinstance(node, ast.Name):
            # Handle True, False, None
            if node.id == "True":
                return True
            elif node.id == "False":
                return False
            elif node.id == "None":
                return None
        return None

    def _extract_parameters_from_ast(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> dict[str, Any]:
        """Extract the complete JSON Schema for a function's inputs."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        args = func_node.args

        # Get defaults - they align with the end of the args list
        defaults = args.defaults
        num_defaults = len(defaults)
        num_args = len(args.args)

        for i, arg in enumerate(args.args):
            param_name = arg.arg

            # Skip 'self', 'cls', and context parameters
            if param_name in ("self", "cls", "context"):
                continue

            # Skip ExecutionContext parameter (by annotation)
            if arg.annotation:
                annotation_str = self._annotation_to_string(arg.annotation)
                if "ExecutionContext" in annotation_str:
                    continue

            # Determine if parameter has a default
            default_index = i - (num_args - num_defaults)
            has_default = default_index >= 0

            property_schema = (
                self._annotation_to_json_schema(arg.annotation)
                if arg.annotation
                else {}
            )
            label = re.sub(r"([a-z])([A-Z])", r"\1 \2", param_name.replace("_", " ")).title()
            property_schema["title"] = label

            if has_default:
                default_node = defaults[default_index]
                try:
                    property_schema["default"] = ast.literal_eval(default_node)
                except (ValueError, TypeError):
                    # Non-literal defaults cannot be represented by static JSON Schema.
                    pass

            properties[param_name] = property_schema
            if not has_default and not (
                arg.annotation
                and self._is_optional_annotation(arg.annotation)
            ):
                required.append(param_name)

        schema: dict[str, Any] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema

    def _annotation_to_string(self, annotation: ast.AST) -> str:
        """Convert annotation AST to string representation."""
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Constant):
            return str(annotation.value)
        elif isinstance(annotation, ast.Subscript):
            return f"{self._annotation_to_string(annotation.value)}[...]"
        elif isinstance(annotation, ast.Attribute):
            return f"{self._annotation_to_string(annotation.value)}.{annotation.attr}"
        elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            # Python 3.10+ union syntax: str | None
            left = self._annotation_to_string(annotation.left)
            right = self._annotation_to_string(annotation.right)
            return f"{left} | {right}"
        return ""

    @staticmethod
    def _annotation_base_name(annotation: ast.AST) -> str:
        if isinstance(annotation, ast.Name):
            return annotation.id
        if isinstance(annotation, ast.Attribute):
            return annotation.attr
        return ""

    @staticmethod
    def _json_type_for_literal(value: Any) -> str | None:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        return None

    def _annotation_to_json_schema(self, annotation: ast.AST) -> dict[str, Any]:
        """Convert a Python annotation AST into a nested JSON Schema."""
        primitive_types = {
            "str": "string",
            "int": "integer",
            "float": "number",
            "bool": "boolean",
            "None": "null",
            "NoneType": "null",
        }

        if isinstance(annotation, ast.Constant):
            if annotation.value is None:
                return {"type": "null"}
            return {}

        if isinstance(annotation, (ast.Name, ast.Attribute)):
            name = self._annotation_base_name(annotation)
            if name in primitive_types:
                return {"type": primitive_types[name]}
            if name in {"list", "List", "Sequence"}:
                return {"type": "array", "items": {}}
            if name in {"dict", "Dict", "Mapping"}:
                return {"type": "object", "additionalProperties": True}
            if name in {"Any", "object"}:
                return {}
            return {"type": "object"}

        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            return {
                "anyOf": [
                    self._annotation_to_json_schema(annotation.left),
                    self._annotation_to_json_schema(annotation.right),
                ]
            }

        if not isinstance(annotation, ast.Subscript):
            return {}

        base_name = self._annotation_base_name(annotation.value)
        slice_node = annotation.slice
        slice_items = (
            list(slice_node.elts)
            if isinstance(slice_node, ast.Tuple)
            else [slice_node]
        )

        if base_name in {"list", "List", "Sequence"}:
            item_schema = (
                self._annotation_to_json_schema(slice_items[0])
                if slice_items
                else {}
            )
            return {"type": "array", "items": item_schema}

        if base_name in {"dict", "Dict", "Mapping"}:
            value_schema = (
                self._annotation_to_json_schema(slice_items[1])
                if len(slice_items) > 1
                else {}
            )
            return {"type": "object", "additionalProperties": value_schema}

        if base_name == "Literal":
            values: list[Any] = []
            for item in slice_items:
                try:
                    values.append(ast.literal_eval(item))
                except (ValueError, TypeError):
                    continue
            schema: dict[str, Any] = {"enum": values}
            literal_types = {
                json_type
                for value in values
                if (json_type := self._json_type_for_literal(value)) is not None
            }
            if len(literal_types) == 1:
                schema["type"] = literal_types.pop()
            return schema

        if base_name == "Optional":
            inner = self._annotation_to_json_schema(slice_items[0])
            return {"anyOf": [inner, {"type": "null"}]}

        if base_name == "Union":
            return {
                "anyOf": [
                    self._annotation_to_json_schema(item)
                    for item in slice_items
                ]
            }

        if base_name == "Annotated" and slice_items:
            return self._annotation_to_json_schema(slice_items[0])

        if base_name in {"tuple", "Tuple"}:
            return {
                "type": "array",
                "prefixItems": [
                    self._annotation_to_json_schema(item)
                    for item in slice_items
                ],
                "minItems": len(slice_items),
                "maxItems": len(slice_items),
            }

        return {"type": "object"}

    def _is_optional_annotation(self, annotation: ast.AST) -> bool:
        """Check if annotation represents an optional type."""
        if isinstance(annotation, ast.Subscript):
            if self._annotation_base_name(annotation.value) == "Optional":
                return True
            if self._annotation_base_name(annotation.value) == "Union":
                items = (
                    annotation.slice.elts
                    if isinstance(annotation.slice, ast.Tuple)
                    else [annotation.slice]
                )
                return any(
                    self._annotation_to_string(item) == "None"
                    for item in items
                )

        elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            # Check for str | None pattern
            right_str = self._annotation_to_string(annotation.right)
            left_str = self._annotation_to_string(annotation.left)
            if right_str == "None" or left_str == "None":
                return True

        return False

    async def delete_workflows_for_file(self, path: str) -> int:
        """
        Soft-delete all workflows associated with a file.

        Uses UPDATE (is_active=False, is_orphaned=True) instead of DELETE to avoid
        deadlocks with concurrent INSERT...ON CONFLICT indexing operations.

        Called when a file is deleted to clean up workflow records from the database.

        Args:
            path: File path that was deleted

        Returns:
            Number of workflows soft-deleted
        """
        # Scope to _repo/ rows (solution_id IS NULL): deleting a WORKSPACE file
        # must never deactivate a solution-managed workflow that happens to share
        # the path — solution rows are written only by deploy (Codex #14).
        stmt = (
            update(Workflow)
            .where(
                Workflow.path == path,
                Workflow.is_active == True,  # noqa: E712
                Workflow.solution_id.is_(None),
            )
            .values(
                is_active=False,
                is_orphaned=True,
                updated_at=datetime.now(timezone.utc),
            )
        )
        result = await self.db.execute(stmt)
        count = result.rowcount if result.rowcount else 0

        if count > 0:
            logger.info(f"Soft-deleted {count} workflow(s) for deleted file: {log_safe(path)}")

        return count
