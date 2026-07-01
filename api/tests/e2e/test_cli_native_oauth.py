"""E2E tests for native CLI OAuth login."""

import base64
import hashlib

import httpx
import pytest


def _pkce_s256(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@pytest.mark.e2e
def test_cli_native_oauth_complete_success(
    e2e_client: httpx.Client,
    org1_user,
):
    code_verifier = "native-cli-test-verifier"
    state = "native-cli-test-state"

    start_response = e2e_client.post(
        "/auth/cli/start",
        json={
            "redirect_uri": "http://127.0.0.1:49152/callback",
            "state": state,
            "code_challenge": _pkce_s256(code_verifier),
            "code_challenge_method": "S256",
        },
    )
    assert start_response.status_code == 200
    start_data = start_response.json()
    assert start_data["authorization_url"].startswith("/auth/cli/authorize?")

    authorize_response = e2e_client.get(
        start_data["authorization_url"],
        headers=org1_user.headers,
        follow_redirects=False,
    )
    assert authorize_response.status_code in {302, 307}
    redirect_url = httpx.URL(authorize_response.headers["location"])
    assert redirect_url.host == "127.0.0.1"
    assert redirect_url.params["state"] == state
    assert redirect_url.params["transaction_id"] == start_data["transaction_id"]

    token_response = e2e_client.post(
        "/auth/cli/token",
        json={
            "transaction_id": redirect_url.params["transaction_id"],
            "code": redirect_url.params["code"],
            "state": redirect_url.params["state"],
            "code_verifier": code_verifier,
        },
    )
    assert token_response.status_code == 200
    token_data = token_response.json()
    assert token_data["token_type"] == "bearer"
    assert token_data["access_token"]
    assert token_data["refresh_token"]

    me_response = e2e_client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token_data['access_token']}"},
    )
    assert me_response.status_code == 200

    reuse_response = e2e_client.post(
        "/auth/cli/token",
        json={
            "transaction_id": redirect_url.params["transaction_id"],
            "code": redirect_url.params["code"],
            "state": redirect_url.params["state"],
            "code_verifier": code_verifier,
        },
    )
    assert reuse_response.status_code == 400


@pytest.mark.e2e
def test_cli_native_oauth_rejects_non_localhost_redirect(
    e2e_client: httpx.Client,
):
    response = e2e_client.post(
        "/auth/cli/start",
        json={
            "redirect_uri": "https://evil.example.com/callback",
            "state": "state",
            "code_challenge": _pkce_s256("verifier"),
            "code_challenge_method": "S256",
        },
    )
    assert response.status_code == 400
    assert "localhost" in response.json()["detail"]


@pytest.mark.e2e
def test_cli_native_oauth_authorize_redirects_to_login_when_unauthenticated(
    e2e_client: httpx.Client,
):
    start_response = e2e_client.post(
        "/auth/cli/start",
        json={
            "redirect_uri": "http://localhost:49153/callback",
            "state": "state",
            "code_challenge": _pkce_s256("verifier"),
            "code_challenge_method": "S256",
        },
    )
    assert start_response.status_code == 200
    e2e_client.cookies.clear()

    authorize_response = e2e_client.get(
        start_response.json()["authorization_url"],
        follow_redirects=False,
    )
    assert authorize_response.status_code in {302, 307}
    assert authorize_response.headers["location"].startswith("/login?returnTo=")


@pytest.mark.e2e
def test_cli_native_oauth_rejects_bad_pkce_verifier(
    e2e_client: httpx.Client,
    org1_user,
):
    start_response = e2e_client.post(
        "/auth/cli/start",
        json={
            "redirect_uri": "http://127.0.0.1:49154/callback",
            "state": "state",
            "code_challenge": _pkce_s256("right-verifier"),
            "code_challenge_method": "S256",
        },
    )
    assert start_response.status_code == 200

    authorize_response = e2e_client.get(
        start_response.json()["authorization_url"],
        headers=org1_user.headers,
        follow_redirects=False,
    )
    assert authorize_response.status_code in {302, 307}
    redirect_url = httpx.URL(authorize_response.headers["location"])

    token_response = e2e_client.post(
        "/auth/cli/token",
        json={
            "transaction_id": redirect_url.params["transaction_id"],
            "code": redirect_url.params["code"],
            "state": redirect_url.params["state"],
            "code_verifier": "wrong-verifier",
        },
    )
    assert token_response.status_code == 400
    assert token_response.json()["detail"] == "Invalid PKCE verifier"
