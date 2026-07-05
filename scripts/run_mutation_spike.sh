#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MUTMUT_VENV="${MUTMUT_VENV:-$ROOT_DIR/.mutmut-venv}"
PROJECT_PYTHON="${PROJECT_PYTHON:-$ROOT_DIR/.venv/bin/python}"

if [ -x "$PROJECT_PYTHON" ]; then
    PYTHON="$PROJECT_PYTHON"
else
    PYTHON="$MUTMUT_VENV/bin/python"
    if [ ! -x "$PYTHON" ]; then
        python3 -m venv "$MUTMUT_VENV"
        "$PYTHON" -m pip install --upgrade pip
        "$PYTHON" -m pip install --require-hashes -r requirements.lock
    fi
fi

if ! "$PYTHON" -c "import mutmut" >/dev/null 2>&1; then
    "$PYTHON" -m pip install "mutmut==3.6.0"
fi

export PYTHONPATH="${PYTHONPATH:-api}"
"$PYTHON" -m mutmut run "$@"
