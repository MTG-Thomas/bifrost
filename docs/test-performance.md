# Test Performance Notes

## Current test paths

`main` now uses the verb-style test runner and split CI jobs:

- `./test.sh stack up` starts the reusable test stack.
- `./test.sh unit` runs backend unit tests.
- `./test.sh e2e` runs backend E2E tests.
- `./test.sh client e2e` runs Playwright browser tests.
- `./test.sh ci` runs the isolated full local CI path.

PR117 is superseded by this smaller change because the previous branch also carried a stale OAuth fix and old `test.sh` edits. Current `main` already has the larger test runner refactor, so this PR keeps only the remaining low-risk speed knob.

## Playwright workers

Playwright worker count is controlled with `PLAYWRIGHT_WORKERS`.

- CI defaults to `2` workers.
- Local runs default to `4` workers.
- Set `PLAYWRIGHT_WORKERS=1` to restore the previous serialized CI behavior.
- Invalid values fall back to the default instead of failing config load.

When Playwright runs through Docker Compose, `playwright-runner` receives `PLAYWRIGHT_WORKERS` from the host and defaults to `2`.

## Benchmark commands

Use a clean stack for comparable timings:

```bash
Measure-Command { bash ./test.sh stack up }
Measure-Command { bash ./test.sh unit }
Measure-Command { bash ./test.sh e2e }
Measure-Command { bash ./test.sh client e2e }
```

Compare Playwright worker settings directly:

```bash
Measure-Command { $env:PLAYWRIGHT_WORKERS='1'; bash ./test.sh client e2e }
Measure-Command { $env:PLAYWRIGHT_WORKERS='2'; bash ./test.sh client e2e }
```

For a config-only check from `client/`:

```bash
PLAYWRIGHT_WORKERS=1 npx playwright test --list
PLAYWRIGHT_WORKERS=2 npx playwright test --list
PLAYWRIGHT_WORKERS=bogus npx playwright test --list
```
