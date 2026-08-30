"""Unit tests for app code files router."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.routers import app_code_files
from src.routers.app_code_files import standalone_v2_runtime_contract, validate_file_path
from src.services.workspace_release_files import WorkspaceReleasePathGoverned


def _app():
    return SimpleNamespace(id=uuid4(), repo_prefix="apps/portal/", slug="portal")


def _ctx():
    return SimpleNamespace(db=object(), org_id=uuid4())


def _admin():
    return SimpleNamespace(is_platform_admin=True, email="admin@example.com")


def _release_view(*, governed: dict[str, bytes]):
    view = MagicMock()
    view.governs.side_effect = lambda path: path in governed
    view.list = AsyncMock(
        side_effect=lambda prefix="": sorted(
            path for path in governed if path.startswith(prefix)
        )
    )
    view.read = AsyncMock(side_effect=lambda path: governed[path])
    return view


@pytest.mark.asyncio
async def test_read_app_file_uses_verified_live_source_not_stale_repo(monkeypatch):
    app = _app()
    ctx = _ctx()
    path = "apps/portal/workflow.py"
    release_view = _release_view(governed={path: b"reviewed = True\n"})
    repo = MagicMock()
    repo.read = AsyncMock(return_value=b"stale = True\n")
    app_storage = MagicMock()
    app_storage.read_file = AsyncMock(side_effect=FileNotFoundError)

    monkeypatch.setattr(app_code_files, "get_application_or_404", AsyncMock(return_value=app))
    monkeypatch.setattr(
        app_code_files,
        "active_workspace_release_file_view",
        AsyncMock(return_value=release_view),
    )
    monkeypatch.setattr(app_code_files, "RepoStorage", lambda: repo)
    monkeypatch.setattr(app_code_files, "AppStorageService", lambda: app_storage)

    result = await app_code_files.read_app_file(
        app.id, "workflow.py", ctx=ctx, _user=object()
    )

    assert result.source == "reviewed = True\n"
    release_view.read.assert_awaited_once_with(path)
    repo.read.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_app_files_unions_live_paths_and_overlays_stale_repo(monkeypatch):
    app = _app()
    ctx = _ctx()
    governed_path = "apps/portal/workflow.py"
    release_view = _release_view(governed={governed_path: b"reviewed = True\n"})
    repo = MagicMock()
    repo.list = AsyncMock(return_value=["apps/portal/pages/index.tsx"])
    repo.read = AsyncMock(return_value=b"export default function Page() {}")
    app_storage = MagicMock()
    app_storage.read_file = AsyncMock(side_effect=FileNotFoundError)

    monkeypatch.setattr(app_code_files, "get_application_or_404", AsyncMock(return_value=app))
    monkeypatch.setattr(
        app_code_files,
        "active_workspace_release_file_view",
        AsyncMock(return_value=release_view),
    )
    monkeypatch.setattr(app_code_files, "RepoStorage", lambda: repo)
    monkeypatch.setattr(app_code_files, "AppStorageService", lambda: app_storage)

    result = await app_code_files.list_app_files(app.id, ctx=ctx, _user=object())

    assert result.total == 2
    by_path = {row.path: row.source for row in result.files}
    assert by_path["workflow.py"] == "reviewed = True\n"
    assert by_path["pages/index.tsx"] == "export default function Page() {}"
    release_view.read.assert_awaited_once_with(governed_path)


@pytest.mark.asyncio
async def test_delete_app_file_rejects_governed_path_before_storage(monkeypatch):
    app = _app()
    ctx = _ctx()
    storage = MagicMock()
    storage.delete_file = AsyncMock()
    guard = AsyncMock(
        side_effect=WorkspaceReleasePathGoverned(
            "apps/portal/workflow.py", "release-1"
        )
    )

    monkeypatch.setattr(app_code_files, "get_application_or_404", AsyncMock(return_value=app))
    monkeypatch.setattr(
        app_code_files, "assert_entity_id_not_solution_managed", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(app_code_files, "reject_release_governed_paths", guard)
    monkeypatch.setattr(app_code_files, "get_file_storage_service", lambda _db: storage)

    with pytest.raises(WorkspaceReleasePathGoverned):
        await app_code_files.delete_app_file(
            app.id, "workflow.py", ctx=ctx, user=_admin()
        )

    guard.assert_awaited_once_with(ctx.db, ctx.org_id, ["apps/portal/workflow.py"])
    storage.delete_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_app_file_rejects_governed_path_before_storage(monkeypatch):
    app = _app()
    ctx = _ctx()
    storage = MagicMock()
    storage.write_file = AsyncMock()
    guard = AsyncMock(
        side_effect=WorkspaceReleasePathGoverned(
            "apps/portal/pages/index.tsx", "release-1"
        )
    )

    monkeypatch.setattr(app_code_files, "get_application_or_404", AsyncMock(return_value=app))
    monkeypatch.setattr(
        app_code_files, "assert_entity_id_not_solution_managed", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(app_code_files, "reject_release_governed_paths", guard)
    monkeypatch.setattr(app_code_files, "get_file_storage_service", lambda _db: storage)

    with pytest.raises(WorkspaceReleasePathGoverned):
        await app_code_files.write_app_file(
            app_code_files.AppFileUpdate(source="export default function Page() {}"),
            app.id,
            "pages/index.tsx",
            ctx=ctx,
            user=_admin(),
        )

    guard.assert_awaited_once_with(
        ctx.db, ctx.org_id, ["apps/portal/pages/index.tsx"]
    )
    storage.write_file.assert_not_awaited()


@pytest.mark.parametrize(
    "meta",
    [
        '<meta name="bifrost-app-runtime" content="mount-v1">',
        "<META content='mount-v1' data-x='1' name='bifrost-app-runtime'>",
    ],
)
def test_standalone_v2_runtime_contract_detects_mount_marker(meta: str):
    assert standalone_v2_runtime_contract(f"<html><head>{meta}</head></html>") == "mount-v1"


@pytest.mark.parametrize(
    "html",
    [
        "<html><head></head></html>",
        '<meta name="bifrost-app-runtime" content="future-v2">',
        '<meta name="something-else" content="mount-v1">',
    ],
)
def test_standalone_v2_runtime_contract_rejects_missing_or_unknown_marker(html: str):
    assert standalone_v2_runtime_contract(html) is None


class TestValidateFilePath:
    """Tests for the validate_file_path function."""

    # ==========================================================================
    # Valid paths
    # ==========================================================================

    def test_valid_root_layout(self):
        """Root _layout.tsx is valid."""
        validate_file_path("_layout.tsx")

    def test_valid_root_providers(self):
        """Root _providers.tsx is valid."""
        validate_file_path("_providers.tsx")

    def test_valid_pages_index(self):
        """pages/index.tsx is valid."""
        validate_file_path("pages/index.tsx")

    def test_valid_pages_layout(self):
        """pages/_layout.tsx is valid."""
        validate_file_path("pages/_layout.tsx")

    def test_valid_pages_nested(self):
        """Nested pages are valid."""
        validate_file_path("pages/clients/index.tsx")
        validate_file_path("pages/clients/_layout.tsx")

    def test_valid_pages_dynamic(self):
        """Dynamic route segments in pages are valid."""
        validate_file_path("pages/clients/[id].tsx")
        validate_file_path("pages/clients/[id]/edit.tsx")

    def test_valid_components_file(self):
        """Component files are valid."""
        validate_file_path("components/Button.tsx")
        validate_file_path("components/ClientCard.tsx")

    def test_valid_components_nested(self):
        """Nested component folders are valid."""
        validate_file_path("components/ui/Button.tsx")
        validate_file_path("components/forms/ClientForm.tsx")

    def test_valid_modules_file(self):
        """Module files are valid."""
        validate_file_path("modules/api.ts")
        validate_file_path("modules/utils.ts")

    def test_valid_modules_nested(self):
        """Nested module folders are valid."""
        validate_file_path("modules/services/api.ts")
        validate_file_path("modules/hooks/useAuth.ts")

    def test_valid_path_with_underscores(self):
        """Paths with underscores are valid."""
        validate_file_path("components/my_component.tsx")
        validate_file_path("modules/api_client.ts")

    def test_valid_path_with_hyphens(self):
        """Paths with hyphens are valid."""
        validate_file_path("components/my-component.tsx")
        validate_file_path("modules/api-client.ts")

    # ==========================================================================
    # Invalid paths - empty/malformed
    # ==========================================================================

    def test_invalid_empty_path(self):
        """Empty path is rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("")
        assert exc_info.value.status_code == 400
        assert "cannot be empty" in exc_info.value.detail

    def test_invalid_double_slashes(self):
        """Double slashes are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("pages//index")
        assert exc_info.value.status_code == 400
        assert "empty segments" in exc_info.value.detail

    # ==========================================================================
    # Invalid paths - root level
    # ==========================================================================

    def test_invalid_root_arbitrary_file(self):
        """Arbitrary files at root are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("main.tsx")
        assert exc_info.value.status_code == 400
        assert "_layout" in exc_info.value.detail
        assert "_providers" in exc_info.value.detail

    def test_invalid_root_index(self):
        """index at root is rejected (must be in pages/)."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("index.tsx")
        assert exc_info.value.status_code == 400

    # ==========================================================================
    # Invalid paths - wrong top directory
    # ==========================================================================

    def test_invalid_top_dir(self):
        """Invalid top-level directories are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("services/api.ts")
        assert exc_info.value.status_code == 400
        assert "pages" in exc_info.value.detail
        assert "components" in exc_info.value.detail
        assert "modules" in exc_info.value.detail

    def test_invalid_top_dir_utils(self):
        """utils/ directory is rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("utils/helpers.ts")
        assert exc_info.value.status_code == 400

    # ==========================================================================
    # Invalid paths - dynamic segments outside pages/
    # ==========================================================================

    def test_invalid_dynamic_in_components(self):
        """Dynamic segments in components/ are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("components/[id].tsx")
        assert exc_info.value.status_code == 400
        assert "Dynamic segments" in exc_info.value.detail
        assert "pages/" in exc_info.value.detail

    def test_invalid_dynamic_in_modules(self):
        """Dynamic segments in modules/ are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("modules/[id]/utils.ts")
        assert exc_info.value.status_code == 400
        assert "Dynamic segments" in exc_info.value.detail

    # ==========================================================================
    # Invalid paths - _layout outside pages/
    # ==========================================================================

    def test_invalid_layout_in_components(self):
        """_layout in components/ is rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("components/_layout.tsx")
        assert exc_info.value.status_code == 400
        assert "_layout" in exc_info.value.detail
        assert "pages/" in exc_info.value.detail

    def test_invalid_layout_in_modules(self):
        """_layout in modules/ is rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("modules/_layout.tsx")
        assert exc_info.value.status_code == 400

    # ==========================================================================
    # Invalid paths - special characters
    # ==========================================================================

    def test_invalid_special_chars(self):
        """Paths with special characters are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("components/my.component.tsx")
        assert exc_info.value.status_code == 400
        assert "Invalid" in exc_info.value.detail

    def test_invalid_spaces(self):
        """Paths with spaces are rejected."""
        with pytest.raises(HTTPException) as exc_info:
            validate_file_path("components/my component.tsx")
        assert exc_info.value.status_code == 400
        assert "Invalid" in exc_info.value.detail

    # ==========================================================================
    # Edge cases
    # ==========================================================================

    def test_strips_leading_slash(self):
        """Leading slashes are stripped."""
        validate_file_path("/pages/index.tsx")

    def test_strips_trailing_slash(self):
        """Trailing slashes are stripped."""
        validate_file_path("pages/index.tsx/")

    def test_valid_deeply_nested(self):
        """Deeply nested paths are valid."""
        validate_file_path("components/ui/forms/fields/TextInput.tsx")
        validate_file_path("modules/services/auth/providers/oauth.ts")
        validate_file_path("pages/admin/users/[id]/settings/profile.tsx")


class TestGetV2DistAsset:
    """get_v2_dist_asset must only 404 on a genuinely missing key — real
    storage errors must be logged and re-raised, not swallowed as 404."""

    @staticmethod
    def _setup(monkeypatch, read_dist_exc: Exception):
        from types import SimpleNamespace
        from uuid import uuid4

        app_id = uuid4()
        fake_app = SimpleNamespace(id=app_id)

        async def _fake_get_app(ctx, _app_id):
            return fake_app

        monkeypatch.setattr(
            "src.routers.app_code_files.get_application_or_404", _fake_get_app
        )

        class _FakeBuilder:
            async def read_dist(self, _app_id, _rel):
                raise read_dist_exc

        monkeypatch.setattr(
            "src.services.solutions.app_build.SolutionAppBuilder", _FakeBuilder
        )
        return app_id

    async def test_missing_key_returns_404(self, monkeypatch):
        """The not-found type read_dist actually raises (botocore ClientError
        with Code=NoSuchKey, from get_object) still maps to 404."""
        from botocore.exceptions import ClientError

        from src.routers.app_code_files import get_v2_dist_asset

        not_found = ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
            "GetObject",
        )
        app_id = self._setup(monkeypatch, not_found)

        with pytest.raises(HTTPException) as exc_info:
            await get_v2_dist_asset(app_id=app_id, path="index.html", ctx=None, _user=None)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "dist asset not found: index.html"

    async def test_storage_error_is_logged_and_reraised_not_404(self, monkeypatch, caplog):
        """A real storage failure (RuntimeError) must NOT become a 404 — it
        surfaces as-is and is logged."""
        from src.routers.app_code_files import get_v2_dist_asset

        app_id = self._setup(monkeypatch, RuntimeError("s3 exploded"))

        with caplog.at_level("ERROR", logger="src.routers.app_code_files"):
            with pytest.raises(RuntimeError, match="s3 exploded"):
                await get_v2_dist_asset(app_id=app_id, path="index.html", ctx=None, _user=None)
        assert any("dist asset read failed" in r.message for r in caplog.records)

    async def test_non_notfound_client_error_is_logged_and_reraised(self, monkeypatch, caplog):
        """A ClientError that is NOT NoSuchKey (e.g. AccessDenied) is a real
        storage error, not a missing asset."""
        from botocore.exceptions import ClientError

        from src.routers.app_code_files import get_v2_dist_asset

        denied = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "denied"}}, "GetObject"
        )
        app_id = self._setup(monkeypatch, denied)

        with caplog.at_level("ERROR", logger="src.routers.app_code_files"):
            with pytest.raises(ClientError):
                await get_v2_dist_asset(app_id=app_id, path="main.js", ctx=None, _user=None)
        assert any("dist asset read failed" in r.message for r in caplog.records)
