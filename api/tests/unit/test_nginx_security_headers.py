from pathlib import Path


NGINX_CONF = Path(__file__).resolve().parents[3] / "client" / "nginx.conf"

NORMAL_SECURITY_HEADERS = [
    'add_header X-Content-Type-Options "nosniff" always;',
    'add_header X-Frame-Options "DENY" always;',
    'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;',
    'add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;',
    "add_header Content-Security-Policy-Report-Only",
]


def _location_block(config: str, location: str) -> str:
    marker = f"    location {location} {{"
    start = config.index(marker)
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
        for header in NORMAL_SECURITY_HEADERS:
            assert header in block, f"{location} is missing {header}"


def test_embed_location_preserves_iframe_framing():
    config = NGINX_CONF.read_text()
    block = _location_block(config, "/embed")

    assert 'add_header X-Frame-Options "DENY" always;' not in block
    assert "Content-Security-Policy-Report-Only" not in block
    assert 'add_header X-Content-Type-Options "nosniff" always;' in block
    assert 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;' in block
