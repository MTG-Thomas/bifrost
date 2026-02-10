"""Tests for virtual import S3 fallback."""
import json
import logging
from unittest.mock import MagicMock, patch

import pytest


def test_s3_fallback_on_redis_miss():
    """When Redis returns None, should try S3 and cache result."""
    from src.core.module_cache_sync import get_module_sync

    with patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory, \
         patch("src.core.module_cache_sync._get_s3_module") as mock_s3:

        # Redis returns None (cache miss)
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis_factory.return_value = mock_redis

        # S3 returns the module content
        mock_s3.return_value = b"def helper(): return 42"

        result = get_module_sync("shared/utils.py")

        # Should have tried S3
        mock_s3.assert_called_once_with("shared/utils.py")
        # Should have cached to Redis
        assert mock_redis.setex.called
        # Should return the module
        assert result is not None
        assert result["content"] == "def helper(): return 42"


def test_redis_hit_skips_s3():
    """When Redis has the module, should not touch S3."""
    from src.core.module_cache_sync import get_module_sync

    cached = json.dumps({"content": "cached content", "path": "shared/utils.py", "hash": "abc"})

    with patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory, \
         patch("src.core.module_cache_sync._get_s3_module") as mock_s3:

        mock_redis = MagicMock()
        mock_redis.get.return_value = cached
        mock_redis_factory.return_value = mock_redis

        result = get_module_sync("shared/utils.py")

        mock_s3.assert_not_called()
        assert result is not None
        assert result["content"] == "cached content"


def test_s3_miss_returns_none():
    """When both Redis and S3 miss, should return None."""
    from src.core.module_cache_sync import get_module_sync

    with patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory, \
         patch("src.core.module_cache_sync._get_s3_module") as mock_s3:

        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_redis_factory.return_value = mock_redis
        mock_s3.return_value = None

        result = get_module_sync("shared/nonexistent.py")

        assert result is None


class TestBoto3ImportCaching:
    """Tests for boto3 import caching in _get_s3_module."""

    @pytest.fixture(autouse=True)
    def reset_cache(self):
        """Reset boto3 cache before each test."""
        from src.core.module_cache_sync import reset_boto3_cache
        reset_boto3_cache()
        yield
        reset_boto3_cache()

    def test_boto3_import_cached_after_first_failure(self):
        """Should only attempt boto3 import once, then cache the failure."""
        import src.core.module_cache_sync as mod

        call_count = 0
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def counting_import(name, *args, **kwargs):
            nonlocal call_count
            if name == "boto3":
                call_count += 1
                raise ImportError("no boto3")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=counting_import):
            # First call — should attempt import and cache failure
            result1 = mod._get_s3_module("test/path.py")
            assert result1 is None
            assert call_count == 1

            # Second call — should skip import entirely
            result2 = mod._get_s3_module("test/other.py")
            assert result2 is None
            assert call_count == 1  # Still 1, not 2

    def test_nosuchkey_logs_debug_not_warning(self, caplog):
        """NoSuchKey errors should log at DEBUG level, not WARNING."""
        import src.core.module_cache_sync as mod

        # Set up mock boto3 client that raises NoSuchKey
        mock_client = MagicMock()
        nosuchkey_error = Exception("NoSuchKey")
        nosuchkey_error.response = {"Error": {"Code": "NoSuchKey"}}
        mock_client.get_object.side_effect = nosuchkey_error

        mock_boto3 = MagicMock()
        mock_boto3.client.return_value = mock_client

        mod._boto3_available = True
        mod._boto3_module = mock_boto3

        env_vars = {
            "BIFROST_S3_ENDPOINT_URL": "http://localhost:9000",
            "BIFROST_S3_ACCESS_KEY": "test",
            "BIFROST_S3_SECRET_KEY": "test",
            "BIFROST_S3_BUCKET": "test-bucket",
        }

        with patch.dict("os.environ", env_vars):
            with caplog.at_level(logging.DEBUG, logger="src.core.module_cache_sync"):
                result = mod._get_s3_module("missing/module.py")

        assert result is None
        # Should have a DEBUG log about not found, no WARNING
        debug_msgs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not found in S3" in r.message for r in debug_msgs)
        assert not any("S3 fallback error" in r.message for r in warning_msgs)

    def test_s3_unavailable_returns_none_gracefully(self):
        """When boto3 import fails, _get_s3_module should return None."""
        import src.core.module_cache_sync as mod

        # Simulate failed import
        mod._boto3_available = False
        mod._boto3_module = None

        result = mod._get_s3_module("any/path.py")
        assert result is None

    def test_module_index_populated_on_s3_fallback(self):
        """S3 fallback should add module to Redis index after caching."""
        from src.core.module_cache_sync import get_module_sync

        with patch("src.core.module_cache_sync._get_sync_redis") as mock_redis_factory, \
             patch("src.core.module_cache_sync._get_s3_module") as mock_s3:

            mock_redis = MagicMock()
            mock_redis.get.return_value = None
            mock_redis_factory.return_value = mock_redis
            mock_s3.return_value = b"print('hello')"

            result = get_module_sync("modules/helper.py")

            assert result is not None
            # Should have added to module index
            mock_redis.sadd.assert_called_once()
            # The key should be the module index key
            from src.core.module_cache import MODULE_INDEX_KEY
            call_args = mock_redis.sadd.call_args
            assert call_args[0][0] == MODULE_INDEX_KEY
            assert call_args[0][1] == "modules/helper.py"
