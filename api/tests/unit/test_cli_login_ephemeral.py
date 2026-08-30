"""Tests for `bifrost login` and `bifrost logout` flows.

Three login modes:
  * Browser native OAuth (default) — token stored in keychain (or JSON
    fallback). On success, login also writes BIFROST_API_URL=<url> to the
    CWD .env so subsequent CLI commands in this folder target this stack.
  * Browser device-code (explicit --device-code) — legacy browser fallback
    using the same persistent credential storage.
  * Password-grant (when --email and --password are passed) — tokens are
    stored by URL in the credential backend and mirrored into the CWD .env
    for isolated unattended sessions. Refuses MFA-enabled instances.
"""

import asyncio
import urllib.error
import urllib.request

import pytest

from bifrost import cli
from bifrost import credentials as creds_mod


@pytest.fixture
def isolated_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(
        creds_mod,
        "get_credentials_path",
        lambda: tmp_path / "credentials.json",
    )
    monkeypatch.setattr(
        creds_mod,
        "get_config_path",
        lambda: tmp_path / "config.json",
    )
    monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
    creds_mod._reset_persistent_backend_for_tests()
    yield
    creds_mod._reset_persistent_backend_for_tests()


def _stub_post(json_payload: dict, status_code: int = 200):
    """Build an httpx.AsyncClient stand-in whose .post() returns the given payload."""

    class StubResponse:
        def __init__(self, code, payload):
            self.status_code = code
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    class StubClient:
        def __init__(self, *_a, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def post(self, _url, json=None, data=None, headers=None):
            return StubResponse(status_code, json_payload)

    return StubClient


class TestPasswordLoginFlagParsing:
    def test_email_without_password_errors(self, capsys):
        rc = cli.handle_login(["--email", "x@y", "--url", "http://localhost:38421"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "--email" in err and "--password" in err

    def test_password_without_email_errors(self, capsys):
        rc = cli.handle_login(["--password", "p", "--url", "http://localhost:38421"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "--email" in err and "--password" in err

    def test_password_grant_without_url_or_env_errors(self, capsys, monkeypatch):
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        rc = cli.handle_login(["--email", "x@y", "--password", "p"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "URL" in err or "url" in err


class TestPasswordLoginSuccess:
    def test_writes_three_vars_to_env_and_warning_to_stderr(
        self,
        capsys,
        monkeypatch,
        tmp_path,
        isolated_credentials,
    ):
        stub = _stub_post(
            {
                "access_token": "at_value",
                "refresh_token": "rt_value",
                "expires_in": 1800,
            }
        )
        monkeypatch.setattr("httpx.AsyncClient", stub)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login(
            [
                "--email",
                "dev@gobifrost.com",
                "--password",
                "password",
                "--url",
                "http://localhost:38421",
            ]
        )
        assert rc == 0

        env_text = (tmp_path / ".env").read_text()
        assert "BIFROST_API_URL=http://localhost:38421" in env_text
        assert "BIFROST_ACCESS_TOKEN=at_value" in env_text
        assert "BIFROST_REFRESH_TOKEN=rt_value" in env_text
        stored = creds_mod.get_credentials("http://localhost:38421")
        assert stored is not None
        assert stored["access_token"] == "at_value"
        assert stored["refresh_token"] == "rt_value"

        captured = capsys.readouterr()
        assert "BIFROST_ACCESS_TOKEN=" not in captured.out
        assert "BIFROST_REFRESH_TOKEN=" not in captured.out
        assert "MFA" in captured.err
        assert "ephemeral" in captured.err.lower()

    def test_null_expiry_uses_default(
        self, monkeypatch, tmp_path, isolated_credentials,
    ):
        stub = _stub_post({
            "access_token": "at_value",
            "refresh_token": "rt_value",
            "expires_in": None,
        })
        monkeypatch.setattr("httpx.AsyncClient", stub)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login([
            "--email", "dev@gobifrost.com",
            "--password", "password",
            "--url", "http://localhost:38421",
        ])

        assert rc == 0
        assert creds_mod.get_credentials("http://localhost:38421") is not None

    def test_does_not_change_saved_default(
        self, capsys, monkeypatch, tmp_path, isolated_credentials,
    ):
        stub = _stub_post({
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 1800,
        })
        monkeypatch.setattr("httpx.AsyncClient", stub)
        creds_mod.save_credentials(
            "https://prod.example.com",
            "prod-at",
            "prod-rt",
            "2099-01-01T00:00:00+00:00",
        )
        creds_mod.set_default_connection("https://prod.example.com")
        monkeypatch.chdir(tmp_path)

        cli.handle_login([
            "--email", "dev@gobifrost.com",
            "--password", "password",
            "--url", "http://localhost:38421",
        ])

        assert creds_mod.get_default_connection() == "https://prod.example.com"
        assert creds_mod.get_credentials("http://localhost:38421") is not None


class TestPasswordLoginMfaRefusal:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"error": "bad"}, "returned HTTP 401"),
            ({"access_token": "only-access"}, "missing access_token/refresh_token"),
        ],
    )
    def test_password_login_reports_http_and_payload_errors(
        self,
        capsys,
        monkeypatch,
        payload,
        expected,
    ):
        status_code = 401 if "error" in payload else 200
        stub = _stub_post(payload, status_code=status_code)
        monkeypatch.setattr("httpx.AsyncClient", stub)

        rc, result = asyncio.run(
            cli.password_login_flow(
                "http://localhost:38421",
                "dev@gobifrost.com",
                "password",
            )
        )

        assert rc == 1
        assert result is None
        assert expected in capsys.readouterr().err

    def test_mfa_required_returns_exit_2(self, capsys, monkeypatch):
        stub = _stub_post({"mfa_required": True, "mfa_token": "mt", "expires_in": 300})
        monkeypatch.setattr("httpx.AsyncClient", stub)

        rc = cli.handle_login(
            [
                "--email",
                "dev@gobifrost.com",
                "--password",
                "password",
                "--url",
                "http://localhost:38421",
            ]
        )
        assert rc == 2
        err = capsys.readouterr().err
        assert "MFA" in err

    def test_mfa_setup_required_returns_exit_2(self, capsys, monkeypatch):
        stub = _stub_post({"mfa_setup_required": True, "mfa_token": "mt", "expires_in": 300})
        monkeypatch.setattr("httpx.AsyncClient", stub)

        rc = cli.handle_login(
            [
                "--email",
                "dev@gobifrost.com",
                "--password",
                "password",
                "--url",
                "http://localhost:38421",
            ]
        )
        assert rc == 2


class TestPasswordLoginUsesBifrostApiUrl:
    def test_falls_back_to_env_var_for_url(self, capsys, monkeypatch, tmp_path):
        from bifrost import credentials as creds_mod

        stub = _stub_post(
            {
                "access_token": "at",
                "refresh_token": "rt",
                "expires_in": 1800,
            }
        )
        monkeypatch.setattr("httpx.AsyncClient", stub)
        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login(
            [
                "--email",
                "dev@gobifrost.com",
                "--password",
                "password",
            ]
        )
        assert rc == 0
        env_text = (tmp_path / ".env").read_text()
        assert "BIFROST_API_URL=http://localhost:38421" in env_text


class TestBrowserLoginWritesEnv:
    """Browser flow on success writes BIFROST_API_URL=<url> to CWD .env."""

    def test_writes_env_after_successful_browser_login(self, monkeypatch, tmp_path, capsys):
        async def fake_login(api_url=None, auto_open=True):
            return True

        monkeypatch.setattr(cli, "native_login_flow", fake_login)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login(["--url", "https://prod.example.com"])
        assert rc == 0

        env_text = (tmp_path / ".env").read_text()
        assert "BIFROST_API_URL=https://prod.example.com" in env_text

    def test_updates_existing_bifrost_api_url_line_in_place(self, monkeypatch, tmp_path):
        async def fake_login(api_url=None, auto_open=True):
            return True

        monkeypatch.setattr(cli, "native_login_flow", fake_login)
        monkeypatch.chdir(tmp_path)

        # Pre-existing .env with another var and a stale BIFROST_API_URL line
        (tmp_path / ".env").write_text("OTHER_VAR=keep-me\nBIFROST_API_URL=http://stale.example.com\n")

        cli.handle_login(["--url", "https://prod.example.com"])
        env_text = (tmp_path / ".env").read_text()

        assert "OTHER_VAR=keep-me" in env_text
        assert "BIFROST_API_URL=https://prod.example.com" in env_text
        assert "stale.example.com" not in env_text

    def test_appends_env_to_gitignore_if_absent(self, monkeypatch, tmp_path):
        async def fake_login(api_url=None, auto_open=True):
            return True

        monkeypatch.setattr(cli, "native_login_flow", fake_login)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("node_modules\n*.pyc\n")

        cli.handle_login(["--url", "https://prod.example.com"])

        gi = (tmp_path / ".gitignore").read_text()
        assert ".env" in gi.splitlines()

    def test_does_not_duplicate_env_in_gitignore(self, monkeypatch, tmp_path):
        async def fake_login(api_url=None, auto_open=True):
            return True

        monkeypatch.setattr(cli, "native_login_flow", fake_login)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".gitignore").write_text("node_modules\n.env\n*.pyc\n")

        cli.handle_login(["--url", "https://prod.example.com"])

        gi = (tmp_path / ".gitignore").read_text()
        assert gi.count(".env") == 1

    def test_browser_login_removes_stale_password_grant_tokens(self, monkeypatch, tmp_path):
        async def fake_login(api_url=None, auto_open=True):
            return True

        monkeypatch.setattr(cli, "native_login_flow", fake_login)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "OTHER_VAR=keep-me\n"
            "BIFROST_API_URL=https://old.example.com\n"
            "BIFROST_ACCESS_TOKEN=stale-at\n"
            "BIFROST_REFRESH_TOKEN=stale-rt\n"
        )

        rc = cli.handle_login(["--url", "https://prod.example.com"])

        assert rc == 0
        env_text = (tmp_path / ".env").read_text()
        assert "OTHER_VAR=keep-me" in env_text
        assert "BIFROST_API_URL=https://prod.example.com" in env_text
        assert "BIFROST_ACCESS_TOKEN=" not in env_text
        assert "BIFROST_REFRESH_TOKEN=" not in env_text

    def test_does_not_write_env_when_browser_login_fails(self, monkeypatch, tmp_path):
        async def fake_login(api_url=None, auto_open=True):
            return False

        monkeypatch.setattr(cli, "native_login_flow", fake_login)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login(["--url", "https://prod.example.com"])
        assert rc == 1
        assert not (tmp_path / ".env").exists()

    def test_default_browser_login_uses_bifrost_api_url_env(self, monkeypatch, tmp_path):
        seen: dict[str, object] = {}

        async def fake_login(api_url=None, auto_open=True):
            seen["api_url"] = api_url
            seen["auto_open"] = auto_open
            return True

        monkeypatch.setenv("BIFROST_API_URL", "https://env.example.com/")
        monkeypatch.setattr(cli, "native_login_flow", fake_login)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login([])

        assert rc == 0
        assert seen == {"api_url": "https://env.example.com", "auto_open": True}
        assert "BIFROST_API_URL=https://env.example.com" in (tmp_path / ".env").read_text()

    def test_default_browser_login_requires_explicit_or_env_url(self, monkeypatch, tmp_path, capsys):
        async def fake_login(api_url=None, auto_open=True):  # pragma: no cover - must not be called
            raise AssertionError("native login should not run without a URL")

        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.setattr(cli, "native_login_flow", fake_login)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login([])

        assert rc == 1
        assert "requires --url or BIFROST_API_URL" in capsys.readouterr().err
        assert not (tmp_path / ".env").exists()

    def test_device_code_flag_uses_legacy_device_flow(self, monkeypatch, tmp_path):
        seen: dict[str, object] = {}

        async def fake_native(api_url=None, auto_open=True):  # pragma: no cover - must not be called
            raise AssertionError("native login should not run for --device-code")

        async def fake_device(api_url=None, auto_open=True):
            seen["api_url"] = api_url
            seen["auto_open"] = auto_open
            return True

        monkeypatch.setattr(cli, "native_login_flow", fake_native)
        monkeypatch.setattr(cli, "device_login_flow", fake_device)
        monkeypatch.chdir(tmp_path)

        rc = cli.handle_login(["--url", "https://prod.example.com/", "--device-code", "--no-browser"])

        assert rc == 0
        assert seen == {"api_url": "https://prod.example.com", "auto_open": False}
        assert "BIFROST_API_URL=https://prod.example.com" in (tmp_path / ".env").read_text()


class TestNativeLoginFlow:
    @pytest.mark.asyncio
    async def test_callback_server_accepts_valid_callback(self):
        server, future, redirect_uri = cli._open_cli_callback_server("state-1")
        try:
            with urllib.request.urlopen(  # noqa: S310 - localhost test callback
                f"{redirect_uri}?state=state-1&code=code-1&transaction_id=txn-1",
                timeout=5,
            ) as response:
                body = response.read().decode("utf-8")

            assert response.status == 200
            assert "login complete" in body
            assert await asyncio.wait_for(future, timeout=5) == {
                "state": "state-1",
                "code": "code-1",
                "transaction_id": "txn-1",
            }
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("query", "expected_error"),
        [
            ("state=wrong&code=code-1&transaction_id=txn-1", "Invalid OAuth state"),
            ("state=state-1&code=code-1", "Missing code or transaction_id"),
        ],
    )
    async def test_callback_server_rejects_bad_callback(self, query, expected_error):
        server, future, redirect_uri = cli._open_cli_callback_server("state-1")
        try:
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f"{redirect_uri}?{query}", timeout=5)  # noqa: S310

            assert exc.value.code == 400
            with pytest.raises(RuntimeError, match=expected_error):
                await asyncio.wait_for(future, timeout=5)
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.asyncio
    async def test_callback_server_returns_404_for_other_paths(self):
        server, _future, redirect_uri = cli._open_cli_callback_server("state-1")
        try:
            other_uri = redirect_uri.replace("/callback", "/elsewhere")
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(other_uri, timeout=5)  # noqa: S310

            assert exc.value.code == 404
        finally:
            server.shutdown()
            server.server_close()

    @pytest.mark.asyncio
    async def test_native_login_saves_tokens_and_reports_user(self, monkeypatch, capsys):
        saved: list[dict[str, str]] = []
        opened_urls: list[str] = []

        class FakeServer:
            def __init__(self):
                self.shutdown_called = False
                self.close_called = False

            def shutdown(self):
                self.shutdown_called = True

            def server_close(self):
                self.close_called = True

        server = FakeServer()

        def open_callback_server(expected_state):
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            future.set_result(
                {
                    "transaction_id": "txn-1",
                    "code": "code-1",
                    "state": expected_state,
                }
            )
            return server, future, "http://127.0.0.1:12345/callback"

        class Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class Client:
            def __init__(self, *args, **kwargs):
                self.base_url = kwargs["base_url"]

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                if path == "/auth/cli/start":
                    assert json["redirect_uri"] == "http://127.0.0.1:12345/callback"
                    assert json["code_challenge_method"] == "S256"
                    return Response(
                        200,
                        {
                            "authorization_url": "/auth/cli/authorize?transaction_id=txn-1",
                            "expires_in": 30,
                        },
                    )
                if path == "/auth/cli/token":
                    assert json["transaction_id"] == "txn-1"
                    assert json["code"] == "code-1"
                    assert json["code_verifier"]
                    return Response(
                        200,
                        {
                            "access_token": "access-1",
                            "refresh_token": "refresh-1",
                            "expires_in": 1800,
                        },
                    )
                raise AssertionError(f"unexpected POST {path}")

            async def get(self, path, headers=None):
                assert path == "/auth/me"
                assert headers == {"Authorization": "Bearer access-1"}
                return Response(200, {"email": "dev@example.test"})

        monkeypatch.setattr(cli, "_open_cli_callback_server", open_callback_server)
        monkeypatch.setattr(cli.httpx, "AsyncClient", Client)
        monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened_urls.append(url))
        monkeypatch.setattr(
            cli.credentials,
            "save_credentials",
            lambda **kwargs: saved.append(kwargs),
        )
        monkeypatch.setattr(
            "bifrost.credentials.warn_if_keyring_fallback",
            lambda: None,
        )

        assert await cli.native_login_flow("https://api.example.test/", auto_open=True)

        assert opened_urls == [
            "https://api.example.test/auth/cli/authorize?transaction_id=txn-1"
        ]
        assert saved[0]["api_url"] == "https://api.example.test"
        assert saved[0]["access_token"] == "access-1"
        assert saved[0]["refresh_token"] == "refresh-1"
        assert server.shutdown_called is True
        assert server.close_called is True
        assert "Logged in as dev@example.test" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_native_login_reports_start_error(self, monkeypatch, capsys):
        class FakeServer:
            def shutdown(self):
                pass

            def server_close(self):
                pass

        def open_callback_server(_expected_state):
            loop = asyncio.get_running_loop()
            return FakeServer(), loop.create_future(), "http://127.0.0.1/callback"

        class Response:
            status_code = 503

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                assert path == "/auth/cli/start"
                return Response()

        monkeypatch.setattr(cli, "_open_cli_callback_server", open_callback_server)
        monkeypatch.setattr(cli.httpx, "AsyncClient", Client)
        monkeypatch.setattr(
            "bifrost.credentials.warn_if_keyring_fallback",
            lambda: None,
        )

        assert await cli.native_login_flow("https://api.example.test") is False
        assert "Error starting native OAuth login: 503" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_native_login_reports_token_exchange_error(self, monkeypatch, capsys):
        class FakeServer:
            def shutdown(self):
                pass

            def server_close(self):
                pass

        def open_callback_server(expected_state):
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            future.set_result(
                {
                    "transaction_id": "txn-1",
                    "code": "code-1",
                    "state": expected_state,
                }
            )
            return FakeServer(), future, "http://127.0.0.1/callback"

        class Response:
            def __init__(self, status_code, payload=None):
                self.status_code = status_code
                self._payload = payload or {}

            def json(self):
                return self._payload

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                if path == "/auth/cli/start":
                    return Response(
                        200,
                        {
                            "authorization_url": "/auth/cli/authorize",
                            "expires_in": 30,
                        },
                    )
                if path == "/auth/cli/token":
                    return Response(401)
                raise AssertionError(f"unexpected POST {path}")

        monkeypatch.setattr(cli, "_open_cli_callback_server", open_callback_server)
        monkeypatch.setattr(cli.httpx, "AsyncClient", Client)
        monkeypatch.setattr(
            "bifrost.credentials.warn_if_keyring_fallback",
            lambda: None,
        )

        assert await cli.native_login_flow("https://api.example.test", auto_open=False) is False
        captured = capsys.readouterr()
        assert "Open this URL to continue" in captured.out
        assert "Error exchanging native OAuth token: 401" in captured.err

    @pytest.mark.asyncio
    async def test_native_login_falls_back_when_user_info_fails(self, monkeypatch, capsys):
        saved: list[dict[str, str]] = []

        class FakeServer:
            def shutdown(self):
                pass

            def server_close(self):
                pass

        def open_callback_server(expected_state):
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            future.set_result(
                {
                    "transaction_id": "txn-1",
                    "code": "code-1",
                    "state": expected_state,
                }
            )
            return FakeServer(), future, "http://127.0.0.1/callback"

        class Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                if path == "/auth/cli/start":
                    return Response(
                        200,
                        {
                            "authorization_url": "/auth/cli/authorize",
                            "expires_in": 30,
                        },
                    )
                if path == "/auth/cli/token":
                    return Response(
                        200,
                        {
                            "access_token": "access-1",
                            "refresh_token": "refresh-1",
                            "expires_in": 1800,
                        },
                    )
                raise AssertionError(f"unexpected POST {path}")

            async def get(self, path, headers=None):
                raise RuntimeError("profile unavailable")

        monkeypatch.setattr(cli, "_open_cli_callback_server", open_callback_server)
        monkeypatch.setattr(cli.httpx, "AsyncClient", Client)
        monkeypatch.setattr(
            cli.credentials,
            "save_credentials",
            lambda **kwargs: saved.append(kwargs),
        )
        monkeypatch.setattr(
            "bifrost.credentials.warn_if_keyring_fallback",
            lambda: None,
        )

        assert await cli.native_login_flow("https://api.example.test", auto_open=False)
        assert saved[0]["access_token"] == "access-1"
        assert "Logged in successfully" in capsys.readouterr().out


class TestDeviceLoginFlow:
    @pytest.mark.asyncio
    async def test_device_login_uses_env_default_and_reports_code_request_error(
        self,
        monkeypatch,
        capsys,
    ):
        class Response:
            status_code = 503

            def json(self):
                return {}

        class Client:
            def __init__(self, *args, **kwargs):
                assert kwargs["base_url"] == "https://env.example.test"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                assert path == "/auth/device/code"
                return Response()

        monkeypatch.setenv("BIFROST_DEV_URL", "https://env.example.test/")
        monkeypatch.setattr(cli.httpx, "AsyncClient", Client)
        monkeypatch.setattr(
            "bifrost.credentials.warn_if_keyring_fallback",
            lambda: None,
        )

        assert await cli.device_login_flow(api_url=None, auto_open=False) is False
        assert "Error requesting device code: 503" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_device_login_polls_until_token_and_saves_credentials(
        self,
        monkeypatch,
        capsys,
    ):
        saved: list[dict[str, str]] = []
        sleeps: list[int] = []

        class Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class Client:
            token_polls = 0

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                if path == "/auth/device/code":
                    return Response(
                        200,
                        {
                            "device_code": "device-1",
                            "user_code": "ABCD-1234",
                            "verification_url": "/device",
                            "interval": 2,
                        },
                    )
                if path == "/auth/device/token":
                    assert json == {"device_code": "device-1"}
                    self.token_polls += 1
                    if self.token_polls == 1:
                        return Response(200, {"error": "authorization_pending"})
                    return Response(
                        200,
                        {
                            "access_token": "access-2",
                            "refresh_token": "refresh-2",
                            "expires_in": 1800,
                        },
                    )
                raise AssertionError(f"unexpected POST {path}")

            async def get(self, path, headers=None):
                assert path == "/auth/me"
                assert headers == {"Authorization": "Bearer access-2"}
                return Response(200, {"email": "device@example.test"})

        async def sleep(delay):
            sleeps.append(delay)

        monkeypatch.setattr(cli.httpx, "AsyncClient", Client)
        monkeypatch.setattr(cli.asyncio, "sleep", sleep)
        monkeypatch.setattr(
            cli.credentials,
            "save_credentials",
            lambda **kwargs: saved.append(kwargs),
        )
        monkeypatch.setattr(
            "bifrost.credentials.warn_if_keyring_fallback",
            lambda: None,
        )

        assert await cli.device_login_flow("https://api.example.test", auto_open=False)

        assert sleeps == [2, 2]
        assert saved[0]["api_url"] == "https://api.example.test"
        assert saved[0]["access_token"] == "access-2"
        assert saved[0]["refresh_token"] == "refresh-2"
        out = capsys.readouterr().out
        assert "Enter this code: ABCD-1234" in out
        assert "Logged in as device@example.test" in out

    @pytest.mark.asyncio
    async def test_device_login_reports_poll_http_error_and_auth_me_fallback(
        self,
        monkeypatch,
        capsys,
    ):
        class Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class PollErrorClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                if path == "/auth/device/code":
                    return Response(
                        200,
                        {
                            "device_code": "device-1",
                            "user_code": "ABCD-1234",
                            "verification_url": "/device",
                            "interval": 1,
                        },
                    )
                if path == "/auth/device/token":
                    return Response(500, {})
                raise AssertionError(f"unexpected POST {path}")

        class SuccessClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                if path == "/auth/device/code":
                    return Response(
                        200,
                        {
                            "device_code": "device-1",
                            "user_code": "ABCD-1234",
                            "verification_url": "/device",
                            "interval": 1,
                        },
                    )
                if path == "/auth/device/token":
                    return Response(
                        200,
                        {
                            "access_token": "access-2",
                            "refresh_token": "refresh-2",
                            "expires_in": 1800,
                        },
                    )
                raise AssertionError(f"unexpected POST {path}")

            async def get(self, path, headers=None):
                raise RuntimeError("profile unavailable")

        async def sleep(_delay):
            return None

        monkeypatch.setattr(cli.httpx, "AsyncClient", PollErrorClient)
        monkeypatch.setattr(cli.asyncio, "sleep", sleep)
        monkeypatch.setattr(
            cli.credentials,
            "save_credentials",
            lambda **_kwargs: None,
        )
        monkeypatch.setattr(
            "bifrost.credentials.warn_if_keyring_fallback",
            lambda: None,
        )

        assert await cli.device_login_flow("https://api.example.test", auto_open=False) is False
        assert "Error polling for token: 500" in capsys.readouterr().err

        monkeypatch.setattr(cli.httpx, "AsyncClient", SuccessClient)
        assert await cli.device_login_flow("https://api.example.test", auto_open=False)
        assert "Logged in successfully" in capsys.readouterr().out

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            ("expired_token", "Device code expired"),
            ("access_denied", "Authorization denied"),
            ("slow_down", "Unknown error: slow_down"),
        ],
    )
    async def test_device_login_reports_terminal_poll_errors(
        self,
        monkeypatch,
        capsys,
        error,
        expected,
    ):
        class Response:
            def __init__(self, status_code, payload):
                self.status_code = status_code
                self._payload = payload

            def json(self):
                return self._payload

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, path, json=None):
                if path == "/auth/device/code":
                    return Response(
                        200,
                        {
                            "device_code": "device-1",
                            "user_code": "ABCD-1234",
                            "verification_url": "/device",
                            "interval": 1,
                        },
                    )
                if path == "/auth/device/token":
                    return Response(200, {"error": error})
                raise AssertionError(f"unexpected POST {path}")

        async def sleep(_delay):
            return None

        monkeypatch.setattr(cli.httpx, "AsyncClient", Client)
        monkeypatch.setattr(cli.asyncio, "sleep", sleep)
        monkeypatch.setattr(
            "bifrost.credentials.warn_if_keyring_fallback",
            lambda: None,
        )

        assert await cli.device_login_flow("https://api.example.test", auto_open=False) is False
        assert expected in capsys.readouterr().err


class TestLogoutClearsKeychainAndPromptsEnv:
    def test_logout_help_and_unknown_option(self, capsys):
        assert cli.handle_logout(["--help"]) == 0
        assert "Usage: bifrost logout" in capsys.readouterr().out

        assert cli.handle_logout(["--url"]) == 1
        assert "--url requires a value" in capsys.readouterr().err

        assert cli.handle_logout(["--bogus"]) == 1
        assert "Unknown option: --bogus" in capsys.readouterr().err

    def test_logout_clears_specific_url(self, monkeypatch, tmp_path):
        from bifrost import credentials as creds_mod

        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)

        creds_mod.save_credentials("https://prod.example.com", "at", "rt", "2099-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://localhost:38421", "at2", "rt2", "2099-01-01T00:00:00+00:00")

        rc = cli.handle_logout(["--url", "https://prod.example.com", "--no-prompt"])
        assert rc == 0
        assert creds_mod.get_credentials("https://prod.example.com") is None
        assert creds_mod.get_credentials("http://localhost:38421") is not None

    def test_logout_yes_removes_matching_browser_env_binding(self, monkeypatch, tmp_path):
        from bifrost import credentials as creds_mod

        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)

        creds_mod.save_credentials("https://prod.example.com", "at", "rt", "2099-01-01T00:00:00+00:00")
        (tmp_path / ".env").write_text("OTHER_VAR=keep-me\nBIFROST_API_URL=https://prod.example.com\n")

        rc = cli.handle_logout(
            [
                "--url",
                "https://prod.example.com",
                "--yes",
            ]
        )
        assert rc == 0
        env_text = (tmp_path / ".env").read_text()
        assert "OTHER_VAR=keep-me" in env_text
        assert "BIFROST_API_URL=" not in env_text

    def test_logout_yes_removes_complete_password_grant_binding(
        self, monkeypatch, tmp_path,
    ):
        from bifrost import credentials as creds_mod

        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")
        monkeypatch.setenv("BIFROST_ACCESS_TOKEN", "debug-at")
        monkeypatch.setenv("BIFROST_REFRESH_TOKEN", "debug-rt")
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text(
            "OTHER_VAR=keep-me\n"
            "BIFROST_API_URL=http://localhost:38421\n"
            "BIFROST_ACCESS_TOKEN=debug-at\n"
            "BIFROST_REFRESH_TOKEN=debug-rt\n"
        )

        rc = cli.handle_logout([
            "--url", "http://localhost:38421",
            "--yes",
        ])

        assert rc == 0
        assert (tmp_path / ".env").read_text() == "OTHER_VAR=keep-me\n"

    def test_logout_does_not_remove_different_folder_binding(
        self, monkeypatch, tmp_path,
    ):
        from bifrost import credentials as creds_mod

        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)
        creds_mod.save_credentials(
            "https://prod.example.com",
            "prod-at",
            "prod-rt",
            "2099-01-01T00:00:00+00:00",
        )
        binding = (
            "BIFROST_API_URL=http://localhost:38421\n"
            "BIFROST_ACCESS_TOKEN=debug-at\n"
            "BIFROST_REFRESH_TOKEN=debug-rt\n"
        )
        (tmp_path / ".env").write_text(binding)

        rc = cli.handle_logout([
            "--url", "https://prod.example.com",
            "--yes",
        ])

        assert rc == 0
        assert (tmp_path / ".env").read_text() == binding

    def test_logout_no_prompt_leaves_env_alone(self, monkeypatch, tmp_path):
        from bifrost import credentials as creds_mod

        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.chdir(tmp_path)

        creds_mod.save_credentials("https://prod.example.com", "at", "rt", "2099-01-01T00:00:00+00:00")
        (tmp_path / ".env").write_text("BIFROST_API_URL=https://prod.example.com\n")

        rc = cli.handle_logout(
            [
                "--url",
                "https://prod.example.com",
                "--no-prompt",
            ]
        )
        assert rc == 0
        assert (tmp_path / ".env").read_text() == "BIFROST_API_URL=https://prod.example.com\n"

    def test_logout_no_prompt_leaves_dotenv_only_session_alone(self, monkeypatch, tmp_path):
        """Password-grant sessions live only in CWD .env — logout must honor --no-prompt."""
        from bifrost import credentials as creds_mod

        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_API_URL", raising=False)
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)
        monkeypatch.chdir(tmp_path)

        env_before = (
            "BIFROST_API_URL=https://prod.example.com\n"
            "BIFROST_ACCESS_TOKEN=secret-at\n"
            "BIFROST_REFRESH_TOKEN=secret-rt\n"
        )
        (tmp_path / ".env").write_text(env_before)

        rc = cli.handle_logout(
            [
                "--url",
                "https://prod.example.com",
                "--no-prompt",
            ]
        )
        assert rc == 0
        assert (tmp_path / ".env").read_text() == env_before

    def test_logout_prompt_decline_or_eof_leaves_env_alone(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("BIFROST_API_URL=https://prod.example.com\n")
        monkeypatch.setattr(
            cli,
            "logout_flow",
            lambda api_url=None: (True, "https://prod.example.com"),
        )

        monkeypatch.setattr("builtins.input", lambda _prompt: "n")
        assert cli.handle_logout([]) == 0
        assert (tmp_path / ".env").read_text() == "BIFROST_API_URL=https://prod.example.com\n"

        monkeypatch.setattr(
            "builtins.input",
            lambda _prompt: (_ for _ in ()).throw(EOFError()),
        )
        assert cli.handle_logout([]) == 0
        assert (tmp_path / ".env").read_text() == "BIFROST_API_URL=https://prod.example.com\n"


class TestAuthList:
    def test_auth_help_and_unknown_subcommand(self, capsys):
        assert cli.handle_auth([]) == 1
        assert "Usage: bifrost auth" in capsys.readouterr().out

        assert cli.handle_auth(["--help"]) == 0
        assert "Usage: bifrost auth" in capsys.readouterr().out

        assert cli.handle_auth(["wat"]) == 1
        assert "Unknown auth subcommand: wat" in capsys.readouterr().err

    def test_auth_list_with_no_credentials(self, monkeypatch, tmp_path, capsys):
        from bifrost import credentials as creds_mod

        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()

        rc = cli.handle_auth(["list"])
        assert rc == 0
        assert "No stored credentials" in capsys.readouterr().out

    def test_auth_list_marks_current_via_env_var(self, monkeypatch, tmp_path, capsys):
        from bifrost import credentials as creds_mod

        monkeypatch.setattr(
            creds_mod,
            "get_credentials_path",
            lambda: tmp_path / "credentials.json",
        )
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.setattr(creds_mod, "_select_persistent_backend", creds_mod.JsonBackend)
        creds_mod._reset_persistent_backend_for_tests()
        monkeypatch.delenv("BIFROST_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("BIFROST_REFRESH_TOKEN", raising=False)

        creds_mod.save_credentials("https://prod.example.com", "at", "rt", "2099-01-01T00:00:00+00:00")
        creds_mod.save_credentials("http://localhost:38421", "at2", "rt2", "2099-01-01T00:00:00+00:00")
        monkeypatch.setenv("BIFROST_API_URL", "http://localhost:38421")

        rc = cli.handle_auth(["list"])
        assert rc == 0
        listed_urls = []
        current_urls = []
        for raw_line in capsys.readouterr().out.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            url_part = stripped.split()[0]
            listed_urls.append(url_part)
            if "current" in raw_line:
                current_urls.append(url_part)
        assert sorted(listed_urls) == sorted(
            [
                "https://prod.example.com",
                "http://localhost:38421",
            ]
        )
        assert current_urls == ["http://localhost:38421"], f"expected only the env-var URL flagged, got {current_urls}"
