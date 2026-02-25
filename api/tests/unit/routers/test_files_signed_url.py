"""
Unit tests for POST /api/files/signed-url endpoint.

Tests path validation, scope resolution, and presigned URL generation.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.routers.files import (
    SignedUrlRequest,
    SignedUrlResponse,
    RESERVED_PREFIXES,
    get_signed_url,
)


class TestSignedUrlRequestModel:
    """Test SignedUrlRequest validation."""

    def test_defaults(self):
        req = SignedUrlRequest(path="invoices/report.pdf")
        assert req.method == "PUT"
        assert req.content_type == "application/octet-stream"
        assert req.scope is None

    def test_explicit_get(self):
        req = SignedUrlRequest(path="data.csv", method="GET")
        assert req.method == "GET"

    def test_explicit_scope(self):
        req = SignedUrlRequest(path="file.txt", scope="org-123")
        assert req.scope == "org-123"


class TestSignedUrlResponseModel:
    """Test SignedUrlResponse shape."""

    def test_fields(self):
        resp = SignedUrlResponse(url="https://s3/presigned", path="uploads/global/file.txt")
        assert resp.url == "https://s3/presigned"
        assert resp.path == "uploads/global/file.txt"
        assert resp.expires_in == 600


class TestReservedPrefixes:
    """Test reserved prefix constants."""

    def test_reserved_prefixes_includes_repo(self):
        assert "_repo/" in RESERVED_PREFIXES

    def test_reserved_prefixes_includes_apps(self):
        assert "_apps/" in RESERVED_PREFIXES

    def test_reserved_prefixes_includes_tmp(self):
        assert "_tmp/" in RESERVED_PREFIXES


class TestPathValidation:
    """Test path validation logic in the endpoint."""

    @pytest.mark.asyncio
    async def test_rejects_path_traversal(self):
        """Paths containing '..' should be rejected."""
        from fastapi import HTTPException

        req = SignedUrlRequest(path="../etc/passwd")
        ctx = MagicMock()
        user = MagicMock()
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, ctx, user, db)
        assert exc_info.value.status_code == 400
        assert "must be relative" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_rejects_absolute_path(self):
        """Paths starting with '/' should be rejected."""
        from fastapi import HTTPException

        req = SignedUrlRequest(path="/absolute/path")
        ctx = MagicMock()
        user = MagicMock()
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, ctx, user, db)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_reserved_prefix_repo(self):
        """Paths starting with _repo/ should be rejected."""
        from fastapi import HTTPException

        req = SignedUrlRequest(path="_repo/secret.py")
        ctx = MagicMock()
        user = MagicMock()
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, ctx, user, db)
        assert exc_info.value.status_code == 400
        assert "reserved prefix" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_rejects_reserved_prefix_apps(self):
        """Paths starting with _apps/ should be rejected."""
        from fastapi import HTTPException

        req = SignedUrlRequest(path="_apps/my-app/index.tsx")
        ctx = MagicMock()
        user = MagicMock()
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_signed_url(req, ctx, user, db)
        assert exc_info.value.status_code == 400


class TestScopeResolution:
    """Test scope resolution in S3 path construction."""

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_global_scope_when_none(self, mock_fss_class):
        """Scope defaults to 'global' when not provided."""
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="report.pdf", scope=None)
        ctx = MagicMock()
        user = MagicMock()
        db = AsyncMock()

        result = await get_signed_url(req, ctx, user, db)
        assert result.path == "uploads/global/report.pdf"

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_explicit_scope(self, mock_fss_class):
        """Explicit scope is used in path construction."""
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="report.pdf", scope="org-abc")
        ctx = MagicMock()
        user = MagicMock()
        db = AsyncMock()

        result = await get_signed_url(req, ctx, user, db)
        assert result.path == "uploads/org-abc/report.pdf"


class TestPresignedUrlGeneration:
    """Test that correct S3 method is called based on request method."""

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_put_calls_upload(self, mock_fss_class):
        """PUT method generates upload URL."""
        mock_fss = MagicMock()
        mock_fss.generate_presigned_upload_url = AsyncMock(return_value="https://s3/put-url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="file.pdf", method="PUT", content_type="application/pdf")
        ctx = MagicMock()
        user = MagicMock()
        db = AsyncMock()

        result = await get_signed_url(req, ctx, user, db)
        assert result.url == "https://s3/put-url"
        mock_fss.generate_presigned_upload_url.assert_awaited_once_with(
            path="uploads/global/file.pdf",
            content_type="application/pdf",
        )

    @pytest.mark.asyncio
    @patch("src.routers.files.FileStorageService")
    async def test_get_calls_download(self, mock_fss_class):
        """GET method generates download URL."""
        mock_fss = MagicMock()
        mock_fss.generate_presigned_download_url = AsyncMock(return_value="https://s3/get-url")
        mock_fss_class.return_value = mock_fss

        req = SignedUrlRequest(path="file.pdf", method="GET")
        ctx = MagicMock()
        user = MagicMock()
        db = AsyncMock()

        result = await get_signed_url(req, ctx, user, db)
        assert result.url == "https://s3/get-url"
        mock_fss.generate_presigned_download_url.assert_awaited_once_with(
            path="uploads/global/file.pdf",
        )
