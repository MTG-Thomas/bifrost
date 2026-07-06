import builtins
from types import SimpleNamespace

import pytest

from bifrost import cli


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["--url"], "--url requires a value"),
        (["--email"], "--email requires a value"),
        (["--password"], "--password requires a value"),
        (["--device-code", "--email", "dev@example.test", "--password", "pw"], "--device-code cannot be used"),
        (["--bad"], "Unknown option"),
    ],
)
def test_handle_login_parser_error_branches(args, expected, capsys):
    assert cli.handle_login(args) == 1
    assert expected in capsys.readouterr().err


def test_handle_login_help(capsys):
    assert cli.handle_login(["--help"]) == 0
    assert "Usage: bifrost login" in capsys.readouterr().out


def test_handle_login_password_grant_env_write_fallback(monkeypatch, capsys):
    async def password_login(api_url, email, password):
        return 0, {
            "access_token": "access-1",
            "refresh_token": "refresh-1",
        }

    def write_env_url(_api_url):
        raise OSError("readonly")

    monkeypatch.setattr(cli, "password_login_flow", password_login)
    monkeypatch.setattr(cli, "_write_env_url", write_env_url)

    rc = cli.handle_login(
        [
            "--url",
            "https://api.example.test",
            "--email",
            "dev@example.test",
            "--password",
            "pw",
        ]
    )

    assert rc == 0
    captured = capsys.readouterr()
    assert "could not update .env" in captured.err
    assert "BIFROST_API_URL=https://api.example.test" in captured.out
    assert "BIFROST_ACCESS_TOKEN=access-1" in captured.out
    assert "BIFROST_REFRESH_TOKEN=refresh-1" in captured.out


def test_handle_login_browser_env_write_warning(monkeypatch, capsys):
    async def native_login(api_url, auto_open=True):
        return True

    monkeypatch.setattr(cli, "native_login_flow", native_login)
    monkeypatch.setattr(cli, "_write_env_url", lambda _url: (_ for _ in ()).throw(OSError("readonly")))

    assert cli.handle_login(["--url", "https://api.example.test"]) == 0
    assert "could not update .env" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "expected_rc", "expected_text", "stream"),
    [
        (["--url"], 1, "--url requires a value", "err"),
        (["--bad"], 1, "Unknown option", "err"),
        (["--help"], 0, "Usage: bifrost logout", "out"),
    ],
)
def test_handle_logout_parser_branches(args, expected_rc, expected_text, stream, capsys):
    assert cli.handle_logout(args) == expected_rc
    captured = capsys.readouterr()
    text = captured.err if stream == "err" else captured.out
    assert expected_text in text


def test_handle_logout_prompt_decline_and_eof(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "BIFROST_API_URL=https://api.example.test\n"
        "BIFROST_ACCESS_TOKEN=access\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "logout_flow", lambda api_url=None: (True, "https://api.example.test"))
    monkeypatch.setattr(builtins, "input", lambda _prompt: "n")

    assert cli.handle_logout([]) == 0
    assert "BIFROST_API_URL=https://api.example.test" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert "Removed" not in capsys.readouterr().out

    monkeypatch.setattr(builtins, "input", lambda _prompt: (_ for _ in ()).throw(EOFError()))
    assert cli.handle_logout([]) == 0
    assert "BIFROST_ACCESS_TOKEN=access" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_handle_logout_yes_removes_matching_url_and_tokens(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "OTHER=keep\n"
        "BIFROST_API_URL=https://api.example.test/\n"
        "BIFROST_ACCESS_TOKEN=access\n"
        "BIFROST_REFRESH_TOKEN=refresh\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "logout_flow", lambda api_url=None: (True, "https://api.example.test"))

    assert cli.handle_logout(["--yes"]) == 0

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "OTHER=keep" in env_text
    assert "BIFROST_API_URL" not in env_text
    assert "BIFROST_ACCESS_TOKEN" not in env_text
    assert "Removed BIFROST_API_URL" in capsys.readouterr().out


def test_handle_auth_help_list_first_stored_and_unknown(monkeypatch, capsys):
    assert cli.handle_auth([]) == 1
    assert "Usage: bifrost auth" in capsys.readouterr().out

    assert cli.handle_auth(["--help"]) == 0
    assert "Usage: bifrost auth" in capsys.readouterr().out

    monkeypatch.delenv("BIFROST_API_URL", raising=False)
    monkeypatch.setattr(
        cli.credentials,
        "list_credentials",
        lambda: ["https://first.example.test", "https://second.example.test"],
    )
    assert cli.handle_auth(["ls"]) == 0
    out = capsys.readouterr().out
    assert "https://first.example.test" in out
    assert "(current" not in out
    assert "https://second.example.test" in out

    assert cli.handle_auth(["unknown"]) == 1
    assert "Unknown auth subcommand" in capsys.readouterr().err


def test_handle_auth_token_refresh_exception_warns_and_emits(monkeypatch, capsys):
    monkeypatch.setattr(
        cli.credentials,
        "get_credentials",
        lambda api_url=None: {
            "api_url": "https://api.example.test",
            "access_token": "stale-access",
        },
    )
    monkeypatch.setattr(cli.credentials, "is_token_expired", lambda api_url=None: True)

    async def refresh_tokens():
        raise RuntimeError("refresh down")

    import bifrost.client as client_mod

    monkeypatch.setattr(client_mod, "refresh_tokens", refresh_tokens)

    assert cli.handle_auth(["token"]) == 0
    captured = capsys.readouterr()
    assert "stale-access" in captured.out
    assert "refresh failed" in captured.err


def test_run_direct_org_context_exception(monkeypatch, capsys):
    class Client:
        _sync_http = SimpleNamespace(
            get=lambda path, params=None: (_ for _ in ()).throw(RuntimeError("network down"))
        )

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            return Client()

    async def workflow():
        return {"ok": True}

    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)

    assert cli._run_direct("workflow", {"workflow": workflow}, {}, organization_id="org-1") == 1
    assert "network down" in capsys.readouterr().err


def test_run_direct_context_setup_best_effort(monkeypatch, capsys):
    class Client:
        user = {"id": "user-1", "email": "dev@example.test"}
        organization = None

    class ClientFactory:
        @staticmethod
        def get_instance(require_auth=True):
            return Client()

    async def workflow():
        return {"ok": True}

    monkeypatch.setattr(cli, "BifrostClient", ClientFactory)
    monkeypatch.setattr(
        "bifrost._context.set_execution_context",
        lambda _ctx: (_ for _ in ()).throw(RuntimeError("context unavailable")),
    )

    assert cli._run_direct("workflow", {"workflow": workflow}, {}, verbose=True) == 0
    out = capsys.readouterr().out
    assert "Running in standalone mode" in out
    assert '"ok": true' in out
