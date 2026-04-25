# Test Performance

The default CI path is optimized for fast pull request feedback. Pull requests run the backend test suite without coverage, while pushes to `main`, version tags, and manual workflow runs still collect coverage and upload `coverage.xml` to Codecov.

## CI Paths

- Pull request gate: `./test.sh --ci`
- Coverage gate: `./test.sh --coverage --ci`
- Client-only Playwright gate: `PLAYWRIGHT_WORKERS=2 ./test.sh --client-only --ci`

The test runner prints a timing summary at the end of each run. Use those phase timings to compare image build, infrastructure startup, unit pytest, e2e startup, e2e pytest, client startup, and Playwright runtime.

## Playwright Workers

Playwright uses `PLAYWRIGHT_WORKERS` when it is set. Invalid, empty, or less-than-one values fall back to safe defaults:

- CI default: `2`
- Local default: `4`

To reproduce the previous single-worker CI behavior:

```bash
PLAYWRIGHT_WORKERS=1 ./test.sh --client-only --ci
```

To try a higher-concurrency run on a larger runner:

```bash
PLAYWRIGHT_WORKERS=3 ./test.sh --client-only --ci
```

## Benchmark Commands

On PowerShell:

```powershell
Measure-Command { bash ./test.sh --ci }
Measure-Command { bash ./test.sh --coverage --ci }
Measure-Command { bash ./test.sh --client-only --ci }
```

On Bash:

```bash
time ./test.sh --ci
time ./test.sh --coverage --ci
time ./test.sh --client-only --ci
```

For before-and-after proof, save the final timing summary from each command and compare the matching phase names.
