"""GET /requests/me — the caller's request, plus their proposal if one exists."""

from __future__ import annotations

from cedar_authz import is_permitted
from handlers._common import identify, response
from repo import get_repo

EMAIL_DOMAIN = "iiitb.ac.in"


def _proposal(group: dict, who: dict) -> dict:
    members = group["members"]
    proposal = {
        "group_id": group["group_id"],
        "state": group["state"],
        "departure_time": group["departure_time"],
        "accept_deadline": group.get("accept_deadline"),
        "size": len(members),
        "my_response": (group.get("responses") or {}).get(who["id"]),
        "explanation": group.get("explanations", {}).get(who["id"]),
        "contacts": None,
    }
    resource = {
        "type": "Group",
        "id": group["group_id"],
        "members": members,
        "state": group["state"],
    }
    if is_permitted(who, "ViewContactDetails", resource):
        proposal["contacts"] = [f"{m}@{EMAIL_DOMAIN}" for m in members if m != who["id"]]
    return proposal


def handler(event, context):
    who = identify(event)
    if who is None:
        return response(401, {"error": "missing or malformed X-Student-Email header"})

    repo = get_repo()
    request = repo.get_request(who["id"])
    if request is None:
        return response(200, {"request": None, "proposal": None})

    # blocked_with is symmetric, so returning it would reveal who blocked this student
    request.pop("blocked_with", None)

    group = repo.group_for(who["id"])
    proposal = _proposal(group, who) if group and group["state"] in ("FORMED", "CONFIRMED") else None
    return response(200, {"request": request, "proposal": proposal})
