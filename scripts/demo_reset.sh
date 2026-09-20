#!/usr/bin/env bash
# Put the demo back on its starting line, and prove it is there.
#
#   scripts/demo_reset.sh [api_base_url]
#
# Resets the tables, seeds the 40-student pool (nobody released yet), stops any
# scheduler that would fire releases underneath a recording, then checks that
# the demo will actually behave: the pool is the size the script expects, the
# three demo accounts are what they should be, and the 7 pm window tells the
# story (alone at +-15, a full cab at +-30). It exits non-zero, and says why,
# if any of that is not true, so a broken setup is found here and not on camera.
#
# Needs LocalStack and the API already running. For the "third seat" row on a
# confirmed pair, start the API with the trip on a later day:
#   EXODUS_TRAVEL_DATE=$(TZ=Asia/Kolkata date -d tomorrow +%F) scripts/start_api.sh

set -euo pipefail
cd "$(dirname "$0")/.."
API="${1:-http://127.0.0.1:3000}"

fail() { echo "demo_reset FAILED: $*" >&2; exit 1; }

curl -s -o /dev/null -m 5 "$API/board" \
  || fail "the API at $API isn't answering. Start it with scripts/start_api.sh (and 'docker compose up -d' for LocalStack)."

# A timer loop would regroup people mid-take.
for pid in $(ps -eo pid,args | grep "[s]cheduler.sh" | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && echo "stopped a scheduler (pid $pid): it would fire releases during the demo"
done

.venv/bin/python create_tables.py >/dev/null || fail "couldn't reset the tables. Is LocalStack up? (docker compose up -d)"
scripts/seed.py --count 40 --seed 3 | tail -1

.venv/bin/python - "$API" <<'PY'
import json, sys, urllib.request

API = sys.argv[1]
problems = []


def call(method, path, email=None, body=None):
    req = urllib.request.Request(
        API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"X-Student-Email": email} if email else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


board = call("GET", "/board")["routes"]["COLLEGE_AIRPORT"]
if board["pool_size"] != 40:
    problems.append(f"expected 40 students waiting on College -> Airport, found {board['pool_size']}")
if board["last_release"]:
    problems.append("a release has already run; the tables were not really reset")

for sid, want in (("imt2022201", None), ("imt2022130", "PENDING"), ("imt2022117", "PENDING")):
    me = call("GET", "/requests/me", f"{sid}@iiitb.ac.in")
    got = (me["request"] or {}).get("status")
    if got != want:
        problems.append(f"{sid}: expected {'no request' if want is None else want}, found {got}")

advice = call("POST", "/advise", "imt2022201@iiitb.ac.in",
              {"route": "COLLEGE_AIRPORT", "p": 1140, "b": 15, "a": 15})["message"]
if "you would travel alone" not in advice or "Widening helps" not in advice:
    problems.append("7 pm +-15 no longer leaves the new user alone with widening helping:\n  " + advice)
wider = call("POST", "/advise", "imt2022201@iiitb.ac.in",
             {"route": "COLLEGE_AIRPORT", "p": 1140, "b": 30, "a": 30})["message"]
if "you would share a cab with 2 other students" not in wider:
    problems.append("7 pm +-30 no longer lands a full cab:\n  " + wider)

if problems:
    print("demo_reset FAILED:\n- " + "\n- ".join(problems), file=sys.stderr)
    sys.exit(1)
print("checked: 40 waiting, nobody released, demo accounts as expected, 7 pm +-15 -> alone, +-30 -> full cab")
PY

# The third-seat row needs the trip to be on a later day than the release.
tdate=$(ps -eo args | grep "[s]am local start-api" | sed -n 's/.*TravelDate=\([0-9-]*\).*/\1/p' | head -1)
today=$(TZ=Asia/Kolkata date +%F)
if [ -z "$tdate" ] || [ ! "$tdate" \> "$today" ]; then
  echo "NOTE: the API has no future travel date (${tdate:-none}), so a confirmed pair will say the seat is NOT being filled." >&2
  echo "      Restart it with EXODUS_TRAVEL_DATE=$(TZ=Asia/Kolkata date -d tomorrow +%F) scripts/start_api.sh if you want to show 'Third seat: Open'." >&2
else
  echo "travel date on the API: $tdate (a confirmed pair will show 'Third seat: Open')"
fi

cat <<EOF

READY. Open three private windows:
  W1  http://127.0.0.1:8090/?demo=1   imt2022201@iiitb.ac.in   (new user: the form opens on 7 pm, +-15)
  W2  http://127.0.0.1:8090/          imt2022130@iiitb.ac.in   (Krishna Gowda, backs out)
  W3  http://127.0.0.1:8090/          imt2022117@iiitb.ac.in   (Ujwal Dutta, stays)
Any password works. In W1, the "Run a release now" button is in the Release board card.
EOF
