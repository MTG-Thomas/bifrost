#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_ROOT="$REPO_ROOT/api"
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Missing Python environment at $PYTHON_BIN" >&2
  exit 1
fi

resolve_api_url() {
  if [[ -n "${BIFROST_DEV_URL:-}" ]]; then
    printf '%s\n' "$BIFROST_DEV_URL"
    return
  fi

  if [[ -n "${BIFROST_API_URL:-}" ]]; then
    printf '%s\n' "$BIFROST_API_URL"
    return
  fi

  if command -v pass >/dev/null 2>&1 && pass show bifrost/credentials >/dev/null 2>&1; then
    python3 - <<'PY'
import json
import subprocess

raw = subprocess.check_output(["pass", "show", "bifrost/credentials"], text=True)
data = json.loads(raw)
print(data.get("api_url", "https://10.1.23.240.nip.io"))
PY
    return
  fi

  printf '%s\n' "https://10.1.23.240.nip.io"
}

ensure_cert_file() {
  local api_url="$1"

  if [[ -n "${BIFROST_DEV_CERT_FILE:-}" && -f "${BIFROST_DEV_CERT_FILE}" ]]; then
    printf '%s\n' "$BIFROST_DEV_CERT_FILE"
    return
  fi

  local host
  host="$(API_URL="$api_url" python3 - <<'PY'
import os
from urllib.parse import urlparse

print(urlparse(os.environ["API_URL"]).hostname or "")
PY
)"

  if [[ -z "$host" ]]; then
    echo "Could not determine dev host from API URL: $api_url" >&2
    exit 1
  fi

  local cert_file="/tmp/bifrost-dev-${host}.pem"
  openssl s_client -showcerts -servername "$host" -connect "${host}:443" </dev/null 2>/dev/null \
    | awk '/BEGIN CERTIFICATE/,/END CERTIFICATE/{print}' > "$cert_file"

  if [[ ! -s "$cert_file" ]]; then
    echo "Failed to fetch TLS certificate from $host" >&2
    exit 1
  fi

  printf '%s\n' "$cert_file"
}

API_URL="$(resolve_api_url)"
CERT_FILE="$(ensure_cert_file "$API_URL")"

exec env \
  BIFROST_DEV_URL="$API_URL" \
  BIFROST_API_URL="$API_URL" \
  PYTHONPATH="$API_ROOT" \
  SSL_CERT_FILE="$CERT_FILE" \
  "$PYTHON_BIN" -m bifrost.cli watch "$@"
