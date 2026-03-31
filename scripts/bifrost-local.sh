#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${REPO_ROOT}/api${PYTHONPATH:+:${PYTHONPATH}}"
export BIFROST_CREDENTIALS_BACKEND="${BIFROST_CREDENTIALS_BACKEND:-pass}"

if [[ "${1:-}" == "run" ]]; then
  if [[ "${BIFROST_ALLOW_LOCAL_RUN:-0}" != "1" ]]; then
    cat >&2 <<'EOF'
Refusing to use 'bifrost run' through the repo-local wrapper by default.

Use sync + server-side execution for normal workflow development so runs are
visible in the dev instance History view.

Only use 'run' when the user explicitly asked for it, then opt in with:
  BIFROST_ALLOW_LOCAL_RUN=1 ./scripts/bifrost-local.sh run ...

If you specifically want Workbench/local-file execution, use:
  BIFROST_ALLOW_LOCAL_RUN=1 ./scripts/bifrost-local-devrun.sh ...
EOF
    exit 2
  fi
fi

exec python3 -m bifrost.cli "$@"
