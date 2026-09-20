"""POST /requests — identify, Cedar domain check, validate, write PENDING."""

from __future__ import annotations

from cedar_authz import is_permitted
from config import CONFIG, ROUTES
from handlers._common import identify, parse_body, response
from repo import get_repo

MAX_FLEX_MINUTES = 240


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def handler(event, context):
    who = identify(event)
    if who is None:
        return response(401, {"error": "missing or malformed X-Student-Email header"})

    if not is_permitted(who, "CreateRequest", {"type": "Request", "id": who["id"]}):
        return response(403, {"error": "only @iiitb.ac.in addresses may submit a request"})

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
    if "min_group_size" in body and not (_is_int(body["min_group_size"])
                                         and 2 <= body["min_group_size"] <= CONFIG["max_group"]):
        return response(400, {"error": f"min_group_size must be an integer between 2 and {CONFIG['max_group']}"})

    repo = get_repo()
    existing = repo.get_request(who["id"])
    if existing and existing["status"] in ("GROUPED", "CONFIRMED"):
        return response(409, {"error": f"you already have a request in status {existing['status']}"})

    request = {
        "student_id": who["id"],
        "route": body["route"],
        "p": body["p"],
        "b": body["b"],
        "a": body["a"],
        # a student may ask for a full cab up front; a TOO_FEW decline sets it later
        "min_group_size": body.get("min_group_size", (existing or {}).get("min_group_size", 2)),
        "status": "SAT_OUT" if (existing or {}).get("status") == "SAT_OUT" else "PENDING",
        "decline_count": (existing or {}).get("decline_count", 0),
        "declined_anchors": (existing or {}).get("declined_anchors", []),
    }
    repo.put_request(request)
    return response(201, request)
