"""GET /requests/me — the caller's request, plus their proposal if one exists."""

from __future__ import annotations

from cedar_authz import is_permitted
from config import EMAIL_DOMAIN
from handlers._common import identify, response
from repo import get_repo
from roster import name_for


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
        proposal["others"] = [{"student_id": m, "name": name_for(m), "email": f"{m}@{EMAIL_DOMAIN}"}
                              for m in sorted(members) if m != who["id"]]
    return proposal


def _last_outcome(repo, request: dict, me: str) -> dict | None:
    """What happened to the cab this student was last in, if it fell through.

    The reason someone gave is deliberately not exposed: "they didn't want to
    travel with you" is not a thing to put on a screen. Who, and whether it was
    a decline or silence, is enough to explain why the group is gone.
    """
    group = repo.get_group(request["last_group_id"]) if request.get("last_group_id") else None
    if group is None or group["state"] != "DISSOLVED":
        return None
    by = group.get("declined_by")
    return {
        "group_id": group["group_id"],
        "departure_time": group["departure_time"],
        "cause": "you_declined" if by == me else ("timeout" if group.get("dissolved_reason") == "TIMEOUT" else "declined"),
        "who": None if by in (None, me) else {"student_id": by, "name": name_for(by)},
    }


def handler(event, context):
    who = identify(event)
    if who is None:
        return response(401, {"error": "missing or malformed X-Student-Email header"})

    me = {"student_id": who["id"], "name": name_for(who["id"]), "email": who["email"]}
    repo = get_repo()
    request = repo.get_request(who["id"])
    if request is None:
        return response(200, {"me": me, "request": None, "proposal": None, "last_outcome": None})

    # blocked_with is symmetric, so returning it would reveal who blocked this student
    request.pop("blocked_with", None)

    group = repo.group_for(who["id"])
    proposal = _proposal(group, who) if group and group["state"] in ("FORMED", "CONFIRMED") else None
    last_outcome = None if proposal else _last_outcome(repo, request, who["id"])
    return response(200, {"me": me, "request": request, "proposal": proposal, "last_outcome": last_outcome})
