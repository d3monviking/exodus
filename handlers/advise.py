"""
POST /advise  body: {route, p, b, a}

What a window is likely to get you, before you submit it. Runs the real solver
on the current open pool plus the hypothetical request (advisor.py), so the
answer is the grouping algorithm's, not a guess. Nothing is written.

Contract 11.7: returns {message, simulated_outcome}. Every time in the body is
minutes since midnight.
"""

from __future__ import annotations

from advisor import advise
from cedar_authz import is_permitted
from config import ROUTES
from handlers._common import identify, parse_body, response
from repo import get_repo

MAX_FLEX_MINUTES = 240


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def handler(event, context):
    who = identify(event)
    if who is None:
        return response(401, {"error": "missing or malformed X-Student-Email header"})

    # Same gate as creating a request: advice reads the pool, so only someone
    # who could join it may ask about it.
    if not is_permitted(who, "CreateRequest", {"type": "Request", "id": who["id"]}):
        return response(403, {"error": "only @iiitb.ac.in addresses may ask for advice"})

    try:
        body = parse_body(event)
    except ValueError:
        return response(400, {"error": "body is not valid JSON"})

    for field in ("route", "p", "b", "a"):
        if field not in body:
            return response(400, {"error": f"missing field: {field}"})
    if body["route"] not in ROUTES:
        return response(400, {"error": f"invalid route: {body['route']}"})
    if not all(_is_int(body[f]) for f in ("p", "b", "a")):
        return response(400, {"error": "p, b and a must be integers (minutes)"})
    if not 0 <= body["p"] < 1440:
        return response(400, {"error": "p must be minutes since midnight, 0-1439"})
    if not (5 <= body["b"] <= MAX_FLEX_MINUTES and 5 <= body["a"] <= MAX_FLEX_MINUTES):
        return response(400, {"error": f"b and a must each be between 5 and {MAX_FLEX_MINUTES}"})

    # The caller's own pending request, if any, is left out of the simulation:
    # otherwise they would be grouped with their old window as well as the new one.
    out = advise(body["route"], body["p"], body["b"], body["a"], get_repo(), student_id=who["id"])
    return response(200, {"message": out["text"], "simulated_outcome": out["facts"]["current"],
                          "pool": out["facts"]["density"],
                          "better_window": out["facts"]["better"]})
