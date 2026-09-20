#!/usr/bin/env python3
"""
The whole backend end to end over HTTP, against LocalStack and the real solver.

Covers what gate2.py doesn't: every decline reason, the decline budget and the
sit-out, the deadline sweep, advice before a release, all four routes at once,
and a 120-student pool checked against the solver run offline on the same pool.

Start from empty tables (.venv/bin/python create_tables.py) and a running API.
Needs boto3 for one thing only: backdating an accept deadline, so the sweep can
be tested without waiting out the accept window.

  scripts/e2e.py
  scripts/e2e.py --api http://127.0.0.1:3000

Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CONFIG  # noqa: E402
from poolgen import generate  # noqa: E402
from solver import solve  # noqa: E402

API = "http://127.0.0.1:3000"
failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail and not ok else ""))


def section(title: str) -> None:
    print(f"\n{title}")


def call(method: str, path: str, email: str | None = None, body: dict | None = None):
    req = urllib.request.Request(
        API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"X-Student-Email": email} if email else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def email(sid: str) -> str:
    return f"{sid}@iiitb.ac.in"


def submit(sid: str, route: str, p: int, b: int, a: int, min_group_size: int | None = None):
    body = {"route": route, "p": p, "b": b, "a": a}
    if min_group_size is not None:
        body["min_group_size"] = min_group_size
    return call("POST", "/requests", email(sid), body)


def mine(sid: str) -> dict:
    return call("GET", "/requests/me", email(sid))[1]


def release() -> dict:
    return call("POST", "/internal/release?force=1")[1]


def sweep() -> dict:
    return call("POST", "/internal/sweep")[1]


def respond(sid: str, group_id: str, action: str, reason: str | None = None, payload: dict | None = None):
    body = {"action": action}
    if reason:
        body["reason"] = reason
    if payload:
        body["payload"] = payload
    return call("POST", f"/groups/{group_id}/respond", email(sid), body)


def group_of(sid: str) -> dict | None:
    return mine(sid).get("proposal")


def seed_pool(pool: list[dict]) -> int:
    ok = 0
    for r in pool:
        status, _ = submit(r["student_id"], r["route"], r["p"], r["b"], r["a"], r["min_group_size"])
        ok += status == 201
    return ok


def backdate_deadline(group_id: str) -> None:
    """Push a group's accept deadline into the past, so the sweep treats it as a timeout."""
    os.environ.setdefault("EXODUS_REPO", "dynamo")
    from repo_dynamo import DynamoRepo

    DynamoRepo().groups.update_item(
        Key={"group_id": group_id},
        UpdateExpression="SET accept_deadline = :t",
        ExpressionAttributeValues={":t": int(time.time()) - 60},
    )


# ---- scenarios ---------------------------------------------------------------

def lifecycle_scenarios() -> None:
    """Each decline reason, on its own cluster of a route so they can't mix.

    Windows are +-10 minutes and clusters are hours apart, so no student from
    one scenario is ever feasible with another's.
    """
    route = "STATION_COLLEGE"
    trios = {"widen": (360, 1), "budget": (480, 11), "toofew": (600, 21), "plans": (720, 31)}
    for name, (centre, first) in trios.items():
        for i in range(3):
            submit(f"imt2023{first + i:03d}", route, centre + 5 * i, 10, 10)
    release()

    section("1. 'time doesn't work', widened: the window changes and no budget is spent")
    g = group_of("imt2023001")
    code, body = respond("imt2023001", g["group_id"], "decline", "TIME", {"b": 60, "a": 5})
    r = mine("imt2023001")["request"]
    check("group dissolved", code == 200 and body["state"] == "DISSOLVED", str(body))
    check("the window widened and never narrowed", (r["b"], r["a"]) == (60, 10), f"b={r['b']} a={r['a']}")
    check("no decline budget spent", r["decline_count"] == 0, str(r["decline_count"]))
    check("the declined time is remembered as an anchor",
          r["declined_anchors"] == [{"T": g["departure_time"], "size": 3}], str(r["declined_anchors"]))
    check("everyone is back in the pool",
          all(mine(f"imt2023{1 + i:03d}")["request"]["status"] == "PENDING" for i in range(3)))

    section("2. 'too few people': only full cabs from now on")
    g = group_of("imt2023021")
    respond("imt2023021", g["group_id"], "decline", "TOO_FEW")
    r = mine("imt2023021")["request"]
    check("min_group_size is now a full cab", r["min_group_size"] == CONFIG["max_group"])
    check("no budget spent for a real change", r["decline_count"] == 0)

    section("3. 'plans changed': the request is gone, the others are freed")
    g = group_of("imt2023031")
    respond("imt2023031", g["group_id"], "decline", "PLANS_CHANGED")
    check("the request is withdrawn", mine("imt2023031")["request"] is None)
    check("the other two are pending again",
          all(mine(f"imt2023{31 + i:03d}")["request"]["status"] == "PENDING" for i in (1, 2)))

    section("4. the decline budget: two no-change declines, then a release sat out")
    same = {"b": 10, "a": 10}  # exactly their current window: nothing changes
    for n in (1, 2):
        g = group_of("imt2023011")
        if g is None:
            release()
            g = group_of("imt2023011")
        respond("imt2023011", g["group_id"], "decline", "TIME", same)
        check(f"decline {n} spent budget", mine("imt2023011")["request"]["decline_count"] == n,
              str(mine("imt2023011")["request"]["decline_count"]))
    out = release()
    check("the student sits out this release", "imt2023011" in out["STATION_COLLEGE"]["sat_out"],
          json.dumps(out["STATION_COLLEGE"]["sat_out"]))
    check("and is in no group", all("imt2023011" not in g["members"] for g in out["STATION_COLLEGE"]["groups"]))
    check("status says SAT_OUT", mine("imt2023011")["request"]["status"] == "SAT_OUT")
    out = release()
    check("the next release revives them with a fresh budget",
          "imt2023011" in out["STATION_COLLEGE"]["revived"] and mine("imt2023011")["request"]["decline_count"] == 0)


def timeout_sweep() -> None:
    section("5. silence past the deadline is a TIMEOUT decline")
    route = "AIRPORT_COLLEGE"
    for i in range(3):
        submit(f"imt2023{41 + i:03d}", route, 900 + 5 * i, 10, 10)
    release()
    g = group_of("imt2023041")
    check("a group formed", g is not None and g["state"] == "FORMED")
    respond("imt2023041", g["group_id"], "accept")  # one accepts, two stay silent

    out = sweep()
    check("nothing is swept before the deadline", not out["dissolved"], json.dumps(out))
    backdate_deadline(g["group_id"])
    out = sweep()
    check("the stale group is dissolved", any(d["group_id"] == g["group_id"] for d in out["dissolved"]),
          json.dumps(out))
    check("only the silent two are penalised",
          mine("imt2023041")["request"]["decline_count"] == 0
          and all(mine(f"imt2023{41 + i:03d}")["request"]["decline_count"] == 1 for i in (1, 2)))
    check("everyone is back in the pool",
          all(mine(f"imt2023{41 + i:03d}")["request"]["status"] == "PENDING" for i in range(3)))


def advice_before_a_release() -> None:
    section("6. advice, on a pool that is still open")
    route = "COLLEGE_AIRPORT"
    pool = generate(40, route, seed=3)
    check("40 requests submitted", seed_pool(pool) == 40)

    code, body = call("POST", "/advise", email("imt2022999"), {"route": route, "p": 1025, "b": 15, "a": 15})
    check("advice returns 200", code == 200, json.dumps(body))
    check("it counts the real pool", body["pool"]["waiting"] == 40, json.dumps(body.get("pool")))
    check("it simulates an outcome", body["simulated_outcome"]["outcome"] in ("group", "alone"))
    check("the message says what the window gets you",
          "15 minutes earlier and 15 minutes later" in body["message"], body["message"])
    check("asking for advice creates no request", mine("imt2022999")["request"] is None)
    print(f"      {body['message']}")

    code, body = call("POST", "/advise", email("imt2022999"), {"route": route, "p": 1025, "b": 0, "a": 15})
    check("a window the form couldn't produce is rejected", code == 400, str(body))


def release_matches_the_solver() -> None:
    section("7. the live release equals the solver run offline on the same pool")
    route = "COLLEGE_AIRPORT"
    pool = generate(40, route, seed=3)  # already submitted in section 6
    offline = solve(pool, CONFIG)
    started = time.monotonic()
    out = release()[route]
    print(f"      release took {time.monotonic() - started:.1f}s")

    check("same stats", out["stats"] == offline["stats"],
          f"live={json.dumps(out['stats'])} offline={json.dumps(offline['stats'])}")
    live_groups = {tuple(sorted(g["members"])) for g in out["groups"]}
    check("same groups", live_groups == {tuple(sorted(g["members"])) for g in offline["groups"]})
    check("every departure time is on the 5-minute grid",
          all(g["departure_time"] % CONFIG["grid_minutes"] == 0 for g in out["groups"]))
    by_id = {r["student_id"]: r for r in pool}
    feasible = all(by_id[m]["p"] - by_id[m]["b"] <= g["departure_time"] <= by_id[m]["p"] + by_id[m]["a"]
                   for g in out["groups"] for m in g["members"])
    check("every member can actually make their group's time", feasible)
    check("a full-cab request is never put in a pair",
          all(len(g["members"]) == 3 for g in out["groups"]
              if any(by_id[m]["min_group_size"] == 3 for m in g["members"])))

    section("8. each member is told why, naming nobody")
    member = next(m for g in out["groups"] for m in g["members"])
    p = group_of(member)
    text = p["explanation"] or ""
    check("the proposal carries an explanation", bool(text), text)
    check("it states the fare split", "ways" in text, text)
    check("it names no other student", not any(o in text for o in by_id if o != member), text)
    print(f"      {text}")

    rel = call("GET", "/board")[1]["routes"][route].get("last_release")
    check("the board reports the release", rel is not None and rel["pool_size"] == 40, json.dumps(rel))


def a_bigger_pool() -> None:
    section("9. a 120-student pool")
    route = "COLLEGE_STATION"
    # a separate id range: section 6's students already exist, and a resubmission
    # would either be refused (409) or move that student to this route
    pool = generate(120, route, seed=5, first_id=301)
    check("120 requests submitted", seed_pool(pool) == 120)
    started = time.monotonic()
    out = release()[route]
    took = time.monotonic() - started
    print(f"      release took {took:.1f}s -> {json.dumps(out['stats'])}")
    check("it matches the solver offline", out["stats"] == solve(pool, CONFIG)["stats"],
          json.dumps(out["stats"]))
    check("well inside the Lambda timeout", took < 15, f"{took:.1f}s")
    seen = [m for g in out["groups"] for m in g["members"]]
    check("nobody is in two cabs", len(seen) == len(set(seen)))
    check("everyone is grouped or ungrouped, exactly once",
          len(seen) + out["stats"]["ungrouped"] == 120, f"{len(seen)} + {out['stats']['ungrouped']}")


def all_four_routes() -> None:
    section("10. all four routes in one release")
    from config import ROUTES

    for i, route in enumerate(ROUTES):
        for j in range(3):
            submit(f"imt2024{i * 10 + j + 1:03d}", route, 1000 + 5 * j, 15, 15)
    out = release()
    for route in ROUTES:
        check(f"{route} released", out[route]["status"] == "released", json.dumps(out[route])[:120])
    board = call("GET", "/board")[1]["routes"]
    check("the board knows every route", set(board) == set(ROUTES))


def main() -> int:
    global API
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=API)
    API = ap.parse_args().api

    code, board = call("GET", "/board")
    if code != 200:
        print(f"GET /board returned {code}: {board}\nis LocalStack up and are the tables created?")
        return 2
    if any(r["pool_size"] for r in board["routes"].values()):
        print("the pool isn't empty; run create_tables.py first")
        return 2

    lifecycle_scenarios()
    timeout_sweep()
    advice_before_a_release()
    release_matches_the_solver()
    a_bigger_pool()
    all_four_routes()

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
