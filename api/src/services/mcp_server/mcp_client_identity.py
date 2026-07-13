"""Resolve a display label for the MCP OAuth callback screen."""

from __future__ import annotations

from urllib.parse import urlparse

_CLIENT_NAME_LABELS = {
    "claude": "Claude",
    "claude code": "Claude Code",
    "claude desktop": "Claude Desktop",
    "codex": "Codex",
    "cursor": "Cursor",
    "gemini": "Gemini CLI",
    "gemini cli": "Gemini CLI",
    "github copilot": "GitHub Copilot",
    "openai codex": "Codex",
    "visual studio code": "VS Code",
    "vscode": "VS Code",
    "windsurf": "Windsurf",
}

_SCHEME_LABELS = {
    "claude": "Claude Desktop",
    "cursor": "Cursor",
    "vscode": "VS Code",
}

_HOST_LABELS = {
    "claude.ai": "Claude",
    "claude.com": "Claude",
    "cursor.com": "Cursor",
    "vscode.dev": "VS Code",
}


def _normalize_client_name(client_name: object) -> str:
    if not isinstance(client_name, str):
        return ""
    normalized = client_name.strip().casefold().replace("-", " ").replace("_", " ")
    return " ".join(normalized.split())


def _host_label(host: str) -> str | None:
    for domain, label in _HOST_LABELS.items():
        if host == domain or host.endswith(f".{domain}"):
            return label
    return None


def _redirect_label(redirect_uri: object) -> str | None:
    if not isinstance(redirect_uri, str):
        return None

    try:
        parsed = urlparse(redirect_uri.strip())
        host = (parsed.hostname or "").casefold()
    except ValueError:
        return None

    return _SCHEME_LABELS.get(parsed.scheme.casefold()) or _host_label(host)


def resolve_mcp_client_label(redirect_uri: object, client_name: object = None) -> str:
    """Return a known client label without reflecting untrusted DCR metadata."""
    redirect_label = _redirect_label(redirect_uri)
    if redirect_label:
        return redirect_label

    return _CLIENT_NAME_LABELS.get(
        _normalize_client_name(client_name),
        "your MCP client",
    )
