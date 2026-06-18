"""
Workflow API Key Authentication Utilities

Provides utilities for generating workflow API keys.
Validation and management functions are in src/routers/workflow_keys.py.
"""

import secrets

from src.core.security import get_password_hash, verify_password


def hash_workflow_key(raw_key: str) -> str:
    """Return a password-style hash for storing workflow API keys."""
    return get_password_hash(raw_key)


def verify_workflow_key(raw_key: str, hashed_key: str) -> bool:
    """Verify a workflow API key against its stored hash."""
    return verify_password(raw_key, hashed_key)


def generate_workflow_key() -> tuple[str, str]:
    """
    Generate a cryptographically secure workflow API key.

    Returns:
        Tuple of (raw_key, hashed_key) for storage in workflows.api_key_hash
    """
    # Generate a secure, URL-safe token
    raw_key = secrets.token_urlsafe(32)

    # Store only a password-style hash of the generated key.
    hashed_key = hash_workflow_key(raw_key)

    return raw_key, hashed_key
