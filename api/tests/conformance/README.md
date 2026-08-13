# MCP conformance runner

This directory exact-pins the official MCP conformance CLI independently of
Bifrost's Python MCP SDK. Run it through the Docker test environment:

```bash
./test.sh stack up
./test.sh mcp conformance
```

The blocking JSON checks, advisory checks, command logs, OAuth preflight, and
`conformance-junit.xml` are written under the worktree-specific
`/tmp/bifrost-*/mcp-conformance/` directory. CI uploads that directory even
when the gate fails.

## Authentication boundary

The exact-pinned `@modelcontextprotocol/conformance@0.2.0-alpha.11` server CLI
has no option for a bearer token or request headers. The
`mcp-conformance-adapter` Compose service closes only that transport gap:

- it runs solely on the isolated test network and exposes no host port;
- it mints a fresh two-hour JWT through `tests.fixtures.auth.create_test_jwt`,
  bound to the real `http://api:8000/mcp` resource, `mcp:access` scope, and MCP
  audience;
- it forwards the raw request line, body, MCP header lines, and raw response
  bytes to/from the real `/mcp` application;
- it replaces only `Authorization` and the canonical upstream `Host` while
  filtering hop-by-hop transport headers; and
- it neither reads nor rewrites MCP payloads and provides no authentication or
  dispatch bypass.

The real Bifrost ASGI boundary, not the adapter, parses RFC 9110 optional
whitespace around MCP standard routing-header values before FastMCP validates
them. The adapter regression test proves those header bytes reach the upstream
socket unchanged.

Before invoking the adapter, `./test.sh` directly requires the protected
endpoint to return `401 Unauthorized` with an RFC 9728 resource-metadata
challenge and requires that metadata to advertise `mcp:access`. Those
non-secret responses are retained as artifacts.

## Blocking subset

The gate runs these official `2026-07-28` server scenarios without an
expected-failures file:

- `tools-list` — 3/3 checks pass and the official artifact records Bifrost's
  exact four gateway tools;
- `caching` — 7/7 exercised checks pass with private, non-negative caching
  hints (the resource-read check skips because this gateway advertises no
  resources); and
- `http-header-validation` — 14/14 checks pass, including required method/name
  headers, mismatch errors, case handling, and RFC 9110 optional whitespace.

After the runner exits, `summarize_results.py` independently requires exactly
one non-empty result directory for every blocking scenario and the exact
reviewed check identity/status profile for this runner pin. It allows only the
documented `resources/read` skip, rejects missing, extra, duplicated, or newly
skipped checks, and requires the exact ordered four-tool gateway list. It
always writes a JUnit report. A runner crash or changed artifact therefore
cannot silently pass as zero or reduced tests.

## Advisory coverage and deliberate exclusions

`server-stateless` is retained as a non-blocking artifact. On Bifrost's
intentional four-tool gateway, 23/25 checks pass. The two remaining checks
(`sep-2575-server-rejects-undeclared-capability` and
`sep-2575-missing-capability-http-400`) require the reference-only
`test_missing_capability` diagnostic tool. Adding that tool to production would
violate the gateway contract, so the scenario is not hidden behind an expected
failure and is not promoted as a whole.

The following official scenarios are not merge gates:

- canonical tool-call and JSON-Schema scenarios require named reference tools;
  notably, `tools-call-simple-text` treats Bifrost's legitimate `isError`
  hidden-tool response as success and is therefore not meaningful evidence;
- resources, prompts, completion, multiple content types, SSE, input-required,
  and extension scenarios require capabilities this gateway does not advertise;
- `http-custom-header-server-validation` requires an `x-mcp-header` reference
  tool; and
- `dns-rebinding-protection` cannot be tested through an adapter that must set
  the canonical upstream `Host`. Bifrost's host/origin policy remains covered
  directly by its MCP transport tests.

The full frozen requirement set is consequently not reported as passing.
Bespoke Bifrost E2E tests remain the authority for bearer-token behavior,
gateway filtering, agent scope, live registration/revocation, dispatch, and
replica-safe catalogs.

## Updating the runner

Change the exact version in `package.json`, regenerate `package-lock.json` with
`npm install --package-lock-only --ignore-scripts`, update the image tag in
`docker-compose.test.yml`, and review official scenario and frozen-requirement
diffs before committing. Never replace the exact version with a range or
floating tag.
