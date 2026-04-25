"""Create deterministic authenticated scan users for nightly security jobs.

The script bootstraps a platform admin, one organization, and one org user
against a fresh Bifrost test stack. It writes SCAN_USER_TOKEN and
PLATFORM_ADMIN_TOKEN to GitHub Actions' GITHUB_ENV when available.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScanUser:
    email: str
    password: str
    name: str


PLATFORM_ADMIN = ScanUser(
    email=os.environ.get("ZAP_ADMIN_EMAIL", "security-admin@example.test"),
    password=os.environ.get("ZAP_ADMIN_PASSWORD", "SecurityScanAdmin123!"),
    name="Security Scan Admin",
)

ORG_USER = ScanUser(
    email=os.environ.get("ZAP_ORG_EMAIL", "security-user@example.test"),
    password=os.environ.get("ZAP_ORG_PASSWORD", "SecurityScanUser123!"),
    name="Security Scan User",
)


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    form: dict[str, str] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    headers: dict[str, str] = {}
    data: bytes | None = None

    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    elif form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urllib.parse.urlencode(form).encode("utf-8")

    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: {exc.code} {detail}") from exc


def totp(secret: str, *, interval: int = 30, digits: int = 6) -> str:
    normalized = secret.replace(" ", "").upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(normalized + padding)
    counter = int(time.time() // interval)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def register_user(base_url: str, user: ScanUser) -> dict[str, Any]:
    return request_json(
        base_url,
        "POST",
        "/auth/register",
        body={"email": user.email, "password": user.password, "name": user.name},
    )


def login_and_finish_mfa(base_url: str, user: ScanUser) -> dict[str, Any]:
    login_data = request_json(
        base_url,
        "POST",
        "/auth/login",
        form={"username": user.email, "password": user.password},
    )

    if login_data.get("access_token"):
        return login_data

    mfa_token = login_data["mfa_token"]
    setup_data = request_json(
        base_url,
        "POST",
        "/auth/mfa/setup",
        token=mfa_token,
    )
    code = totp(setup_data["secret"])
    return request_json(
        base_url,
        "POST",
        "/auth/mfa/verify",
        token=mfa_token,
        body={"code": code},
    )


def create_org(base_url: str, admin_token: str) -> str:
    org = request_json(
        base_url,
        "POST",
        "/api/organizations",
        token=admin_token,
        body={"name": "Security Scan Org", "domain": "security-scan.example.test"},
    )
    return org["id"]


def create_org_user_stub(base_url: str, admin_token: str, organization_id: str) -> None:
    request_json(
        base_url,
        "POST",
        "/api/users",
        token=admin_token,
        body={
            "email": ORG_USER.email,
            "name": ORG_USER.name,
            "organization_id": organization_id,
            "is_superuser": False,
        },
    )


def write_env(name: str, value: str) -> None:
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")
    print(f"{name}={value}")


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"

    register_user(base_url, PLATFORM_ADMIN)
    admin_tokens = login_and_finish_mfa(base_url, PLATFORM_ADMIN)
    admin_token = admin_tokens["access_token"]

    organization_id = create_org(base_url, admin_token)
    create_org_user_stub(base_url, admin_token, organization_id)
    register_user(base_url, ORG_USER)
    org_tokens = login_and_finish_mfa(base_url, ORG_USER)

    write_env("PLATFORM_ADMIN_TOKEN", admin_token)
    write_env("SCAN_USER_TOKEN", org_tokens["access_token"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
