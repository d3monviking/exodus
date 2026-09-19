#!/usr/bin/env python3
"""
Seed the pool through the real API (so Cedar and validation run too).
Stdlib only. Deterministic for a given --seed, so rehearsals are repeatable.

  scripts/seed.py                    # 3 students on COLLEGE_AIRPORT: one clean triple
  scripts/seed.py --count 40 --seed 3   # the demo pool

Any other pool comes from poolgen, the same generator the solver is tuned on,
so a seeded board shows exactly what `scripts/sweep.py --as-submitted` reports
for that seed. Only route, p, b and a are sent: that's all POST /requests takes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from poolgen import generate  # noqa: E402

ROUTES = ["COLLEGE_AIRPORT", "COLLEGE_STATION", "AIRPORT_COLLEGE", "STATION_COLLEGE"]


def make_requests(count: int, route: str, seed: int, first_id: int) -> list[tuple[str, dict]]:
    if count == 3 and seed == 0:
        # hand-picked so the three windows overlap: a triple leaving at 5:10 pm (1030)
        spec = [(1020, 30, 30), (1030, 20, 20), (1040, 15, 30)]
    else:
        spec = [(r["p"], r["b"], r["a"]) for r in generate(count, route, seed, first_id=first_id)]
    return [
        (f"imt2022{first_id + i}@iiitb.ac.in", {"route": route, "p": p, "b": b, "a": a})
        for i, (p, b, a) in enumerate(spec)
    ]


def post(api: str, email: str, body: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        f"{api}/requests",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Student-Email": email},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://127.0.0.1:3000")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--route", default="COLLEGE_AIRPORT", choices=ROUTES)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--first-id", type=int, default=101, help="numeric suffix of the first student id")
    args = ap.parse_args()

    failures = 0
    for email, body in make_requests(args.count, args.route, args.seed, args.first_id):
        status, text = post(args.api, email, body)
        ok = status == 201
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {status} {email.split('@')[0]} p={body['p']} b={body['b']} a={body['a']}"
              + ("" if ok else f"  {text}"))
    print(f"seeded {args.count - failures}/{args.count}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
