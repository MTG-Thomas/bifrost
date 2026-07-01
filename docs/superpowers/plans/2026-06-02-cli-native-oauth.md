# CLI Native OAuth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make production `bifrost login` use a native browser OAuth-style flow with a localhost callback and PKCE, defaulting the API URL from `BIFROST_API_URL` so users do not type it.

**Architecture:** Add a CLI auth transaction flow beside the existing device-code flow. The CLI starts a localhost callback listener, opens an API authorize URL with a one-time transaction id, state, redirect URI, and PKCE challenge, the browser authenticates through normal production cookies/MFA, the API redirects back to localhost with a short-lived code, and the CLI exchanges code + verifier for normal Bifrost tokens stored in the persistent credential backend. Device-code remains available as explicit fallback.

**Tech Stack:** Python stdlib HTTP server + `httpx` in `api/bifrost/cli.py`; FastAPI auth router in `api/src/routers/auth.py`; Redis transaction state via `get_shared_redis`; Pydantic contracts in `api/src/models/contracts/auth.py`; unit tests with Windows-native `py -3 -m pytest`; backend e2e on pve-t340.

---

## Task 1: URL Resolution And CLI Mode Selection

**Files:**
- Modify: `api/bifrost/credentials.py`
- Modify: `api/bifrost/cli.py`
- Test: `api/tests/unit/test_cli_login_ephemeral.py`

- [ ] Add a strict login URL resolver that uses `--url`, then `BIFROST_API_URL`, then errors without falling back to localhost.
- [ ] Update `bifrost login --help` so production default is native browser OAuth and device-code is explicit fallback.
- [ ] Add `--device-code` flag to preserve current device-code flow.
- [ ] Keep `--email/--password` dev-only password-grant behavior unchanged except it shares the strict URL resolver.
- [ ] Add unit tests for `bifrost login` using `BIFROST_API_URL`, missing URL errors, and `--device-code` dispatch.

## Task 2: Backend Native CLI Auth Contracts And Endpoints

**Files:**
- Modify: `api/src/models/contracts/auth.py`
- Modify: `api/src/models/contracts/__init__.py`
- Modify: `api/src/routers/auth.py`
- Test: `api/tests/e2e/test_cli_native_oauth.py`

- [ ] Add request/response models for native auth start and token exchange.
- [ ] Add `POST /auth/cli/start` to validate localhost redirect URI + PKCE challenge and store transaction state in Redis.
- [ ] Add authenticated `GET /auth/cli/authorize` to mint a one-time auth code and redirect to the CLI callback URL.
- [ ] Add `POST /auth/cli/token` to validate transaction id, code, verifier, and state, then call `_generate_login_tokens()` and delete transaction state.
- [ ] Add e2e coverage for success, invalid redirect URI, invalid verifier, one-time code use, and inactive user rejection.

## Task 3: Native OAuth CLI Flow

**Files:**
- Modify: `api/bifrost/cli.py`
- Test: `api/tests/unit/test_cli_native_oauth.py`

- [ ] Add PKCE verifier/challenge generation.
- [ ] Add localhost callback listener bound to `127.0.0.1` on an ephemeral port.
- [ ] Implement `native_login_flow()` that calls `/auth/cli/start`, opens `/auth/cli/authorize`, waits for callback, exchanges `/auth/cli/token`, saves persistent credentials, and fetches `/auth/me` for the success message.
- [ ] If callback bind fails, report a clear error telling the user to rerun with `--device-code`.
- [ ] Add unit tests with mocked `httpx.AsyncClient`, mocked browser open, and a fake callback result.

## Task 4: Docs, LLM Text, And Verification

**Files:**
- Modify: `.claude/skills/bifrost-debug/SKILL.md` if needed
- Modify: `docs/llm.txt` if login help is documented there
- Test: targeted Windows-native unit tests and pve-t340 e2e

- [ ] Document production login:
  - `setx BIFROST_API_URL "https://app.gobifrost.com"`
  - `bifrost login`
  - `bifrost login --device-code` as fallback
- [ ] Keep debug docs clear that password-grant is debug-only and MFA-refusing.
- [ ] Run local Windows-native tests:
  - `py -3 -m ruff check api/bifrost/cli.py api/bifrost/credentials.py api/tests/unit/test_cli_login_ephemeral.py api/tests/unit/test_cli_native_oauth.py`
  - `$env:PYTHONPATH='api'; py -3 -m pytest api/tests/unit/test_cli_login_ephemeral.py api/tests/unit/test_cli_native_oauth.py -v`
- [ ] Run pve-t340 backend e2e for `api/tests/e2e/test_cli_native_oauth.py`.

---

## Notes

- Do not use password grant for production.
- Do not silently fall back to localhost for production login.
- Do not remove device-code entirely; make it explicit fallback.
- Treat browser auth as the production authority so MFA/passkeys/SSO remain in the browser.
