#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == ".bifrost" || "${1:-}" == ".bifrost/" ]]; then
  if [[ "${BIFROST_ALLOW_MANIFEST_SYNC:-0}" != "1" ]]; then
    cat >&2 <<'EOF'
Refusing to sync .bifrost by default.

.bifrost/*.yaml is treated in this repo as generated or transitional state, not a
normal authored surface. Sync authored paths like features/ or modules/ instead.

If you are intentionally doing a one-off tactical manifest repair, rerun with:
  BIFROST_ALLOW_MANIFEST_SYNC=1 ./scripts/bifrost-local-sync.sh .bifrost
EOF
    exit 2
  fi
fi

exec "${SCRIPT_DIR}/bifrost-local.sh" sync "$@"
