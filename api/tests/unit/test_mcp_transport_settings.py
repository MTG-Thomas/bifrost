"""MCP HTTP transport trust-boundary settings."""

from src.config import Settings


def test_mcp_transport_uses_explicit_public_and_cors_authorities():
    settings = Settings(
        secret_key="test-secret-key-that-is-at-least-32-characters",
        public_url="https://bifrost.example.com:8443/base",
        cors_origins="*, https://console.example.com",
    )

    assert settings.mcp_allowed_hosts == ["bifrost.example.com:8443"]
    assert settings.mcp_allowed_origins == [
        "https://bifrost.example.com:8443",
        "https://console.example.com",
    ]
