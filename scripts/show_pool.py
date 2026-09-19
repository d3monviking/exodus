#!/usr/bin/env python3
"""
Print each request exactly as it leaves the repo -- i.e. as the solver receives it.
Most Gate 1 failures are shape mismatches; diffing this against the contract
turns a long argument into a short one.

  AWS_ENDPOINT_URL=http://localhost:4566 EXODUS_REPO=dynamo .venv/bin/python scripts/show_pool.py [ROUTE]
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("EXODUS_REPO", "dynamo")
os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:4566")

from config import ROUTES  # noqa: E402
from repo import get_repo  # noqa: E402

repo = get_repo()
for route in sys.argv[1:] or ROUTES:
    pool = repo.open_pool(route)
    print(f"# {route}: {len(pool)} open")
    for r in sorted(pool, key=lambda r: r["p"]):
        print(json.dumps(r, sort_keys=True))
        for f in ("p", "b", "a", "min_group_size"):
            assert type(r[f]) is int, f"{r['student_id']}.{f} is {type(r[f]).__name__}, solver needs int"
