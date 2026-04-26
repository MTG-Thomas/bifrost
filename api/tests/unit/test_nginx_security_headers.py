from pathlib import Path

import pytest


def _repo_file(*parts: str) -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent.joinpath(*parts)
        if candidate.exists():
            return candidate
    pytest.skip(
        f"{Path(*parts)} is not available in this packaged test environment",
        allow_module_level=True,
    )


NGINX_CONF = _repo_file("client", "nginx.conf")
SECURITY_HEADERS_CONF = _repo_file("client", "security-headers.conf")

NORMAL_SECURITY_HEADERS = [
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header X-Frame-Options "DENY" always;',
    'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;',
    'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;',
    'add_header Content-Security-Policy-Report-Only "default-src',
]


def _location_block(config: str, location: str) -> str:
    marker = f"    location {location} {{"
    start = config.find(marker)
    if start == -1:
        raise AssertionError(f"Location block not found: {location}")
    depth = 0
    for offset, char in enumerate(config[start:], start=start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return config[start : offset + 1]
    raise AssertionError(f"Location block not closed: {location}")


def test_cache_bearing_locations_repeat_normal_security_headers():
    config = NGINX_CONF.read_text()
    locations = [
        "^~ /api/auth",
        "^~ /api",
        "/auth",
        "/.well-known",
        "/",
        "~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$",
    ]

    for location in locations:
        block = _location_block(config, location)
        assert "include /etc/nginx/conf.d/security-headers.conf;" in block


def test_normal_security_header_snippet_contains_required_headers():
    config = SECURITY_HEADERS_CONF.read_text()

    for header in NORMAL_SECURITY_HEADERS:
        assert header in config, f"security header snippet is missing {header}"

    assert "connect-src 'self' ws: wss:" not in config
    assert "connect-src 'self'" in config


def test_dockerfile_copies_security_header_snippet():
    dockerfile = (Path(__file__).resolve().parents[3] / "client" / "Dockerfile").read_text()

    assert "COPY security-headers.conf /etc/nginx/conf.d/security-headers.conf" in dockerfile


def test_normal_security_headers_are_not_duplicated_in_locations():
    config = NGINX_CONF.read_text()

    for location in [
        "^~ /api/auth",
        "^~ /api",
        "/auth",
        "/.well-known",
        "/",
        "~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$",
    ]:
        block = _location_block(config, location)
        for header in NORMAL_SECURITY_HEADERS:
            assert header not in block, f"{location} duplicates {header}"


def test_embed_location_preserves_iframe_framing():
    config = NGINX_CONF.read_text()
    block = _location_block(config, "/embed")

    assert 'add_header X-Frame-Options "DENY" always;' not in block
    assert "Content-Security-Policy-Report-Only" not in block
    assert 'add_header X-Content-Type-Options "nosniff" always;' in block
    assert 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;' in block
    assert 'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;' in block
