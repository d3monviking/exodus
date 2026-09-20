#!/usr/bin/env python3
"""
Gate 2, end to end over HTTP: the loop closes.

  seed 3 -> release -> one member declines "not with this person" by name -> the
  other two are asked to carry on as a pair -> one would rather split, so it
  dissolves and everyone is back in the pool -> next release routes around the
  block -> the remaining pair accepts -> CONFIRMED, contacts revealed.

Start from empty tables (.venv/bin/python create_tables.py) and a running API.
Stdlib only. Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from seed import make_requests, post  # noqa: E402

failures = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail and not ok else ""))


def call(api: str, method: str, path: str, email: str | None = None, body: dict | None = None):
    req = urllib.request.Request(
        api + path, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **({"X-Student-Email": email} if email else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://127.0.0.1:3000")
    api = ap.parse_args().api
    e1, e2, e3 = (f"imt2022{n}@iiitb.ac.in" for n in (101, 102, 103))
    mine = lambda e: call(api, "GET", "/requests/me", e)[1]  # noqa: E731

    print("1. seed three students and release")
    code, board = call(api, "GET", "/board")
    if code != 200:
        print(f"  GET /board returned {code}: {board}\n  is LocalStack up and are the tables created?")
        return 2
    if board["routes"]["COLLEGE_AIRPORT"]["pool_size"]:
        print("  the pool isn't empty; run create_tables.py first")
        return 2
    for email, body in make_requests(3, "COLLEGE_AIRPORT", 0, 101):
        post(api, email, body)
    _, rel = call(api, "POST", "/internal/release?force=1")
    check("one triple formed", rel["COLLEGE_AIRPORT"]["stats"]["groups_of_3"] == 1)
    p = mine(e1)["proposal"]
    check("proposal is FORMED, size 3, naming the other two",
          p["state"] == "FORMED" and p["size"] == 3
          and [o["student_id"] for o in p["others"]] == ["imt2022102", "imt2022103"], json.dumps(p.get("others")))
    gid = p["group_id"]

    print("2. imt2022101 declines: 'not with this person' (imt2022103); the other two are asked to carry on")
    code, body = call(api, "POST", f"/groups/{gid}/respond", e1,
                      {"action": "decline", "reason": "PERSON", "payload": {"named_student_id": "imt2022103"}})
    check("decline accepted; the cab carries on without them", code == 200 and body["state"] == "REDUCED", str(body))
    check("the decliner is told when the next release is", bool(body.get("next_release_at")), str(body))
    check("101 is back in the pool", mine(e1)["request"]["status"] == "PENDING" and mine(e1)["proposal"] is None)
    asked = [mine(e)["proposal"] for e in (e2, e3)]
    check("102 and 103 are asked whether to stay as a pair, at the same time",
          all(q and q["reduced"] and q["size"] == 2 and q["departure_time"] == p["departure_time"]
              and q["left_by"]["student_id"] == "imt2022101" for q in asked), json.dumps(asked))

    print("2b. imt2022103 would rather split, so the pair dissolves")
    code, body = call(api, "POST", f"/groups/{gid}/respond", e3, {"action": "decline", "reason": "TIME"})
    check("the pair dissolves", code == 200 and body["state"] == "DISSOLVED", str(body))
    check("everyone is back in the pool",
          all(mine(e)["request"]["status"] == "PENDING" and mine(e)["proposal"] is None for e in (e1, e2, e3)))
    told = mine(e2)["last_outcome"]
    check("102 is told 103 backed out", told and told["cause"] == "declined"
          and told["who"]["student_id"] == "imt2022103", json.dumps(told))
    _, board = call(api, "GET", "/board")
    check("board shows all 3 pending again", board["routes"]["COLLEGE_AIRPORT"]["pool_size"] == 3)

    print("3. next release routes around the block")
    _, rel = call(api, "POST", "/internal/release?force=1")
    route = rel["COLLEGE_AIRPORT"]
    together = [g for g in route["groups"] if {"imt2022101", "imt2022103"} <= set(g["members"])]
    check("101 and 103 are not together", not together)
    check("a pair formed and one student is left ungrouped",
          route["stats"]["groups_of_2"] == 1 and route["stats"]["ungrouped"] == 1, json.dumps(route["stats"]))
    check("a different group than the one declined", route["groups"][0]["group_id"] != gid)

    print("4. the pair accepts")
    p1 = mine(e1)["proposal"]
    check("101 has a pair proposal", p1 and p1["size"] == 2)
    gid2 = p1["group_id"]
    call(api, "POST", f"/groups/{gid2}/respond", e1, {"action": "accept"})
    partner = e2 if mine(e2)["proposal"] else e3
    _, body = call(api, "POST", f"/groups/{gid2}/respond", partner, {"action": "accept"})
    check("group CONFIRMED", body.get("state") == "CONFIRMED", str(body))
    after = mine(e1)
    check("101 is CONFIRMED, with the partner's contact",
          after["request"]["status"] == "CONFIRMED"
          and [o["email"] for o in after["proposal"]["others"]] == [partner], str(after["proposal"]))
    left_out = e3 if partner == e2 else e2
    check("the left-out student is still PENDING, no proposal",
          mine(left_out)["request"]["status"] == "PENDING" and mine(left_out)["proposal"] is None)

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{failures} CHECK(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
