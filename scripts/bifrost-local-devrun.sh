#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <workflow-file> [run args...]" >&2
  echo "Example: $0 features/autotask/workflows/work_item_index.py -w sync_autotask_work_item_index" >&2
  exit 1
fi

exec "${SCRIPT_DIR}/bifrost-local.sh" run "$1" --interactive "${@:2}"
