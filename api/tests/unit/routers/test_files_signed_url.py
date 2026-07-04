"""
Unit tests for POST /api/files/signed-url endpoint.

Tests path validation, location/scope handling, and presigned URL generation.
Path resolution is delegated to `shared.file_paths.resolve_s3_key`.
"""

from uuid import UUID

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.core.principal import UserPrincipal
from src.routers.files import (
    SignedUrlBatchRequest,
    SignedUploadCompleteRequest,
    SignedUrlRequest,
    SignedUrlResponse,
    complete_signed_upload,
    get_signed_url,
    get_signed_urls,
)

# A concrete org UUID — scope resolution (`resolve_target_org`) validates that a
# non-"global" scope is a real UUID, so these path-resolution tests use one.
ORG_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def _ctx():
    """A signed-url request ctx whose principal is a superuser with no context
    org, so an explicit `scope=` on the request is honored verbatim (a regular
    user would be pinned to their own org) and an omitted scope on a scoped
    location falls back to 'global'. These path-resolution tests pin the
    explicit-scope and global-fallback arms; cross-org pinning has e2e coverage."""
    ctx = MagicMock()
    ctx.org_id = None
    ctx.solution_id = None
    ctx.scope = None
    ctx.user = UserPrincipal(
        user_id=UUID("11111111-1111-1111-1111-111111111111"),
        email="admin@example.com",
        organization_id=None,
        is_superuser=True,
    )
    return ctx


@pytest.fixture(autouse=True)
def _allow_policy():
    """These tests exercise path resolution + S3 method dispatch, not the policy
    gate. Bypass the default-deny file-policy check so resolution is reached;
    policy enforcement has its own e2e coverage (test_file_policies_rest.py)."""
    with patch("src.routers.files._require_file_policy", new=AsyncMock(return_value=None)):
        yield


class TestSignedUrlRequestModel:
    """Test SignedUrlRequest validation."""

    def test_defaults(self):
        req = SignedUrlRequest(path="invoices/report.pdf")
        assert req.method == "PUT"
        assert req.content_type == "application/octet-stream"
        assert req.location == "uploads"  # backwards-compat default
        assert req.scope is None

    def test_explicit_get(self):
        req = SignedUrlRequest(path="data.csv", method="GET")
        assert req.method == "GET"

    def test_explicit_location(self):
        req = SignedUrlRequest(path="file.txt", location="workspace")
        assert req.location == "workspace"

    def test_explicit_scope(self):
        req = SignedUrlRequest(path="file.txt", location="temp", scope="org-123")
        assert req.scope == "org-123"


class TestSignedUrlResponseModel:
    """Test SignedUrlResponse shape."""

    def test_fields(self):
        resp = SignedUrlResponse(
            url="https://s3/presigned", path="uploads/org-a/file.txt"
        )
        assert resp.url == "https://s3/presigned"
        assert resp.path == "uploads/org-a/file.txt"
        assert resp.expires_in == 600
        assert resp.headers == {}


class TestPathResolution:
    """Test that the handler delegates to shared.file_paths.resolve_s3_key."""

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_uploads_scoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(
            return_value="https://s3/url"
        )
        mock_fss.presigned_upload_headers.return_value = {
            "Content-Type": "application/octet-stream"
        }
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="report.pdf", scope=str(ORG_A))
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == f"uploads/{ORG_A}/report.pdf"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_uploads_no_scope_falls_back_to_global(self, mock_fss_class):
        # A caller with no org (ctx.org_id=None) and no explicit scope resolves
        # to the 'global' scope (a real logged-in user would default to their
        # own org instead). Resolution succeeds; the policy gate governs access.
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="report.pdf")  # default location=uploads, no scope
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == "uploads/global/report.pdf"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_workspace_unscoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(
            return_value="https://s3/url"
        )
        mock_fss.presigned_upload_headers.return_value = {
            "Content-Type": "application/octet-stream"
        }
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="report.pdf", location="workspace")
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == "_repo/report.pdf"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_temp_scoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(
            return_value="https://s3/url"
        )
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(
            path="x.bin", location="temp", scope="org-a", method="GET"
        )
        result = await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert result.path == "_tmp/org-a/x.bin"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_freeform_scoped(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(
            return_value="https://s3/url"
        )
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(
            path="q1.pdf", location="reports", scope="org-a", method="GET"
        )
        result = await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert result.path == "reports/org-a/q1.pdf"


class TestPathValidation:
    """Test that handler returns 400 on resolver-rejected inputs."""

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="../etc/passwd", scope=str(ORG_A))
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400
        assert "traversal" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_rejects_absolute_path(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="/absolute/path", scope=str(ORG_A))
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_reserved_location_name(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="x.txt", location="_repo", scope=str(ORG_A))
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400
        assert "reserved bucket prefix" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_rejects_invalid_freeform_name(self):
        from fastapi import HTTPException

        req = SignedUrlRequest(path="x.txt", location="Bad Name!", scope=str(ORG_A))
        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_temp_no_scope_falls_back_to_global(self, mock_fss_class):
        # Same global-fallback arm as uploads: a scopeless caller on a scoped
        # location resolves to 'global' rather than erroring.
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="x.txt", location="temp", scope=None)
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.path == "_tmp/global/x.txt"


class TestPresignedUrlGeneration:
    """Test that correct S3 method is called based on request method."""

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_put_calls_upload(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(
            return_value="https://s3/put-url"
        )
        mock_fss.presigned_upload_headers.return_value = {
            "Content-Type": "application/pdf"
        }
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(
            path="file.pdf", method="PUT", content_type="application/pdf", scope="org-a"
        )
        result = await get_signed_url(req, MagicMock(), MagicMock(), AsyncMock())
        assert result.url == "https://s3/put-url"
        assert result.headers == {"Content-Type": "application/pdf"}
        mock_fss.presigned_upload_headers.assert_called_once_with("application/pdf")
        mock_fss.generate_presigned_upload_url.assert_awaited_once_with(
            path="uploads/org-a/file.pdf",
            content_type="application/pdf",
        )

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_get_calls_download(self, mock_fss_class):
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(
            return_value="https://s3/get-url"
        )
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="file.pdf", method="GET", scope=str(ORG_A))
        result = await get_signed_url(req, _ctx(), MagicMock(), AsyncMock())
        assert result.url == "https://s3/get-url"
        assert result.headers == {}
        mock_fss.generate_presigned_download_url.assert_awaited_once_with(
            path=f"uploads/{ORG_A}/file.pdf",
        )


class TestSignedUrlBatch:
    """Batch endpoint returns per-item success/error results without aborting."""

    @pytest.mark.asyncio
    async def test_batch_mixes_success_forbidden_and_bad_request(self, monkeypatch):
        from fastapi import HTTPException

        async def fake_build_signed_url(item, ctx, db):
            if item.path == "ok.pdf":
                return SignedUrlResponse(
                    url="https://s3/ok",
                    path=f"uploads/{ORG_A}/ok.pdf",
                )
            if item.path == "denied.pdf":
                raise HTTPException(status_code=403, detail="policy details hidden")
            raise HTTPException(status_code=400, detail="bad path")

        monkeypatch.setattr(
            "src.routers.files._build_signed_url",
            fake_build_signed_url,
        )

        response = await get_signed_urls(
            SignedUrlBatchRequest(
                requests=[
                    SignedUrlRequest(path="ok.pdf", method="GET", scope=str(ORG_A)),
                    SignedUrlRequest(path="denied.pdf", method="GET", scope=str(ORG_A)),
                    SignedUrlRequest(path="../bad.pdf", method="PUT", scope=str(ORG_A)),
                ]
            ),
            _ctx(),
            MagicMock(),
            AsyncMock(),
        )

        assert [result.path for result in response.results] == [
            "ok.pdf",
            "denied.pdf",
            "../bad.pdf",
        ]
        assert response.results[0].status_code == 200
        assert response.results[0].resolved_path == f"uploads/{ORG_A}/ok.pdf"
        assert response.results[0].url == "https://s3/ok"
        assert response.results[0].expires_in == 600

        assert response.results[1].status_code == 403
        assert response.results[1].error == "forbidden"
        assert response.results[1].url is None

        assert response.results[2].status_code == 400
        assert response.results[2].error == "bad path"
        assert response.results[2].method == "PUT"


class TestSignedUploadCompletion:
    """Completing a browser PUT records metadata and publishes file changes."""

    @pytest.mark.asyncio
    async def test_complete_upload_records_metadata_and_publishes(self, monkeypatch):
        metadata_calls = []
        publish_calls = []

        class FakeStorage:
            def __init__(self, db):
                self.db = db

            async def file_exists(self, s3_path):
                assert s3_path == f"uploads/{ORG_A}/report.pdf"
                return True

            async def record_signed_upload_metadata(self, **kwargs):
                metadata_calls.append(kwargs)

        class Db:
            def __init__(self):
                self.commits = 0

            async def commit(self):
                self.commits += 1

        async def fake_publish_file_change(**kwargs):
            publish_calls.append(kwargs)

        db = Db()
        monkeypatch.setattr(
            "src.routers.files.FileStorageService",
            FakeStorage,
        )
        monkeypatch.setattr(
            "src.routers.files._require_declared_solution_file_location",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "src.routers.files._require_file_policy",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "src.routers.files._install_org_id",
            AsyncMock(return_value=ORG_A),
        )
        monkeypatch.setattr(
            "src.core.pubsub.publish_file_change",
            fake_publish_file_change,
        )

        await complete_signed_upload(
            SignedUploadCompleteRequest(
                path="report.pdf",
                location="uploads",
                scope=str(ORG_A),
                content_type="application/pdf",
                size_bytes=123,
                sha256="a" * 64,
            ),
            _ctx(),
            MagicMock(),
            db,
        )

        assert db.commits == 1
        assert metadata_calls == [
            {
                "location": "uploads",
                "scope": str(ORG_A),
                "path": "report.pdf",
                "s3_path": f"uploads/{ORG_A}/report.pdf",
                "content_type": "application/pdf",
                "size_bytes": 123,
                "sha256": "a" * 64,
                "updated_by": "admin@example.com",
                "user_id": "11111111-1111-1111-1111-111111111111",
                "solution_id": None,
                "org_id": ORG_A,
            }
        ]
        assert publish_calls == [
            {
                "location": "uploads",
                "scope": str(ORG_A),
                "path": "report.pdf",
                "action": "upload",
            }
        ]

    @pytest.mark.asyncio
    async def test_complete_upload_404s_when_object_is_missing(self, monkeypatch):
        class FakeStorage:
            def __init__(self, db):
                self.db = db

            async def file_exists(self, s3_path):
                return False

            async def record_signed_upload_metadata(self, **_kwargs):
                raise AssertionError("metadata should not be recorded")

        monkeypatch.setattr(
            "src.routers.files.FileStorageService",
            FakeStorage,
        )
        monkeypatch.setattr(
            "src.routers.files._require_declared_solution_file_location",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            "src.routers.files._require_file_policy",
            AsyncMock(return_value=None),
        )

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await complete_signed_upload(
                SignedUploadCompleteRequest(
                    path="missing.pdf",
                    location="uploads",
                    scope=str(ORG_A),
                ),
                _ctx(),
                MagicMock(),
                MagicMock(),
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Uploaded object not found"
