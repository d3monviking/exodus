#!/usr/bin/env bash
# Fire one release right now (same endpoint the timer loop calls) and print the result.
#   scripts/release.sh [api_base_url] [--force] [--quiet]
# --force skips the "released this route a moment ago" guard, for rehearsals.
# --quiet prints only the one-line summary. Either way the exit code is 0 only if
# the release really ran, and a failure says so on stderr: never redirect that away.

set -euo pipefail

API="http://127.0.0.1:3000"
QUERY=""
QUIET=""
for arg in "$@"; do
  case "$arg" in
    --force) QUERY="?force=1" ;;
    --quiet) QUIET=1 ;;
    *) API="$arg" ;;
  esac
done

body=$(mktemp)
trap 'rm -f "$body"' EXIT
# -S keeps curl's error message even with -s: a connection failure must not be silent
if ! code=$(curl -sS -o "$body" -w "%{http_code}" -X POST "${API}/internal/release${QUERY}"); then
  echo "release FAILED: could not reach ${API}. Is scripts/start_api.sh running?" >&2
  exit 1
fi

[ -n "$QUIET" ] || python3 -m json.tool "$body" 2>/dev/null || cat "$body"

python3 - "$body" "$code" <<'PY'
import json, sys
body, code = open(sys.argv[1]).read(), sys.argv[2]
try:
    routes = json.loads(body)
    done = [r for r in routes.values() if r.get("status") == "released" and r.get("stats")]
    errors = [r["error"] for r in routes.values() if r.get("status") == "error"]
except (ValueError, AttributeError):
    routes, done, errors = {}, [], [body[:200]]
if code != "200" or errors:
    print(f"release FAILED (HTTP {code}): {errors[0] if errors else body[:200]}", file=sys.stderr)
    sys.exit(1)
students = sum(r["stats"]["pool_size"] for r in done)
cabs = sum(r["stats"]["groups_of_3"] + r["stats"]["groups_of_2"] + r["stats"]["ungrouped"] for r in done)
print(f"RELEASED: {students} students -> {cabs} cabs" if students else "release ran, but nobody was waiting to be grouped")
PY
