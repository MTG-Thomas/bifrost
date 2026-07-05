"""Unit tests for pure FileOperationsService branches."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.file_storage.file_ops import compute_git_blob_sha


class _AsyncClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _S3ClientFactory:
    def __init__(self, client):
        self.client = client

    def get_client(self):
        return _AsyncClientContext(self.client)


def _service(**attrs):
    service = SimpleNamespace(settings=SimpleNamespace())
    for name, value in attrs.items():
        setattr(service, name, value)
    return service


def test_compute_git_blob_sha_matches_git_blob_format():
    assert compute_git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_file_operations_constructor_wires_collaborators():
    from src.services.file_storage.file_ops import FileOperationsService

    service = FileOperationsService(
        db="db",
        settings="settings",
        s3_client="s3",
        diagnostics="diagnostics",
        deactivation="deactivation",
        file_hash_fn="hash",
        content_type_fn="content-type",
        platform_entity_detector_fn="detector",
        extract_metadata_fn="extract",
        remove_metadata_fn="remove",
    )

    assert service.db == "db"
    assert service.settings == "settings"
    assert service._s3_client == "s3"
    assert service._diagnostics == "diagnostics"
    assert service._deactivation == "deactivation"
    assert service._compute_hash == "hash"
    assert service._guess_content_type == "content-type"
    assert service._detect_platform_entity_type == "detector"
    assert service._extract_metadata == "extract"
    assert service._remove_metadata == "remove"


class TestReadFileModuleCacheGuard:
    """Verify get_module() is only called for .py files."""

    @pytest.mark.asyncio
    async def test_read_py_file_checks_module_cache(self):
        """Python files should check Redis module cache."""
        with patch("src.core.module_cache.get_module", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = {"content": "print('hello')", "hash": "abc123"}

            from src.services.file_storage.file_ops import FileOperationsService

            service = MagicMock(spec=FileOperationsService)
            service.read_file = FileOperationsService.read_file.__get__(service)

            result = await service.read_file("workflows/test.py")

            mock_get.assert_called_once_with("workflows/test.py")
            assert result[0] == b"print('hello')"

    @pytest.mark.asyncio
    async def test_read_tsx_file_skips_module_cache(self):
        """Non-Python files should NOT check Redis module cache."""
        with patch("src.core.module_cache.get_module", new_callable=AsyncMock) as mock_get:
            # Set up S3 fallback
            mock_s3_client = AsyncMock()
            mock_response = {"Body": AsyncMock()}
            mock_response["Body"].read = AsyncMock(return_value=b"<div>hello</div>")
            mock_s3_client.get_object = AsyncMock(return_value=mock_response)

            from src.services.file_storage.file_ops import FileOperationsService

            service = MagicMock()
            service._s3_client.get_client.return_value.__aenter__ = AsyncMock(return_value=mock_s3_client)
            service._s3_client.get_client.return_value.__aexit__ = AsyncMock()
            service.settings.s3_bucket = "test"
            service.read_file = FileOperationsService.read_file.__get__(service)

            await service.read_file("apps/myapp/pages/index.tsx")

            mock_get.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_py_file_falls_back_to_s3_when_module_cache_empty(self):
        """Python files should still read durable S3 content on a cache miss."""
        with patch("src.core.module_cache.get_module", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            mock_s3_client = MagicMock()
            mock_response = {"Body": MagicMock()}
            mock_response["Body"].read = AsyncMock(return_value=b"from workflows import run")
            mock_s3_client.get_object = AsyncMock(return_value=mock_response)

            from src.services.file_storage.file_ops import FileOperationsService

            service = _service(_s3_client=_S3ClientFactory(mock_s3_client))
            service.settings.s3_bucket = "workspace-bucket"
            service.read_file = FileOperationsService.read_file.__get__(service)

            content, metadata = await service.read_file("workflows/cache_miss.py")

            mock_get.assert_called_once_with("workflows/cache_miss.py")
            mock_s3_client.get_object.assert_awaited_once_with(
                Bucket="workspace-bucket",
                Key="_repo/workflows/cache_miss.py",
            )
            assert content == b"from workflows import run"
            assert metadata is None

    @pytest.mark.asyncio
    async def test_read_file_translates_s3_no_such_key(self):
        """S3 NoSuchKey should surface as a path-specific FileNotFoundError."""
        class NoSuchKey(Exception):
            pass

        mock_s3_client = MagicMock()
        mock_s3_client.exceptions.NoSuchKey = NoSuchKey
        mock_s3_client.get_object = AsyncMock(side_effect=NoSuchKey("missing"))

        from src.services.file_storage.file_ops import FileOperationsService

        service = _service(_s3_client=_S3ClientFactory(mock_s3_client))
        service.settings.s3_bucket = "workspace-bucket"
        service.read_file = FileOperationsService.read_file.__get__(service)

        with pytest.raises(FileNotFoundError, match="File not found: docs/missing.md"):
            await service.read_file("docs/missing.md")

        mock_s3_client.get_object.assert_awaited_once_with(
            Bucket="workspace-bucket",
            Key="_repo/docs/missing.md",
        )


class TestWriteFilePathValidation:
    """Verify write_file exits before storage for excluded paths."""

    @pytest.mark.asyncio
    async def test_write_file_rejects_excluded_path_before_hashing_or_storage(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = _service(
            _compute_hash=MagicMock(side_effect=AssertionError("hash should not run")),
            _s3_client=MagicMock(),
        )
        service.write_file = FileOperationsService.write_file.__get__(service)

        with patch("src.services.editor.file_filter.is_excluded_path", return_value=True):
            with pytest.raises(ValueError, match="Path is excluded from workspace: __pycache__/x.pyc"):
                await service.write_file("__pycache__/x.pyc", b"compiled")


class TestWriteFileBehavior:
    """Verify write_file storage, indexing, diagnostics, and notifications."""

    @pytest.mark.asyncio
    async def test_write_file_stores_content_indexes_and_publishes_activity(self):
        from src.services.file_storage.file_ops import FileOperationsService

        s3 = MagicMock()
        s3.put_object = AsyncMock()
        db = MagicMock()
        db.execute = AsyncMock()
        db.flush = AsyncMock()
        diagnostics = MagicMock()
        diagnostics.clear_diagnostic_notification = AsyncMock()
        diagnostics.scan_for_sdk_issues = AsyncMock()
        extract_metadata = AsyncMock(
            return_value=(b"# docs\n", False, False, [], [], [], {}),
        )

        service = _service(
            db=db,
            settings=SimpleNamespace(s3_bucket="workspace-bucket"),
            _s3_client=_S3ClientFactory(s3),
            _diagnostics=diagnostics,
            _compute_hash=MagicMock(return_value="sha256"),
            _guess_content_type=MagicMock(return_value="text/markdown"),
            _extract_metadata=extract_metadata,
        )
        service._find_app_by_path = AsyncMock(return_value=None)
        service.write_file = FileOperationsService.write_file.__get__(service)

        with patch("src.services.editor.file_filter.is_excluded_path", return_value=False):
            with patch("src.core.pubsub.publish_file_activity", new_callable=AsyncMock) as publish:
                with patch("src.core.request_context.get_request_user", return_value=None):
                    with patch("src.core.request_context.get_request_session_id", return_value="session-1"):
                        result = await service.write_file(
                            "docs/readme.md",
                            b"# docs\n",
                            updated_by="alice",
                            skip_dirty_flag=True,
                        )

        s3.put_object.assert_awaited_once_with(
            Bucket="workspace-bucket",
            Key="_repo/docs/readme.md",
            Body=b"# docs\n",
            ContentType="text/markdown",
        )
        assert db.execute.await_count == 1
        db.flush.assert_awaited_once()
        extract_metadata.assert_awaited_once_with(
            "docs/readme.md",
            b"# docs\n",
            False,
            None,
            cached_ast=None,
            cached_content_str=None,
            workflows_to_deactivate=None,
        )
        diagnostics.clear_diagnostic_notification.assert_awaited_once_with("docs/readme.md")
        publish.assert_awaited_once_with(
            user_id="alice",
            user_name="alice",
            activity_type="file_push",
            paths=["docs/readme.md"],
            session_id="session-1",
        )
        assert result.final_content == b"# docs\n"
        assert result.diagnostics is None

    @pytest.mark.asyncio
    async def test_write_python_file_returns_pending_deactivations_before_notifications(self):
        from src.services.file_storage.file_ops import FileOperationsService

        s3 = MagicMock()
        s3.put_object = AsyncMock()
        db = MagicMock()
        db.execute = AsyncMock()
        db.flush = AsyncMock()
        diagnostics = MagicMock()
        diagnostics.scan_for_sdk_issues = AsyncMock()
        pending = [{"workflow_id": "wf-1", "function_name": "old_name"}]
        replacements = {"wf-1": "new_name"}
        extract_metadata = AsyncMock(
            return_value=(b"def run(): pass\n", True, True, [], [], pending, replacements),
        )

        service = _service(
            db=db,
            settings=SimpleNamespace(s3_bucket="workspace-bucket"),
            _s3_client=_S3ClientFactory(s3),
            _diagnostics=diagnostics,
            _compute_hash=MagicMock(return_value="hash"),
            _guess_content_type=MagicMock(return_value="text/x-python"),
            _extract_metadata=extract_metadata,
        )
        service.write_file = FileOperationsService.write_file.__get__(service)

        detection = SimpleNamespace(ast_tree=object(), content_str="def run(): pass\n")
        with patch("src.services.editor.file_filter.is_excluded_path", return_value=False):
            with patch(
                "src.services.file_storage.entity_detector.detect_python_entity_type_with_ast",
                return_value=detection,
            ):
                with patch("src.services.file_storage.file_ops.set_module", new_callable=AsyncMock) as set_mod:
                    result = await service.write_file(
                        "workflows/run.py",
                        b"def run(): pass\n",
                        force_deactivation=False,
                    )

        set_mod.assert_awaited_once_with("workflows/run.py", "def run(): pass\n", "hash")
        diagnostics.scan_for_sdk_issues.assert_not_called()
        assert result.pending_deactivations == pending
        assert result.available_replacements == replacements

    @pytest.mark.asyncio
    async def test_record_signed_upload_metadata_indexes_placeholder(self):
        from src.services.file_storage.file_ops import FileOperationsService

        db = MagicMock()
        db.execute = AsyncMock()
        db.flush = AsyncMock()
        service = _service(db=db)
        service.record_signed_upload_metadata = (
            FileOperationsService.record_signed_upload_metadata.__get__(service)
        )

        await service.record_signed_upload_metadata(
            "uploads/manual.bin",
            updated_by="alice",
        )

        stmt = db.execute.call_args[0][0]
        params = stmt.compile().params
        assert params["path"] == "uploads/manual.bin"
        assert params["content_hash"] == ""
        assert params["updated_by"] == "alice"
        db.flush.assert_awaited_once()


class TestDeleteFilePureBranches:
    """Verify delete side effects are best-effort and path-aware."""

    @pytest.mark.asyncio
    async def test_delete_file_continues_when_side_effect_fails(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = _service()
        service._delete_from_s3 = AsyncMock()
        service._remove_from_search_index = AsyncMock(side_effect=RuntimeError("index down"))
        service._handle_app_file_cleanup = AsyncMock()
        service._remove_metadata = AsyncMock()
        service._invalidate_module_cache_if_python = AsyncMock()
        service.delete_file = FileOperationsService.delete_file.__get__(service)

        with patch("src.core.pubsub.publish_file_activity", new_callable=AsyncMock) as publish:
            with patch("src.core.request_context.get_request_user", return_value=None):
                with patch("src.core.request_context.get_request_session_id", return_value="session-1"):
                    await service.delete_file("workflows/remove.py")

        service._delete_from_s3.assert_awaited_once_with("workflows/remove.py")
        service._remove_from_search_index.assert_awaited_once_with("workflows/remove.py")
        service._handle_app_file_cleanup.assert_awaited_once_with("workflows/remove.py")
        service._remove_metadata.assert_awaited_once_with("workflows/remove.py")
        service._invalidate_module_cache_if_python.assert_awaited_once_with("workflows/remove.py")
        publish.assert_awaited_once_with(
            user_id="system",
            user_name="system",
            activity_type="file_delete",
            paths=["workflows/remove.py"],
            session_id="session-1",
        )

    @pytest.mark.asyncio
    async def test_delete_from_s3_uses_repo_prefix(self):
        from src.services.file_storage.file_ops import FileOperationsService

        mock_s3_client = MagicMock()
        mock_s3_client.delete_object = AsyncMock()
        service = _service(_s3_client=_S3ClientFactory(mock_s3_client))
        service.settings.s3_bucket = "workspace-bucket"
        service._delete_from_s3 = FileOperationsService._delete_from_s3.__get__(service)

        await service._delete_from_s3("docs/readme.md")

        mock_s3_client.delete_object.assert_awaited_once_with(
            Bucket="workspace-bucket",
            Key="_repo/docs/readme.md",
        )

    @pytest.mark.asyncio
    async def test_invalidate_module_cache_for_detected_module_without_py_suffix(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = _service()
        service._invalidate_module_cache_if_python = (
            FileOperationsService._invalidate_module_cache_if_python.__get__(service)
        )

        with patch(
            "src.services.file_storage.file_ops.detect_platform_entity_type",
            return_value="module",
        ) as detect:
            with patch("src.services.file_storage.file_ops.invalidate_module", new_callable=AsyncMock) as invalidate:
                await service._invalidate_module_cache_if_python("modules/no_suffix")

        detect.assert_called_once_with("modules/no_suffix", b"")
        invalidate.assert_awaited_once_with("modules/no_suffix")

    @pytest.mark.asyncio
    async def test_handle_app_file_cleanup_returns_when_no_app_matches(self):
        from src.services.file_storage.file_ops import FileOperationsService

        service = _service()
        service._find_app_by_path = AsyncMock(return_value=None)
        service._handle_app_file_cleanup = FileOperationsService._handle_app_file_cleanup.__get__(service)

        await service._handle_app_file_cleanup("apps/missing/page.tsx")

        service._find_app_by_path.assert_awaited_once_with("apps/missing/page.tsx")

    @pytest.mark.asyncio
    async def test_handle_app_file_cleanup_deletes_preview_and_publishes_delete(self):
        from src.services.file_storage.file_ops import FileOperationsService

        app = SimpleNamespace(id="app-1", repo_prefix="apps/helpdesk/")
        service = _service()
        service._find_app_by_path = AsyncMock(return_value=app)
        service._handle_app_file_cleanup = FileOperationsService._handle_app_file_cleanup.__get__(service)

        app_storage = MagicMock()
        app_storage.delete_preview_file = AsyncMock()
        with patch("src.core.pubsub.publish_app_code_file_update", new_callable=AsyncMock) as publish:
            with patch("src.services.app_storage.AppStorageService", return_value=app_storage):
                await service._handle_app_file_cleanup("apps/helpdesk/pages/index.tsx")

        publish.assert_awaited_once_with(
            app_id="app-1",
            user_id="system",
            user_name="system",
            path="pages/index.tsx",
            source=None,
            compiled=None,
            action="delete",
        )
        app_storage.delete_preview_file.assert_awaited_once_with("app-1", "pages/index.tsx")

    @pytest.mark.asyncio
    async def test_remove_from_search_index_deletes_matching_path(self):
        from src.services.file_storage.file_ops import FileOperationsService

        db = MagicMock()
        db.execute = AsyncMock()
        service = _service(db=db)
        service._remove_from_search_index = FileOperationsService._remove_from_search_index.__get__(service)

        await service._remove_from_search_index("docs/readme.md")

        stmt = db.execute.call_args[0][0]
        assert stmt.compile().params["path_1"] == "docs/readme.md"

    @pytest.mark.asyncio
    async def test_find_app_by_path_uses_longest_repo_prefix_match(self):
        from src.services.file_storage.file_ops import FileOperationsService

        app = SimpleNamespace(id="app-1")
        result = MagicMock()
        result.scalar_one_or_none.return_value = app
        db = MagicMock()
        db.execute = AsyncMock(return_value=result)
        service = _service(db=db)
        service._find_app_by_path = FileOperationsService._find_app_by_path.__get__(service)

        assert await service._find_app_by_path("custom/deep/app/pages/index.tsx") is app
        compiled = db.execute.call_args[0][0].compile()
        assert compiled.params["path"] == "custom/deep/app/pages/index.tsx"

    @pytest.mark.asyncio
    async def test_report_bundle_result_publishes_success_payload_and_clears_diagnostics(self):
        from src.services.file_storage.file_ops import FileOperationsService

        diagnostics = MagicMock()
        diagnostics.clear_diagnostic_notification = AsyncMock()
        service = _service(_diagnostics=diagnostics)
        service._report_bundle_result = FileOperationsService._report_bundle_result.__get__(service)
        result = SimpleNamespace(
            success=True,
            manifest=SimpleNamespace(entry="assets/app.js", css=["assets/app.css"], duration_ms=42),
            errors=[],
        )

        with patch("src.core.pubsub.publish_app_code_file_update", new_callable=AsyncMock) as publish:
            await service._report_bundle_result(
                result,
                app_id="app-1",
                app_prefix="apps/helpdesk/",
                path="apps/helpdesk/pages/index.tsx",
                relative_path="pages/index.tsx",
                content_str="export default function App() {}",
                updated_by="alice",
            )

        diagnostics.clear_diagnostic_notification.assert_awaited_once_with(
            "apps/helpdesk/pages/index.tsx"
        )
        publish.assert_awaited_once_with(
            app_id="app-1",
            user_id="alice",
            user_name="alice",
            path="pages/index.tsx",
            source="export default function App() {}",
            action="update",
            bundle={
                "entry": "assets/app.js",
                "css": ["assets/app.css"],
                "duration_ms": 42,
            },
            error=None,
        )

    @pytest.mark.asyncio
    async def test_report_bundle_result_publishes_error_payload_and_diagnostic(self):
        from src.services.file_storage.file_ops import FileOperationsService

        diagnostics = MagicMock()
        diagnostics.create_diagnostic_notification = AsyncMock()
        service = _service(_diagnostics=diagnostics)
        service._report_bundle_result = FileOperationsService._report_bundle_result.__get__(service)
        error = SimpleNamespace(
            text="Expected ;",
            file="pages/index.tsx",
            line=7,
            column=12,
            line_text="const x =",
        )
        result = SimpleNamespace(success=False, manifest=None, errors=[error])

        with patch("src.core.pubsub.publish_app_code_file_update", new_callable=AsyncMock) as publish:
            await service._report_bundle_result(
                result,
                app_id="app-1",
                app_prefix="apps/helpdesk/",
                path="apps/helpdesk/pages/index.tsx",
                relative_path="pages/index.tsx",
                content_str="const x =",
                updated_by="alice",
            )

        diagnostics.create_diagnostic_notification.assert_awaited_once()
        target_path, diagnostic_infos = diagnostics.create_diagnostic_notification.call_args[0]
        assert target_path == "apps/helpdesk/pages/index.tsx"
        assert diagnostic_infos[0].message == "Expected ;"
        assert diagnostic_infos[0].line == 7
        publish.assert_awaited_once_with(
            app_id="app-1",
            user_id="alice",
            user_name="alice",
            path="pages/index.tsx",
            source="const x =",
            action="update",
            bundle=None,
            error={
                "messages": [
                    {
                        "text": "Expected ;",
                        "file": "pages/index.tsx",
                        "line": 7,
                        "column": 12,
                        "line_text": "const x =",
                    }
                ],
            },
        )

    @pytest.mark.asyncio
    async def test_rebuild_app_bundle_reports_bundler_crash_as_error_result(self):
        from src.services.app_bundler import BundleResult
        from src.services.file_storage.file_ops import FileOperationsService

        app = SimpleNamespace(id="app-1", repo_prefix="apps/helpdesk/", dependencies={"react": "^18"})
        service = _service()
        service._report_bundle_result = AsyncMock()
        service._rebuild_app_bundle = FileOperationsService._rebuild_app_bundle.__get__(service)

        with patch(
            "src.services.app_bundler.build_with_migrate",
            new_callable=AsyncMock,
            side_effect=RuntimeError("esbuild missing"),
        ):
            await service._rebuild_app_bundle(
                app,
                "apps/helpdesk/pages/index.tsx",
                "const x =",
                "alice",
            )

        reported = service._report_bundle_result.call_args[0][0]
        assert isinstance(reported, BundleResult)
        assert reported.success is False
        assert reported.errors[0].text == "Bundler crashed: esbuild missing"
        assert service._report_bundle_result.call_args.kwargs["relative_path"] == "pages/index.tsx"


class TestMoveFilePreconditions:
    """Verify move_file path errors before storage mutations."""

    @pytest.mark.asyncio
    async def test_move_file_raises_when_old_path_missing(self):
        from src.services.file_storage.file_ops import FileOperationsService

        missing_result = MagicMock()
        missing_result.scalar_one_or_none.return_value = None
        db = MagicMock()
        db.execute = AsyncMock(return_value=missing_result)
        service = _service(db=db, _s3_client=MagicMock())
        service.move_file = FileOperationsService.move_file.__get__(service)

        with pytest.raises(FileNotFoundError, match="File not found: old.py"):
            await service.move_file("old.py", "new.py")

        db.execute.assert_awaited_once()
        service._s3_client.get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_file_raises_when_new_path_exists(self):
        from src.services.file_storage.file_ops import FileOperationsService

        old_record = MagicMock()
        old_result = MagicMock()
        old_result.scalar_one_or_none.return_value = old_record
        existing_result = MagicMock()
        existing_result.scalar_one_or_none.return_value = MagicMock()
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[old_result, existing_result])
        service = _service(db=db, _s3_client=MagicMock())
        service.move_file = FileOperationsService.move_file.__get__(service)

        with pytest.raises(FileExistsError, match="File already exists: new.py"):
            await service.move_file("old.py", "new.py")

        assert db.execute.await_count == 2
        service._s3_client.get_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_python_file_updates_workspace_workflow_cache_s3_and_index(self):
        from src.services.file_storage.file_ops import FileOperationsService

        old_record = SimpleNamespace(
            content="print('old')",
            content_hash="hash",
            updated_by="alice",
        )
        old_result = MagicMock()
        old_result.scalar_one_or_none.return_value = old_record
        missing_new_result = MagicMock()
        missing_new_result.scalar_one_or_none.return_value = None
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[old_result, missing_new_result, None, None, None])
        s3 = MagicMock()
        s3.copy_object = AsyncMock()
        s3.delete_object = AsyncMock()
        service = _service(
            db=db,
            settings=SimpleNamespace(s3_bucket="workspace-bucket"),
            _s3_client=_S3ClientFactory(s3),
        )
        service.move_file = FileOperationsService.move_file.__get__(service)

        with patch("src.services.file_storage.file_ops.invalidate_module", new_callable=AsyncMock) as invalidate:
            with patch("src.services.file_storage.file_ops.set_module", new_callable=AsyncMock) as set_mod:
                await service.move_file("workflows/old.py", "workflows/new.py")

        assert db.execute.await_count == 5
        workflow_update = db.execute.call_args_list[2][0][0]
        compiled_update = workflow_update.compile()
        assert compiled_update.params["path_1"] == "workflows/old.py"
        assert compiled_update.params["path"] == "workflows/new.py"
        assert "solution_id IS NULL" in str(compiled_update)
        invalidate.assert_awaited_once_with("workflows/old.py")
        set_mod.assert_awaited_once_with("workflows/new.py", "print('old')", "hash")
        s3.copy_object.assert_awaited_once_with(
            Bucket="workspace-bucket",
            CopySource={"Bucket": "workspace-bucket", "Key": "_repo/workflows/old.py"},
            Key="_repo/workflows/new.py",
        )
        s3.delete_object.assert_awaited_once_with(
            Bucket="workspace-bucket",
            Key="_repo/workflows/old.py",
        )
