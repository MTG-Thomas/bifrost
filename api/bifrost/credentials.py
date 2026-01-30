"""
Bifrost SDK Credentials Storage

Cross-platform credential storage for CLI authentication.
Stores API URL, access token, refresh token, and expiration in a JSON file.
"""

import json
import os
import platform
from datetime import datetime
from pathlib import Path


def get_config_dir() -> Path:
    """
    Get platform-specific config directory.

    Returns:
        Path to config directory:
        - Windows: %APPDATA%/Bifrost
        - macOS/Linux: ~/.bifrost
    """
    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "Bifrost"
        return Path.home() / "Bifrost"
    else:
        return Path.home() / ".bifrost"


def get_credentials_path() -> Path:
    """Get path to credentials file."""
    return get_config_dir() / "credentials.json"


def get_credentials() -> dict | None:
    """
    Load credentials from config file.

    Returns:
        Dict with keys: api_url, access_token, refresh_token, expires_at
        None if credentials don't exist or are invalid
    """
    creds_path = get_credentials_path()

    if not creds_path.exists():
        return None

    try:
        with open(creds_path, "r") as f:
            data = json.load(f)

        # Validate required fields
        required = ["api_url", "access_token", "refresh_token", "expires_at"]
        if not all(key in data for key in required):
            return None

        return data
    except (json.JSONDecodeError, OSError):
        return None


def save_credentials(
    api_url: str,
    access_token: str,
    refresh_token: str,
    expires_at: str,
) -> None:
    """
    Save credentials to config file.

    Creates config directory if it doesn't exist.
    Overwrites existing credentials.

    Args:
        api_url: Bifrost API URL
        access_token: JWT access token
        refresh_token: JWT refresh token
        expires_at: ISO 8601 timestamp when access token expires
    """
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)

    # Set restrictive permissions (only owner can read/write)
    if platform.system() != "Windows":
        config_dir.chmod(0o700)

    creds_path = get_credentials_path()

    data = {
        "api_url": api_url,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
    }

    with open(creds_path, "w") as f:
        json.dump(data, f, indent=2)

    # Set restrictive permissions on credentials file
    if platform.system() != "Windows":
        creds_path.chmod(0o600)


def clear_credentials() -> None:
    """Delete credentials file if it exists."""
    creds_path = get_credentials_path()

    if creds_path.exists():
        creds_path.unlink()


def is_token_expired(buffer_seconds: int = 60) -> bool:
    """
    Check if access token is expired.

    Args:
        buffer_seconds: Refresh token this many seconds before actual expiry

    Returns:
        True if token is expired or will expire within buffer_seconds
        False if token is still valid
        True if credentials don't exist
    """
    creds = get_credentials()

    if not creds:
        return True

    expires_at_str = creds.get("expires_at")
    if not expires_at_str:
        return True

    try:
        # Parse ISO 8601 timestamp
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
        now = datetime.utcnow()

        # Add buffer to current time (refresh early)
        return (expires_at - now).total_seconds() <= buffer_seconds
    except (ValueError, AttributeError):
        return True
