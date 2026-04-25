#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:3000}"

python scripts/security/create_scan_users.py "$API_URL"
