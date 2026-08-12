# MCP conformance runner

This directory exact-pins the official MCP conformance CLI independently of
Bifrost's Python MCP SDK. Run it through the Docker test environment:

```bash
./test.sh stack up
./test.sh mcp conformance
```

The runner is intentionally advisory. Its JSON checks are written under the
worktree-specific `/tmp/bifrost-*/mcp-conformance/` directory and uploaded by
CI even when a check fails.

## Current baseline

The dependency-independent foundation runs `server-initialize` using the
runner's published `2025-11-25` schema against the real
`http://api:8000/mcp` endpoint. `expected-failures.yml` temporarily records
the whole scenario because its prerequisites are not yet available. In this
checkout the API cannot mount `/mcp` because the installed FastMCP auth API
expects `challenge_scopes`; after that dependency mismatch is resolved,
Bifrost still requires a bearer token and the official server runner does not
expose an option for supplying one. The baseline covers either prerequisite
failure. It does not bypass authentication or dispatch and is not evidence
that initialization itself passes. An unexpected pass also fails the baseline
check so the entry cannot silently become stale.

Existing Bifrost E2E tests remain the blocking authority for authentication,
legacy initialization, gateway filtering, agent scoping, and dispatch.
They currently exercise the server's supported `2024-11-05` initialization
surface. Promoting the supported legacy revision to `2025-11-25` belongs with
the dual-era endpoint work rather than this dependency-independent harness.

## Applicable after #524

Once #524 lands the modern `2026-07-28` endpoint shape and an authenticated
test adapter that still traverses Bifrost's real auth and dispatch layers, the
first applicable official server scenarios are:

- `server-stateless`
- `tools-list`
- `dns-rebinding-protection`
- `caching`
- the tool-call scenarios that can be backed by real Bifrost tools without
  introducing conformance-only production behavior

Run those with check-level expected failures. Do not baseline an entire
scenario after its prerequisites can execute.

The outbound client scenarios remain blocked until #524 supplies the modern
lifecycle and controlled legacy fallback. Initial candidates are `tools_call`,
`request-metadata`, `http-standard-headers`, `http-custom-headers`, and
`http-invalid-tool-headers`.

## Deliberate exclusions

The full frozen requirement sets are not a merge gate. They assume canonical
fixture tools and capabilities such as resources, prompts, completion,
multiple content types, progress, SSE, MRTR, and optional extensions. Bifrost's
small progressive gateway does not advertise all of those surfaces. A full
requirements run may be retained for scheduled/release visibility, but it must
not be reported as passing conformance unless every scored requirement passes.

The following stay in bespoke Bifrost tests rather than this runner:

- missing, invalid, and role-scoped bearer-token behavior
- the exact four-tool progressive gateway contract
- organization and agent-scoped filtering
- live registration, rename, revocation, and cross-replica consistency
- catalog persistence, retry, result-size, and OAuth-token semantics

## Updating the runner

Change the exact version in `package.json`, regenerate `package-lock.json` with
`npm install --package-lock-only --ignore-scripts`, update the image tag in
`docker-compose.test.yml`, and review the official scenario and frozen
requirement diffs before committing. Never replace the exact version with a
range or floating tag.
