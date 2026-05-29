#!/usr/bin/env python3
"""Submit .bestpractices.json to OpenSSF BadgeApp project 13022."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from http.cookiejar import Cookie, CookieJar
from pathlib import Path

COOKIE_NAME = "_BadgeApp_session"
BASE_URL = "https://www.bestpractices.dev"
PROJECT_ID = 13022
LEVEL = os.environ.get("BADGE_LEVEL", "passing").strip().lower() or "passing"
VALID_LEVELS = frozenset({"passing", "baseline-1", "silver"})
HTTP_TIMEOUT_SECONDS = 20
REDIRECT_CODES = {301, 302, 303, 307, 308}
ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / ".bestpractices.json"

AUTO_DETECTED_FIELDS = {
    "homepage_url_status",
    "homepage_url_justification",
    "report_url_status",
    "report_url_justification",
}

AUTH_TOKEN_PATTERN = re.compile(
    r'name="authenticity_token"[^>]*value="([^"]+)"|'
    r'value="([^"]+)"[^>]*name="authenticity_token"'
)
LOCK_VERSION_PATTERN = re.compile(
    r'name="project\[lock_version\]"[^>]*value="(\d+)"|'
    r'value="(\d+)"[^>]*name="project\[lock_version\]"'
)


def make_opener(session_cookie: str):
    jar = CookieJar()
    jar.set_cookie(
        Cookie(
            version=0,
            name=COOKIE_NAME,
            value=session_cookie,
            port=None,
            port_specified=False,
            domain="www.bestpractices.dev",
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=True,
            expires=None,
            discard=True,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
    )
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
    )


def first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1) or match.group(2)


def redirect_is_auth_failure(location: str | None) -> bool:
    if not location:
        return True
    lower = location.lower()
    return any(
        token in lower
        for token in ("login", "sign_in", "signin", "/users/", "session/new")
    )


def ensure_not_auth_redirect(response, action: str) -> None:
    code = response.getcode()
    if code not in REDIRECT_CODES:
        return
    location = response.getheader("Location")
    if redirect_is_auth_failure(location):
        raise RuntimeError(
            f"BadgeApp redirected {action} to sign-in; session cookie may be expired."
        )


def fetch_edit_page(opener, project_id: int, level: str):
    url = f"{BASE_URL}/en/projects/{project_id}/{level}/edit"
    response = opener.open(url, timeout=HTTP_TIMEOUT_SECONDS)
    ensure_not_auth_redirect(response, "edit page load")
    if response.getcode() in REDIRECT_CODES:
        raise RuntimeError("Unexpected redirect while loading BadgeApp edit page.")
    html = response.read().decode("utf-8", errors="replace")
    auth_token = first_match(AUTH_TOKEN_PATTERN, html)
    lock_version = first_match(LOCK_VERSION_PATTERN, html)
    if not auth_token:
        raise RuntimeError("Could not parse authenticity_token from edit page.")
    return auth_token, lock_version


def submit(
    opener,
    project_id: int,
    level: str,
    data: dict[str, str],
    auth_token: str,
    lock_version: str | None,
):
    url = f"{BASE_URL}/en/projects/{project_id}/{level}"
    form_data = {"_method": "patch", "authenticity_token": auth_token}
    if lock_version:
        form_data["project[lock_version]"] = lock_version
    for key, value in data.items():
        if key in AUTO_DETECTED_FIELDS:
            continue
        form_data[f"project[{key}]"] = value
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form_data).encode("utf-8"),
        method="POST",
    )
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    response = opener.open(request, timeout=HTTP_TIMEOUT_SECONDS)
    code = response.getcode()
    body = response.read().decode("utf-8", errors="replace")

    if code in REDIRECT_CODES:
        if redirect_is_auth_failure(response.getheader("Location")):
            return False, "redirected to sign-in; session cookie may be expired"
        return True, f"redirect {code}"

    if code >= 400:
        return False, f"HTTP {code}"

    if "error" in body.lower() and "form contains" in body.lower():
        return False, "validation errors in form response"
    return True, f"status {code}"


def main() -> int:
    if LEVEL not in VALID_LEVELS:
        print(
            f"BADGE_LEVEL must be one of: {', '.join(sorted(VALID_LEVELS))}",
            file=sys.stderr,
        )
        return 1

    cookie = os.environ.get("BADGE_COOKIE", "").strip()
    if not cookie:
        print(
            "Set BADGE_COOKIE to your _BadgeApp_session cookie from bestpractices.dev.\n"
            "Chrome: DevTools → Application → Cookies → www.bestpractices.dev\n"
            "Alternative: merge .bestpractices.json to main, open\n"
            f"{BASE_URL}/en/projects/{PROJECT_ID}/{LEVEL}/edit\n"
            f"(BADGE_LEVEL: passing | baseline-1 | silver)\n"
            "and click Save (and continue) 🤖",
            file=sys.stderr,
        )
        return 1

    if not DATA_FILE.exists():
        print(f"Missing {DATA_FILE}. Run scripts/generate-bestpractices-json.py first.", file=sys.stderr)
        return 1

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    opener = make_opener(cookie)
    auth_token, lock_version = fetch_edit_page(opener, PROJECT_ID, LEVEL)
    ok, detail = submit(opener, PROJECT_ID, LEVEL, data, auth_token, lock_version)
    print(f"Submit project {PROJECT_ID} ({LEVEL}): {'OK' if ok else 'FAILED'} ({detail})")
    if ok:
        verify = urllib.request.urlopen(
            f"{BASE_URL}/projects/{PROJECT_ID}.json",
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        payload = json.loads(verify.read().decode("utf-8"))
        print(
            "Current badge progress:",
            f"passing={payload.get('badge_percentage_0')}%,",
            f"name={payload.get('name')!r}",
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
