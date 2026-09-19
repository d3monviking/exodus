#!/usr/bin/env bash
# Fire one release right now (same endpoint the timer loop calls) and print the result.
#   scripts/release.sh [api_base_url] [--force]
# --force skips the "released this route a moment ago" guard, for rehearsals.

set -euo pipefail

API="http://127.0.0.1:3000"
QUERY=""
for arg in "$@"; do
  case "$arg" in
    --force) QUERY="?force=1" ;;
    *) API="$arg" ;;
  esac
done

body=$(mktemp)
trap 'rm -f "$body"' EXIT
code=$(curl -s -o "$body" -w "%{http_code}" -X POST "${API}/internal/release${QUERY}")
python3 -m json.tool "$body" 2>/dev/null || cat "$body"
echo "HTTP ${code}"
[ "$code" = "200" ]
