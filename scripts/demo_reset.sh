#!/usr/bin/env bash
# Put the demo back on its starting line, and prove it is there.
#
#   scripts/demo_reset.sh [api_base_url]
#
# Resets the tables, seeds the demo pool (nobody released yet), stops any
# scheduler that would fire releases underneath a recording, then checks that
# the demo will behave. It exits non-zero, and says why, if any of that is not
# true, so a broken setup is found here and not on camera.
#
# The demo pool is poolgen seed 396, chosen by search because the new user's
# cab is the same two people whatever reasonable window they type. Most seeds
# have four or five other students near 7 pm, so a slightly different time or
# flexibility swaps someone in; here nobody else is close enough to compete.
#
# Needs LocalStack and the API already running. For the "third seat" row on a
# confirmed pair, start the API with the trip on a later day:
#   EXODUS_TRAVEL_DATE=$(TZ=Asia/Kolkata date -d tomorrow +%F) scripts/start_api.sh

set -euo pipefail
cd "$(dirname "$0")/.."
API="${1:-http://127.0.0.1:3000}"

DEMO_SEED=396
NEW=imt2022201        # Anirudh Hegde: signs up live, in W1
STAYER=imt2022107     # Rashmi Kulkarni: wants 7:15 pm exactly, stays (W3)
DECLINER=imt2022125   # Vikram Das: wants 7:40 pm, pushed 25 min early, backs out (W2)

fail() { echo "demo_reset FAILED: $*" >&2; exit 1; }

curl -s -o /dev/null -m 5 "$API/board" \
  || fail "the API at $API isn't answering. Start it with scripts/start_api.sh (and 'docker compose up -d' for LocalStack)."

# A timer loop would regroup people mid-take.
for pid in $(ps -eo pid,args | grep "[s]cheduler.sh" | awk '{print $1}'); do
  kill "$pid" 2>/dev/null && echo "stopped a scheduler (pid $pid): it would fire releases during the demo"
done

.venv/bin/python create_tables.py >/dev/null || fail "couldn't reset the tables. Is LocalStack up? (docker compose up -d)"
scripts/seed.py --count 40 --seed "$DEMO_SEED" | tail -1

.venv/bin/python - "$API" "$DEMO_SEED" "$NEW" "$STAYER" "$DECLINER" <<'PY'
import json, sys, urllib.request

sys.path.insert(0, ".")
from config import CONFIG
from poolgen import generate
from solver import solve

API, SEED, NEW, STAYER, DECLINER = sys.argv[1], int(sys.argv[2]), *sys.argv[3:6]
problems = []


def call(method, path, email=None, body=None):
    req = urllib.request.Request(
        API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"X-Student-Email": email} if email else {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


# ---- the live system is in the state the script expects --------------------------------
board = call("GET", "/board")["routes"]["COLLEGE_AIRPORT"]
if board["pool_size"] != 40:
    problems.append(f"expected 40 students waiting on College -> Airport, found {board['pool_size']}")
if board["last_release"]:
    problems.append("a release has already run; the tables were not really reset")

for sid, want in ((NEW, None), (DECLINER, "PENDING"), (STAYER, "PENDING")):
    got = (call("GET", "/requests/me", f"{sid}@iiitb.ac.in")["request"] or {}).get("status")
    if got != want:
        problems.append(f"{sid}: expected {'no request' if want is None else want}, found {got}")

advice = call("POST", "/advise", f"{NEW}@iiitb.ac.in", {"route": "COLLEGE_AIRPORT", "p": 1140, "b": 15, "a": 15})["message"]
if "you would travel alone" not in advice or "Widening helps" not in advice:
    problems.append("7 pm +-15 no longer leaves the new user alone with widening helping:\n  " + advice)
wider = call("POST", "/advise", f"{NEW}@iiitb.ac.in", {"route": "COLLEGE_AIRPORT", "p": 1140, "b": 30, "a": 30})["message"]
if "you would share a cab with 2 other students" not in wider:
    problems.append("7 pm +-30 no longer lands a full cab:\n  " + wider)

# ---- and the cab does not depend on exactly what gets typed ------------------------------
pool = generate(40, "COLLEGE_AIRPORT", seed=SEED)
cast = {STAYER, DECLINER}


def cab(p, b, a):
    me = {"student_id": NEW, "route": "COLLEGE_AIRPORT", "p": p, "b": b, "a": a, "min_group_size": 2,
          "blocked_with": [], "declined_anchors": []}
    out = solve(pool + [me], CONFIG)
    g = next((g for g in out["groups"] if NEW in g["members"]), None)
    return ({m for m in g["members"] if m != NEW}, g["departure_time"]) if g else (set(), None), out["stats"]


(mates, T), stats = cab(1140, 30, 30)
if mates != cast or T != 1155:
    problems.append(f"7 pm +-30 gives {sorted(mates)} at {T}, expected {sorted(cast)} at 1155 (7:15 pm)")
cabs = stats["groups_of_3"] + stats["groups_of_2"] + stats["ungrouped"]
if (stats["pool_size"], cabs) != (41, 15):
    problems.append(f"the release should be 41 students -> 15 cabs, the solver says {stats['pool_size']} -> {cabs}")

entries = [(p, b, a) for p in range(1140, 1161, 5) for b in range(30, 65, 5) for a in range(30, 65, 5)]
wrong = [(p, b, a) for p, b, a in entries if cab(p, b, a)[0][0] != cast]
if wrong:
    problems.append(f"{len(wrong)} of {len(entries)} plausible entries (7:00-7:20 pm, 30-60 min either side) "
                    f"put someone else in the cab, e.g. {wrong[:3]}")

if problems:
    print("demo_reset FAILED:\n- " + "\n- ".join(problems), file=sys.stderr)
    sys.exit(1)
print(f"checked: 40 waiting, nobody released, accounts as expected, 7 pm +-15 -> alone, +-30 -> full cab, "
      f"and all {len(entries)} entries from 7:00-7:20 pm with 30-60 min flexibility give the same two people")
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

READY. The cab will be you + Rashmi Kulkarni + Vikram Das, leaving 7:15 pm. Open three private windows:
  W1  http://127.0.0.1:8090/?demo=1   $NEW@iiitb.ac.in   Anirudh Hegde   (new user: the form opens on 7 pm, +-15)
  W2  http://127.0.0.1:8090/          $DECLINER@iiitb.ac.in   Vikram Das      (backs out)
  W3  http://127.0.0.1:8090/          $STAYER@iiitb.ac.in   Rashmi Kulkarni  (stays)
Any password works. In W1 the "Run a release now" button is in the Release board card.
Type 7:00 pm: at +-15 you'll be alone (the point), and any flexibility from +-30 to +-60 is always this cab.
EOF
