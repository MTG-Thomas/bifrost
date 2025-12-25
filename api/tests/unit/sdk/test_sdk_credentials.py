"""
Unit tests for SDK credentials storage.

Tests the cross-platform credential management for CLI authentication.
"""

import json
import os
import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from bifrost.credentials import (
    clear_credentials,
    get_config_dir,
    get_credentials,
    get_credentials_path,
    is_token_expired,
    save_credentials,
)


@pytest.fixture
def temp_config_dir(tmp_path):
    """Temporarily override config directory for testing."""
    with patch("bifrost.credentials.get_config_dir", return_value=tmp_path):
        yield tmp_path


def test_get_config_dir_linux():
    """Test config directory on Linux/macOS."""
    with patch("platform.system", return_value="Linux"):
        config_dir = get_config_dir()
        assert config_dir == Path.home() / ".bifrost"


def test_get_config_dir_windows():
    """Test config directory on Windows."""
    with patch("platform.system", return_value="Windows"):
        with patch.dict(os.environ, {"APPDATA": "C:\\Users\\Test\\AppData\\Roaming"}):
            config_dir = get_config_dir()
            assert config_dir == Path("C:\\Users\\Test\\AppData\\Roaming") / "Bifrost"


def test_get_config_dir_windows_fallback():
    """Test config directory on Windows without APPDATA."""
    with patch("platform.system", return_value="Windows"):
        with patch.dict(os.environ, {}, clear=True):
            config_dir = get_config_dir()
            assert config_dir == Path.home() / "Bifrost"


def test_save_and_load_credentials(temp_config_dir):
    """Test saving and loading credentials."""
    api_url = "https://api.example.com"
    access_token = "access_token_123"
    refresh_token = "refresh_token_456"
    expires_at = "2025-01-01T12:00:00+00:00"

    # Save credentials
    save_credentials(api_url, access_token, refresh_token, expires_at)

    # Load credentials
    creds = get_credentials()

    assert creds is not None
    assert creds["api_url"] == api_url
    assert creds["access_token"] == access_token
    assert creds["refresh_token"] == refresh_token
    assert creds["expires_at"] == expires_at


def test_get_credentials_nonexistent(temp_config_dir):
    """Test loading credentials when file doesn't exist."""
    creds = get_credentials()
    assert creds is None


def test_get_credentials_invalid_json(temp_config_dir):
    """Test loading credentials with invalid JSON."""
    creds_path = get_credentials_path()
    creds_path.parent.mkdir(parents=True, exist_ok=True)

    # Write invalid JSON
    with open(creds_path, "w") as f:
        f.write("not valid json{")

    creds = get_credentials()
    assert creds is None


def test_get_credentials_missing_fields(temp_config_dir):
    """Test loading credentials with missing required fields."""
    creds_path = get_credentials_path()
    creds_path.parent.mkdir(parents=True, exist_ok=True)

    # Write incomplete credentials
    with open(creds_path, "w") as f:
        json.dump({"api_url": "https://example.com"}, f)

    creds = get_credentials()
    assert creds is None


def test_clear_credentials(temp_config_dir):
    """Test clearing credentials."""
    # Save credentials first
    save_credentials(
        api_url="https://api.example.com",
        access_token="token",
        refresh_token="refresh",
        expires_at="2025-01-01T12:00:00+00:00"
    )

    assert get_credentials() is not None

    # Clear credentials
    clear_credentials()

    # Should be gone
    assert get_credentials() is None


def test_clear_credentials_nonexistent(temp_config_dir):
    """Test clearing credentials when file doesn't exist."""
    # Should not raise an error
    clear_credentials()


def test_is_token_expired_no_credentials(temp_config_dir):
    """Test token expiry check with no credentials."""
    assert is_token_expired() is True


def test_is_token_expired_valid_token(temp_config_dir):
    """Test token expiry check with valid (future) expiry."""
    # Token expires 10 minutes from now
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    save_credentials(
        api_url="https://api.example.com",
        access_token="token",
        refresh_token="refresh",
        expires_at=expires_at.isoformat()
    )

    assert is_token_expired() is False


def test_is_token_expired_expired_token(temp_config_dir):
    """Test token expiry check with expired token."""
    # Token expired 5 minutes ago
    expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    save_credentials(
        api_url="https://api.example.com",
        access_token="token",
        refresh_token="refresh",
        expires_at=expires_at.isoformat()
    )

    assert is_token_expired() is True


def test_is_token_expired_buffer(temp_config_dir):
    """Test token expiry check with buffer."""
    # Token expires in 30 seconds
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)

    save_credentials(
        api_url="https://api.example.com",
        access_token="token",
        refresh_token="refresh",
        expires_at=expires_at.isoformat()
    )

    # With default buffer (60s), should be considered expired
    assert is_token_expired() is True

    # With no buffer, should be valid
    assert is_token_expired(buffer_seconds=0) is False


def test_is_token_expired_invalid_date(temp_config_dir):
    """Test token expiry check with invalid date format."""
    creds_path = get_credentials_path()
    creds_path.parent.mkdir(parents=True, exist_ok=True)

    # Write credentials with invalid date
    with open(creds_path, "w") as f:
        json.dump({
            "api_url": "https://api.example.com",
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_at": "not a date"
        }, f)

    assert is_token_expired() is True


def test_credentials_file_permissions(temp_config_dir):
    """Test that credentials file has restrictive permissions (Unix only)."""
    if platform.system() == "Windows":
        pytest.skip("Permission test not applicable on Windows")

    save_credentials(
        api_url="https://api.example.com",
        access_token="token",
        refresh_token="refresh",
        expires_at="2025-01-01T12:00:00+00:00"
    )

    creds_path = get_credentials_path()

    # Check file permissions (should be 0o600 - owner read/write only)
    stat_info = creds_path.stat()
    permissions = stat_info.st_mode & 0o777
    assert permissions == 0o600, f"Expected 0o600, got {oct(permissions)}"


def test_config_dir_permissions(temp_config_dir):
    """Test that config directory has restrictive permissions (Unix only)."""
    if platform.system() == "Windows":
        pytest.skip("Permission test not applicable on Windows")

    save_credentials(
        api_url="https://api.example.com",
        access_token="token",
        refresh_token="refresh",
        expires_at="2025-01-01T12:00:00+00:00"
    )

    # Use temp_config_dir from fixture (not get_config_dir which returns real dir)
    config_dir = temp_config_dir

    # Check directory permissions (should be 0o700 - owner read/write/execute only)
    stat_info = config_dir.stat()
    permissions = stat_info.st_mode & 0o777
    assert permissions == 0o700, f"Expected 0o700, got {oct(permissions)}"
