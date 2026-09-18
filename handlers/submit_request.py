"""POST /requests — validate, write PENDING.

Cedar domain check lands in hours 8-14; this is deliberately just enough to
prove the handler can reach LocalStack for Gate 1.
"""

from __future__ import annotations

from config import ROUTES
from handlers._common import parse_body, response
from repo import get_repo

REQUIRED_FIELDS = ("student_id", "route", "p", "b", "a")


def handler(event, context):
    body = parse_body(event)

    missing = [f for f in REQUIRED_FIELDS if f not in body]
    if missing:
        return response(400, {"error": f"missing fields: {missing}"})
    if body["route"] not in ROUTES:
        return response(400, {"error": f"invalid route: {body['route']}"})
    if body["b"] < 5 or body["a"] < 5:
        return response(400, {"error": "b and a must each be at least 5"})

    request = {
        "student_id": body["student_id"],
        "route": body["route"],
        "p": int(body["p"]),
        "b": int(body["b"]),
        "a": int(body["a"]),
        "min_group_size": int(body.get("min_group_size", 2)),
        "status": "PENDING",
        "decline_count": 0,
        "declined_anchors": [],
    }

    repo = get_repo()
    repo.put_request(request)

    return response(201, request)
