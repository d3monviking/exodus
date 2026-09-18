#!/usr/bin/env bash
# Timer-loop fallback for the two scheduled handlers. LocalStack's EventBridge
# can't directly invoke a function hosted by `sam local start-api` (they're
# separate systems -- see the write-up in the session), so this calls the
# same handlers over HTTP on an interval instead. The handler code is
# identical either way; only the trigger differs.
#
# Usage: scripts/scheduler.sh [api_base_url] [release_interval_s] [sweep_interval_s]

set -euo pipefail

API="${1:-http://127.0.0.1:3000}"
RELEASE_INTERVAL="${2:-120}"   # seconds between release cycles
SWEEP_INTERVAL="${3:-30}"      # seconds between deadline sweeps

echo "scheduler: releasing every ${RELEASE_INTERVAL}s, sweeping every ${SWEEP_INTERVAL}s, against ${API}"

release_loop() {
  while true; do
    sleep "$RELEASE_INTERVAL"
    ts=$(date +%H:%M:%S)
    echo "[$ts] POST /internal/release"
    curl -s -o /dev/null -w "  -> %{http_code}\n" -X POST "${API}/internal/release" || echo "  -> request failed"
  done
}

sweep_loop() {
  while true; do
    sleep "$SWEEP_INTERVAL"
    ts=$(date +%H:%M:%S)
    echo "[$ts] POST /internal/sweep"
    curl -s -o /dev/null -w "  -> %{http_code}\n" -X POST "${API}/internal/sweep" || echo "  -> request failed"
  done
}

release_loop &
RELEASE_PID=$!
sweep_loop &
SWEEP_PID=$!

trap 'echo "stopping scheduler"; kill "$RELEASE_PID" "$SWEEP_PID" 2>/dev/null' EXIT INT TERM

wait
