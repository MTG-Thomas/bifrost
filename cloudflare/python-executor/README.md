# Bifrost Cloudflare Python executor

This is the optional remote runtime used by workflows whose
`execution_backend` is `cloudflare-python`. Bifrost remains responsible for
queueing, authorization, execution rows, status events, history, and results.
The Worker receives one self-contained Python module over authenticated HTTPS,
executes its selected function, and returns Bifrost's normal result envelope.

## Deploy

```bash
cd cloudflare/python-executor
uv run pywrangler secret put EXECUTOR_TOKEN
uv run pywrangler deploy
```

Pywrangler requires `uv`, Node.js 22 or newer, Wrangler 4.64 or newer, and
`workers-py` 1.72 or newer. Validate the package-enabled bundle without
deploying it with `uv run pywrangler deploy --dry-run`.

Configure the Bifrost worker service with the deployed URL and the same secret:

```dotenv
BIFROST_CLOUDFLARE_PYTHON_EXECUTOR_URL=https://bifrost-python-executor.<account>.workers.dev
BIFROST_CLOUDFLARE_PYTHON_EXECUTOR_TOKEN=<shared-random-secret>
```

Then opt in a workflow:

```bash
bifrost workflows update <workflow-ref> --execution-backend cloudflare-python
```

Packages required by remote workflows must be listed in this directory's
`pyproject.toml` before deployment. Pure-Python and PyEmscripten wheels are
supported by Cloudflare; ordinary native Linux wheels are not.

## MVP compatibility boundary

- One self-contained workflow module; relative workspace imports are rejected.
- Async and sync Python functions are supported.
- `@workflow`, `@tool`, `@data_provider`, and read-only `bifrost.context`
  access are provided by a small compatibility module.
- Container-only SDK operations, dynamic `pip install`, subprocesses, `fork`,
  multiprocessing, threads, signals, and persistent local files are unsupported.
- Solution-managed workflows and inline scripts stay on the process backend.
- Running remote executions cannot yet be cancelled from Bifrost.
- Worker stdout is returned in the protocol response but is not yet streamed
  into Bifrost's live execution log.
- The Cloudflare Workers limits still apply: 128 MB isolate memory, configured
  plan CPU allowance, deployment bundle limits, and runtime compatibility
  rules.

This boundary is explicit: selecting `cloudflare-python` never silently falls
back to the process worker.
