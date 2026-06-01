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

NORMAL_SECURITY_HEADERS = [
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header X-Frame-Options "DENY" always;',
    'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;',
    'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;',
    'add_header Content-Security-Policy-Report-Only "default-src \'self\'; base-uri \'self\'; object-src \'none\'; frame-ancestors \'none\'; img-src \'self\' data: blob:; font-src \'self\' data:; style-src \'self\' \'unsafe-inline\'; script-src \'self\'; connect-src \'self\'" always;',
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
    server_preamble = config.split("    location ", 1)[0]
    for header in NORMAL_SECURITY_HEADERS:
        assert header in server_preamble, f"server scope is missing {header}"

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
        for header in NORMAL_SECURITY_HEADERS:
            assert header in block, f"{location} is missing {header}"

    assert "connect-src 'self' ws: wss:" not in config


def test_embed_location_preserves_iframe_framing():
    config = NGINX_CONF.read_text()
    block = _location_block(config, "/embed")

    assert 'add_header X-Frame-Options "DENY" always;' not in block
    assert "Content-Security-Policy-Report-Only" not in block
    assert 'add_header X-Content-Type-Options "nosniff" always;' in block
    assert 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;' in block
    assert 'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;' in block
