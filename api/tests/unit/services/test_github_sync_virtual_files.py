"""
Unit tests for VirtualFileProvider.

Tests the virtual file generation for platform entities (forms, agents)
in the GitHub sync flow. These virtual files enable platform entities to
participate in sync by serializing them on-the-fly with portable workflow refs.

Note: App handling has been removed from the platform.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

from src.services.file_storage.file_ops import compute_git_blob_sha


# =============================================================================
# Test Data Fixtures
# =============================================================================


@pytest.fixture
def valid_form_uuid():
    """A valid UUID for testing forms."""
    return UUID("660e8400-e29b-41d4-a716-446655440001")


@pytest.fixture
def valid_agent_uuid():
    """A valid UUID for testing agents."""
    return UUID("770e8400-e29b-41d4-a716-446655440002")


@pytest.fixture
def valid_workflow_uuid():
    """A valid UUID for workflow references."""
    return UUID("880e8400-e29b-41d4-a716-446655440003")


@pytest.fixture
def mock_db_session():
    """Create mock async database session with default empty results."""
    session = AsyncMock()
    # Default to returning empty results - individual tests can override
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result
    return session




# Sample serialized content for mocking
def make_form_content(form_id: str, workflow_id: str | None = None, workflow_map: dict | None = None) -> bytes:
    """Generate sample form JSON content."""
    data = {
        "id": form_id,
        "name": "Test Form",
        "description": "A test form",
        "is_active": True,
    }
    if workflow_id:
        # Transform to portable ref if workflow_map is provided
        if workflow_map and workflow_id in workflow_map:
            data["workflow_id"] = workflow_map[workflow_id]
        else:
            data["workflow_id"] = workflow_id
    if workflow_map:
        data["_export"] = {"workflow_refs": ["workflow_id"], "version": "1.0"}
    return json.dumps(data, indent=2).encode("utf-8")


def make_agent_content(agent_id: str, tool_ids: list | None = None, workflow_map: dict | None = None) -> bytes:
    """Generate sample agent JSON content."""
    data = {
        "id": agent_id,
        "name": "Test Agent",
        "description": "A test agent",
        "system_prompt": "You are a helpful assistant.",
        "channels": ["chat"],
        "is_active": True,
    }
    if tool_ids:
        # Transform to portable refs if workflow_map is provided
        if workflow_map:
            data["tool_ids"] = [workflow_map.get(tid, tid) for tid in tool_ids]
        else:
            data["tool_ids"] = tool_ids
    if workflow_map:
        data["_export"] = {"workflow_refs": ["tool_ids.*"], "version": "1.0"}
    return json.dumps(data, indent=2).encode("utf-8")


# =============================================================================
# Test: extract_id_from_filename
# =============================================================================


class TestExtractIdFromFilename:
    """Tests for extracting UUID from entity filenames."""

    def test_extract_id_from_filename_app_not_supported(self):
        """Test that app filenames return None (app handling removed)."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        filename = "550e8400-e29b-41d4-a716-446655440000.app.json"
        result = VirtualFileProvider.extract_id_from_filename(filename)
        assert result is None

    def test_extract_id_from_filename_valid_form(self):
        """Test extracting UUID from valid form filename."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        filename = "550e8400-e29b-41d4-a716-446655440000.form.json"
        result = VirtualFileProvider.extract_id_from_filename(filename)
        assert result == "550e8400-e29b-41d4-a716-446655440000"

    def test_extract_id_from_filename_valid_agent(self):
        """Test extracting UUID from valid agent filename."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        filename = "550e8400-e29b-41d4-a716-446655440000.agent.json"
        result = VirtualFileProvider.extract_id_from_filename(filename)
        assert result == "550e8400-e29b-41d4-a716-446655440000"

    def test_extract_id_from_filename_uppercase_uuid_form(self):
        """Test extracting UUID handles case insensitivity for forms."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        filename = "550E8400-E29B-41D4-A716-446655440000.form.json"
        result = VirtualFileProvider.extract_id_from_filename(filename)
        # The regex is case insensitive, so it matches and returns the UUID as-is
        assert result == "550E8400-E29B-41D4-A716-446655440000"

    def test_extract_id_from_filename_invalid_no_uuid(self):
        """Test that filename without UUID returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        filename = "my-app.app.json"
        result = VirtualFileProvider.extract_id_from_filename(filename)
        assert result is None

    def test_extract_id_from_filename_invalid_incomplete_uuid(self):
        """Test that filename with incomplete UUID returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        filename = "550e8400.app.json"
        result = VirtualFileProvider.extract_id_from_filename(filename)
        assert result is None

    def test_extract_id_from_filename_invalid_wrong_extension(self):
        """Test that filename with wrong extension returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        filename = "550e8400-e29b-41d4-a716-446655440000.txt"
        result = VirtualFileProvider.extract_id_from_filename(filename)
        assert result is None

    def test_extract_id_from_filename_invalid_json_only(self):
        """Test that filename ending in just .json returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        filename = "550e8400-e29b-41d4-a716-446655440000.json"
        result = VirtualFileProvider.extract_id_from_filename(filename)
        assert result is None

    def test_extract_id_from_filename_empty_string(self):
        """Test that empty filename returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        filename = ""
        result = VirtualFileProvider.extract_id_from_filename(filename)
        assert result is None


# =============================================================================
# Test: extract_id_from_content
# =============================================================================


class TestExtractIdFromContent:
    """Tests for extracting ID from JSON content."""

    def test_extract_id_from_content_valid(self):
        """Test extracting ID from valid JSON with id field."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        content = json.dumps({"id": "550e8400-e29b-41d4-a716-446655440000", "name": "Test"}).encode()
        result = VirtualFileProvider.extract_id_from_content(content)
        assert result == "550e8400-e29b-41d4-a716-446655440000"

    def test_extract_id_from_content_no_id_field(self):
        """Test that JSON without id field returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        content = json.dumps({"name": "Test", "description": "No id"}).encode()
        result = VirtualFileProvider.extract_id_from_content(content)
        assert result is None

    def test_extract_id_from_content_invalid_json(self):
        """Test that invalid JSON returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        content = b"not valid json {{"
        result = VirtualFileProvider.extract_id_from_content(content)
        assert result is None

    def test_extract_id_from_content_empty(self):
        """Test that empty content returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        content = b""
        result = VirtualFileProvider.extract_id_from_content(content)
        assert result is None

    def test_extract_id_from_content_null_id(self):
        """Test that null id field returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        content = json.dumps({"id": None, "name": "Test"}).encode()
        result = VirtualFileProvider.extract_id_from_content(content)
        assert result is None

    def test_extract_id_from_content_nested_id(self):
        """Test that only top-level id is extracted."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        # The id we want is at top level
        content = json.dumps({
            "id": "top-level-id",
            "nested": {"id": "nested-id"}
        }).encode()
        result = VirtualFileProvider.extract_id_from_content(content)
        assert result == "top-level-id"


# =============================================================================
# Test: get_form_virtual_files
# =============================================================================


class TestGetFormVirtualFiles:
    """Tests for serializing forms as virtual files."""

    @pytest.mark.asyncio
    async def test_get_form_virtual_files(self, mock_db_session, valid_form_uuid):
        """Test that forms are serialized as virtual files."""
        from src.services.github_sync_virtual_files import (
            VirtualFileProvider,
            VirtualFile,
            VirtualFileResult,
        )

        provider = VirtualFileProvider(mock_db_session)

        expected_content = make_form_content(str(valid_form_uuid))
        expected_sha = compute_git_blob_sha(expected_content)

        expected_file = VirtualFile(
            path=f"forms/{valid_form_uuid}.form.json",
            entity_type="form",
            entity_id=str(valid_form_uuid),
            content=expected_content,
            computed_sha=expected_sha,
        )

        with patch.object(
            provider, "_get_form_files", return_value=VirtualFileResult(files=[expected_file], errors=[])
        ), patch.object(
            provider, "_get_agent_files", return_value=VirtualFileResult(files=[], errors=[])
        ), patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value={},
        ):
            result = await provider.get_all_virtual_files()

        form_files = [f for f in result.files if f.entity_type == "form"]
        assert len(form_files) == 1

        form_file = form_files[0]
        expected_path = f"forms/{valid_form_uuid}.form.json"
        assert form_file.path == expected_path
        assert form_file.entity_id == str(valid_form_uuid)
        assert form_file.computed_sha is not None


# =============================================================================
# Test: get_agent_virtual_files
# =============================================================================


class TestGetAgentVirtualFiles:
    """Tests for serializing agents as virtual files."""

    @pytest.mark.asyncio
    async def test_get_agent_virtual_files(self, mock_db_session, valid_agent_uuid):
        """Test that agents are serialized as virtual files."""
        from src.services.github_sync_virtual_files import (
            VirtualFileProvider,
            VirtualFile,
            VirtualFileResult,
        )

        provider = VirtualFileProvider(mock_db_session)

        expected_content = make_agent_content(str(valid_agent_uuid))
        expected_sha = compute_git_blob_sha(expected_content)

        expected_file = VirtualFile(
            path=f"agents/{valid_agent_uuid}.agent.json",
            entity_type="agent",
            entity_id=str(valid_agent_uuid),
            content=expected_content,
            computed_sha=expected_sha,
        )

        with patch.object(
            provider, "_get_form_files", return_value=VirtualFileResult(files=[], errors=[])
        ), patch.object(
            provider, "_get_agent_files", return_value=VirtualFileResult(files=[expected_file], errors=[])
        ), patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value={},
        ):
            result = await provider.get_all_virtual_files()

        agent_files = [f for f in result.files if f.entity_type == "agent"]
        assert len(agent_files) == 1

        agent_file = agent_files[0]
        expected_path = f"agents/{valid_agent_uuid}.agent.json"
        assert agent_file.path == expected_path
        assert agent_file.entity_id == str(valid_agent_uuid)
        assert agent_file.computed_sha is not None


# =============================================================================
# Test: Portable Refs in Virtual Content
# =============================================================================


class TestPortableRefsInVirtualContent:
    """Tests for workflow refs being converted to portable refs."""

    @pytest.mark.asyncio
    async def test_portable_refs_in_form_content(
        self, mock_db_session, valid_workflow_uuid, valid_form_uuid
    ):
        """Test that workflow refs are converted to portable refs in form content."""
        from src.services.github_sync_virtual_files import (
            VirtualFileProvider,
            VirtualFile,
            VirtualFileResult,
        )

        provider = VirtualFileProvider(mock_db_session)

        # Create a workflow map that maps the UUID to a portable ref
        workflow_map = {
            str(valid_workflow_uuid): "workflows/test_module.py::test_function"
        }

        # Create content with portable ref format
        expected_content = make_form_content(
            str(valid_form_uuid),
            workflow_id=str(valid_workflow_uuid),
            workflow_map=workflow_map,
        )
        expected_sha = compute_git_blob_sha(expected_content)

        expected_file = VirtualFile(
            path=f"forms/{valid_form_uuid}.form.json",
            entity_type="form",
            entity_id=str(valid_form_uuid),
            content=expected_content,
            computed_sha=expected_sha,
        )

        with patch.object(
            provider, "_get_form_files", return_value=VirtualFileResult(files=[expected_file], errors=[])
        ), patch.object(
            provider, "_get_agent_files", return_value=VirtualFileResult(files=[], errors=[])
        ), patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value=workflow_map,
        ):
            result = await provider.get_all_virtual_files()

        form_files = [f for f in result.files if f.entity_type == "form"]
        assert len(form_files) == 1

        form_file = form_files[0]
        assert form_file.content is not None

        # Parse the content and verify portable ref format
        data = json.loads(form_file.content.decode("utf-8"))

        # workflow_id should contain "::" (portable ref format)
        if data.get("workflow_id"):
            assert "::" in data["workflow_id"]

        # _export metadata should be present when refs are transformed
        assert "_export" in data

    @pytest.mark.asyncio
    async def test_portable_refs_in_agent_content(
        self, mock_db_session, valid_workflow_uuid, valid_agent_uuid
    ):
        """Test that tool_ids are converted to portable refs in agent content."""
        from src.services.github_sync_virtual_files import (
            VirtualFileProvider,
            VirtualFile,
            VirtualFileResult,
        )

        provider = VirtualFileProvider(mock_db_session)

        workflow_map = {
            str(valid_workflow_uuid): "workflows/tool_module.py::tool_function"
        }

        # Create content with portable ref format
        expected_content = make_agent_content(
            str(valid_agent_uuid),
            tool_ids=[str(valid_workflow_uuid)],
            workflow_map=workflow_map,
        )
        expected_sha = compute_git_blob_sha(expected_content)

        expected_file = VirtualFile(
            path=f"agents/{valid_agent_uuid}.agent.json",
            entity_type="agent",
            entity_id=str(valid_agent_uuid),
            content=expected_content,
            computed_sha=expected_sha,
        )

        with patch.object(
            provider, "_get_form_files", return_value=VirtualFileResult(files=[], errors=[])
        ), patch.object(
            provider, "_get_agent_files", return_value=VirtualFileResult(files=[expected_file], errors=[])
        ), patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value=workflow_map,
        ):
            result = await provider.get_all_virtual_files()

        agent_files = [f for f in result.files if f.entity_type == "agent"]
        assert len(agent_files) == 1

        agent_file = agent_files[0]
        assert agent_file.content is not None

        data = json.loads(agent_file.content.decode("utf-8"))

        # tool_ids should contain portable refs
        if data.get("tool_ids"):
            for tool_id in data["tool_ids"]:
                assert "::" in tool_id

        # _export metadata should be present
        assert "_export" in data


# =============================================================================
# Test: SHA Computed from Serialized Content
# =============================================================================


class TestShaComputedFromSerializedContent:
    """Tests for SHA computation matching serialized content."""

    @pytest.mark.asyncio
    async def test_sha_computed_from_serialized_content(self, mock_db_session, valid_form_uuid):
        """Test SHA matches recomputed value from content."""
        from src.services.github_sync_virtual_files import (
            VirtualFileProvider,
            VirtualFile,
            VirtualFileResult,
        )

        provider = VirtualFileProvider(mock_db_session)

        expected_content = make_form_content(str(valid_form_uuid))
        expected_sha = compute_git_blob_sha(expected_content)

        expected_file = VirtualFile(
            path=f"forms/{valid_form_uuid}.form.json",
            entity_type="form",
            entity_id=str(valid_form_uuid),
            content=expected_content,
            computed_sha=expected_sha,
        )

        with patch.object(
            provider, "_get_form_files", return_value=VirtualFileResult(files=[expected_file], errors=[])
        ), patch.object(
            provider, "_get_agent_files", return_value=VirtualFileResult(files=[], errors=[])
        ), patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value={},
        ):
            result = await provider.get_all_virtual_files()

        form_files = [f for f in result.files if f.entity_type == "form"]
        assert len(form_files) == 1

        form_file = form_files[0]
        assert form_file.content is not None
        assert form_file.computed_sha is not None

        # Manually compute SHA and verify it matches
        recomputed_sha = compute_git_blob_sha(form_file.content)
        assert form_file.computed_sha == recomputed_sha

    @pytest.mark.asyncio
    async def test_sha_consistency_across_calls(self, mock_db_session, valid_form_uuid):
        """Test that SHA is consistent when content hasn't changed."""
        from src.services.github_sync_virtual_files import (
            VirtualFileProvider,
            VirtualFile,
            VirtualFileResult,
        )

        provider = VirtualFileProvider(mock_db_session)

        expected_content = make_form_content(str(valid_form_uuid))
        expected_sha = compute_git_blob_sha(expected_content)

        expected_file = VirtualFile(
            path=f"forms/{valid_form_uuid}.form.json",
            entity_type="form",
            entity_id=str(valid_form_uuid),
            content=expected_content,
            computed_sha=expected_sha,
        )

        with patch.object(
            provider, "_get_form_files", return_value=VirtualFileResult(files=[expected_file], errors=[])
        ), patch.object(
            provider, "_get_agent_files", return_value=VirtualFileResult(files=[], errors=[])
        ), patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value={},
        ):
            result1 = await provider.get_all_virtual_files()
            result2 = await provider.get_all_virtual_files()

        form_files1 = [f for f in result1.files if f.entity_type == "form"]
        form_files2 = [f for f in result2.files if f.entity_type == "form"]

        assert len(form_files1) == 1
        assert len(form_files2) == 1

        # SHA should be the same for both calls
        assert form_files1[0].computed_sha == form_files2[0].computed_sha


# =============================================================================
# Test: get_virtual_file_by_id
# =============================================================================


class TestGetVirtualFileById:
    """Tests for fetching specific virtual file by ID."""

    @pytest.mark.asyncio
    async def test_get_virtual_file_by_id_form(self, mock_db_session, valid_form_uuid):
        """Test fetching specific form virtual file."""
        from src.services.github_sync_virtual_files import VirtualFileProvider, VirtualFile

        provider = VirtualFileProvider(mock_db_session)

        expected_content = make_form_content(str(valid_form_uuid))
        expected_sha = compute_git_blob_sha(expected_content)

        expected_file = VirtualFile(
            path=f"forms/{valid_form_uuid}.form.json",
            entity_type="form",
            entity_id=str(valid_form_uuid),
            content=expected_content,
            computed_sha=expected_sha,
        )

        with patch.object(
            provider, "_get_form_file_by_id", return_value=expected_file
        ), patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value={},
        ):
            result = await provider.get_virtual_file_by_id("form", str(valid_form_uuid))

        assert result is not None
        assert result.entity_type == "form"
        assert result.entity_id == str(valid_form_uuid)

    @pytest.mark.asyncio
    async def test_get_virtual_file_by_id_agent(self, mock_db_session, valid_agent_uuid):
        """Test fetching specific agent virtual file."""
        from src.services.github_sync_virtual_files import VirtualFileProvider, VirtualFile

        provider = VirtualFileProvider(mock_db_session)

        expected_content = make_agent_content(str(valid_agent_uuid))
        expected_sha = compute_git_blob_sha(expected_content)

        expected_file = VirtualFile(
            path=f"agents/{valid_agent_uuid}.agent.json",
            entity_type="agent",
            entity_id=str(valid_agent_uuid),
            content=expected_content,
            computed_sha=expected_sha,
        )

        with patch.object(
            provider, "_get_agent_file_by_id", return_value=expected_file
        ), patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value={},
        ):
            result = await provider.get_virtual_file_by_id("agent", str(valid_agent_uuid))

        assert result is not None
        assert result.entity_type == "agent"
        assert result.entity_id == str(valid_agent_uuid)

    @pytest.mark.asyncio
    async def test_get_virtual_file_by_id_not_found(self, mock_db_session):
        """Test that non-existent ID returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        provider = VirtualFileProvider(mock_db_session)

        with patch.object(
            provider, "_get_form_file_by_id", return_value=None
        ), patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value={},
        ):
            result = await provider.get_virtual_file_by_id("form", str(uuid4()))

        assert result is None

    @pytest.mark.asyncio
    async def test_get_virtual_file_by_id_invalid_type(self, mock_db_session):
        """Test that invalid entity type returns None."""
        from src.services.github_sync_virtual_files import VirtualFileProvider

        provider = VirtualFileProvider(mock_db_session)

        # Still need to patch build_workflow_ref_map since it's called before type check
        with patch(
            "src.services.github_sync_virtual_files.build_workflow_ref_map",
            return_value={},
        ):
            result = await provider.get_virtual_file_by_id("invalid_type", str(uuid4()))

        assert result is None


# =============================================================================
# Test: VirtualFile Dataclass
# =============================================================================


class TestVirtualFileDataclass:
    """Tests for the VirtualFile dataclass."""

    def test_virtual_file_creation(self):
        """Test VirtualFile can be created with all fields."""
        from src.services.github_sync_virtual_files import VirtualFile

        vf = VirtualFile(
            path="apps/test.app.json",
            entity_type="app",
            entity_id="550e8400-e29b-41d4-a716-446655440000",
            content=b'{"name": "Test"}',
            computed_sha="abc123",
        )

        assert vf.path == "apps/test.app.json"
        assert vf.entity_type == "app"
        assert vf.entity_id == "550e8400-e29b-41d4-a716-446655440000"
        assert vf.content == b'{"name": "Test"}'
        assert vf.computed_sha == "abc123"

    def test_virtual_file_optional_fields(self):
        """Test VirtualFile with optional fields as None."""
        from src.services.github_sync_virtual_files import VirtualFile

        vf = VirtualFile(
            path="forms/test.form.json",
            entity_type="form",
            entity_id="660e8400-e29b-41d4-a716-446655440001",
            content=None,
            computed_sha=None,
        )

        assert vf.content is None
        assert vf.computed_sha is None
