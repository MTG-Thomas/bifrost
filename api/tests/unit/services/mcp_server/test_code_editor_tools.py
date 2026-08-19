"""
Unit tests for Code Editor MCP Tools.

Tests the precision editing tools:
- list_content: List files, optionally filtered by path prefix
- search_content: Regex search with context
- read_content_lines: Line range reading
- get_content: Full content read
- patch_content: Surgical edits
- replace_content: Full content write
- delete_content: Delete files

All files are accessed via their path in file_index / S3 _repo/ store.
No entity_type or app_id parameters -- everything is path-based.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
import importlib
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from src.services.mcp_server.server import MCPContext


def get_result_data(result: ToolResult) -> dict:
    """Extract structured data from a ToolResult."""
    return result.structured_content or {}


def get_result_text(result: ToolResult) -> str:
    """Extract display text from a ToolResult."""
    if not result.content:
        return ""
    texts = [block.text for block in result.content if isinstance(block, TextContent)]
    return "\n".join(texts)


def is_error_result(result: ToolResult) -> bool:
    """Check if a ToolResult represents an error."""
    if result.structured_content and "error" in result.structured_content:
        return True
    if result.content and isinstance(result.content, str) and result.content.startswith("Error:"):
        return True
    return False


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        return self


@pytest.fixture
def platform_admin_context() -> MCPContext:
    """Create an MCPContext for a platform admin user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=None,
        is_platform_admin=True,
        user_email="admin@platform.local",
        user_name="Platform Admin",
    )


@pytest.fixture
def org_user_context() -> MCPContext:
    """Create an MCPContext for a regular org user."""
    return MCPContext(
        user_id=uuid4(),
        org_id=uuid4(),
        is_platform_admin=False,
        user_email="user@org.local",
        user_name="Org User",
    )


@pytest.fixture(autouse=True)
def _legacy_workspace_authority():
    """Existing cases exercise the no-Live legacy path unless they opt in."""
    with (
        patch(
            "src.services.mcp_server.tools.code_editor._active_workspace_release_view",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.services.mcp_server.tools.code_editor.reject_release_governed_paths",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        yield


class TestListContent:
    """Tests for the list_content MCP tool."""

    @pytest.mark.asyncio
    async def test_list_workflows(self, platform_admin_context):
        """Should list workflow paths using path_prefix."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "workflows/sync_tickets.py",
                "workflows/sync_users.py",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
                path_prefix="workflows/",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert "files" in data
            assert len(data["files"]) == 2
            assert data["files"][0]["path"] == "workflows/sync_tickets.py"
            assert data["files"][1]["path"] == "workflows/sync_users.py"
            mock_repo.list.assert_called_once_with("workflows/")

    @pytest.mark.asyncio
    async def test_list_modules(self, platform_admin_context):
        """Should list module paths using path_prefix."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "modules/helpers.py",
                "modules/utils.py",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
                path_prefix="modules/",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert "files" in data
            assert len(data["files"]) == 2
            assert data["files"][0]["path"] == "modules/helpers.py"
            assert data["files"][1]["path"] == "modules/utils.py"

    @pytest.mark.asyncio
    async def test_list_app_files(self, platform_admin_context):
        """Should list app files using path_prefix with app slug."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "apps/test-app/components/Header.tsx",
                "apps/test-app/pages/index.tsx",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
                path_prefix="apps/test-app/",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert "files" in data
            assert len(data["files"]) == 2
            assert data["files"][0]["path"] == "apps/test-app/components/Header.tsx"
            assert data["files"][1]["path"] == "apps/test-app/pages/index.tsx"

    @pytest.mark.asyncio
    async def test_list_with_path_prefix(self, platform_admin_context):
        """Should filter by path_prefix when provided."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "workflows/sync_tickets.py",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
                path_prefix="workflows/",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_list_all_files(self, platform_admin_context):
        """Should list all files when no path_prefix given."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(return_value=[
                "apps/my-app/pages/index.tsx",
                "modules/helpers.py",
                "workflows/sync.py",
            ])
            mock_repo_cls.return_value = mock_repo

            result = await list_content(
                context=platform_admin_context,
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["count"] == 3
            mock_repo.list.assert_called_once_with("")

    @pytest.mark.asyncio
    async def test_list_content_denies_non_admin(self, org_user_context):
        """Path-based code editor tools are platform-admin only."""
        from src.services.mcp_server.tools.code_editor import list_content

        result = await list_content(
            context=org_user_context,
            path_prefix="workflows/",
        )

        assert is_error_result(result)
        data = get_result_data(result)
        assert "Platform administrator privileges are required" in data["error"]

    @pytest.mark.asyncio
    async def test_list_content_rejects_invalid_organization_id(self, platform_admin_context):
        """Should validate organization_id before touching storage."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            result = await list_content(
                context=platform_admin_context,
                organization_id="not-a-uuid",
            )

        assert is_error_result(result)
        assert "valid UUID" in get_result_data(result)["error"]
        mock_repo_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_content_reports_repo_storage_errors(self, platform_admin_context):
        """Should return a structured error if listing storage fails."""
        from src.services.mcp_server.tools.code_editor import list_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.list = AsyncMock(side_effect=RuntimeError("storage unavailable"))
            mock_repo_cls.return_value = mock_repo

            result = await list_content(context=platform_admin_context)

        assert is_error_result(result)
        assert "List failed: storage unavailable" in get_result_data(result)["error"]


class TestSearchContent:
    """Tests for the search_content MCP tool."""

    @pytest.mark.asyncio
    async def test_search_workflow_content(self, platform_admin_context):
        """Should find matches in file content with context."""
        from src.services.mcp_server.tools.code_editor import search_content

        code = '''from bifrost import workflow

@workflow(name="Sync Tickets")
async def sync_tickets(client_id: str) -> dict:
    """Sync tickets from HaloPSA."""
    return {"synced": True}
'''

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            # search_content does: select(FileIndex.path, FileIndex.content) -> result.all()
            mock_fi_result = MagicMock()
            mock_fi_row = MagicMock()
            mock_fi_row.path = "workflows/sync_tickets.py"
            mock_fi_row.content = code
            mock_fi_result.all.return_value = [mock_fi_row]
            mock_session.execute.return_value = mock_fi_result

            result = await search_content(
                context=platform_admin_context,
                pattern="async def",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert "matches" in data
            assert len(data["matches"]) == 1
            assert data["matches"][0]["line_number"] == 4
            assert "sync_tickets" in data["matches"][0]["match"]

    @pytest.mark.asyncio
    async def test_search_invalid_regex(self, platform_admin_context):
        """Should return error for invalid regex pattern."""
        from src.services.mcp_server.tools.code_editor import search_content

        result = await search_content(
            context=platform_admin_context,
            pattern="[invalid",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "Invalid regex" in data["error"]

    @pytest.mark.asyncio
    async def test_search_empty_pattern(self, platform_admin_context):
        """Should return error for empty pattern."""
        from src.services.mcp_server.tools.code_editor import search_content

        result = await search_content(
            context=platform_admin_context,
            pattern="",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_search_rejects_requested_org_outside_context(self, platform_admin_context):
        """Org-scoped admins must not search another organization."""
        from src.services.mcp_server.tools.code_editor import search_content

        platform_admin_context.org_id = uuid4()
        with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
            result = await search_content(
                context=platform_admin_context,
                pattern="needle",
                organization_id=str(uuid4()),
            )

        assert is_error_result(result)
        assert "not authorized" in get_result_data(result)["error"].lower()
        mock_db.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_honors_path_filter_and_max_results(self, platform_admin_context):
        """Should pass the path filter into the DB query and mark truncated max results."""
        from src.services.mcp_server.tools.code_editor import search_content

        rows = [
            SimpleNamespace(path="workflows/a.py", content="needle\nneedle"),
            SimpleNamespace(path="workflows/b.py", content="needle"),
        ]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=_RowsResult(rows))

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield mock_session

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db", fake_get_tool_db):
            result = await search_content(
                context=platform_admin_context,
                pattern="needle",
                path="workflows/a.py",
                max_results=1,
            )

        assert not is_error_result(result)
        data = get_result_data(result)
        assert data["total_matches"] == 1
        assert data["truncated"] is True
        assert data["matches"][0]["path"] == "workflows/a.py"
        executed_query = mock_session.execute.await_args.args[0]
        assert "workflows/a.py" in str(executed_query.compile(compile_kwargs={"literal_binds": True}))


class TestReadContentLines:
    """Tests for the read_content_lines MCP tool."""

    @pytest.mark.asyncio
    async def test_read_line_range(self, platform_admin_context):
        """Should read specific line range from a file."""
        from src.services.mcp_server.tools.code_editor import read_content_lines

        code = "line 1\nline 2\nline 3\nline 4\nline 5\nline 6\nline 7\nline 8\nline 9\nline 10"

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            result = await read_content_lines(
                context=platform_admin_context,
                path="workflows/sync.py",
                start_line=3,
                end_line=6,
            )

        assert isinstance(result, ToolResult)
        data = get_result_data(result)
        assert data["start_line"] == 3
        assert data["end_line"] == 6
        assert data["total_lines"] == 10
        assert "3: line 3" in data["content"]
        assert "6: line 6" in data["content"]
        assert "line 2" not in data["content"]

    @pytest.mark.asyncio
    async def test_read_requires_path(self, platform_admin_context):
        """Should return error if path not provided."""
        from src.services.mcp_server.tools.code_editor import read_content_lines

        result = await read_content_lines(
            context=platform_admin_context,
            path="",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "path" in data["error"]

    @pytest.mark.asyncio
    async def test_read_line_range_clamps_bounds_and_defaults_end(self, platform_admin_context):
        """Should normalize invalid start_line and cap the default end_line at EOF."""
        from src.services.mcp_server.tools.code_editor import read_content_lines

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value="first\nsecond\nthird",
        ):
            result = await read_content_lines(
                context=platform_admin_context,
                path="modules/helpers.py",
                start_line=-50,
                end_line=None,
            )

        assert not is_error_result(result)
        data = get_result_data(result)
        assert data["start_line"] == 1
        assert data["end_line"] == 3
        assert data["content"] == "1: first\n2: second\n3: third"


class TestGetContent:
    """Tests for the get_content MCP tool."""

    @pytest.mark.asyncio
    async def test_get_full_content(self, platform_admin_context):
        """Should return full file content with metadata."""
        from src.services.mcp_server.tools.code_editor import get_content

        code = "line 1\nline 2\nline 3"

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            result = await get_content(
                context=platform_admin_context,
                path="workflows/sync.py",
            )

        assert isinstance(result, ToolResult)
        data = get_result_data(result)
        assert data["path"] == "workflows/sync.py"
        assert data["total_lines"] == 3
        assert "line 1" in data["content"]
        assert "line 3" in data["content"]

    @pytest.mark.asyncio
    async def test_get_content_not_found(self, platform_admin_context):
        """Should return error if file not found."""
        from src.services.mcp_server.tools.code_editor import get_content

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await get_content(
                context=platform_admin_context,
                path="workflows/nonexistent.py",
            )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_get_content_denies_out_of_scope_workflow_path(self, platform_admin_context):
        """Org-scoped admins must not read workflow files owned by another org."""
        from src.services.mcp_server.tools.code_editor import get_content

        caller_org = uuid4()
        other_org = uuid4()
        platform_admin_context.org_id = caller_org
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_RowsResult([SimpleNamespace(organization_id=other_org)])
        )

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield mock_session

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db", fake_get_tool_db), patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value="secret workflow code",
        ) as mock_read:
            result = await get_content(
                context=platform_admin_context,
                path="workflows/other_org.py",
            )

        assert is_error_result(result)
        assert "not authorized" in get_result_data(result)["error"].lower()
        mock_read.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_get_content_truncates_large_files(self, platform_admin_context):
        """Should return a warning and truncated flag for oversized content."""
        code_editor = importlib.import_module("src.services.mcp_server.tools.code_editor")

        original_limit = code_editor.MAX_CONTENT_CHARS
        code_editor.MAX_CONTENT_CHARS = 12
        try:
            with patch(
                "src.services.mcp_server.tools.code_editor._read_from_s3",
                new_callable=AsyncMock,
                return_value="alpha\nbeta\ngamma\n",
            ):
                result = await code_editor.get_content(
                    context=platform_admin_context,
                    path="docs/large.md",
                )
        finally:
            code_editor.MAX_CONTENT_CHARS = original_limit

        assert not is_error_result(result)
        data = get_result_data(result)
        assert data["truncated"] is True
        assert "Content truncated" in data["warning"]
        assert data["content"] == "alpha\nbeta"


class TestReadFromS3:
    """Tests for direct S3 read helper behavior."""

    @pytest.mark.asyncio
    async def test_read_from_s3_decodes_utf8_bytes(self):
        """Should decode UTF-8 bytes returned by RepoStorage."""
        from src.services.mcp_server.tools.code_editor import _read_from_s3

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.read = AsyncMock(return_value="hello".encode())
            mock_repo_cls.return_value = mock_repo

            result = await _read_from_s3("docs/readme.md")

        assert result == "hello"
        mock_repo.read.assert_awaited_once_with("docs/readme.md")

    @pytest.mark.asyncio
    async def test_read_from_s3_returns_none_for_binary_or_storage_failure(self):
        """Should treat unreadable storage values as missing content."""
        from src.services.mcp_server.tools.code_editor import _read_from_s3

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.read = AsyncMock(return_value=b"\xff\xfe")
            mock_repo_cls.return_value = mock_repo

            binary_result = await _read_from_s3("assets/icon.bin")

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.read = AsyncMock(side_effect=FileNotFoundError("missing"))
            mock_repo_cls.return_value = mock_repo

            missing_result = await _read_from_s3("missing.py")

        assert binary_result is None
        assert missing_result is None


class TestReplaceWorkspaceFile:
    """Tests for the FileStorageService write boundary wrapper."""

    @pytest.mark.asyncio
    async def test_replace_workspace_file_updates_existing_file(self, platform_admin_context):
        """Should mark existing files as updated and pass encoded content to storage."""
        from src.services.mcp_server.tools.code_editor import _replace_workspace_file

        mock_session = AsyncMock()
        service = MagicMock()
        service.read_file = AsyncMock(return_value=b"old")
        service.write_file = AsyncMock(
            return_value=SimpleNamespace(pending_deactivations=[], available_replacements=None)
        )

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield mock_session

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db", fake_get_tool_db), patch(
            "src.services.mcp_server.tools.code_editor.FileStorageService",
            return_value=service,
        ) as mock_service_cls:
            result = await _replace_workspace_file(
                platform_admin_context,
                "modules/helpers.py",
                "print('new')",
                force_deactivation=True,
                replacements={"old-id": "new_name"},
            )

        assert result.created is False
        mock_service_cls.assert_called_once_with(mock_session)
        service.read_file.assert_awaited_once_with("modules/helpers.py")
        service.write_file.assert_awaited_once_with(
            path="modules/helpers.py",
            content=b"print('new')",
            updated_by="admin@platform.local",
            force_deactivation=True,
            replacements={"old-id": "new_name"},
        )

    @pytest.mark.asyncio
    async def test_replace_workspace_file_creates_missing_file_with_default_user(self, platform_admin_context):
        """Should mark missing files as created and fall back to mcp user label."""
        from src.services.mcp_server.tools.code_editor import _replace_workspace_file

        platform_admin_context.user_email = None
        service = MagicMock()
        service.read_file = AsyncMock(side_effect=FileNotFoundError("missing"))
        service.write_file = AsyncMock(
            return_value=SimpleNamespace(pending_deactivations=[], available_replacements=None)
        )

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield AsyncMock()

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db", fake_get_tool_db), patch(
            "src.services.mcp_server.tools.code_editor.FileStorageService",
            return_value=service,
        ):
            result = await _replace_workspace_file(platform_admin_context, "docs/new.md", "# New")

        assert result.created is True
        assert service.write_file.await_args.kwargs["updated_by"] == "mcp"

    @pytest.mark.asyncio
    async def test_replace_workspace_file_maps_pending_deactivations(self, platform_admin_context):
        """Should convert storage deactivation objects into structured dictionaries."""
        from src.services.mcp_server.tools.code_editor import _replace_workspace_file

        pending = SimpleNamespace(
            id="wf-1",
            name="Old Workflow",
            function_name="old_workflow",
            path="workflows/old.py",
            description="Old description",
            decorator_type="workflow",
            has_executions=True,
            last_execution_at="2026-07-01T12:00:00Z",
            endpoint_enabled=True,
            affected_entities=[{"entity_type": "form", "name": "Form", "reference_type": "workflow_id"}],
        )
        replacement = SimpleNamespace(
            function_name="new_workflow",
            name="New Workflow",
            decorator_type="workflow",
            similarity_score=0.87,
        )
        service = MagicMock()
        service.read_file = AsyncMock(return_value=b"old")
        service.write_file = AsyncMock(
            return_value=SimpleNamespace(
                pending_deactivations=[pending],
                available_replacements=[replacement],
            )
        )

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield AsyncMock()

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db", fake_get_tool_db), patch(
            "src.services.mcp_server.tools.code_editor.FileStorageService",
            return_value=service,
        ):
            result = await _replace_workspace_file(platform_admin_context, "workflows/old.py", "new")

        assert result.created is False
        assert result.pending_deactivations == [
            {
                "id": "wf-1",
                "name": "Old Workflow",
                "function_name": "old_workflow",
                "path": "workflows/old.py",
                "description": "Old description",
                "decorator_type": "workflow",
                "has_executions": True,
                "last_execution_at": "2026-07-01T12:00:00Z",
                "endpoint_enabled": True,
                "affected_entities": [{"entity_type": "form", "name": "Form", "reference_type": "workflow_id"}],
            }
        ]
        assert result.available_replacements == [
            {
                "function_name": "new_workflow",
                "name": "New Workflow",
                "decorator_type": "workflow",
                "similarity_score": 0.87,
            }
        ]


class TestPatchContent:
    """Tests for the patch_content MCP tool."""

    @pytest.mark.asyncio
    async def test_patch_unique_string(self, platform_admin_context):
        """Should replace unique string successfully."""
        from src.services.mcp_server.tools.code_editor import patch_content

        code = '''async def sync_tickets():
    return {"status": "old"}
'''

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            with patch(
                "src.services.mcp_server.tools.code_editor._replace_workspace_file",
                new_callable=AsyncMock,
            ) as mock_write:
                from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult
                mock_write.return_value = WorkspaceWriteResult(created=False)

                result = await patch_content(
                    context=platform_admin_context,
                    path="workflows/test.py",
                    old_string='return {"status": "old"}',
                    new_string='return {"status": "new"}',
                )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["success"] is True

    @pytest.mark.asyncio
    async def test_patch_non_unique_string_fails(self, platform_admin_context):
        """Should fail when old_string matches multiple locations."""
        from src.services.mcp_server.tools.code_editor import patch_content

        code = '''def func1():
    return "duplicate"

def func2():
    return "duplicate"
'''

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            result = await patch_content(
                context=platform_admin_context,
                path="workflows/sync.py",
                old_string='return "duplicate"',
                new_string='return "new_value"',
            )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "match_locations" in data or "matches" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_patch_string_not_found(self, platform_admin_context):
        """Should fail when old_string not found."""
        from src.services.mcp_server.tools.code_editor import patch_content

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value="some code here",
        ):
            result = await patch_content(
                context=platform_admin_context,
                path="workflows/sync.py",
                old_string="nonexistent string",
                new_string="replacement",
            )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "not found" in data["error"]

    @pytest.mark.asyncio
    async def test_patch_requires_old_string(self, platform_admin_context):
        """Should return error if old_string not provided."""
        from src.services.mcp_server.tools.code_editor import patch_content

        result = await patch_content(
            context=platform_admin_context,
            path="workflows/sync.py",
            old_string="",
            new_string="replacement",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "old_string" in data["error"]

    @pytest.mark.asyncio
    async def test_patch_content_denies_out_of_scope_workflow_path(self, platform_admin_context):
        """Org-scoped admins must not write files containing another org's workflows."""
        from src.services.mcp_server.tools.code_editor import patch_content

        platform_admin_context.org_id = uuid4()
        other_org = uuid4()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_RowsResult([SimpleNamespace(organization_id=other_org)])
        )

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield mock_session

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db", fake_get_tool_db), patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value='return {"status": "old"}',
        ), patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
        ) as mock_write:
            result = await patch_content(
                context=platform_admin_context,
                path="workflows/other_org.py",
                old_string='return {"status": "old"}',
                new_string='return {"status": "new"}',
            )

        assert is_error_result(result)
        assert "not authorized" in get_result_data(result)["error"].lower()
        mock_write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_patch_passes_normalized_content_and_deactivation_options(self, platform_admin_context):
        """Should normalize line endings and pass write options through to storage."""
        from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult, patch_content

        replacements = {str(uuid4()): "renamed_workflow"}
        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value="before\r\nold\r\nafter",
        ), patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
            return_value=WorkspaceWriteResult(created=False),
        ) as mock_write:
            result = await patch_content(
                context=platform_admin_context,
                path="workflows/test.py",
                old_string="old\r\n",
                new_string="new\r\n",
                force_deactivation=True,
                replacements=replacements,
            )

        assert not is_error_result(result)
        mock_write.assert_awaited_once_with(
            platform_admin_context,
            "workflows/test.py",
            "before\nnew\nafter",
            force_deactivation=True,
            replacements=replacements,
        )

    @pytest.mark.asyncio
    async def test_patch_returns_pending_deactivation_without_success(self, platform_admin_context):
        """Should surface deactivation protection details instead of reporting success."""
        from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult, patch_content

        pending = [
            {
                "function_name": "old_workflow",
                "decorator_type": "workflow",
                "has_executions": False,
                "last_execution_at": None,
                "endpoint_enabled": False,
                "affected_entities": [],
            }
        ]
        available = [{"function_name": "new_workflow", "name": "New", "decorator_type": "workflow", "similarity_score": 0.92}]

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value="old_workflow()",
        ), patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
            return_value=WorkspaceWriteResult(
                created=False,
                pending_deactivations=pending,
                available_replacements=available,
            ),
        ):
            result = await patch_content(
                context=platform_admin_context,
                path="workflows/test.py",
                old_string="old_workflow",
                new_string="new_workflow",
            )

        data = get_result_data(result)
        assert data["status"] == "pending_deactivations"
        assert data["pending_deactivations"] == pending
        assert data["available_replacements"] == available

    @pytest.mark.asyncio
    async def test_patch_reports_write_errors(self, platform_admin_context):
        """Should convert persistence errors into structured error results."""
        from src.services.mcp_server.tools.code_editor import patch_content

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value="old",
        ), patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
            side_effect=RuntimeError("write failed"),
        ):
            result = await patch_content(
                context=platform_admin_context,
                path="modules/test.py",
                old_string="old",
                new_string="new",
            )

        assert is_error_result(result)
        assert "Failed to save changes: write failed" in get_result_data(result)["error"]


class TestReplaceContent:
    """Tests for the replace_content MCP tool."""

    @pytest.mark.asyncio
    async def test_replace_existing_workflow(self, platform_admin_context):
        """Should replace entire file content."""
        from src.services.mcp_server.tools.code_editor import replace_content

        with patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
        ) as mock_write:
            from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult
            mock_write.return_value = WorkspaceWriteResult(created=False)

            result = await replace_content(
                context=platform_admin_context,
                path="workflows/test.py",
                content='''from bifrost import workflow

@workflow(name="Sync")
async def sync():
    return {"done": True}
''',
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["success"] is True
            assert data["path"] == "workflows/test.py"
            assert data["created"] is False

    @pytest.mark.asyncio
    async def test_replace_requires_content(self, platform_admin_context):
        """Should return error if content not provided."""
        from src.services.mcp_server.tools.code_editor import replace_content

        result = await replace_content(
            context=platform_admin_context,
            path="workflows/sync.py",
            content="",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "content" in data["error"]

    @pytest.mark.asyncio
    async def test_replace_requires_path(self, platform_admin_context):
        """Should return error if path not provided."""
        from src.services.mcp_server.tools.code_editor import replace_content

        result = await replace_content(
            context=platform_admin_context,
            path="",
            content="some content",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "path" in data["error"]

    @pytest.mark.asyncio
    async def test_replace_workflow_missing_decorator(self, platform_admin_context):
        """Should succeed when writing to workflow path without decorator (path-based, no validation)."""
        from src.services.mcp_server.tools.code_editor import replace_content

        with patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
        ) as mock_write:
            from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult
            mock_write.return_value = WorkspaceWriteResult(created=True)

            result = await replace_content(
                context=platform_admin_context,
                path="workflows/sync.py",
                content='''def regular_function():
    return {"done": True}
''',
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            # Path-based system no longer validates entity_type vs content
            assert data["success"] is True

    @pytest.mark.asyncio
    async def test_replace_app_file_creates_new(self, platform_admin_context):
        """Should create new app file using full path."""
        from src.services.mcp_server.tools.code_editor import replace_content

        with patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
        ) as mock_write:
            from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult
            mock_write.return_value = WorkspaceWriteResult(created=True)

            result = await replace_content(
                context=platform_admin_context,
                path="apps/test-app/pages/new.tsx",
                content="export default function NewComponent() { return <div>New</div>; }",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            assert data["success"] is True
            assert data["created"] is True
            assert data["path"] == "apps/test-app/pages/new.tsx"

    @pytest.mark.asyncio
    async def test_replace_normalizes_content_and_passes_write_options(self, platform_admin_context):
        """Should normalize line endings and forward deactivation controls."""
        from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult, replace_content

        replacements = {str(uuid4()): "renamed_workflow"}
        with patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
            return_value=WorkspaceWriteResult(created=False),
        ) as mock_write:
            result = await replace_content(
                context=platform_admin_context,
                path="workflows/test.py",
                content="line 1\r\nline 2\r",
                force_deactivation=True,
                replacements=replacements,
            )

        assert not is_error_result(result)
        mock_write.assert_awaited_once_with(
            platform_admin_context,
            "workflows/test.py",
            "line 1\nline 2\n",
            force_deactivation=True,
            replacements=replacements,
        )

    @pytest.mark.asyncio
    async def test_replace_returns_pending_deactivation_result(self, platform_admin_context):
        """Should surface workflow deactivation protection from storage."""
        from src.services.mcp_server.tools.code_editor import WorkspaceWriteResult, replace_content

        pending = [
            {
                "function_name": "removed_workflow",
                "decorator_type": "workflow",
                "has_executions": True,
                "last_execution_at": "2026-07-01T12:00:00Z",
                "endpoint_enabled": True,
                "affected_entities": [{"entity_type": "form", "name": "Form", "reference_type": "workflow_id"}],
            }
        ]

        with patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
            return_value=WorkspaceWriteResult(created=False, pending_deactivations=pending),
        ):
            result = await replace_content(
                context=platform_admin_context,
                path="workflows/test.py",
                content="def replacement(): pass",
            )

        data = get_result_data(result)
        assert data["status"] == "pending_deactivations"
        assert data["path"] == "workflows/test.py"
        assert data["pending_deactivations"] == pending

    @pytest.mark.asyncio
    async def test_replace_reports_storage_errors(self, platform_admin_context):
        """Should return storage exceptions as structured error responses."""
        from src.services.mcp_server.tools.code_editor import replace_content

        with patch(
            "src.services.mcp_server.tools.code_editor._replace_workspace_file",
            new_callable=AsyncMock,
            side_effect=RuntimeError("write denied"),
        ):
            result = await replace_content(
                context=platform_admin_context,
                path="modules/test.py",
                content="x = 1",
            )

        assert is_error_result(result)
        assert get_result_data(result)["error"] == "write denied"


class TestDeleteContent:
    """Tests for the delete_content MCP tool."""

    @pytest.mark.asyncio
    async def test_delete_workflow(self, platform_admin_context):
        """Should delete a workflow file via FileStorageService."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                with patch(
                    "src.services.mcp_server.tools.code_editor.FileStorageService"
                ) as mock_fs_cls:
                    mock_fs_instance = MagicMock()
                    mock_fs_instance.delete_file = AsyncMock()
                    mock_fs_cls.return_value = mock_fs_instance

                    result = await delete_content(
                        context=platform_admin_context,
                        path="workflows/test.py",
                    )

                    assert isinstance(result, ToolResult)
                    data = get_result_data(result)
                    assert data["success"] is True
                    assert data["path"] == "workflows/test.py"
                    mock_fs_instance.delete_file.assert_called_once_with("workflows/test.py")

    @pytest.mark.asyncio
    async def test_delete_module(self, platform_admin_context):
        """Should delete a module file via FileStorageService."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                with patch(
                    "src.services.mcp_server.tools.code_editor.FileStorageService"
                ) as mock_fs_cls:
                    mock_fs_instance = MagicMock()
                    mock_fs_instance.delete_file = AsyncMock()
                    mock_fs_cls.return_value = mock_fs_instance

                    result = await delete_content(
                        context=platform_admin_context,
                        path="modules/test.py",
                    )

                    assert isinstance(result, ToolResult)
                    data = get_result_data(result)
                    assert data["success"] is True
                    assert data["path"] == "modules/test.py"
                    mock_fs_instance.delete_file.assert_called_once_with("modules/test.py")

    @pytest.mark.asyncio
    async def test_delete_app_file(self, platform_admin_context):
        """Should delete an app file using full path."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                with patch(
                    "src.services.mcp_server.tools.code_editor.FileStorageService"
                ) as mock_fs_cls:
                    mock_fs_instance = MagicMock()
                    mock_fs_instance.delete_file = AsyncMock()
                    mock_fs_cls.return_value = mock_fs_instance

                    result = await delete_content(
                        context=platform_admin_context,
                        path="apps/test-app/pages/index.tsx",
                    )

                    assert isinstance(result, ToolResult)
                    data = get_result_data(result)
                    assert data["success"] is True
                    assert data["path"] == "apps/test-app/pages/index.tsx"
                    mock_fs_instance.delete_file.assert_called_once_with("apps/test-app/pages/index.tsx")

    @pytest.mark.asyncio
    async def test_delete_not_found(self, platform_admin_context):
        """Should return error if file not found."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=False)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                result = await delete_content(
                    context=platform_admin_context,
                    path="workflows/nonexistent.py",
                )

            assert isinstance(result, ToolResult)
            assert is_error_result(result)
            data = get_result_data(result)
            assert "not found" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_delete_requires_path(self, platform_admin_context):
        """Should return error if path not provided."""
        from src.services.mcp_server.tools.code_editor import delete_content

        result = await delete_content(
            context=platform_admin_context,
            path="",
        )

        assert isinstance(result, ToolResult)
        assert is_error_result(result)
        data = get_result_data(result)
        assert "error" in data
        assert "path" in data["error"]

    @pytest.mark.asyncio
    async def test_delete_content_denies_non_admin(self, org_user_context):
        """Non-admin users must not delete global workspace paths."""
        from src.services.mcp_server.tools.code_editor import delete_content

        result = await delete_content(
            context=org_user_context,
            path="workflows/test.py",
        )

        assert is_error_result(result)
        data = get_result_data(result)
        assert "Platform administrator privileges are required" in data["error"]

    @pytest.mark.asyncio
    async def test_delete_content_denies_out_of_scope_workflow_path(self, platform_admin_context):
        """Deleting a file must not deactivate workflows outside the caller's org scope."""
        from src.services.mcp_server.tools.code_editor import delete_content

        platform_admin_context.org_id = uuid4()
        other_org = uuid4()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=_RowsResult([SimpleNamespace(organization_id=other_org)])
        )

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield mock_session

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db", fake_get_tool_db), patch(
            "src.services.mcp_server.tools.code_editor.RepoStorage"
        ) as mock_repo_cls, patch(
            "src.services.mcp_server.tools.code_editor.FileStorageService"
        ) as mock_fs_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo
            mock_fs_instance = MagicMock()
            mock_fs_instance.delete_file = AsyncMock()
            mock_fs_cls.return_value = mock_fs_instance

            result = await delete_content(
                context=platform_admin_context,
                path="workflows/other_org.py",
            )

        assert is_error_result(result)
        assert "not authorized" in get_result_data(result)["error"].lower()
        mock_fs_instance.delete_file.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_reports_storage_errors(self, platform_admin_context):
        """Should convert delete failures into structured error results."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls, patch(
            "src.services.mcp_server.tools.code_editor.get_tool_db"
        ) as mock_db, patch("src.services.mcp_server.tools.code_editor.FileStorageService") as mock_fs_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session
            mock_fs_instance = MagicMock()
            mock_fs_instance.delete_file = AsyncMock(side_effect=RuntimeError("delete failed"))
            mock_fs_cls.return_value = mock_fs_instance

            result = await delete_content(
                context=platform_admin_context,
                path="modules/test.py",
            )

        assert is_error_result(result)
        assert get_result_data(result)["error"] == "delete failed"


class TestCodeEditorAuthorization:
    """All path-based code editor MCP tools are platform-admin only."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("tool_name", "args"),
        [
            ("list_content", ()),
            ("search_content", ("needle",)),
            ("read_content_lines", ("workflows/test.py",)),
            ("get_content", ("workflows/test.py",)),
            ("patch_content", ("workflows/test.py", "old", "new")),
            ("replace_content", ("workflows/test.py", "content")),
            ("delete_content", ("workflows/test.py",)),
        ],
    )
    async def test_non_admin_code_editor_tools_return_error(
        self,
        org_user_context,
        tool_name,
        args,
    ):
        code_editor = importlib.import_module("src.services.mcp_server.tools.code_editor")

        result = await getattr(code_editor, tool_name)(org_user_context, *args)

        assert is_error_result(result)
        data = get_result_data(result)
        assert "Platform administrator privileges are required" in data["error"]


class TestMultiFunctionWorkflows:
    """Tests for multi-function workflow file handling."""

    @pytest.mark.asyncio
    async def test_get_content_multi_function_file(self, platform_admin_context):
        """Should return content when reading a multi-function file."""
        from src.services.mcp_server.tools.code_editor import get_content

        code = '''from bifrost import workflow, tool

@workflow(name="Sync Tickets")
async def sync_tickets():
    return {"synced": True}

@tool(name="Get Ticket")
async def get_ticket(ticket_id: str):
    return {"id": ticket_id}
'''

        with patch(
            "src.services.mcp_server.tools.code_editor._read_from_s3",
            new_callable=AsyncMock,
            return_value=code,
        ):
            result = await get_content(
                context=platform_admin_context,
                path="workflows/multi.py",
            )

        assert isinstance(result, ToolResult)
        assert not is_error_result(result)
        data = get_result_data(result)
        assert data["path"] == "workflows/multi.py"
        assert "sync_tickets" in data["content"]
        assert "get_ticket" in data["content"]

    @pytest.mark.asyncio
    async def test_delete_multi_function_file(self, platform_admin_context):
        """Should delete a multi-function file via FileStorageService."""
        from src.services.mcp_server.tools.code_editor import delete_content

        with patch("src.services.mcp_server.tools.code_editor.RepoStorage") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.exists = AsyncMock(return_value=True)
            mock_repo_cls.return_value = mock_repo

            with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
                mock_session = AsyncMock()
                mock_db.return_value.__aenter__.return_value = mock_session

                with patch(
                    "src.services.mcp_server.tools.code_editor.FileStorageService"
                ) as mock_fs_cls:
                    mock_fs_instance = MagicMock()
                    mock_fs_instance.delete_file = AsyncMock()
                    mock_fs_cls.return_value = mock_fs_instance

                    result = await delete_content(
                        context=platform_admin_context,
                        path="workflows/multi.py",
                    )

                    assert isinstance(result, ToolResult)
                    data = get_result_data(result)
                    assert data["success"] is True
                    assert data["path"] == "workflows/multi.py"
                    mock_fs_instance.delete_file.assert_called_once_with("workflows/multi.py")

    @pytest.mark.asyncio
    async def test_search_deduplicates_multi_function_results(self, platform_admin_context):
        """Should not produce duplicate search results from multi-function files."""
        from src.services.mcp_server.tools.code_editor import search_content

        code = '''from bifrost import workflow

@workflow(name="Sync")
async def sync():
    return {"done": True}

@workflow(name="Cleanup")
async def cleanup():
    return {"done": True}
'''

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__.return_value = mock_session

            # search_content queries FileIndex directly, one row per file
            mock_fi_result = MagicMock()
            mock_fi_row = MagicMock()
            mock_fi_row.path = "workflows/multi.py"
            mock_fi_row.content = code
            mock_fi_result.all.return_value = [mock_fi_row]
            mock_session.execute.return_value = mock_fi_result

            result = await search_content(
                context=platform_admin_context,
                pattern="return",
            )

            assert isinstance(result, ToolResult)
            data = get_result_data(result)
            # Should have exactly 2 matches (one per "return" line), NOT 4
            assert data["total_matches"] == 2

    @pytest.mark.asyncio
    async def test_search_content_omits_out_of_scope_workflow_paths(self, platform_admin_context):
        """Search results must not leak workflow content from another org."""
        from src.services.mcp_server.tools.code_editor import search_content

        caller_org = uuid4()
        other_org = uuid4()
        platform_admin_context.org_id = caller_org
        allowed_row = SimpleNamespace(path="workflows/allowed.py", content="needle allowed")
        denied_row = SimpleNamespace(path="workflows/denied.py", content="needle denied")
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                _RowsResult([SimpleNamespace(path="workflows/denied.py", organization_id=other_org)]),
                _RowsResult([allowed_row, denied_row]),
            ]
        )

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield mock_session

        with patch("src.services.mcp_server.tools.code_editor.get_tool_db", fake_get_tool_db):
            result = await search_content(
                context=platform_admin_context,
                pattern="needle",
            )

        assert not is_error_result(result)
        matches = get_result_data(result)["matches"]
        assert [match["path"] for match in matches] == ["workflows/allowed.py"]


class TestFormatDeactivationResult:
    """Tests for _format_deactivation_result."""

    def _get_text(self, result: ToolResult) -> str:
        """Extract text from ToolResult content (handles TextContent list)."""
        content = result.content
        if isinstance(content, str):
            return content
        if isinstance(content, list) and len(content) > 0:
            return content[0].text
        return ""

    def test_format_without_schedule_key(self):
        """Should not crash when pending deactivation dict lacks 'schedule' key."""
        from src.services.mcp_server.tools.code_editor import _format_deactivation_result

        pending = [
            {
                "function_name": "sync_tickets",
                "decorator_type": "workflow",
                "has_executions": False,
                "last_execution_at": None,
                "endpoint_enabled": False,
                "affected_entities": [],
            }
        ]

        result = _format_deactivation_result(
            path="workflows/sync.py",
            pending_deactivations=pending,
            available_replacements=None,
        )

        assert isinstance(result, ToolResult)
        text = self._get_text(result)
        assert "sync_tickets" in text
        assert "workflow" in text

    def test_format_with_affected_entities(self):
        """Should list affected entities in deactivation result."""
        from src.services.mcp_server.tools.code_editor import _format_deactivation_result

        pending = [
            {
                "function_name": "sync_tickets",
                "decorator_type": "workflow",
                "has_executions": True,
                "last_execution_at": "2026-01-15T12:00:00Z",
                "endpoint_enabled": True,
                "affected_entities": [
                    {
                        "entity_type": "form",
                        "name": "Ticket Sync Form",
                        "reference_type": "workflow_id",
                    }
                ],
            }
        ]

        result = _format_deactivation_result(
            path="workflows/sync.py",
            pending_deactivations=pending,
            available_replacements=None,
        )

        text = self._get_text(result)
        assert "execution history" in text
        assert "API endpoint" in text
        assert "Ticket Sync Form" in text


class TestImmutableWorkspaceAuthority:
    """MCP code editing honors the same global Live authority as files APIs."""

    @staticmethod
    def _view(path: str, content: bytes):
        view = SimpleNamespace(
            release=SimpleNamespace(release_id="sha256:" + "a" * 64)
        )
        view.governs = lambda candidate: candidate == path
        view.read = AsyncMock(return_value=content)
        view.read_many = AsyncMock(return_value={path: content})
        view.list = AsyncMock(return_value=[path])
        return view

    @pytest.mark.asyncio
    async def test_get_content_reads_governed_bytes_from_global_live(
        self, platform_admin_context
    ):
        from src.services.mcp_server.tools.code_editor import get_content

        path = "modules/governed.py"
        view = self._view(path, b"live = True\n")
        with (
            patch(
                "src.services.mcp_server.tools.code_editor._active_workspace_release_view",
                new_callable=AsyncMock,
                return_value=view,
            ),
            patch(
                "src.services.mcp_server.tools.code_editor._read_from_s3",
                new_callable=AsyncMock,
                return_value="live = False\n",
            ) as repo_read,
        ):
            result = await get_content(platform_admin_context, path)

        assert get_result_data(result)["content"] == "live = True\n"
        view.read.assert_awaited_once_with(path)
        repo_read.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_listing_unions_governed_paths_and_marks_authority(
        self, platform_admin_context
    ):
        from src.services.mcp_server.tools.code_editor import list_content

        path = "modules/governed.py"
        view = self._view(path, b"live = True\n")
        repo = MagicMock()
        repo.list = AsyncMock(return_value=["modules/legacy.py"])
        with (
            patch(
                "src.services.mcp_server.tools.code_editor._active_workspace_release_view",
                new_callable=AsyncMock,
                return_value=view,
            ),
            patch(
                "src.services.mcp_server.tools.code_editor.RepoStorage",
                return_value=repo,
            ),
        ):
            result = await list_content(platform_admin_context, path_prefix="modules/")

        files = {row["path"]: row for row in get_result_data(result)["files"]}
        assert set(files) == {path, "modules/legacy.py"}
        assert files[path]["source_authority"] == "workspace-release-v1"
        assert files["modules/legacy.py"]["source_authority"] == "repo-v1"

    @pytest.mark.asyncio
    async def test_search_overlays_stale_index_content_with_immutable_live(
        self, platform_admin_context
    ):
        from src.services.mcp_server.tools.code_editor import search_content

        path = "modules/governed.py"
        view = self._view(path, b"authority = 'immutable-live'\n")
        session = AsyncMock()
        session.execute = AsyncMock(
            return_value=_RowsResult(
                [SimpleNamespace(path=path, content="authority = 'stale-repo'")]
            )
        )

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield session

        with (
            patch(
                "src.services.mcp_server.tools.code_editor._active_workspace_release_view",
                new_callable=AsyncMock,
                return_value=view,
            ),
            patch(
                "src.services.mcp_server.tools.code_editor.get_tool_db",
                fake_get_tool_db,
            ),
        ):
            result = await search_content(
                platform_admin_context, pattern="immutable-live", path=path
            )

        matches = get_result_data(result)["matches"]
        assert len(matches) == 1
        assert matches[0]["path"] == path
        view.read_many.assert_awaited_once_with([path])

    @pytest.mark.asyncio
    async def test_replace_rejects_governed_path_before_storage(
        self, platform_admin_context
    ):
        from src.services.mcp_server.tools.code_editor import replace_content
        from src.services.workspace_release_files import WorkspaceReleasePathGoverned

        path = "modules/governed.py"
        session = AsyncMock()

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield session

        guard = AsyncMock(side_effect=WorkspaceReleasePathGoverned(path, "release-1"))
        storage = MagicMock()
        with (
            patch(
                "src.services.mcp_server.tools.code_editor.get_tool_db",
                fake_get_tool_db,
            ),
            patch(
                "src.services.mcp_server.tools.code_editor.reject_release_governed_paths",
                guard,
            ),
            patch(
                "src.services.mcp_server.tools.code_editor.FileStorageService",
                return_value=storage,
            ),
        ):
            result = await replace_content(
                platform_admin_context, path, "replacement = True\n"
            )

        assert is_error_result(result)
        guard.assert_awaited_once_with(session, None, [path])
        assert not storage.method_calls

    @pytest.mark.asyncio
    async def test_delete_rejects_governed_path_before_repo_mutation(
        self, platform_admin_context
    ):
        from src.services.mcp_server.tools.code_editor import delete_content
        from src.services.workspace_release_files import WorkspaceReleasePathGoverned

        path = "modules/governed.py"
        session = AsyncMock()

        @asynccontextmanager
        async def fake_get_tool_db(_context):
            yield session

        guard = AsyncMock(side_effect=WorkspaceReleasePathGoverned(path, "release-1"))
        repo = MagicMock()
        with (
            patch(
                "src.services.mcp_server.tools.code_editor.get_tool_db",
                fake_get_tool_db,
            ),
            patch(
                "src.services.mcp_server.tools.code_editor.reject_release_governed_paths",
                guard,
            ),
            patch(
                "src.services.mcp_server.tools.code_editor.RepoStorage",
                return_value=repo,
            ),
        ):
            result = await delete_content(platform_admin_context, path)

        assert is_error_result(result)
        guard.assert_awaited_once_with(session, None, [path])
        assert not repo.method_calls


class TestToolRegistration:
    """Tests for code editor FastMCP tool registration."""

    def test_register_tools_registers_each_code_editor_tool(self):
        """Should register all declared code editor tools with context injection."""
        from src.services.mcp_server.tools import code_editor

        mcp = MagicMock()
        get_context_fn = MagicMock()

        with patch(
            "src.services.mcp_server.generators.fastmcp_generator.register_tool_with_context"
        ) as mock_register:
            code_editor.register_tools(mcp, get_context_fn)

        assert mock_register.call_count == len(code_editor.TOOLS)
        registered_ids = [call.args[2] for call in mock_register.call_args_list]
        assert registered_ids == [tool_id for tool_id, _name, _description in code_editor.TOOLS]
        assert all(call.args[0] is mcp for call in mock_register.call_args_list)
        assert all(call.args[4] is get_context_fn for call in mock_register.call_args_list)
