#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DEV_URL="https://bifrost-poc-host.netbird.cloud:18443/"
PASS_ENTRY="${BIFROST_PASS_ENTRY:-bifrost/credentials}"

printf 'Bifrost Session Bootstrap\n'
printf 'repo_root: %s\n' "${REPO_ROOT}"
printf 'default_dev_url: %s\n' "${DEV_URL}"
printf 'credentials_backend: %s\n' "${BIFROST_CREDENTIALS_BACKEND:-pass}"
printf 'pass_entry: %s\n' "${PASS_ENTRY}"
printf 'local_docker_urls: http://localhost:3000 , http://localhost:8000\n'
printf 'note: localhost is for local Docker only; do not assume it is running.\n'
printf 'note: current credential store is pass; AKV is the long-term target.\n'
printf '\n'

if command -v pass >/dev/null 2>&1; then
  if pass show "${PASS_ENTRY}" >/dev/null 2>&1; then
    printf 'pass_credentials: present\n'
  else
    printf 'pass_credentials: missing\n'
  fi
else
  printf 'pass_credentials: pass-not-installed\n'
fi

if [[ -f "${HOME}/.bifrost/credentials.json" ]]; then
  printf 'file_credentials: present (fallback only)\n'
else
  printf 'file_credentials: missing\n'
fi

printf '\n'
printf 'recommended_exports:\n'
printf '  export BIFROST_CREDENTIALS_BACKEND=pass\n'
printf '  export BIFROST_PASS_ENTRY=%s\n' "${PASS_ENTRY}"
printf '  export BIFROST_SSL_NO_VERIFY=1    # only if NetBird/private CA requires it\n'
printf '\n'
printf 'quick_check:\n'
printf '  cd %s\n' "${REPO_ROOT}"
printf '  ./scripts/bifrost-local.sh api GET /health\n'
