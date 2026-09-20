"""GET /requests/me — the caller's request, plus their proposal if one exists."""

from __future__ import annotations

from cedar_authz import is_permitted
from config import EMAIL_DOMAIN
from handlers._common import identify, response
from repo import get_repo


def _proposal(group: dict, who: dict) -> dict:
    members = group["members"]
    proposal = {
        "group_id": group["group_id"],
        "state": group["state"],
        "departure_time": group["departure_time"],
        "accept_deadline": group.get("accept_deadline"),
        "size": len(members),
        "my_response": (group.get("responses") or {}).get(who["id"]),
        "accepted": sum(1 for m in members if (group.get("responses") or {}).get(m) is True),
        "explanation": group.get("explanations", {}).get(who["id"]),
        "others": [],
    }
    resource = {"type": "Group", "id": group["group_id"], "members": members, "state": group["state"]}
    # Cedar decides whether this caller may see who else is in the cab. They can:
    # naming someone in a "not with this person" decline needs a name.
    if is_permitted(who, "ViewContactDetails", resource):
        proposal["others"] = [{"student_id": m, "email": f"{m}@{EMAIL_DOMAIN}"}
                              for m in sorted(members) if m != who["id"]]
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
