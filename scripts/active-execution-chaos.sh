#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"
source "$SCRIPT_DIR/scripts/lib/test_helpers.sh"

export COMPOSE_PROJECT_NAME
COMPOSE_PROJECT_NAME="$(compute_project_name .)"
COMPOSE_FILE="docker-compose.test.yml"
LOG_DIR="/tmp/bifrost-$COMPOSE_PROJECT_NAME"
READY_PATH="$LOG_DIR/active-execution-chaos-ready.json"

case "$COMPOSE_PROJECT_NAME" in
    bifrost-test-*) ;;
    *) echo "ERROR: refusing chaos outside an isolated Bifrost test project" >&2; exit 1 ;;
esac

rm -f "$READY_PATH"
export BIFROST_RUN_ACTIVE_EXECUTION_CHAOS=1
./test.sh tests/e2e/chaos/test_active_execution_recovery.py -v &
pytest_pid=$!

cleanup() {
    rm -f "$READY_PATH"
    if kill -0 "$pytest_pid" 2>/dev/null; then
        kill "$pytest_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

for _ in $(seq 1 120); do
    [ -s "$READY_PATH" ] && break
    if ! kill -0 "$pytest_pid" 2>/dev/null; then
        wait "$pytest_pid"
        exit $?
    fi
    sleep 1
done
[ -s "$READY_PATH" ] || { echo "ERROR: chaos workflow never reached its checkpoint" >&2; exit 1; }

execution_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["execution_id"])' "$READY_PATH")"
echo "Killing worker during execution $execution_id"
docker compose -f "$COMPOSE_FILE" kill -s SIGKILL worker
docker compose -f "$COMPOSE_FILE" start worker

replacement_started_epoch="$(date -u +%s)"
for _ in $(seq 1 60); do
    heartbeat_epoch="$(docker compose -f "$COMPOSE_FILE" exec -T redis sh -lc \
        'redis-cli --scan --pattern "bifrost:pool:*:heartbeat" | head -n 1 | xargs -r redis-cli GET' \
        | python3 -c 'import datetime,json,sys; raw=sys.stdin.read().strip(); print(int(datetime.datetime.fromisoformat(json.loads(raw)["started_at"]).timestamp()) if raw else 0)' \
        2>/dev/null || echo 0)"
    [ "$heartbeat_epoch" -ge "$replacement_started_epoch" ] && break
    sleep 1
done
[ "${heartbeat_epoch:-0}" -ge "$replacement_started_epoch" ] || { echo "ERROR: replacement worker never heartbeated" >&2; exit 1; }
sleep 2

docker compose -f "$COMPOSE_FILE" exec -T \
    -e BIFROST_WORKFLOW_RUNNER_LOSS_MAX_ATTEMPTS=2 \
    -e BIFROST_WORKFLOW_RESTART_ORPHAN_GRACE_SECONDS=1 \
    scheduler python -c \
    'import asyncio, json; from src.jobs.schedulers.execution_cleanup import cleanup_stuck_executions; print(json.dumps(asyncio.run(cleanup_stuck_executions()), default=str))'

wait "$pytest_pid"
trap - EXIT INT TERM
rm -f "$READY_PATH"
echo "Active execution chaos scenario passed for $execution_id"
